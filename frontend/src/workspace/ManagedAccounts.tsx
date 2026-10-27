import { useState, type FormEvent } from 'react';
import { authErrorMessage, workspaceMutation, type AuthProfile } from '../services/api';
import type { Member } from './management';

export function ManagedAccounts({workspace,members,onSaved}:{workspace:AuthProfile['workspaces'][number];members:Member[];onSaved:()=>void}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  async function create(e:FormEvent<HTMLFormElement>) {
    e.preventDefault();if(busy)return;
    const form=e.currentTarget,fields=new FormData(form);setBusy(true);setError('');setNotice('');
    try { const result=await workspaceMutation(`${workspace.id}/accounts`,{name:String(fields.get('name')),email:String(fields.get('email')),username:String(fields.get('username')),role:String(fields.get('role'))});setNotice(result.message);form.reset();onSaved(); }
    catch(failure){setError(authErrorMessage(failure));}finally{setBusy(false);}
  }
  async function resend(member:Member) {
    setBusy(true);setError('');setNotice('');
    try{const result=await workspaceMutation(`${workspace.id}/members/${member.id}/activation`);setNotice(result.message);}catch(failure){setError(authErrorMessage(failure));}finally{setBusy(false);}
  }
  return <section className="management-panel"><h2>Create a colleague account</h2><p>Create their account now, then let them set their own password. No separate registration is needed.</p><form onSubmit={create}><fieldset disabled={busy} className="management-fields"><label>Colleague name<input name="name" required maxLength={200}/></label><label>Contact email<input name="email" type="email" required maxLength={254}/></label><label>Username<input name="username" required pattern="[a-z][a-z0-9.\-]{1,39}" minLength={2} maxLength={40} placeholder="suyash"/></label><p className="field-hint">Login format: username@{workspace.login_namespace||'your-firm'}.aligned.com</p><label>Account role<select name="role"><option value="ACCOUNTANT">Accountant · assigned clients only</option><option value="ADMIN">Administrator · full workspace access</option></select></label><button className="management-primary">{busy?'Please wait…':'Create colleague account'}</button></fieldset></form><p className="field-hint">Firm IDs are login names, not email inboxes. Activation is saved to the real contact’s local mailbox in scratch/auth-mail.</p>{error&&<p role="alert" className="auth-notice">{error}</p>}{notice&&<p role="status" className="auth-notice">{notice}</p>}
    {members.some(m=>m.login_id)&&<div className="managed-login-list"><h3>Firm login IDs</h3>{members.filter(m=>m.login_id).map(m=><div className="management-row" key={m.id}><div><strong>{m.name}</strong><small>{m.login_id}</small><small>{m.activation_pending?'Account created · awaiting activation':m.status}</small></div>{m.activation_pending&&m.status==='ACTIVE'&&<button disabled={busy} onClick={()=>void resend(m)}>Resend activation</button>}</div>)}</div>}
  </section>;
}
