import { useCallback, useEffect, useState } from 'react';
import { authErrorMessage, workspaceGet } from '../services/api';

export interface ClientSummary { id: string; name: string; legal_name: string | null; pan: string | null; status: 'ACTIVE' | 'ARCHIVED' }
export interface ClientDetail extends ClientSummary { gstins: { id: string; gstin: string; status: 'ACTIVE' | 'INACTIVE' }[]; membership_ids: string[] }
export interface Member { id: string; user_id: string; name: string; email: string; role: 'OWNER' | 'ADMIN' | 'ACCOUNTANT'; status: 'ACTIVE' | 'SUSPENDED' | 'REMOVED'; login_id?:string|null; activation_pending?:boolean; email_verified?:boolean }
export interface TeamData { members: Member[]; invites: { id: string; email: string; role: string; expires_at: string }[]; has_more: boolean }

export function useWorkspaceData<T>(path: string) {
  const [version, setVersion] = useState(0);
  const [state, setState] = useState<{ path: string; version: number; data?: T; error?: string }>({ path: '', version: -1 });
  const reload = useCallback(() => setVersion(v => v + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    workspaceGet<T>(path, controller.signal).then(data => {
      if (!controller.signal.aborted) setState({ path, version, data });
    }).catch(error => { if (!controller.signal.aborted) setState({ path, version, error: authErrorMessage(error) }); });
    return () => controller.abort();
  }, [path, version]);
  const current = state.path === path && state.version === version;
  return { data: current ? state.data : undefined, error: current ? state.error : undefined, reload };
}
