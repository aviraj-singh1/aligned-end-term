import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ManagementPage } from './ManagementPage';
import { InvitationPage } from './InvitationPage';
import { AuthProvider } from './AuthContext';
import * as api from '../services/api';

vi.mock('../services/api');
const firm = { id: 'firm-1', name: 'Example Firm', type: 'FIRM' as const, role: 'ADMIN' as const };
const profile: api.AuthProfile = { user: { id: 'user-1', name: 'Test User', email: 'user@example.com', email_verified: true }, workspaces: [firm], reconciliation_available: false };
const detail = { id: 'client-1', name: 'Example Client', legal_name: null, pan: null, status: 'ACTIVE', gstins: [], membership_ids: [] };
const accountant = { id: 'member-2', user_id: 'user-2', name: 'Accountant', email: 'colleague@example.com', role: 'ACCOUNTANT', status: 'ACTIVE' };
const owner = { id:'member-owner',user_id:'user-1',name:'Test User',email:'user@example.com',role:'OWNER' as const,status:'ACTIVE' as const,email_verified:true };

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getAuthProfile).mockResolvedValue(profile);
  vi.mocked(api.authErrorMessage).mockReturnValue('Workspace request failed');
  vi.mocked(api.workspaceMutation).mockResolvedValue({ message: 'Saved' });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});

function mount(mode: 'clients' | 'team') { return render(<AuthProvider enabled><ManagementPage mode={mode} /></AuthProvider>); }

describe('Connected clients and team', () => {
  it('creates a client using only editable fields and loads the saved record', async () => {
    vi.mocked(api.workspaceGet).mockImplementation(async path => path.endsWith('/client-1') ? detail : path.includes('/team') ? { members: [], invites: [], has_more: false } : { items: [], has_more: false });
    vi.mocked(api.workspaceMutation).mockResolvedValue(detail);
    mount('clients');
    fireEvent.click(await screen.findByRole('button', { name: 'Add client' }));
    fireEvent.change(screen.getByLabelText('Client name'), { target: { value: 'Example Client' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save client' }));
    expect(await screen.findByRole('heading', { name: 'Example Client' })).toBeInTheDocument();
    expect(api.workspaceMutation).toHaveBeenCalledWith('firm-1/clients', { name: 'Example Client', legal_name: null, pan: null }, 'POST');
  });

  it('does not show creation or team controls for accountants', async () => {
    vi.mocked(api.getAuthProfile).mockResolvedValue({ ...profile, workspaces: [{ ...firm, role: 'ACCOUNTANT' }] });
    vi.mocked(api.workspaceGet).mockResolvedValue({ items: [], has_more: false });
    mount('clients');
    expect(await screen.findByText(/Unassigned clients stay private/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add client' })).not.toBeInTheDocument();
  });

  it('requires verification before requesting client data', async () => {
    vi.mocked(api.getAuthProfile).mockResolvedValue({ ...profile, user: { ...profile.user, email_verified: false } });
    mount('clients');
    await waitFor(() => expect(api.getAuthProfile).toHaveBeenCalled());
    expect(screen.getByRole('heading', { name: 'Verify your email first' })).toBeInTheDocument();
    expect(api.workspaceGet).not.toHaveBeenCalled();
  });

  it('clears old workspace views and ignores late responses when switching', async () => {
    vi.mocked(api.getAuthProfile).mockResolvedValue({ ...profile, workspaces: [firm, { ...firm, id: 'firm-2', name: 'Second Firm' }] });
    let resolveFirst!: (value: unknown) => void;
    vi.mocked(api.workspaceGet).mockImplementation(path => path.startsWith('firm-1/') ? new Promise(resolve => { resolveFirst = resolve; }) : Promise.resolve({ items: [{ ...detail, name: 'Second firm client' }], has_more: false }));
    mount('clients');
    await waitFor(() => expect(api.workspaceGet).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText('Workspace'), { target: { value: 'firm-2' } });
    expect(await screen.findByRole('button', { name: /Second firm client/ })).toBeInTheDocument();
    await act(async () => resolveFirst({ items: [{ ...detail, name: 'Private old client' }], has_more: false }));
    expect(screen.queryByText('Private old client')).not.toBeInTheDocument();
    expect(api.clearRequestScope).toHaveBeenCalled();
  });

  it('shows an explicit conversion for individuals and does not request firm data', async () => {
    vi.mocked(api.getAuthProfile).mockResolvedValue({ ...profile, workspaces: [{ ...firm, type: 'INDIVIDUAL' }] });
    mount('team');
    fireEvent.click(await screen.findByRole('button', { name: 'Convert to firm' }));
    await waitFor(() => expect(api.workspaceMutation).toHaveBeenCalledWith('firm-1/convert-to-firm', { name: 'Example Firm' }));
    expect(api.workspaceGet).not.toHaveBeenCalled();
  });

  it('sends invitations and updates member access through scoped endpoints', async () => {
    vi.mocked(api.workspaceGet).mockResolvedValue({ members: [accountant], invites: [], has_more: false });
    mount('team');
    await screen.findByText('Accountant', { selector: 'strong' });
    fireEvent.change(screen.getByLabelText('Email address'), { target: { value: 'new@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send invitation' }));
    await waitFor(() => expect(api.workspaceMutation).toHaveBeenCalledWith('firm-1/invites', { email: 'new@example.com', role: 'ACCOUNTANT' }, 'POST'));
    await screen.findByText('Accountant', { selector: 'strong' });
    fireEvent.change(screen.getByLabelText('Status for colleague@example.com'), { target: { value: 'SUSPENDED' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save access' }));
    await waitFor(() => expect(api.workspaceMutation).toHaveBeenCalledWith('firm-1/members/member-2', { role: 'ACCOUNTANT', status: 'SUSPENDED' }, 'PATCH'));
  });

  it('protects the Owner UI and starts a two-email ownership transfer only to a verified admin', async()=>{
    const admin={...accountant,role:'ADMIN' as const,email_verified:true};
    vi.mocked(api.getAuthProfile).mockResolvedValue({...profile,workspaces:[{...firm,role:'OWNER' as const}]});
    vi.mocked(api.workspaceGet).mockImplementation(async path=>path.endsWith('/ownership-transfers/current')?{transfer:null}:{members:[owner,admin],invites:[],has_more:false});
    vi.mocked(api.workspaceMutation).mockResolvedValue({message:'Confirmation sent to your registered email.'});
    mount('team');
    expect(await screen.findByText('Protected · transfer ownership to change')).toBeInTheDocument();
    expect(screen.queryByLabelText(`Role for ${owner.email}`)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('New Owner'),{target:{value:admin.user_id}});
    fireEvent.click(screen.getByRole('button',{name:'Transfer ownership'}));
    await waitFor(()=>expect(api.workspaceMutation).toHaveBeenCalledWith('firm-1/ownership-transfers',{recipient_user_id:admin.user_id},'POST'));
    expect(await screen.findByText('Confirmation sent to your registered email.')).toBeInTheDocument();
  });

  it('does not let an administrator export the Owner or an administrator peer', async()=>{
    const protectedOwner={...owner,user_id:'owner-user',email:'owner@example.com'};
    const peer={...accountant,id:'member-3',user_id:'user-3',name:'Peer Admin',email:'peer@example.com',role:'ADMIN' as const};
    vi.mocked(api.workspaceGet).mockResolvedValue({members:[protectedOwner,peer],invites:[],has_more:false});
    mount('team');
    await screen.findByText('Peer Admin');
    expect(screen.queryByRole('button',{name:'Export data'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button',{name:'Export'})).not.toBeInTheDocument();
  });

  it('adds GSTINs and assigns an accountant to the selected client', async () => {
    vi.mocked(api.workspaceGet).mockImplementation(async path => path.includes('/team') ? { members: [accountant], invites: [], has_more: false } : path.endsWith('/client-1') ? detail : { items: [detail], has_more: false });
    mount('clients');
    fireEvent.click(await screen.findByRole('button', { name: /Example Client/ }));
    fireEvent.change(await screen.findByLabelText('GSTIN'), { target: { value: '27ABCDE1234F1Z5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add GSTIN' }));
    await waitFor(() => expect(api.workspaceMutation).toHaveBeenCalledWith('firm-1/clients/client-1/gstins', { gstin: '27ABCDE1234F1Z5' }, 'POST'));
    fireEvent.click(await screen.findByRole('button', { name: 'Assign client' }));
    await waitFor(() => expect(api.workspaceMutation).toHaveBeenCalledWith('firm-1/clients/client-1/assignments/member-2', {}, 'POST'));
  });

  it('shows a retryable API error instead of claiming an empty client list', async () => {
    vi.mocked(api.workspaceGet).mockRejectedValue(new Error('offline'));
    mount('clients');
    expect(await screen.findByRole('alert')).toHaveTextContent('Workspace request failed');
    expect(screen.queryByRole('heading', { name: 'Your clients start here' })).not.toBeInTheDocument();
  });
});

describe('Invitation flow', () => {
  it('accepts only after explicit confirmation, using the supplied token', async () => {
    vi.mocked(api.acceptInvitation).mockResolvedValue({ message: 'Invitation accepted.' });
    const token = 'a'.repeat(43);
    render(<AuthProvider enabled><InvitationPage token={token} /></AuthProvider>);
    const button = await screen.findByRole('button', { name: 'Accept invitation' });
    expect(api.acceptInvitation).not.toHaveBeenCalled();
    fireEvent.click(button);
    expect(await screen.findByRole('status')).toHaveTextContent('Invitation accepted.');
    expect(api.acceptInvitation).toHaveBeenCalledWith(token);
  });

  it('rejects a malformed invitation without calling the API', () => {
    render(<InvitationPage token="broken" />);
    expect(screen.getByRole('alert')).toHaveTextContent('incomplete');
    expect(api.acceptInvitation).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Accept invitation' })).not.toBeInTheDocument();
  });

  it('registers an invited colleague without asking for a new workspace', async () => {
    vi.mocked(api.getAuthProfile).mockResolvedValue(null);
    vi.mocked(api.registerInvited).mockResolvedValue({ message: 'Verify your email, then sign in and reopen the original invitation.' });
    render(<AuthProvider enabled><InvitationPage token={'a'.repeat(43)} /></AuthProvider>);
    fireEvent.click(await screen.findByRole('button', { name: 'Create invited account' }));
    fireEvent.change(screen.getByLabelText('Full name'), { target: { value: 'New Colleague' } });
    fireEvent.change(screen.getByLabelText('Email address'), { target: { value: 'colleague@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'A suitable passphrase 54!' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Verify your email');
    expect(api.registerInvited).toHaveBeenCalledWith({ token: 'a'.repeat(43), name: 'New Colleague', email: 'colleague@example.com', password: 'A suitable passphrase 54!' });
    expect(screen.getByLabelText('Password')).toHaveValue('');
    expect(api.registerAccount).not.toHaveBeenCalled();
  });
});
