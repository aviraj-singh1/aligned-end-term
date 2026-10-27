import { useState } from 'react';
import { ArrowUpRight, Building2, CheckCheck, FileCheck2, ScrollText, Users } from 'lucide-react';
import { useAuth } from './AuthContext';
import { useWorkspaceData } from './management';
import { clearRequestScope } from '../services/api';

interface DashboardData { workspace_name:string; clients:number; accountants:number|null; runs:number; completed:number; daily:{date:string;runs:number}[]; recent:{id:string;client_name:string;status:string;created_at:string;deleted:boolean}[] }

export function DashboardPage() {
  const {profile,refresh}=useAuth();
  const [selected,setSelected]=useState('');
  const workspace=profile?.workspaces.find(w=>w.id===selected)||profile?.workspaces[0];
  return <main className="workspace-settings dashboard-page"><div className="dashboard-heading"><div><p className="auth-eyebrow">WORKSPACE OVERVIEW</p><h1>Dashboard</h1><p className="settings-intro">Welcome back, {profile?.user.name.split(' ')[0]}. Here’s your practice at a glance.</p></div>{workspace&&<label className="workspace-picker">Workspace<select value={workspace.id} onChange={e=>{clearRequestScope();setSelected(e.target.value);}}>{profile?.workspaces.map(w=><option key={w.id} value={w.id}>{w.name}</option>)}</select></label>}</div>
    {!profile?.user.email_verified ? <section className="dashboard-onboarding"><span className="management-badge">ACCOUNT CREATED</span><h2>Confirm your Supabase email.</h2><p>Open the confirmation message sent by Supabase, then return here to start managing clients and reconciling records.</p><div className="landing-actions"><button onClick={()=>void refresh()}>I’ve confirmed my email</button></div></section>
      : workspace ? <DashboardContent key={workspace.id} workspaceId={workspace.id}/> : <section className="management-panel"><h2>No active workspace</h2><p>Ask your firm administrator to check your membership.</p></section>}
  </main>;
}

function DashboardContent({workspaceId}:{workspaceId:string}) {
  const resource=useWorkspaceData<DashboardData>(`${workspaceId}/dashboard`);
  if(resource.error)return <section className="management-panel" role="alert"><p>{resource.error}</p><button onClick={resource.reload}>Retry dashboard</button></section>;
  if(!resource.data)return <p role="status">Loading workspace analytics…</p>;
  const data=resource.data,max=Math.max(1,...data.daily.map(d=>d.runs));
  const metrics=[{label:'Active clients',value:data.clients,icon:Building2,tone:'sage'},...(data.accountants===null?[]:[{label:'Active accountants',value:data.accountants,icon:Users,tone:'lavender'}]),{label:'Reconciliation runs',value:data.runs,icon:FileCheck2,tone:'sand'},{label:'Completed runs',value:data.completed,icon:CheckCheck,tone:'white'}];
  return <><div className="dashboard-metrics">{metrics.map(({label,value,icon:Icon,tone})=><article className={`metric-card ${tone}`} key={label}><div><span>{label}</span><Icon size={20}/></div><strong>{value.toLocaleString()}</strong><small>Within your current client access</small></article>)}</div>
    <div className="dashboard-main-grid"><section className="dashboard-panel"><div className="panel-heading"><div><h2>Reconciliation activity</h2><p>Runs created over the last 7 days · UTC</p></div><button onClick={resource.reload}>Refresh</button></div><div className="activity-bars" role="img" aria-label={data.daily.map(d=>`${d.date}: ${d.runs} runs`).join('; ')}>{data.daily.map(d=><div className="activity-column" key={d.date}><span>{d.runs}</span><div className="bar-track"><i style={{height:`${d.runs/max*100}%`}}/></div><small>{new Date(d.date+'T00:00:00Z').toLocaleDateString(undefined,{weekday:'short',timeZone:'UTC'})}</small></div>)}</div>{!data.daily.some(d=>d.runs>0)&&<p className="chart-empty">Your first reconciliation will appear here.</p>}</section>
    <section className="dashboard-panel dashboard-shortcuts"><p className="auth-eyebrow">PICK UP THE WORK</p><h2>What’s next?</h2><a href="#/reconcile"><FileCheck2/><div><strong>Start a reconciliation</strong><span>Choose a client and upload source files</span></div><ArrowUpRight/></a><a href="#/clients"><Building2/><div><strong>Manage clients</strong><span>Details and GST registrations</span></div><ArrowUpRight/></a><a href="#/logs"><ScrollText/><div><strong>View activity logs</strong><span>Review workspace activity</span></div><ArrowUpRight/></a></section></div>
    <section className="dashboard-panel"><div className="panel-heading"><div><h2>Recent runs</h2><p>Durable run history, including expired or deleted results</p></div><a href="#/reconcile">Open client history <ArrowUpRight size={15}/></a></div>{data.recent.length?<div className="dashboard-run-list">{data.recent.map(r=><div key={r.id}><span className="recent-run-icon"><FileCheck2 size={18}/></span><strong>{r.client_name}</strong><time dateTime={r.created_at}>{new Date(r.created_at).toLocaleString()}</time><span className="management-badge">{r.deleted?'RESULTS DELETED':r.status.replaceAll('_',' ')}</span></div>)}</div>:<div className="dashboard-empty"><FileCheck2 size={30}/><h3>No reconciliations yet</h3><p>Add a client, then start your first run. Your activity will be recorded here.</p><a href="#/clients">Go to clients →</a></div>}</section>
  </>;
}
