import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../services/api';
import * as supabase from '../services/supabase';
import { AuthProvider } from './AuthContext';
import { ActivityLogsPage } from './ActivityLogsPage';
import { DataSettingsPage } from './DataSettingsPage';

vi.mock('../services/api');
vi.mock('../utils/downloadHelper', () => ({ triggerBrowserDownload: vi.fn() }));
vi.mock('../services/supabase', () => ({
  getSupabaseUser: vi.fn(),
  getSupabaseSession: vi.fn(),
  signInWithSupabasePassword: vi.fn(),
  supabaseProviderIds: vi.fn(),
  clearSupabaseSession: vi.fn(),
  updateSupabasePassword: vi.fn(),
  linkSupabaseGoogle: vi.fn(),
}));

const workspace = { id: 'firm-1', name: 'Example Firm', type: 'FIRM' as const, role: 'OWNER' as const };
const profile: api.AuthProfile = { user: { id: 'owner-1', name: 'Owner User', email: 'owner@example.com', email_verified: true }, workspaces: [workspace], reconciliation_available: true };

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getAuthProfile).mockResolvedValue(profile);
  vi.mocked(api.authErrorMessage).mockReturnValue('Request failed');
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.mocked(supabase.getSupabaseUser).mockResolvedValue({} as never);
  vi.mocked(supabase.supabaseProviderIds).mockReturnValue(['google']);
});

describe('data settings', () => {
  it('does not expose advanced workspace export or reset controls', async () => {
    render(<AuthProvider enabled><DataSettingsPage /></AuthProvider>);
    expect(await screen.findByRole('heading', { name: 'Profile' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Download workspace ZIP/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reset workspace' })).not.toBeInTheDocument();
    expect(api.workspaceDownload).not.toHaveBeenCalled();
    expect(api.workspaceMutation).not.toHaveBeenCalled();
  });

  it('uses a fresh Supabase password session when deleting an email account', async () => {
    vi.mocked(supabase.supabaseProviderIds).mockReturnValue(['email']);
    vi.mocked(supabase.signInWithSupabasePassword).mockResolvedValue({ access_token: 'fresh-token' } as never);
    vi.mocked(api.deleteAccount).mockResolvedValue({ message: 'deleted' });
    render(<AuthProvider enabled><DataSettingsPage /></AuthProvider>);
    fireEvent.change(await screen.findByLabelText('Current password'), { target: { value: 'A proper sample passphrase 42!' } });
    fireEvent.change(screen.getByLabelText(/Type DELETE MY ACCOUNT/), { target: { value: 'DELETE MY ACCOUNT' } });
    fireEvent.click(screen.getByRole('button', { name: 'Delete account' }));
    await waitFor(() => expect(api.deleteAccount).toHaveBeenCalledWith('A proper sample passphrase 42!', 'fresh-token'));
    expect(supabase.clearSupabaseSession).toHaveBeenCalled();
  });

  it('uses the current Supabase session when deleting a Google account', async () => {
    vi.mocked(supabase.getSupabaseSession).mockResolvedValue({ access_token: 'google-token' } as never);
    vi.mocked(api.deleteAccount).mockResolvedValue({ message: 'deleted' });
    render(<AuthProvider enabled><DataSettingsPage /></AuthProvider>);
    fireEvent.change(await screen.findByLabelText(/Type DELETE MY ACCOUNT/), { target: { value: 'DELETE MY ACCOUNT' } });
    fireEvent.click(screen.getByRole('button', { name: 'Delete account' }));
    await waitFor(() => expect(api.deleteAccount).toHaveBeenCalledWith(undefined, 'google-token'));
  });

  it('creates a Supabase password for a Google-only user', async () => {
    render(<AuthProvider enabled><DataSettingsPage /></AuthProvider>);
    fireEvent.click(await screen.findByRole('button', { name: 'Create password' }));
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'A secure Supabase passphrase 42!' } });
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'A secure Supabase passphrase 42!' } });
    fireEvent.submit(screen.getByRole('form', { name: 'Create password' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Password created');
    expect(supabase.updateSupabasePassword).toHaveBeenCalledWith('A secure Supabase passphrase 42!', undefined);
  });

  it('requires the current password when changing a Supabase password', async () => {
    vi.mocked(supabase.supabaseProviderIds).mockReturnValue(['email']);
    render(<AuthProvider enabled><DataSettingsPage /></AuthProvider>);
    fireEvent.click(await screen.findByRole('button', { name: 'Change password' }));
    const form = screen.getByRole('form', { name: 'Change password' });
    fireEvent.change(within(form).getByLabelText('Current password'), { target: { value: 'Current passphrase 42!' } });
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'A changed Supabase passphrase 42!' } });
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'A changed Supabase passphrase 42!' } });
    fireEvent.submit(screen.getByRole('form', { name: 'Change password' }));
    await waitFor(() => expect(supabase.updateSupabasePassword).toHaveBeenCalledWith('A changed Supabase passphrase 42!', 'Current passphrase 42!'));
  });

  it('rejects mismatched new passwords before calling Supabase', async () => {
    render(<AuthProvider enabled><DataSettingsPage /></AuthProvider>);
    fireEvent.click(await screen.findByRole('button', { name: 'Create password' }));
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'A secure Supabase passphrase 42!' } });
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'A different Supabase passphrase!' } });
    fireEvent.submit(screen.getByRole('form', { name: 'Create password' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('do not match');
    expect(supabase.updateSupabasePassword).not.toHaveBeenCalled();
  });
});

describe('activity logs', () => {
  it('renders server events, filters employees and exports CSV', async () => {
    vi.mocked(api.workspaceGet).mockResolvedValue({ items: [{ id: 'log-1', timestamp: '2026-09-01T00:00:00Z', action: 'CLIENT_CREATED', actor_user_id: 'employee-1', actor_name: 'Employee', actor_email: 'e@example.com', entity_type: 'clients', entity_id: 'c1', metadata: {} }], has_more: false, can_filter_people: true, actors: [{ user_id: 'employee-1', name: 'Employee', email: 'e@example.com', role: 'ACCOUNTANT', status: 'ACTIVE' }] });
    vi.mocked(api.workspaceDownload).mockResolvedValue({ blob: new Blob(['csv']), filename: 'logs.csv' });
    render(<AuthProvider enabled><ActivityLogsPage /></AuthProvider>);
    expect(await screen.findByText('CLIENT CREATED')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Employee log filter'), { target: { value: 'employee-1' } });
    await waitFor(() => expect(api.workspaceGet).toHaveBeenCalledWith(expect.stringContaining('actor_user_id=employee-1'), expect.any(AbortSignal)));
    fireEvent.click(screen.getByRole('button', { name: 'Export CSV' }));
    await waitFor(() => expect(api.workspaceDownload).toHaveBeenCalledWith('firm-1/logs/export?actor_user_id=employee-1', 'aligned-audit-log.csv'));
  });
});
