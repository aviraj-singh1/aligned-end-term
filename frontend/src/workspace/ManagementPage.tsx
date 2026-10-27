import { useState, type FormEvent, type ReactNode } from 'react';
import { Building2, Users, Plus, ArrowLeft, Download, Trash2 } from 'lucide-react';
import { authErrorMessage, clearRequestScope, workspaceDownload, workspaceMutation, type AuthProfile } from '../services/api';
import { triggerBrowserDownload } from '../utils/downloadHelper';
import { useAuth } from './AuthContext';
import { useWorkspaceData, type ClientDetail, type ClientSummary, type Member, type TeamData } from './management';
import { ManagedAccounts } from './ManagedAccounts';

type Workspace = AuthProfile['workspaces'][number];

export function ManagementPage({ mode }: { mode: 'clients' | 'team' }) {
  const auth = useAuth();
  const [selected, setSelected] = useState('');
  const workspace = auth.profile?.workspaces.find(w => w.id === selected) || auth.profile?.workspaces[0];
  return <main className="workspace-settings management-page">
    <p className="auth-eyebrow">YOUR WORKSPACE</p>
    <div className="management-heading"><div><h1>{mode === 'clients' ? 'Clients' : 'Team'}</h1><p className="settings-intro">{mode === 'clients' ? 'Keep client details and access in one place.' : 'Bring your colleagues together. Give each person the access they need.'}</p></div>
      {!!auth.profile?.workspaces.length && <label className="workspace-picker">Workspace<select value={workspace?.id} onChange={e => { clearRequestScope(); setSelected(e.target.value); }}>{auth.profile.workspaces.map(w => <option key={w.id} value={w.id}>{w.name} · {w.type === 'FIRM' ? 'Firm' : 'Individual'}</option>)}</select></label>}
    </div>
    {!auth.profile?.user.email_verified ? <Empty title="Verify your email first">Open your verification message, then return here. <a href="#/resend-verification">Request verification instructions</a></Empty>
      : !workspace ? <Empty title="No workspace yet">Open your firm invitation and accept it to get started.</Empty>
        : <div key={`${workspace.id}:${workspace.role}:${mode}`}>
          <p className="workspace-role">{workspace.name} <span>{workspace.role === 'OWNER' ? 'Owner' : workspace.role === 'ADMIN' ? 'Administrator' : 'Accountant · assigned clients only'}</span></p>
          {mode === 'clients' ? <Clients workspace={workspace} /> : workspace.type === 'INDIVIDUAL' ? <ConvertToFirm workspace={workspace} /> : workspace.role === 'ACCOUNTANT' ? <Empty title="Team is managed by your administrator">You can view the clients assigned to you on the Clients page.</Empty> : <Team workspace={workspace} />}
        </div>}
  </main>;
}

function Empty({ title, children }: { title: string; children: ReactNode }) {
  return <section className="management-empty"><Users size={28} aria-hidden="true" /><h2>{title}</h2><p>{children}</p></section>;
}

function Pager({ page, more, onChange }: { page: number; more: boolean; onChange: (p: number) => void }) {
  return <nav className="management-pager" aria-label="Pagination"><button disabled={page === 1} onClick={() => onChange(page-1)}>Previous</button><span>Page {page}</span><button disabled={!more} onClick={() => onChange(page+1)}>Next</button></nav>;
}

function LoadState({ error, retry }: { error?: string; retry: () => void }) {
  return error ? <div className="auth-notice" role="alert">{error} <button onClick={retry}>Retry</button></div> : <p role="status">Loading workspace data…</p>;
}

function Clients({ workspace }: { workspace: Workspace }) {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const list = useWorkspaceData<{ items: ClientSummary[]; has_more: boolean }>(`${workspace.id}/clients?page=${page}&search=${encodeURIComponent(search)}`);
  if (selected) return selected === 'new' ? <ClientEditor workspace={workspace} onBack={() => setSelected(null)} onSaved={id => { list.reload(); setSelected(id); }} /> : <ClientRecord key={selected} workspace={workspace} id={selected} onBack={() => { setSelected(null); list.reload(); }} />;
  return <>
    <div className="management-toolbar"><form onSubmit={e => { e.preventDefault(); setPage(1); setSearch(String(new FormData(e.currentTarget).get('search')).trim()); }}><label className="sr-only" htmlFor="client-search">Search clients</label><input id="client-search" name="search" placeholder="Search clients by name" maxLength={200} /><button>Search</button></form>{workspace.role !== 'ACCOUNTANT' && <button className="management-primary" onClick={() => setSelected('new')}><Plus size={17} aria-hidden="true" /> Add client</button>}</div>
    {!list.data ? <LoadState error={list.error} retry={list.reload} /> : <>
      {!list.data.items.length ? <Empty title={search ? 'No matching clients' : 'Your clients start here'}>{workspace.role !== 'ACCOUNTANT' ? 'Add a client to save their business details and GST registrations.' : 'Your firm administrator will assign clients to you. Unassigned clients stay private.'}</Empty> : <div className="management-grid">{list.data.items.map(c => <button className="client-tile" key={c.id} onClick={() => setSelected(c.id)}><span className="client-tile-icon"><Building2 size={23} aria-hidden="true" /></span><strong>{c.name}</strong><span>{c.legal_name || 'Client record'}</span><small>{c.pan || 'PAN not added'}</small><span className={`management-badge ${c.status === 'ACTIVE' ? 'active' : ''}`}>{c.status === 'ACTIVE' ? 'Active' : 'Archived'}</span></button>)}</div>}
      <Pager page={page} more={list.data.has_more} onChange={setPage} />
    </>}
  </>;
}

function ClientEditor({ workspace, client, onBack, onSaved }: { workspace: Workspace; client?: ClientDetail; onBack: () => void; onSaved: (id: string) => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); if (busy) return;
    const fields = new FormData(e.currentTarget); setBusy(true); setError('');
    try {
      const saved = await workspaceMutation<ClientDetail>(`${workspace.id}/clients${client ? '/'+client.id : ''}`, { name: String(fields.get('name')).trim(), legal_name: String(fields.get('legal_name')).trim() || null, pan: String(fields.get('pan')).trim().toUpperCase() || null }, client ? 'PATCH' : 'POST');
      onSaved(saved.id);
    } catch (failure) { setError(authErrorMessage(failure)); } finally { setBusy(false); }
  }
  return <section className="management-panel"><button className="management-back" onClick={onBack}><ArrowLeft size={16} /> Back to clients</button><h2>{client ? 'Edit client' : 'Add a client'}</h2><p>Client records belong to this workspace. Only administrators can edit them.</p><form onSubmit={submit}><fieldset disabled={busy} className="management-fields"><label>Client name<input name="name" required maxLength={200} defaultValue={client?.name} /></label><label>Legal name <small>optional</small><input name="legal_name" maxLength={255} defaultValue={client?.legal_name || ''} /></label><label>PAN <small>optional</small><input name="pan" maxLength={10} pattern="[A-Za-z]{5}[0-9]{4}[A-Za-z]" defaultValue={client?.pan || ''} placeholder="ABCDE1234F" /></label><button className="management-primary">{busy ? 'Saving…' : 'Save client'}</button></fieldset>{error && <p role="alert" className="auth-notice">{error}</p>}</form></section>;
}

function ClientRecord({ workspace, id, onBack }: { workspace: Workspace; id: string; onBack: () => void }) {
  const resource = useWorkspaceData<ClientDetail>(`${workspace.id}/clients/${id}`);
  const [edit, setEdit] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const client = resource.data;
  async function change(path: string, data: unknown, method: 'PATCH' | 'POST' = 'PATCH') {
    if (busy) return; setBusy(true); setError('');
    try { await workspaceMutation(`${workspace.id}/clients/${id}${path}`, data, method); resource.reload(); }
    catch (failure) { setError(authErrorMessage(failure)); } finally { setBusy(false); }
  }
  if (!client) return <><button onClick={onBack}>Back to clients</button><LoadState error={resource.error} retry={resource.reload} /></>;
  if (edit) return <ClientEditor workspace={workspace} client={client} onBack={() => setEdit(false)} onSaved={() => { setEdit(false); resource.reload(); }} />;
  const admin = workspace.role !== 'ACCOUNTANT';
  return <>
    <button className="management-back" onClick={onBack}><ArrowLeft size={16} /> Back to clients</button>
    <section className="management-panel"><div className="management-heading"><div><h2>{client.name}</h2><p>{client.legal_name || 'No legal name added'} · PAN: {client.pan || 'Not added'}</p></div><span className="management-badge">{client.status}</span></div>
      {admin && <div className="management-actions"><button disabled={busy} onClick={() => setEdit(true)}>Edit details</button><button disabled={busy} onClick={() => { if (window.confirm(client.status === 'ACTIVE' ? 'Archive this client? Accountants will lose access until you restore it.' : 'Restore this client and its existing assignments?')) void change('/status', { status: client.status === 'ACTIVE' ? 'ARCHIVED' : 'ACTIVE' }); }}>{client.status === 'ACTIVE' ? 'Archive client' : 'Restore client'}</button></div>}
    </section>
    {error && <p role="alert" className="auth-notice">{error}</p>}
    <section className="management-panel"><h2>GST registrations</h2><p>Store the GSTINs that belong to this client.</p>
      {!client.gstins.length && <p>No GST registrations added yet.</p>}
      {client.gstins.map(g => <div className="management-row" key={g.id}><div><strong>{g.gstin}</strong><small>{g.status}</small></div>{admin && client.status === 'ACTIVE' && <button disabled={busy} onClick={() => void change(`/gstins/${g.id}`, { status: g.status === 'ACTIVE' ? 'INACTIVE' : 'ACTIVE' })}>{g.status === 'ACTIVE' ? 'Deactivate' : 'Activate'}</button>}</div>)}
      {admin && client.status === 'ACTIVE' && <form className="management-inline-form" onSubmit={e => { e.preventDefault(); void change('/gstins', { gstin: String(new FormData(e.currentTarget).get('gstin')).trim().toUpperCase() }, 'POST'); }}><label>GSTIN<input name="gstin" required minLength={15} maxLength={15} placeholder="27ABCDE1234F1Z5" /></label><button disabled={busy}>Add GSTIN</button></form>}
    </section>
    {admin && workspace.type === 'FIRM' && client.status === 'ACTIVE' && <Assignments workspace={workspace} client={client} onSaved={resource.reload} />}
  </>;
}

function Assignments({ workspace, client, onSaved }: { workspace: Workspace; client: ClientDetail; onSaved: () => void }) {
  const [page, setPage] = useState(1), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const team = useWorkspaceData<TeamData>(`${workspace.id}/team?page=${page}`);
  async function toggle(member: Member) {
    if (busy) return; setBusy(true); setError('');
    try { await workspaceMutation(`${workspace.id}/clients/${client.id}/assignments/${member.id}`, {}, client.membership_ids.includes(member.id) ? 'DELETE' : 'POST'); onSaved(); }
    catch (failure) { setError(authErrorMessage(failure)); } finally { setBusy(false); }
  }
  return <section className="management-panel"><h2>Assigned accountants</h2><p>Administrators see every client. Accountants see only clients assigned to them.</p>{error && <p role="alert">{error}</p>}{!team.data ? <LoadState error={team.error} retry={team.reload} /> : <>{!team.data.members.some(m => m.role === 'ACCOUNTANT' && m.status === 'ACTIVE') && <p>No active accountants on this page. Invite colleagues from Team.</p>}{team.data.members.filter(m => m.role === 'ACCOUNTANT' && m.status === 'ACTIVE').map(m => <div className="management-row" key={m.id}><div><strong>{m.name}</strong><small>{m.email}</small></div><button disabled={busy} onClick={() => void toggle(m)}>{client.membership_ids.includes(m.id) ? 'Remove assignment' : 'Assign client'}</button></div>)}<Pager page={page} more={team.data.has_more} onChange={setPage} /></>}</section>;
}

function ConvertToFirm({ workspace }: { workspace: Workspace }) {
  const auth = useAuth();
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function convert(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); if (busy || !window.confirm('Convert this individual workspace to a firm? Your clients stay in place. This cannot be reversed in the app.')) return;
    const name = String(new FormData(e.currentTarget).get('name')); setBusy(true); setError('');
    try { await workspaceMutation(`${workspace.id}/convert-to-firm`, { name }); await auth.refresh(); }
    catch (failure) { setError(authErrorMessage(failure)); } finally { setBusy(false); }
  }
  return <section className="management-panel"><span className="client-tile-icon"><Building2 /></span><h2>Ready to work as a team?</h2><p>Individual workspaces have one administrator. Convert this workspace to a firm to invite colleagues and assign clients. Your existing clients and workspace history stay in place.</p><p>This conversion is permanent in the current app.</p><form onSubmit={convert}><fieldset disabled={busy} className="management-fields"><label>Firm name<input name="name" required maxLength={200} defaultValue={workspace.name} /></label><button className="management-primary">{busy ? 'Converting…' : 'Convert to firm'}</button></fieldset>{error && <p role="alert" className="auth-notice">{error}</p>}</form></section>;
}

function Team({ workspace }: { workspace: Workspace }) {
  const auth = useAuth();
  const [page, setPage] = useState(1), [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const resource = useWorkspaceData<TeamData>(`${workspace.id}/team?page=${page}`);
  async function change(path: string, data: unknown, method: 'POST' | 'PATCH' | 'DELETE', self = false) {
    if (busy) return; setBusy(true); setError(''); setNotice('');
    try { const result = await workspaceMutation(`${workspace.id}/${path}`, data, method); setNotice(result.message); if (self) await auth.refresh(); else resource.reload(); }
    catch (failure) { setError(authErrorMessage(failure)); } finally { setBusy(false); }
  }
  async function exportMember(member:Member){if(busy)return;setBusy(true);setError('');setNotice('');try{const file=await workspaceDownload(`${workspace.id}/members/${member.id}/data-export`,'aligned-member-data.zip');triggerBrowserDownload(file.blob,file.filename);setNotice(`Exported data for ${member.name}.`);}catch(f){setError(authErrorMessage(f));}finally{setBusy(false);}}
  return <>
    <ManagedAccounts workspace={workspace} members={resource.data?.members||[]} onSaved={resource.reload}/>
    <details className="management-panel"><summary>Connect someone who already has an ALIGNED account</summary><p>Existing account holders must consent to joining. This does not replace their identity or password. Invitations are saved in the local mailbox.</p><form className="management-inline-form" onSubmit={e => { e.preventDefault(); const fields = new FormData(e.currentTarget); void change('invites', { email: String(fields.get('email')), role: String(fields.get('role')) }, 'POST'); }}><label>Email address<input name="email" type="email" required maxLength={254} /></label><label>Role<select name="role"><option value="ACCOUNTANT">Accountant · assigned clients</option><option value="ADMIN">Administrator · full workspace access</option></select></label><button className="management-primary" disabled={busy}>Send invitation</button></form></details>
    {error && <p role="alert" className="auth-notice">{error}</p>}{notice && <p role="status" className="auth-notice">{notice}</p>}
    {!resource.data ? <LoadState error={resource.error} retry={resource.reload} /> : <>
      {workspace.role === 'OWNER' && <OwnershipTransfer workspace={workspace} members={resource.data.members}/>}
      <section className="management-panel"><h2>Members</h2><p>Export a person’s workspace data, change access, or permanently remove their membership. Removing a member clears every client assignment.</p>{resource.data.members.map(m => <MemberRow key={`${m.id}:${m.role}:${m.status}`} member={m} busy={busy} onExport={workspace.role==='OWNER'||m.user_id===auth.profile?.user.id||(workspace.role==='ADMIN'&&m.role==='ACCOUNTANT')?()=>void exportMember(m):undefined} onRemove={m.role==='OWNER'||m.user_id===auth.profile?.user.id?undefined:()=>{if(window.confirm(`Remove ${m.name} from ${workspace.name}? Their client assignments will be cleared immediately.`))void change(`members/${m.id}`,{},'DELETE');}} onSave={(role, status) => { if (window.confirm(`Update access for ${m.name} to ${role.toLowerCase()}, ${status.toLowerCase()}?`)) void change(`members/${m.id}`, { role, status }, 'PATCH', m.user_id === auth.profile?.user.id); }} />)}</section>
      <section className="management-panel"><h2>Pending invitations</h2>{!resource.data.invites.length && <p>No pending invitations on this page.</p>}{resource.data.invites.map(i => <div className="management-row" key={i.id}><div><strong>{i.email}</strong><small>{i.role} · expires {new Date(i.expires_at).toLocaleDateString()}</small></div><button disabled={busy} onClick={() => { if (window.confirm('Revoke this invitation? Its link will stop working.')) void change(`invites/${i.id}`, {}, 'DELETE'); }}>Revoke invitation</button></div>)}</section><Pager page={page} more={resource.data.has_more} onChange={setPage} />
    </>}
  </>;
}

function MemberRow({ member, busy, onSave, onExport, onRemove }: { member: Member; busy: boolean; onSave: (role: Member['role'], status: Member['status']) => void; onExport?:()=>void; onRemove?:()=>void }) {
  const [role, setRole] = useState(member.role), [status, setStatus] = useState(member.status);
  if(member.role==='OWNER')return <div className="management-row"><div><strong>{member.name}</strong><small>{member.email}</small></div><div className="member-controls"><div><strong>Owner</strong><small>Protected · transfer ownership to change</small></div>{onExport&&<button disabled={busy} onClick={onExport}><Download size={15}/> Export data</button>}</div></div>;
  return <div className="management-row"><div><strong>{member.name}</strong><small>{member.email}</small></div><div className="member-controls"><label className="sr-only" htmlFor={`role-${member.id}`}>Role for {member.email}</label><select id={`role-${member.id}`} value={role} disabled={busy} onChange={e => setRole(e.target.value as Member['role'])}><option value="ADMIN">Administrator</option><option value="ACCOUNTANT">Accountant</option></select><label className="sr-only" htmlFor={`status-${member.id}`}>Status for {member.email}</label><select id={`status-${member.id}`} value={status} disabled={busy} onChange={e => setStatus(e.target.value as Member['status'])}><option value="ACTIVE">Active</option><option value="SUSPENDED">Suspended</option><option value="REMOVED">Removed</option></select><button disabled={busy || (role === member.role && status === member.status)} onClick={() => onSave(role, status)}>Save access</button>{onExport&&<button disabled={busy} onClick={onExport}><Download size={15}/> Export</button>}{onRemove&&<button className="member-delete" disabled={busy} onClick={onRemove}><Trash2 size={15}/> Remove</button>}</div></div>;
}

function OwnershipTransfer({workspace,members}:{workspace:Workspace;members:Member[]}) {
  const path=`${workspace.id}/ownership-transfers`;
  const current=useWorkspaceData<{transfer:null|{recipient_name:string;recipient_email:string;status:string;expires_at:string}}>(`${path}/current`);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const eligible=members.filter(m=>m.role==='ADMIN'&&m.status==='ACTIVE'&&!m.activation_pending&&m.email_verified);
  async function start(e:FormEvent<HTMLFormElement>){e.preventDefault();if(busy)return;const id=String(new FormData(e.currentTarget).get('recipient'));if(!window.confirm('Start ownership transfer? You remain Owner until you confirm by email and the recipient separately accepts by email.'))return;setBusy(true);setError('');try{const r=await workspaceMutation(path,{recipient_user_id:id},'POST');setNotice(r.message);current.reload();}catch(f){setError(authErrorMessage(f));}finally{setBusy(false);}}
  async function cancel(){if(!window.confirm('Cancel this ownership transfer?'))return;setBusy(true);setError('');try{const r=await workspaceMutation(`${path}/current`,{},'DELETE');setNotice(r.message);current.reload();}catch(f){setError(authErrorMessage(f));}finally{setBusy(false);}}
  return <section className="management-panel"><h2>Workspace ownership</h2><p>The Owner has all administrator powers and cannot be demoted, suspended, or removed. Transfer requires separate consent links sent to both registered emails.</p>{current.data?.transfer?<div className="management-row"><div><strong>Transfer to {current.data.transfer.recipient_name}</strong><small>{current.data.transfer.recipient_email} · {current.data.transfer.status==='PENDING_OWNER'?'Awaiting your email confirmation':'Awaiting recipient consent'}</small></div><button disabled={busy} onClick={()=>void cancel()}>Cancel transfer</button></div>:<form className="management-inline-form" onSubmit={start}><label>New Owner<select name="recipient" required><option value="">Select an active verified administrator</option>{eligible.map(m=><option value={m.user_id} key={m.id}>{m.name} · {m.email}</option>)}</select></label><button className="management-primary" disabled={busy||!eligible.length}>Transfer ownership</button></form>}{!eligible.length&&!current.data?.transfer&&<p>Add and verify another Administrator before transferring ownership.</p>}{error&&<p role="alert" className="auth-notice">{error}</p>}{notice&&<p role="status" className="auth-notice">{notice}</p>}</section>;
}
