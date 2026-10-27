import axios from 'axios';
import { workspaceGet, workspaceGetDownload, workspaceMutation, workspaceMutationWithHeaders, type ExportDownloadResponse } from './api';
import type { PartyMapping, PurchaseMapping, TallyBatchCreate, TallyBatchDetail, TallyBatchSummary, TallyCandidateList, TallyProfile, TallyTarget, TallyValidation } from '../types/tallyExport';

const root = (t:TallyTarget) => `${encodeURIComponent(t.workspaceId)}/clients/${encodeURIComponent(t.clientId)}/gstins/${encodeURIComponent(t.gstinId)}/tally-export`;
export const getTallyProfile = (t:TallyTarget) => workspaceGet<TallyProfile>(`${root(t)}/profile`);
export const saveTallyProfile = (t:TallyTarget, data:Record<string,unknown>, exists:boolean) => workspaceMutation<TallyProfile>(`${root(t)}/profile`, data, exists?'PUT':'POST');
export const listPartyMappings = async(t:TallyTarget) => (await workspaceGet<{items:PartyMapping[]}>(`${root(t)}/party-mappings`)).items;
export const savePartyMapping = (t:TallyTarget, gstin:string, data:Record<string,unknown>, exists:boolean) => workspaceMutation<PartyMapping>(exists?`${root(t)}/party-mappings/${encodeURIComponent(gstin)}`:`${root(t)}/party-mappings`, data, exists?'PUT':'POST');
export const deactivatePartyMapping = (t:TallyTarget, gstin:string, expectedVersion:number) => workspaceMutation<PartyMapping>(`${root(t)}/party-mappings/${encodeURIComponent(gstin)}?expected_version=${expectedVersion}`,{},'DELETE');
export const listPurchaseMappings = async(t:TallyTarget) => (await workspaceGet<{items:PurchaseMapping[]}>(`${root(t)}/purchase-ledger-mappings`)).items;
export const savePurchaseMapping = (t:TallyTarget, rate:string, data:Record<string,unknown>, exists:boolean) => workspaceMutation<PurchaseMapping>(exists?`${root(t)}/purchase-ledger-mappings/purchase_invoice/${encodeURIComponent(rate)}`:`${root(t)}/purchase-ledger-mappings`, data, exists?'PUT':'POST');
export const getTallyCandidates = (t:TallyTarget, sessionId:string, options:{page?:number;pageSize?:number;search?:string}={}) => workspaceGet<TallyCandidateList>(`${root(t)}/runs/${encodeURIComponent(sessionId)}/candidates?page=${options.page||1}&page_size=${options.pageSize||50}&search=${encodeURIComponent(options.search||'')}`);
export const validateTallyCandidates = (t:TallyTarget, sessionId:string, candidateIds:string[], runVersion:number) => workspaceMutation<TallyValidation>(`${root(t)}/runs/${encodeURIComponent(sessionId)}/validate`, {candidate_ids:candidateIds,expected_run_version:runVersion});
export const generateTallyExport = (t:TallyTarget, sessionId:string, candidateIds:string[], runVersion:number, allowReexport:boolean, idempotencyKey:string) => workspaceMutationWithHeaders<TallyBatchCreate>(`${root(t)}/runs/${encodeURIComponent(sessionId)}/batches`, {candidate_ids:candidateIds,expected_run_version:runVersion,allow_reexport:allowReexport}, {'Idempotency-Key':idempotencyKey});
export const listTallyBatches = (t:TallyTarget, page=1, sessionId?:string) => workspaceGet<{items:TallyBatchSummary[];page:number;page_size:number;total:number}>(`${root(t)}/batches?page=${page}&page_size=25${sessionId?`&session_id=${encodeURIComponent(sessionId)}`:''}`);
export const getTallyBatch = (t:TallyTarget, id:string) => workspaceGet<TallyBatchDetail>(`${root(t)}/batches/${encodeURIComponent(id)}`);
export const downloadTallyBatch = (t:TallyTarget, id:string):Promise<ExportDownloadResponse> => workspaceGetDownload(`${root(t)}/batches/${encodeURIComponent(id)}/download`, `tally-purchases-${id}.xml`);

export function tallyError(error:unknown):{message:string;stale:boolean} {
  if (!axios.isAxiosError(error)) return {message:'Something went wrong. Please try again.',stale:false};
  const status=error.response?.status, detail=error.response?.data?.detail;
  const message=typeof detail==='string'?detail:typeof detail?.message==='string'?detail.message:'';
  const stale=status===409 && /stale|changed|version|candidate/i.test(message);
  if (!error.response) return {message:'Cannot reach Aligned. Check your connection and retry.',stale:false};
  if (status===404) return {message:'This reconciliation or Tally export is no longer available.',stale:false};
  if (status===410) return {message:'This XML artifact is no longer available. Generate a new export.',stale:false};
  if (status===503) return {message:'Tally XML storage is temporarily unavailable. Please retry.',stale:false};
  if (status===409) return {message:message||'The Tally export state changed. Refresh and try again.',stale};
  if (status===422) return {message:message||'Check the Tally setup and selected invoices.',stale:false};
  return {message:message||'The Tally export request failed. Please try again.',stale:false};
}
