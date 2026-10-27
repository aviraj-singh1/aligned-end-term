import axios from 'axios';
import { validateCombinedUpload } from '../utils/transportLimits';
import type { GstrMergeCreatePayload, GstrMergeCreateResponse, GstrMergeRun, GstrMergeUploadTarget, HealthResponse, InspectionResponse, ReconciliationResponse, ResultsPageResponse, SessionStatusResponse } from '../types/api';

export function resolveApiBaseUrl(configured: string, browserOrigin: string): string {
  try {
    const apiUrl = new URL(configured);
    const browserUrl = new URL(browserOrigin);
    const loopback = (hostname: string) => hostname === 'localhost' || hostname === '127.0.0.1';
    if (apiUrl.protocol === 'http:' && browserUrl.protocol === 'http:'
      && loopback(apiUrl.hostname) && loopback(browserUrl.hostname)) {
      // Cookies are site-bound: localhost and 127.0.0.1 cannot be mixed even
      // though they reach the same machine. Follow the address used by Vite.
      apiUrl.hostname = browserUrl.hostname;
      return apiUrl.origin;
    }
  } catch {
    // Axios will surface an invalid configured URL through its normal error path.
  }
  return configured;
}

const CONFIGURED_API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const API_BASE_URL = resolveApiBaseUrl(CONFIGURED_API_BASE_URL, window.location.origin);

const api = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: import.meta.env.VITE_AUTH_ENABLED === 'true',
  timeout: 90_000,
});

let requestScope = new AbortController();
let expectedUserId: string | null = null;
export function setExpectedUserId(userId: string | null) {
  expectedUserId = userId;
}
const runVersions = new Map<string, number>();
const operationKeys = new Map<string, string>();
const uploadIdentities = new WeakMap<File, number>();
let nextUploadIdentity = 1;
api.interceptors.request.use(config => {
  if (!config.signal) config.signal = requestScope.signal;
  if (window.location.hash.startsWith('#/demo')) config.headers.set('X-ALIGNED-Demo', 'true');
  // Bind protected requests to the account displayed in this tab, not just a shared cookie.
  if (expectedUserId && config.url?.startsWith('/api/') && !config.url.startsWith('/api/auth/') && config.url !== '/api/me' && config.url !== '/api/health') {
    config.headers.set('X-Expected-User-ID', expectedUserId);
  }
  return config;
});
api.interceptors.response.use(response => response, error => {
  if (axios.isAxiosError(error) && (error.response?.data?.detail?.code === 'ACCOUNT_CHANGED' || error.response?.status === 403)) {
    window.dispatchEvent(new Event('aligned-account-changed'));
  }
  if (axios.isAxiosError(error) && error.response?.status === 401 && !error.config?.url?.startsWith('/api/auth/') && error.config?.url !== '/api/me') {
    window.dispatchEvent(new Event('aligned-session-expired'));
  }
  return Promise.reject(error);
});

export function clearRequestScope() {
  requestScope.abort();
  requestScope = new AbortController();
  runVersions.clear();
  operationKeys.clear();
}

export interface ReconciliationContext { workspaceId: string; clientId: string; gstinId?: string }
export type OpenRun = { version: number; phase: 'READY'; data: InspectionResponse } | { version: number; phase: 'COMPLETED'; data: ReconciliationResponse };
export interface RunHistoryItem { id: string; session_id: string; client_gstin_id: string | null; created_at: string; status: string; version: number; expires_at: string | null; availability: 'AVAILABLE' | 'EXPIRED' | 'DELETED' | 'UNAVAILABLE' | 'PENDING' | 'UNRESOLVED' }

function rememberVersion(sid: string, headers: Record<string, unknown> | undefined) {
  const value = Number(headers?.['x-run-version']);
  if (headers?.['x-run-version'] !== undefined && Number.isInteger(value)) runVersions.set(sid, value);
}

async function financialMutation<T>(method: 'POST' | 'DELETE', url: string, data: unknown, signature: string, headers: Record<string, string>) {
  const signal = requestScope.signal;
  const key = operationKeys.get(signature) || crypto.randomUUID();
  operationKeys.set(signature, key);
  const csrf = await api.get<{ csrf_token: string }>('/api/auth/csrf', { signal, withCredentials: true });
  try {
    const result = await api.request<T>({ method, url, data, signal, withCredentials: true,
      headers: { ...headers, 'X-CSRF-Token': csrf.data.csrf_token, 'Idempotency-Key': key } });
    operationKeys.delete(signature);
    return result;
  } catch (error) {
    // Retain keys after uncertain network/5xx failures for an explicit retry.
    if (axios.isAxiosError(error) && error.response && error.response.status < 500) operationKeys.delete(signature);
    throw error;
  }
}

export async function openOwnedRun(context: ReconciliationContext, sid: string, signal?: AbortSignal): Promise<OpenRun> {
  const result = await workspaceGet<OpenRun>(`${context.workspaceId}/clients/${context.clientId}/runs/${sid}/open`, signal);
  if (!signal?.aborted) runVersions.set(sid, result.version);
  return result;
}

export interface AuthProfile {
  user: { id: string; name: string; email: string; email_verified: boolean; phone_number?:string|null; phone_verified?:boolean; identity_providers?:string[] };
  workspaces: { id: string; name: string; type: 'INDIVIDUAL' | 'FIRM'; role: 'OWNER' | 'ADMIN' | 'ACCOUNTANT'; login_id?:string|null; login_namespace?:string|null }[];
  reconciliation_available: boolean;
}

export async function getAuthProfile(signal?: AbortSignal): Promise<AuthProfile | null> {
  try { return (await api.get<AuthProfile>('/api/me', { signal, withCredentials: true })).data; }
  catch (error) { if (axios.isAxiosError(error) && error.response?.status === 401) return null; throw error; }
}

async function authMutation<T>(path: string, data: unknown): Promise<T> {
  const csrf = await api.get<{ csrf_token: string }>('/api/auth/csrf', { withCredentials: true });
  return (await api.post<T>(`/api/auth/${path}`, data, { withCredentials: true, headers: { 'X-CSRF-Token': csrf.data.csrf_token } })).data;
}

export const registerAccount = (data: { name: string; email: string; password: string; workspace_type: 'INDIVIDUAL' | 'FIRM'; firm_name?: string }) => authMutation<{ message: string; signed_in?: boolean }>('register', data);
export const signIn = (email: string, password: string) => authMutation<{ message: string }>('login', { email, password });
export const supabaseSignIn = (access_token:string) => authMutation<{message:string}>('supabase/login',{access_token});
export type SupabaseRegistration = {access_token:string;name:string;workspace_type:'INDIVIDUAL'|'FIRM';firm_name?:string;address_line1:string;address_line2?:string;city:string;state:string;postal_code:string;country_code:string};
export const registerSupabaseAccount = (data:SupabaseRegistration) => authMutation<{message:string;signed_in:boolean}>('supabase/register',data);
export async function updateProfile(data:{name:string;email:string;current_password?:string;supabase_access_token?:string}) {
  const csrf = await api.get<{csrf_token:string}>('/api/auth/csrf',{withCredentials:true});
  return (await api.patch<{message:string}>('/api/auth/profile',data,{withCredentials:true,
    headers:{'X-CSRF-Token':csrf.data.csrf_token}})).data;
}
export const signOut = () => authMutation<{ message: string }>('logout', {});
export async function deleteAccount(password?: string, supabase_access_token?:string): Promise<{message:string}> {
  const csrf = await api.get<{ csrf_token: string }>('/api/auth/csrf', { withCredentials: true });
  return (await api.delete<{message:string}>('/api/auth/account', { withCredentials: true,
    headers: { 'X-CSRF-Token': csrf.data.csrf_token },
    data: { ...(password?{password}:{}), ...(supabase_access_token?{supabase_access_token}:{}), confirmation: 'DELETE MY ACCOUNT' } })).data;
}
export const requestVerification = (email: string) => authMutation<{ message: string }>('verification/request', { email });
export const resendCurrentVerification = () => authMutation<{message:string;address:string}>('verification/resend',{});
export const verifyEmail = (token: string) => authMutation<{ message: string }>('verification/confirm', { token });
export const forgotPassword = (email: string) => authMutation<{ message: string }>('password/forgot', { email });
export const resetPassword = (token: string, password: string) => authMutation<{ message: string }>('password/reset', { token, password });
export const activateAccount = (token: string, password: string) => authMutation<{ message: string }>('account/activate', { token, password });
export const acceptInvitation = (token: string) => authMutation<{ message: string }>('invitations/accept', { token });
export const registerInvited = (data: { token: string; name: string; email: string; password: string }) => authMutation<{ message: string }>('register-invited', data);
export const ownershipPreview = (token:string) => authMutation<{workspace_id:string;workspace_name:string;stage:'OWNER'|'RECIPIENT';owner_name:string;recipient_name:string;expires_at:string}>('ownership/preview',{token});
export const ownershipConfirm = (token:string) => authMutation<{message:string}>('ownership/confirm',{token});

export async function workspaceGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return (await api.get<T>(`/api/workspaces/${path}`, { signal, withCredentials: true })).data;
}

export async function workspaceMutation<T = { message: string }>(path: string, data: unknown = {}, method: 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'POST'): Promise<T> {
  // Capture the current scope before CSRF so logout/switch also cancels a queued mutation.
  const signal = requestScope.signal;
  const csrf = await api.get<{ csrf_token: string }>('/api/auth/csrf', { signal, withCredentials: true });
  return (await api.request<T>({ url: `/api/workspaces/${path}`, method, data, signal, withCredentials: true, headers: { 'X-CSRF-Token': csrf.data.csrf_token } })).data;
}

export async function workspaceMutationWithHeaders<T>(path: string, data: unknown, headers: Record<string, string>, method: 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'POST'): Promise<T> {
  const signal = requestScope.signal;
  const csrf = await api.get<{ csrf_token: string }>('/api/auth/csrf', { signal, withCredentials: true });
  return (await api.request<T>({ url: `/api/workspaces/${path}`, method, data, signal, withCredentials: true,
    headers: { ...headers, 'X-CSRF-Token': csrf.data.csrf_token } })).data;
}

export async function workspaceGetDownload(path: string, defaultFilename: string): Promise<ExportDownloadResponse> {
  const response = await api.get(`/api/workspaces/${path}`, { responseType: 'blob', signal: requestScope.signal, withCredentials: true });
  return { blob: response.data, filename: extractFilename(response.headers['content-disposition'], defaultFilename) };
}

function gstrPath(workspaceId: string, clientId: string, suffix = '') {
  return `/api/workspaces/${encodeURIComponent(workspaceId)}/clients/${encodeURIComponent(clientId)}/gstr-merges${suffix}`;
}

export async function createGstrMerge(workspaceId: string, clientId: string, payload: GstrMergeCreatePayload): Promise<GstrMergeCreateResponse> {
  const response = await financialMutation<GstrMergeCreateResponse>(
    'POST', gstrPath(workspaceId, clientId), payload,
    JSON.stringify(['GSTR_CREATE', workspaceId, clientId, payload]), {},
  );
  return response.data;
}

export function listGstrMerges(workspaceId: string, clientId: string, signal?: AbortSignal): Promise<{ items: GstrMergeRun[] }> {
  return workspaceGet(`${workspaceId}/clients/${clientId}/gstr-merges?limit=10`, signal);
}

export function getGstrMerge(workspaceId: string, clientId: string, runId: string, signal?: AbortSignal): Promise<GstrMergeRun> {
  return workspaceGet(`${workspaceId}/clients/${clientId}/gstr-merges/${encodeURIComponent(runId)}`, signal);
}

export async function submitGstrMerge(workspaceId: string, clientId: string, runId: string, version: number): Promise<GstrMergeRun> {
  const response = await financialMutation<GstrMergeRun>(
    'POST', `${gstrPath(workspaceId, clientId, `/${encodeURIComponent(runId)}`)}/submit`, {},
    JSON.stringify(['GSTR_SUBMIT', workspaceId, clientId, runId, version]),
    { 'X-Run-Version': String(version) },
  );
  return response.data;
}

export async function deleteGstrMerge(workspaceId: string, clientId: string, runId: string, version: number): Promise<{ message: string; run: GstrMergeRun }> {
  const response = await financialMutation<{ message: string; run: GstrMergeRun }>(
    'DELETE', gstrPath(workspaceId, clientId, `/${encodeURIComponent(runId)}`), {},
    JSON.stringify(['GSTR_DELETE', workspaceId, clientId, runId, version]),
    { 'X-Run-Version': String(version) },
  );
  return response.data;
}

export function gstrMergeDownloadUrl(workspaceId: string, clientId: string, runId: string): string {
  return new URL(`${gstrPath(workspaceId, clientId, `/${encodeURIComponent(runId)}`)}/download`, API_BASE_URL).toString();
}

export async function uploadGstrFile(
  target: GstrMergeUploadTarget,
  file: File,
  options: { signal?: AbortSignal; onProgress?: (percentage: number | null) => void } = {},
): Promise<void> {
  let csrfToken = '';
  if (target.provider === 'local') {
    const csrf = await api.get<{ csrf_token: string }>('/api/auth/csrf', { signal: options.signal, withCredentials: true });
    csrfToken = csrf.data.csrf_token;
  }
  await new Promise<void>((resolve, reject) => {
    const request = new XMLHttpRequest();
    const url = target.provider === 'local' ? new URL(target.url, API_BASE_URL).toString() : target.url;
    request.open(target.method, url);
    request.withCredentials = target.provider === 'local';
    Object.entries(target.headers).forEach(([name, value]) => request.setRequestHeader(name, value));
    if (target.provider === 'local') {
      request.setRequestHeader('X-CSRF-Token', csrfToken);
      if (expectedUserId) request.setRequestHeader('X-Expected-User-ID', expectedUserId);
    }
    request.upload.onprogress = event => options.onProgress?.(event.lengthComputable && event.total > 0 ? Math.round((event.loaded / event.total) * 100) : null);
    request.onload = () => request.status >= 200 && request.status < 300
      ? resolve()
      : reject(new Error(`Upload failed with status ${request.status}.`));
    request.onerror = () => reject(new Error('The workbook upload could not reach private storage.'));
    request.onabort = () => reject(new DOMException('The workbook upload was cancelled.', 'AbortError'));
    const abort = () => request.abort();
    options.signal?.addEventListener('abort', abort, { once: true });
    request.onloadend = () => options.signal?.removeEventListener('abort', abort);
    request.send(file);
  });
}

export async function workspaceDownload(path: string, defaultFilename: string): Promise<ExportDownloadResponse> {
  const signal = requestScope.signal;
  const csrf = await api.get<{ csrf_token: string }>('/api/auth/csrf', { signal, withCredentials: true });
  const response = await api.post(`/api/workspaces/${path}`, {}, { responseType: 'blob', signal,
    withCredentials: true, headers: { 'X-CSRF-Token': csrf.data.csrf_token } });
  return { blob: response.data, filename: extractFilename(response.headers['content-disposition'], defaultFilename) };
}

export function authErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (error.response?.status === 429) {
      const seconds = Number(error.response.headers?.['retry-after']);
      return Number.isFinite(seconds) && seconds > 0
        ? `Too many attempts for this action. Wait ${Math.ceil(seconds)} seconds before trying again. Other accounts on the same network may share this limit.`
        : 'Too many attempts for this action. Please wait a few minutes before trying again.';
    }
    const detail = error.response?.data?.detail;
    if (detail?.code === 'ACCOUNT_CHANGED') return detail.message;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map(item => `${item.loc?.slice(1).join('.') || 'Form'}: ${item.msg}`).join(' ');
    if (!error.response) return 'The server is starting or temporarily unavailable. Please wait a moment, then try again.';
  }
  return 'Something went wrong. Please try again.';
}

export const healthCheck = async (): Promise<HealthResponse> => {
  const response = await api.get<HealthResponse>('/api/health');
  return response.data;
};

export const inspectFiles = async (
  accountsFile: File,
  gstrFile: File,
  debitNotesFile?: File | null,
  context?: ReconciliationContext
): Promise<InspectionResponse> => {
  validateCombinedUpload([accountsFile, gstrFile, debitNotesFile]);
  const formData = new FormData();
  formData.append('accounts_file', accountsFile);
  formData.append('gstr_file', gstrFile);
  if (debitNotesFile) {
    formData.append('debit_notes_file', debitNotesFile);
  }

  let response;
  if (context) {
    const ids = [accountsFile, gstrFile, debitNotesFile].map(file => { if (!file) return null; if (!uploadIdentities.has(file)) uploadIdentities.set(file, nextUploadIdentity++); return uploadIdentities.get(file); });
    response = await financialMutation<InspectionResponse>('POST', '/api/inspect', formData, JSON.stringify(['INSPECT', context, ids]), {
      'X-Workspace-ID': context.workspaceId, 'X-Client-ID': context.clientId,
      ...(context.gstinId ? { 'X-Client-GSTIN-ID': context.gstinId } : {}),
    });
    rememberVersion(response.data.session_id, response.headers);
  } else response = await api.post<InspectionResponse>('/api/inspect', formData);
  return response.data;
};

export const deleteSession = async (sessionId: string): Promise<void> => {
  if (runVersions.has(sessionId)) {
    const version = runVersions.get(sessionId)!;
    const response = await financialMutation('DELETE', `/api/session/${encodeURIComponent(sessionId)}`, {}, JSON.stringify(['DELETE', sessionId, version]), { 'X-Run-Version': String(version) });
    rememberVersion(sessionId, response.headers);
    return;
  }
  await api.delete(`/api/session/${encodeURIComponent(sessionId)}`);
};

export const reconcileSession = async (
  sessionId: string,
  mappingOverrides: { accounts?: Record<string, string>; gstr?: Record<string, string> } = {}
): Promise<ReconciliationResponse> => {
  if (runVersions.has(sessionId)) {
    const version = runVersions.get(sessionId)!;
    const response = await financialMutation<ReconciliationResponse>('POST', `/api/reconcile?session_id=${encodeURIComponent(sessionId)}`, { mapping_overrides: mappingOverrides }, JSON.stringify(['RECONCILE', sessionId, version, mappingOverrides]), { 'X-Run-Version': String(version) });
    rememberVersion(sessionId, response.headers);
    return response.data;
  }
  const response = await api.post<ReconciliationResponse>(`/api/reconcile?session_id=${encodeURIComponent(sessionId)}`, {
    mapping_overrides: mappingOverrides,
  });
  return response.data;
};

export const manualMatch = async (
  sessionId: string,
  resultId: string,
  candidateId: string
): Promise<ReconciliationResponse> => {
  if (runVersions.has(sessionId)) {
    const version = runVersions.get(sessionId)!;
    const response = await financialMutation<ReconciliationResponse>('POST', `/api/manual-match?session_id=${encodeURIComponent(sessionId)}`, { result_id: resultId, candidate_id: candidateId }, JSON.stringify(['MANUAL_MATCH', sessionId, version, resultId, candidateId]), { 'X-Run-Version': String(version) });
    rememberVersion(sessionId, response.headers);
    return response.data;
  }
  const response = await api.post<ReconciliationResponse>(`/api/manual-match?session_id=${encodeURIComponent(sessionId)}`, {
    result_id: resultId,
    candidate_id: candidateId,
  });
  return response.data;
};

export const getResultPage = async (
  sessionId: string,
  params: { category: string; page: number; pageSize: number; search?: string; sortBy?: string; sortOrder?: 'asc' | 'desc' }
): Promise<ResultsPageResponse> => {
  const response = await api.get<ResultsPageResponse>(`/api/session/${encodeURIComponent(sessionId)}/results`, {
    params: {
      category: params.category, page: params.page, page_size: params.pageSize,
      search: params.search || undefined, sort_by: params.sortBy || undefined,
      sort_order: params.sortOrder || 'asc',
    },
  });
  return response.data;
};

export const getSessionStatus = async (sessionId: string): Promise<SessionStatusResponse> => {
  const response = await api.get<SessionStatusResponse>(`/api/session/${encodeURIComponent(sessionId)}`);
  return response.data;
};

export interface ExportDownloadResponse {
  blob: Blob;
  filename: string;
}

const extractFilename = (disposition: string | undefined, defaultFilename: string) => {
  if (disposition && disposition.indexOf('filename=') !== -1) {
    const filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/;
    const matches = filenameRegex.exec(disposition);
    if (matches != null && matches[1]) { 
      return matches[1].replace(/['"]/g, '');
    }
  }
  return defaultFilename;
};

export const downloadExcel = async (sessionId: string): Promise<ExportDownloadResponse> => {
  const response = await api.get(`/api/export/${encodeURIComponent(sessionId)}/excel`, {
    responseType: 'blob'
  });
  return {
    blob: response.data,
    filename: extractFilename(response.headers['content-disposition'], 'GSTR2B_Reconciliation.xlsx')
  };
};

export const downloadNotesExcel = async (sessionId: string): Promise<ExportDownloadResponse> => {
  const response = await api.get(`/api/export/${encodeURIComponent(sessionId)}/notes/excel`, {
    responseType: 'blob'
  });
  return {
    blob: response.data,
    filename: extractFilename(response.headers['content-disposition'], 'GSTR2B_Notes_Reconciliation.xlsx')
  };
};

export const downloadJson = async (sessionId: string): Promise<ExportDownloadResponse> => {
  const response = await api.get(`/api/export/${encodeURIComponent(sessionId)}/json`, {
    responseType: 'blob'
  });
  return {
    blob: response.data,
    filename: extractFilename(response.headers['content-disposition'], 'GSTR2B_Reconciliation.json')
  };
};
