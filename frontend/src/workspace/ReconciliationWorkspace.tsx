import { useState } from 'react';
import App from '../App';
import { useAuth } from './AuthContext';
import { authErrorMessage, clearRequestScope, deleteSession, openOwnedRun, workspaceMutation, type OpenRun, type ReconciliationContext, type RunHistoryItem } from '../services/api';
import { useWorkspaceData, type ClientSummary, type ClientDetail } from './management';
import { ClientChooser } from './ClientChooser';

export function ReconciliationWorkspace() {
  const { profile } = useAuth();
  const [workspaceId, setWorkspaceId] = useState('');
  const [client, setClient] = useState<ClientSummary | null>(null);
  const workspace = profile?.workspaces.find(w => w.id === workspaceId) || profile?.workspaces[0];
  return <div className="owned-reconciliation"><div className="workspace-settings">
    <div className="dashboard-heading"><div><p className="auth-eyebrow">CLIENT RECONCILIATION</p><h1>Reconciliation</h1><p className="settings-intro">Compare source records. Review differences. Close with confidence.</p></div>{workspace&&<label className="workspace-picker">Workspace<select value={workspace.id} onChange={e => { clearRequestScope(); setWorkspaceId(e.target.value); setClient(null); }}>{profile?.workspaces.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>}</div>
    {!profile?.user.email_verified ? <p className="auth-notice">Verify your email before reconciling. <a href="#/resend-verification">Request verification</a></p>
      : !workspace ? <p>No workspace yet. Accept your firm invitation first.</p> : <>
        {client ? <><button className="management-back" onClick={() => { clearRequestScope(); setClient(null); }}>Choose another client</button><ClientRuns key={`${workspace.id}:${client.id}`} workspaceId={workspace.id} role={workspace.role} client={client} /></> : <ClientChooser key={workspace.id} workspaceId={workspace.id} onChoose={setClient} />}
      </>}
  </div></div>;
}

function ClientRuns({ workspaceId, client, role }: { workspaceId: string; client: ClientSummary; role:'OWNER'|'ADMIN'|'ACCOUNTANT' }) {
  const [gstin, setGstin] = useState(''), [page, setPage] = useState(1);
  const [working, setWorking] = useState<{ key: string; context: ReconciliationContext; initial?: OpenRun } | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const detail = useWorkspaceData<ClientDetail>(`${workspaceId}/clients/${client.id}`);
  const history = useWorkspaceData<{ items: RunHistoryItem[]; has_more: boolean }>(`${workspaceId}/clients/${client.id}/runs?page=${page}`);
  const context: ReconciliationContext = { workspaceId, clientId: client.id, ...(gstin ? { gstinId: gstin } : {}) };
  async function open(run: RunHistoryItem) {
    setBusy(true); setError('');
    try { const initial = await openOwnedRun(context, run.session_id); setWorking({ key: run.id, context: { ...context, gstinId: run.client_gstin_id || undefined }, initial }); }
    catch (failure) { setError(authErrorMessage(failure)); history.reload(); } finally { setBusy(false); }
  }
  async function remove(run: RunHistoryItem) {
    if (!window.confirm('Delete this run’s temporary results? The audit history will remain.')) return;
    setBusy(true); setError('');
    try { await openOwnedRun(context, run.session_id); await deleteSession(run.session_id); history.reload(); }
    catch (failure) { setError(authErrorMessage(failure)); } finally { setBusy(false); }
  }
  async function recover(run: RunHistoryItem) {
    if (!window.confirm('Recover this run? A stored result will be finalized; an operation that has not published may be canceled.')) return;
    setBusy(true); setError(''); setNotice('');
    try { const result = await workspaceMutation(`${workspaceId}/clients/${client.id}/runs/${run.session_id}/recover`); setNotice(result.message); history.reload(); }
    catch (failure) { setError(authErrorMessage(failure)); } finally { setBusy(false); }
  }
  return <>
    <section className={`management-panel ${working?'run-context-bar':''}`}><div><h2>{client.name}</h2>{working&&<p>{working.context.gstinId ? detail.data?.gstins.find(g => g.id === working.context.gstinId)?.gstin || 'Saved GST registration' : 'Client-level reconciliation'}</p>}</div>
      {working ? <button onClick={() => { clearRequestScope(); setWorking(null); history.reload(); }}>Back to run history</button> : <div className="management-inline-form"><label>GST registration<select value={gstin} disabled={!detail.data || busy} onChange={e => setGstin(e.target.value)}><option value="">Client level (no GSTIN selected)</option>{detail.data?.gstins.filter(g => g.status === 'ACTIVE').map(g => <option key={g.id} value={g.id}>{g.gstin}</option>)}</select></label><button className="management-primary" disabled={!detail.data || busy} onClick={() => setWorking({ key: crypto.randomUUID(), context })}>New reconciliation</button></div>}
      {detail.error && <p role="alert">{detail.error}</p>}
    </section>
    <details className="run-retention"><summary>Saved reconciliation state</summary><p>PostgreSQL preserves available reconciliation results and manual decisions for the configured retention period. Reports are regenerated when downloaded, so a server restart does not depend on old local files.</p></details>
    {working ? <App key={working.key} context={working.context} initial={working.initial} workspaceRole={role} /> : <section className="management-panel"><h2>Run history</h2><p>Open available runs to continue review or download reports. An unresolved outcome is never replayed automatically.</p>
      {error && <p role="alert" className="auth-notice">{error}</p>}{notice && <p role="status" className="auth-notice">{notice}</p>}
      {history.error ? <p role="alert">{history.error} <button onClick={history.reload}>Retry</button></p> : !history.data ? <p role="status">Loading runs…</p> : <>
        {!history.data.items.length && <p>No runs yet. Start a new reconciliation above.</p>}
        {history.data.items.map(run => <div className="management-row" key={run.id}><div><strong>{new Date(run.created_at).toLocaleString()}</strong><small>{run.status} · {run.availability} · Version {run.version}</small><small>{run.expires_at ? `Expiry: ${new Date(run.expires_at).toLocaleString()}` : 'Temporary results unavailable'}</small></div><div className="management-actions">{run.availability === 'AVAILABLE' && <><button disabled={busy} onClick={() => void open(run)}>Open run</button><button disabled={busy} onClick={() => void remove(run)}>Delete results</button></>}{['PENDING','UNRESOLVED'].includes(run.availability) && <button disabled={busy} onClick={() => void recover(run)}>Recover</button>}</div></div>)}
        <div className="management-pager"><button disabled={page === 1} onClick={() => setPage(p => p-1)}>Previous</button><span>Page {page}</span><button disabled={!history.data.has_more} onClick={() => setPage(p => p+1)}>Next</button></div>
      </>}
    </section>}
  </>;
}
