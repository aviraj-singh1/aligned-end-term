import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import WorkspaceApp from './WorkspaceApp';
import { AuthPage } from './AuthPage';
import * as api from '../services/api';
import * as supabase from '../services/supabase';
import { AuthProvider } from './AuthContext';

vi.mock('../services/api');
vi.mock('../services/supabase',()=>({
  supabaseConfigured:true,
  getSupabaseSession:vi.fn().mockResolvedValue(null),
  signInWithSupabasePassword:vi.fn(),
  signUpWithSupabasePassword:vi.fn(),
  startSupabaseGoogle:vi.fn(),
  clearSupabaseSession:vi.fn(),
  sendSupabasePasswordReset:vi.fn(),
  updateSupabasePassword:vi.fn(),
  getSupabaseUser:vi.fn().mockResolvedValue(null),
  supabaseProviderIds:vi.fn().mockReturnValue([]),
  linkSupabaseGoogle:vi.fn(),
}));
function navigate(page: string) {
  window.location.hash = `/${page}`;
  fireEvent(window, new HashChangeEvent('hashchange'));
}

describe('Authentication screens', () => {
  it('labels sign-in fields, toggles password visibility, and keeps preview credentials local', async () => {
    render(<AuthPage mode="sign-in" />);
    expect(screen.getByRole('heading', { name: 'Welcome back.' })).toBeInTheDocument();
    const password = screen.getByLabelText('Password');
    expect(password).toHaveAttribute('type', 'password');
    expect(password).toHaveAttribute('autocomplete', 'current-password');
    fireEvent.click(screen.getByRole('button', { name: 'Show password' }));
    expect(password).toHaveAttribute('type', 'text');
    fireEvent.click(screen.getByRole('button', { name: 'Hide password' }));
    expect(password).toHaveAttribute('type', 'password');
    fireEvent.change(screen.getByLabelText('Email address'), { target: { value: 'preview@example.com' } });
    fireEvent.change(password, { target: { value: 'sample-password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('status')).toHaveTextContent('credentials were not sent');
    expect(api.signIn).not.toHaveBeenCalled();
  });

  it('requires a valid email and password before submitting', () => {
    render(<AuthPage mode="sign-in" />);
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Email address')).toBeInvalid();
    expect(screen.getByLabelText('Password')).toBeInvalid();
  });

  it('links password recovery to the dedicated recovery screen', () => {
    render(<AuthPage mode="sign-in" />);
    expect(screen.getByRole('link', { name: 'Forgot password?' })).toHaveAttribute('href','#/forgot-password');
  });

  it('requires matching passwords before starting registration', async () => {
    render(<AuthPage mode="sign-up" />);
    fireEvent.change(screen.getByLabelText('Email address'),{target:{value:'new@example.com'}});
    fireEvent.change(screen.getByLabelText('Password'),{target:{value:'A long sample passphrase'}});
    fireEvent.change(screen.getByLabelText('Confirm password'),{target:{value:'A different passphrase'}});
    fireEvent.click(screen.getByRole('button',{name:'Continue'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('Passwords do not match');
    expect(supabase.signUpWithSupabasePassword).not.toHaveBeenCalled();
  });
});

describe('Workspace shell', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/');
    vi.clearAllMocks();
    vi.mocked(api.healthCheck).mockResolvedValue({ status: 'ok', service: 'GST Tool' });
  });

  it('shows a public landing page without workspace navigation by default', () => {
    render(<WorkspaceApp />);
    expect(screen.getByRole('heading', { name: /Less spreadsheet work/ })).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'Workspace navigation' })).not.toBeInTheDocument();
    expect(api.healthCheck).not.toHaveBeenCalled();
  });

  it('supports direct auth links and clears password fields when switching auth pages', () => {
    window.history.replaceState(null, '', '/#/sign-up');
    render(<WorkspaceApp />);
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'sample-password' } });
    navigate('sign-in');
    expect(screen.getByLabelText('Password')).toHaveValue('');
    expect(screen.getByRole('heading', { name: 'Welcome back.' })).toBeInTheDocument();
    expect(document.title).toBe('Sign in · ALIGNED');
    expect(api.healthCheck).not.toHaveBeenCalled();
  });

  it('retains the anonymous tool only on its explicit development route', async () => {
    window.history.replaceState(null, '', '/#/reconcile');
    render(<WorkspaceApp />);
    await screen.findByText('Connected');
    expect(screen.getByRole('heading', { name: 'Upload source data' })).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'Workspace navigation' })).not.toBeInTheDocument();
  });

  it('does not expose the excluded developer demo route', async () => {
    window.history.replaceState(null, '', '/#/demo');
    render(<WorkspaceApp />);
    expect(await screen.findByRole('heading', { name: 'Welcome back.' })).toBeInTheDocument();
    expect(screen.queryByText(/Developer demo mode/)).not.toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'Workspace navigation' })).not.toBeInTheDocument();
  });

  it('never renders a sidebar or mobile workspace menu on logged-out auth screens', () => {
    window.history.replaceState(null, '', '/#/sign-in');
    render(<WorkspaceApp />);
    expect(screen.queryByRole('button', { name: 'Open navigation' })).not.toBeInTheDocument();
    expect(document.getElementById('workspace-sidebar')).toBeNull();
  });
});

describe('Connected local authentication', () => {
  const profile: api.AuthProfile = { user: { id: 'user-1', name: 'Test User', email: 'user@example.com', email_verified: false }, workspaces: [{ id: 'workspace-1', name: 'Test Firm', type: 'FIRM', role: 'ADMIN' }], reconciliation_available: false };
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    vi.mocked(supabase.getSupabaseSession).mockResolvedValue(null);
    window.history.replaceState(null, '', '/#/sign-up');
    vi.mocked(api.getAuthProfile).mockResolvedValue(null);
    vi.mocked(api.authErrorMessage).mockReturnValue('Account service unavailable');
  });

  it('creates an individual workspace after confirmed Supabase registration', async () => {
    const session={access_token:'supabase-access-token',user:{email:profile.user.email}};
    vi.mocked(supabase.signUpWithSupabasePassword).mockResolvedValue({user:session.user,session} as never);
    vi.mocked(supabase.getSupabaseSession).mockResolvedValueOnce(null).mockResolvedValue(session as never);
    vi.mocked(api.registerSupabaseAccount).mockImplementation(async () => {
      vi.mocked(api.getAuthProfile).mockResolvedValue(profile);
      return {message:'Account created',signed_in:true};
    });
    render(<AuthProvider enabled><WorkspaceApp/></AuthProvider>);
    await screen.findByLabelText('Email address');
    fireEvent.change(screen.getByLabelText('Email address'),{target:{value:profile.user.email}});
    fireEvent.change(screen.getByLabelText('Password'),{target:{value:'A long sample passphrase'}});
    fireEvent.change(screen.getByLabelText('Confirm password'),{target:{value:'A long sample passphrase'}});
    fireEvent.click(screen.getByRole('button',{name:'Continue'}));
    fireEvent.change(await screen.findByLabelText('Full name'),{target:{value:'Test User'}});
    fireEvent.change(screen.getByLabelText('Address'),{target:{value:'10 Main Road'}});
    fireEvent.change(screen.getByLabelText('City'),{target:{value:'Delhi'}});
    fireEvent.change(screen.getByLabelText('State'),{target:{value:'Delhi'}});
    fireEvent.change(screen.getByLabelText('PIN code'),{target:{value:'110001'}});
    fireEvent.click(screen.getByRole('button',{name:'Create workspace'}));
    expect(await screen.findByRole('heading',{name:'Dashboard'})).toBeInTheDocument();
    expect(window.location.hash).toBe('#/dashboard');
    expect(api.registerSupabaseAccount).toHaveBeenCalledWith(expect.objectContaining({access_token:'supabase-access-token',workspace_type:'INDIVIDUAL',address_line1:'10 Main Road',postal_code:'110001'}));
    expect(api.registerSupabaseAccount).toHaveBeenCalledTimes(1);
  });

  it('waits for Supabase email confirmation when signup has no session', async () => {
    vi.mocked(supabase.signUpWithSupabasePassword).mockResolvedValue({user:{email:profile.user.email},session:null} as never);
    render(<AuthProvider enabled><WorkspaceApp/></AuthProvider>);
    fireEvent.change(await screen.findByLabelText('Email address'),{target:{value:profile.user.email}});
    fireEvent.change(screen.getByLabelText('Password'),{target:{value:'A long sample passphrase'}});
    fireEvent.change(screen.getByLabelText('Confirm password'),{target:{value:'A long sample passphrase'}});
    fireEvent.click(screen.getByRole('button',{name:'Continue'}));
    expect(await screen.findByRole('status')).toHaveTextContent('Check your email');
    expect(api.registerSupabaseAccount).not.toHaveBeenCalled();
  });

  it('redirects excluded feature hashes to the dashboard', async()=>{
    window.history.replaceState(null, '', '/#/gstr-merge');
    vi.mocked(api.getAuthProfile).mockResolvedValue({...profile,user:{...profile.user,email_verified:true},reconciliation_available:true});
    vi.mocked(api.workspaceGet).mockResolvedValue({workspace_name:'Test Firm',clients:0,accountants:null,runs:0,completed:0,daily:[],recent:[]});
    render(<AuthProvider enabled><WorkspaceApp/></AuthProvider>);
    expect(await screen.findByRole('heading',{name:'Dashboard'})).toBeInTheDocument();
    expect(screen.queryByText('GSTR Consolidation')).not.toBeInTheDocument();
  });

  it('refreshes a shared-cookie account change on focus and removes stale client controls', async () => {
    window.history.replaceState(null, '', '/#/clients');
    const admin = { ...profile, user: { ...profile.user, email_verified: true } };
    vi.mocked(api.getAuthProfile).mockResolvedValue(admin);
    vi.mocked(api.workspaceGet).mockResolvedValue({ items: [], has_more: false });
    render(<AuthProvider enabled><WorkspaceApp /></AuthProvider>);
    fireEvent.click(await screen.findByRole('button', { name: 'Add client' }));
    fireEvent.change(screen.getByLabelText('Client name'), { target: { value: 'Unsaved private draft' } });
    // Returning to the same account must not discard the form.
    fireEvent(window, new Event('focus'));
    await waitFor(() => expect(api.getAuthProfile).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText('Client name')).toHaveValue('Unsaved private draft');
    vi.mocked(api.getAuthProfile).mockResolvedValue({ ...admin, user: { ...admin.user, id:'other', name:'Accountant' }, workspaces:[{ ...admin.workspaces[0], role:'ACCOUNTANT' }] });
    fireEvent(window, new Event('focus'));
    expect(await screen.findByText('Accountant · assigned clients only')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name:'Save client' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name:'Add client' })).not.toBeInTheDocument();
    expect(screen.getByText(/The signed-in account changed/)).toBeInTheDocument();
    expect(api.setExpectedUserId).toHaveBeenLastCalledWith('other');
    expect(api.workspaceMutation).not.toHaveBeenCalled();
  });

  it('refreshes stale permissions after an API account-change notification', async () => {
    window.history.replaceState(null, '', '/#/clients');
    const admin = { ...profile, user:{ ...profile.user, email_verified:true } };
    vi.mocked(api.getAuthProfile).mockResolvedValue(admin);
    vi.mocked(api.workspaceGet).mockResolvedValue({ items:[], has_more:false });
    render(<AuthProvider enabled><WorkspaceApp /></AuthProvider>);
    await screen.findByRole('button', { name:'Add client' });
    vi.mocked(api.getAuthProfile).mockResolvedValue({ ...admin, workspaces:[{ ...admin.workspaces[0], role:'ACCOUNTANT' }] });
    fireEvent(window, new Event('aligned-account-changed'));
    expect(await screen.findByText('Accountant · assigned clients only')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name:'Add client' })).not.toBeInTheDocument();
  });

  it('signs in, loads the workspace, and signs out without retaining account data', async () => {
    window.history.replaceState(null, '', '/#/sign-in');
    vi.mocked(supabase.signInWithSupabasePassword).mockResolvedValue({access_token:'password-access-token'} as never);
    vi.mocked(api.supabaseSignIn).mockResolvedValue({ message: 'Signed in' });
    vi.mocked(api.signOut).mockResolvedValue({ message: 'Signed out' });
    render(<AuthProvider enabled><WorkspaceApp /></AuthProvider>);
    await waitFor(() => expect(api.getAuthProfile).toHaveBeenCalled());
    const verifiedProfile={...profile,user:{...profile.user,email_verified:true}};
    vi.mocked(api.getAuthProfile).mockResolvedValue(verifiedProfile);
    vi.mocked(api.workspaceGet).mockResolvedValue({workspace_name:'Test Firm',clients:0,accountants:null,runs:0,completed:0,daily:[],recent:[]});
    fireEvent.change(screen.getByLabelText('Email address'), { target: { value: profile.user.email } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'A long sample passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeInTheDocument();
    expect(supabase.signInWithSupabasePassword).toHaveBeenCalledWith(profile.user.email,'A long sample passphrase');
    expect(api.supabaseSignIn).toHaveBeenCalledWith('password-access-token');
    const navigation = within(screen.getByRole('navigation', { name: 'Workspace navigation' }));
    expect(navigation.queryByRole('link', { name: 'Sign in' })).not.toBeInTheDocument();
    expect(navigation.queryByRole('link', { name: 'Create an account' })).not.toBeInTheDocument();
    expect(navigation.getByRole('link', { name: 'Clients' })).toHaveAttribute('href', '#/clients');
    expect(navigation.queryByRole('link', { name: 'Team' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Recent runs' })).toBeInTheDocument();
    expect(screen.queryByText('A little more clarity.')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Welcome back.' })).toBeInTheDocument());
    expect(api.clearRequestScope).toHaveBeenCalled();
    expect(screen.queryByRole('heading', { name: 'Test Firm' })).not.toBeInTheDocument();
  });

  it('uses Supabase credentials for a normal contact email',async()=>{
    window.history.replaceState(null,'','/#/sign-in');
    vi.mocked(supabase.signInWithSupabasePassword).mockResolvedValue({access_token:'contact-token'} as never);
    vi.mocked(api.supabaseSignIn).mockImplementation(async()=>{vi.mocked(api.getAuthProfile).mockResolvedValue(profile);return {message:'Signed in'};});
    render(<AuthProvider enabled><WorkspaceApp/></AuthProvider>);
    const email=await screen.findByLabelText('Email address');
    fireEvent.change(email,{target:{value:'COLLEAGUE@EXAMPLE.COM'}});
    fireEvent.change(screen.getByLabelText('Password'),{target:{value:'A suitable passphrase 54!'}});
    fireEvent.click(screen.getByRole('button',{name:'Sign in'}));
    expect(await screen.findByRole('heading',{name:'Dashboard'})).toBeInTheDocument();
    expect(supabase.signInWithSupabasePassword).toHaveBeenCalledWith('colleague@example.com','A suitable passphrase 54!');
    expect(api.supabaseSignIn).toHaveBeenCalledWith('contact-token');
  });

  it('routes generated firm login IDs directly to ALIGNED authentication',async()=>{
    window.history.replaceState(null,'','/#/sign-in');
    vi.mocked(api.signIn).mockImplementation(async()=>{vi.mocked(api.getAuthProfile).mockResolvedValue(profile);return {message:'Signed in'};});
    render(<AuthProvider enabled><WorkspaceApp/></AuthProvider>);
    const loginId='yuvraj@avraj-advisory-9a498b4754f3.aligned.com';
    fireEvent.change(await screen.findByLabelText('Email address'),{target:{value:loginId}});
    fireEvent.change(screen.getByLabelText('Password'),{target:{value:'A suitable passphrase 54!'}});
    fireEvent.click(screen.getByRole('button',{name:'Sign in'}));
    expect(await screen.findByRole('heading',{name:'Dashboard'})).toBeInTheDocument();
    expect(supabase.signInWithSupabasePassword).not.toHaveBeenCalled();
    expect(api.signIn).toHaveBeenCalledWith(loginId,'A suitable passphrase 54!');
  });

  it('shows backend errors instead of simulated success', async () => {
    window.history.replaceState(null, '', '/#/sign-in');
    vi.mocked(supabase.signInWithSupabasePassword).mockRejectedValue(new Error('offline'));
    render(<AuthProvider enabled><WorkspaceApp /></AuthProvider>);
    await screen.findByLabelText('Email address');
    fireEvent.change(screen.getByLabelText('Email address'), { target: { value: 'user@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'A long sample passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Account service unavailable');
  });

  it('uses Supabase to send password reset email',async()=>{
    window.history.replaceState(null,'','/#/forgot-password');
    vi.mocked(supabase.sendSupabasePasswordReset).mockResolvedValue();
    render(<AuthProvider enabled><WorkspaceApp/></AuthProvider>);
    const email=await screen.findByLabelText('Email address');
    fireEvent.change(email,{target:{value:'password@example.com'}});
    fireEvent.click(screen.getByRole('button',{name:'Send instructions'}));
    expect(await screen.findByRole('status')).toHaveTextContent('Supabase has sent reset instructions');
    expect(supabase.sendSupabasePasswordReset).toHaveBeenCalledWith('password@example.com');
    expect(api.forgotPassword).not.toHaveBeenCalled();
  });

  it('does not mount the anonymous upload tool during account bootstrap', async () => {
    window.history.replaceState(null, '', '/');
    vi.mocked(api.getAuthProfile).mockReturnValue(new Promise(() => {}));
    render(<AuthProvider enabled><WorkspaceApp /></AuthProvider>);
    expect(screen.getByRole('status')).toHaveTextContent('Loading your account');
    expect(screen.queryByRole('heading', { name: 'Upload source data' })).not.toBeInTheDocument();
  });

  it('removes action tokens, verifies explicitly, then redirects a signed-out tab to sign in', async () => {
    const token = 'a'.repeat(43);
    window.history.replaceState(null, '', `/#/verify-email?token=${token}`);
    vi.mocked(api.verifyEmail).mockResolvedValue({ message: 'Email verified.' });
    render(<AuthProvider enabled><WorkspaceApp /></AuthProvider>);
    expect(window.location.hash).toBe('#/verify-email');
    expect(api.verifyEmail).not.toHaveBeenCalled();
    await screen.findByRole('button', { name: 'Verify email' });
    fireEvent.click(screen.getByRole('button', { name: 'Verify email' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Email verified.');
    expect(api.verifyEmail).toHaveBeenCalledWith(token);
    expect(screen.getByText(/You may close this tab/)).toBeInTheDocument();
    await waitFor(()=>expect(window.location.hash).toBe('#/sign-in'),{timeout:3500});
  });

  it('resends verification to the signed-in account without asking for email', async () => {
    window.history.replaceState(null,'','/#/resend-verification');
    vi.mocked(api.getAuthProfile).mockResolvedValue(profile);
    vi.mocked(api.resendCurrentVerification).mockResolvedValue({message:'Instructions saved.',address:profile.user.email});
    render(<AuthProvider enabled><WorkspaceApp/></AuthProvider>);
    expect((await screen.findAllByText(new RegExp(profile.user.email))).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText('Email address')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'Send instructions'}));
    expect(await screen.findByRole('status')).toHaveTextContent('Instructions saved.');
    expect(api.resendCurrentVerification).toHaveBeenCalledWith();
  });

  it('redirects successful signed-in email verification to the dashboard', async()=>{
    const verified={...profile,user:{...profile.user,email_verified:true}};
    vi.mocked(api.getAuthProfile).mockResolvedValue(verified);
    vi.mocked(api.workspaceGet).mockResolvedValue({workspace_name:'Test Firm',clients:0,accountants:0,runs:0,completed:0,daily:[],recent:[]});
    vi.mocked(api.verifyEmail).mockResolvedValue({message:'Email verified.'});
    window.history.replaceState(null,'',`/#/verify-email?token=${'v'.repeat(43)}`);
    render(<AuthProvider enabled><WorkspaceApp/></AuthProvider>);
    await screen.findByText('Email verified');
    const verifyButton=screen.getByRole('button',{name:'Verify email'});
    fireEvent.submit(verifyButton.closest('form')!);
    await waitFor(()=>expect(api.verifyEmail).toHaveBeenCalled());
    expect(await screen.findByText(/Redirecting to your dashboard/)).toBeInTheDocument();
    await waitFor(()=>expect(window.location.hash).toBe('#/dashboard'),{timeout:3500});
  });

  it.each(['3D' + 'a'.repeat(32) + '=', 'a'.repeat(35), ''])('rejects a broken copied mail link before sending it to the API: %s', async token => {
    window.history.replaceState(null, '', `/#/verify-email?token=${encodeURIComponent(token)}`);
    render(<AuthProvider enabled><WorkspaceApp /></AuthProvider>);
    await waitFor(() => expect(api.getAuthProfile).toHaveBeenCalled());
    expect(screen.getByRole('alert')).toHaveTextContent(token ? 'incomplete or contains email-formatting characters' : 'missing its token');
    expect(screen.getByRole('button', { name: 'Verify email' })).toBeDisabled();
    fireEvent.submit(screen.getByRole('button', { name: 'Verify email' }).closest('form')!);
    expect(api.verifyEmail).not.toHaveBeenCalled();
  });

  it.each(['sign-in', 'sign-up'])('redirects a signed-in user away from %s', async page => {
    window.history.replaceState(null, '', `/#/${page}`);
    vi.mocked(api.getAuthProfile).mockResolvedValue(profile);
    render(<AuthProvider enabled><WorkspaceApp /></AuthProvider>);
    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeInTheDocument();
    expect(window.location.hash).toBe('#/dashboard');
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
  });
});
