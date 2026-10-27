import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { AuthProvider } from './AuthContext';
import { ReconciliationWorkspace } from './ReconciliationWorkspace';
import * as api from '../services/api';

vi.mock('../services/api');
vi.mock('../App', () => ({ default: ({ context, initial }: { context: api.ReconciliationContext; initial?: api.OpenRun }) => <div data-testid="scoped-tool">{context.workspaceId}/{context.clientId}/{context.gstinId || 'client-level'} {initial?.phase || 'NEW'}</div> }));
const client = { id: 'client-1', name: 'Example Client', status: 'ACTIVE', legal_name: null, pan: null };
const run = { id: 'run-1', session_id: 'session-1', client_gstin_id: null, created_at: '2026-08-31T12:00:00Z', status: 'COMPLETED', version: 2, availability: 'AVAILABLE', expires_at: '2026-08-31T13:00:00Z' };

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getAuthProfile).mockResolvedValue({ user: { id: 'u',name: 'Test',email: 'test@example.com',email_verified: true }, workspaces: [{ id: 'workspace-1',name: 'Firm',type: 'FIRM',role: 'ADMIN' }], reconciliation_available: true });
  vi.mocked(api.workspaceGet).mockImplementation(async path => path.includes('/runs') ? { items: [run],has_more: false } : path.endsWith('/client-1') ? { ...client, gstins: [{id:'gstin-1',gstin:'27ABCDE1234F1Z5',status:'ACTIVE'}],membership_ids:[] } : {items:[client],has_more:false});
});

async function choose() {
  render(<AuthProvider enabled><ReconciliationWorkspace /></AuthProvider>);
  fireEvent.click(await screen.findByRole('button',{name:'Select Example Client'}));
  await screen.findByRole('button',{name:'Open run'});
}

it('requires client selection and sends workspace/client/GSTIN context to a new upload', async () => {
  await choose();
  expect(screen.queryByTestId('scoped-tool')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('GST registration'), { target: { value: 'gstin-1' } });
  fireEvent.click(screen.getByRole('button',{name:'New reconciliation'}));
  expect(screen.getByTestId('scoped-tool')).toHaveTextContent('workspace-1/client-1/gstin-1 NEW');
  fireEvent.click(screen.getByRole('button',{name:'Choose another client'}));
  expect(screen.queryByTestId('scoped-tool')).not.toBeInTheDocument();
  expect(api.clearRequestScope).toHaveBeenCalled();
});

it('reopens a completed run without triggering another reconciliation', async () => {
  vi.mocked(api.openOwnedRun).mockResolvedValue({ phase:'COMPLETED',version:2,data:{session_id:'session-1'} } as api.OpenRun);
  await choose();
  fireEvent.click(screen.getByRole('button',{name:'Open run'}));
  expect(await screen.findByTestId('scoped-tool')).toHaveTextContent('COMPLETED');
  expect(api.openOwnedRun).toHaveBeenCalledWith({workspaceId:'workspace-1',clientId:'client-1'},'session-1');
  expect(api.reconcileSession).not.toHaveBeenCalled();
});

it('shows expired and unresolved runs without offering to open their results', async () => {
  vi.mocked(api.workspaceGet).mockImplementation(async path => path.includes('/runs') ? { items:[{...run,availability:'EXPIRED'},{...run,id:'run-2',availability:'UNRESOLVED'}],has_more:false } : path.endsWith('/client-1') ? {...client,gstins:[],membership_ids:[]} : {items:[client],has_more:false});
  render(<AuthProvider enabled><ReconciliationWorkspace /></AuthProvider>);
  fireEvent.click(await screen.findByRole('button',{name:'Select Example Client'}));
  expect(await screen.findByText(/COMPLETED · EXPIRED/)).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Open run'})).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Recover'})).toBeInTheDocument();
});

it('blocks an unverified account without mounting uploads or fetching business data', async () => {
  vi.mocked(api.getAuthProfile).mockResolvedValue({user:{id:'u',name:'Test',email:'test@example.com',email_verified:false},workspaces:[],reconciliation_available:true});
  render(<AuthProvider enabled><ReconciliationWorkspace /></AuthProvider>);
  await waitFor(()=>expect(api.getAuthProfile).toHaveBeenCalled());
  expect(screen.getByText(/Verify your email before reconciling/)).toBeInTheDocument();
  expect(api.workspaceGet).not.toHaveBeenCalled();
});
