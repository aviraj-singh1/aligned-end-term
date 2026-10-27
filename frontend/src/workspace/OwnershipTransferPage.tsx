import { useEffect, useState } from 'react';
import { authErrorMessage, ownershipConfirm, ownershipPreview } from '../services/api';
import { useAuth } from './AuthContext';
import { AuthPage } from './AuthPage';

export function OwnershipTransferPage({token}:{token:string}) {
  const auth=useAuth();
  const [details,setDetails]=useState<Awaited<ReturnType<typeof ownershipPreview>>|null>(null);
  const [error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
  const valid=/^[A-Za-z0-9_-]{43}$/.test(token);
  useEffect(()=>{if(auth.profile&&valid)ownershipPreview(token).then(setDetails).catch(e=>setError(authErrorMessage(e)));},[auth.profile,token,valid]);
  async function confirm(){if(!details||busy)return;if(!window.confirm(details.stage==='OWNER'?`Confirm transfer of ${details.workspace_name} to ${details.recipient_name}? You will become an Administrator only after they accept.`:`Accept ownership of ${details.workspace_name}? ${details.owner_name} will become an Administrator.`))return;const stage=details.stage;setBusy(true);setError('');try{const result=await ownershipConfirm(token);setMessage(result.message);setDetails(null);if(stage==='OWNER')await auth.refresh(true);else window.setTimeout(()=>{window.location.hash='/sign-in';},2500);}catch(e){setError(authErrorMessage(e));}finally{setBusy(false);}}
  return <main className="workspace-settings recovery-page"><p className="auth-eyebrow">OWNERSHIP CONSENT</p><h1>Workspace ownership transfer</h1>
    {!valid?<p role="alert" className="auth-notice">This ownership link is incomplete or invalid.</p>:!auth.profile?<><p>Sign in with the registered email address that received this link.</p><AuthPage mode="sign-in" /></>:null}
    {details&&<section className="management-panel"><h2>{details.workspace_name}</h2><p>{details.stage==='OWNER'?`You are confirming ${details.recipient_name} as the proposed Owner.`:`${details.owner_name} nominated you to become Owner.`}</p><p>Ownership has not changed yet. This action cannot bypass the other person’s email consent.</p><button className="management-primary" disabled={busy} onClick={()=>void confirm()}>{busy?'Confirming…':details.stage==='OWNER'?'Confirm and email recipient':'Accept ownership'}</button></section>}
    {error&&<p role="alert" className="auth-notice">{error}</p>}{message&&<><p role="status" className="auth-notice">{message}</p>{message.includes('transferred')&&<p>You have been signed out. Redirecting to sign in in a moment; you may close this tab.</p>}</>}
  </main>;
}
