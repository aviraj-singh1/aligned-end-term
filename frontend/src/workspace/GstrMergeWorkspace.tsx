import { useState } from 'react';
import { clearRequestScope } from '../services/api';
import { GstrMergeTool } from '../components/gstr-merge/GstrMergeTool';
import { ClientChooser } from './ClientChooser';
import { useAuth } from './AuthContext';
import type { ClientSummary } from './management';

export function GstrMergeWorkspace() {
  const { profile } = useAuth();
  const [workspaceId, setWorkspaceId] = useState('');
  const [client, setClient] = useState<ClientSummary | null>(null);
  const workspace = profile?.workspaces.find(item => item.id === workspaceId) || profile?.workspaces[0];

  return <div className="owned-reconciliation"><main className="workspace-settings">
    <div className="dashboard-heading"><div><p className="auth-eyebrow">CLIENT GSTR WORKSPACE</p><h1>GSTR Consolidation</h1><p className="settings-intro">Combine quarterly or monthly GSTR workbooks into one independently verified result.</p></div>
      {workspace ? <label className="workspace-picker">Workspace<select value={workspace.id} onChange={event => {
        clearRequestScope();
        setWorkspaceId(event.target.value);
        setClient(null);
      }}>{profile?.workspaces.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> : null}
    </div>
    {!profile?.user.email_verified ? <p className="auth-notice">Verify your email before consolidating GSTR workbooks. <a href="#/resend-verification">Request verification</a></p>
      : !workspace ? <p>No workspace yet. Accept your firm invitation first.</p>
        : client ? <><button type="button" className="management-back" onClick={() => { clearRequestScope(); setClient(null); }}>Choose another client</button><GstrMergeTool key={`${workspace.id}:${client.id}`} workspaceId={workspace.id} client={client} /></>
          : <ClientChooser key={workspace.id} workspaceId={workspace.id} onChoose={setClient} />}
  </main></div>;
}
