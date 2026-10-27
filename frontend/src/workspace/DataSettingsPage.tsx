import { useEffect, useState, type FormEvent } from 'react';
import { KeyRound, ScrollText, ShieldCheck, Trash2, UserRound } from 'lucide-react';
import { authErrorMessage, deleteAccount, updateProfile } from '../services/api';
import { clearSupabaseSession, getSupabaseSession, getSupabaseUser, linkSupabaseGoogle, signInWithSupabasePassword, supabaseProviderIds, updateSupabasePassword } from '../services/supabase';
import { useAuth } from './AuthContext';

export function DataSettingsPage() {
  const auth = useAuth();
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [providers, setProviders] = useState<string[]>([]);
  const [methodsLoading, setMethodsLoading] = useState(true);
  const [passwordMode, setPasswordMode] = useState<'create' | 'change' | null>(null);
  const [securityError, setSecurityError] = useState('');
  const [securityNotice, setSecurityNotice] = useState('');
  const googleConnected = providers.includes('google') || Boolean(auth.profile?.user.identity_providers?.includes('google'));
  const passwordConnected = providers.includes('email');

  useEffect(() => {
    let current = true;
    void getSupabaseUser()
      .then(user => { if (current) setProviders(supabaseProviderIds(user)); })
      .catch(() => { if (current) setProviders([]); })
      .finally(() => { if (current) setMethodsLoading(false); });
    return () => { current = false; };
  }, [auth.profile?.user.id]);

  function message(failure: unknown) {
    const apiMessage = authErrorMessage(failure);
    return apiMessage !== 'Something went wrong. Please try again.' ? apiMessage : failure instanceof Error ? failure.message : apiMessage;
  }

  async function removeAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const fields = new FormData(form);
    if (String(fields.get('confirmation')) !== 'DELETE MY ACCOUNT') {
      setError('Type DELETE MY ACCOUNT exactly.');
      return;
    }
    if (!window.confirm('Permanently delete your main ALIGNED account? Your identity will be anonymized and all sessions will end.')) return;
    setBusy('account'); setError(''); setNotice('');
    try {
      const password = String(fields.get('password') || '');
      const session = passwordConnected && password
        ? await signInWithSupabasePassword(auth.profile!.user.email, password)
        : await getSupabaseSession();
      if (!session) throw new Error('Your Supabase session has expired. Sign in again before deleting the account.');
      await deleteAccount(password || undefined, session.access_token);
      await clearSupabaseSession();
      form.reset();
      window.dispatchEvent(new Event('aligned-session-expired'));
    } catch (failure) { setError(message(failure)); }
    finally { setBusy(''); }
  }

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setBusy('profile'); setError(''); setNotice('');
    try {
      const result = await updateProfile({ name: String(fields.get('name')), email: auth.profile!.user.email });
      setNotice(result.message);
      await auth.refresh();
    } catch (failure) { setError(message(failure)); }
    finally { setBusy(''); }
  }

  async function savePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!passwordMode) return;
    const fields = new FormData(event.currentTarget);
    const next = String(fields.get('new-password'));
    const confirm = String(fields.get('confirm-password'));
    const current = String(fields.get('current-password') || '');
    setSecurityError(''); setSecurityNotice('');
    if (next !== confirm) { setSecurityError('New password and confirmation do not match.'); return; }
    if (next.length < 15) { setSecurityError('Use at least 15 characters for your password.'); return; }
    setBusy('password');
    try {
      await updateSupabasePassword(next, passwordMode === 'change' ? current : undefined);
      setProviders(existing => [...new Set([...existing, 'email'])]);
      setPasswordMode(null);
      setSecurityNotice(passwordMode === 'create' ? 'Password created. You can now sign in with email and password.' : 'Password changed successfully.');
    } catch (failure) { setSecurityError(message(failure)); }
    finally { setBusy(''); }
  }

  async function linkGoogle() {
    setBusy('google'); setError('');
    try { await linkSupabaseGoogle(); }
    catch (failure) { setError(message(failure)); setBusy(''); }
  }

  return <main className="workspace-settings data-settings"><p className="auth-eyebrow">ACCOUNT &amp; SECURITY</p><div className="management-heading"><div><h1>Settings</h1><p className="settings-intro">Manage your profile and Supabase sign-in methods.</p></div></div>
    <section className="settings-card"><div className="settings-icon sage"><UserRound /></div><div className="settings-card-body"><h2>Profile</h2><p>Manage the profile stored with your ALIGNED account.</p><form className="profile-form" onSubmit={saveProfile}><label>Name<input name="name" defaultValue={auth.profile?.user.name} required /></label><label>Supabase sign-in email<input name="email" type="email" value={auth.profile?.user.email || ''} readOnly aria-readonly="true" /><small>Change the primary email through Supabase account recovery.</small></label><button className="settings-action" disabled={!!busy}>{busy === 'profile' ? 'Saving…' : 'Save profile'}</button></form></div></section>
    <section className="settings-card"><div className="settings-icon"><ShieldCheck /></div><div className="settings-card-body"><h2>Sign-in &amp; Security</h2><p>Supabase manages confirmed email, Google and password sign-in.</p><h3 className="settings-subheading">Sign-in methods</h3><div className="sign-in-methods"><div><span className="method-icon google-mark">G</span><strong>Google</strong><small>{googleConnected ? 'Connected' : 'Not connected'}</small>{!googleConnected ? <button className="settings-action" disabled={!!busy} onClick={() => void linkGoogle()}>Connect</button> : null}</div><div><span className="method-icon"><KeyRound /></span><strong>Password</strong><small>{methodsLoading ? 'Checking…' : passwordConnected ? 'Password configured' : 'No password created'}</small><button className="settings-action" disabled={!!busy || methodsLoading} onClick={() => { setSecurityError(''); setSecurityNotice(''); setPasswordMode(passwordConnected ? 'change' : 'create'); }}>{passwordConnected ? 'Change password' : 'Create password'}</button></div></div>{passwordMode ? <form className="security-form" onSubmit={savePassword} aria-label={passwordMode === 'create' ? 'Create password' : 'Change password'}><h3>{passwordMode === 'create' ? 'Create password' : 'Change password'}</h3>{passwordMode === 'change' ? <label>Current password<input name="current-password" type="password" required autoComplete="current-password" /></label> : null}<label>New password<input name="new-password" type="password" required minLength={15} maxLength={128} autoComplete="new-password" /></label><label>Confirm password<input name="confirm-password" type="password" required minLength={15} maxLength={128} autoComplete="new-password" /></label>{securityError ? <p className="auth-notice security-feedback" role="alert">{securityError}</p> : null}<div className="security-actions"><button type="button" onClick={() => setPasswordMode(null)}>Cancel</button><button className="settings-action" disabled={!!busy}>{busy === 'password' ? 'Saving…' : passwordMode === 'create' ? 'Create password' : 'Change password'}</button></div></form> : null}{securityNotice ? <p className="auth-notice security-feedback" role="status">{securityNotice}</p> : null}</div></section>
    <section className="settings-card"><div className="settings-icon"><ScrollText /></div><div><h2>Activity &amp; audit log</h2><p>Review workspace changes, employee activity and exports from the settings area.</p><a className="settings-action" href="#/logs">Open activity logs</a></div></section>
    <section className="settings-card danger-card"><div className="settings-icon yellow"><Trash2 /></div><div><h2>Delete account</h2><p>This anonymizes and disables your PostgreSQL identity, deletes the linked Supabase Auth user and revokes every session.</p><form className="danger-form" onSubmit={removeAccount}>{passwordConnected ? <label>Current password<input name="password" type="password" required autoComplete="current-password" /></label> : <p>A current Supabase session will confirm this request.</p>}<label>Type <strong>DELETE MY ACCOUNT</strong><input name="confirmation" required autoComplete="off" /></label><button className="danger-button" disabled={!!busy}><Trash2 size={16} />{busy === 'account' ? 'Deleting…' : 'Delete account'}</button></form></div></section>
    {error && <p className="auth-notice" role="alert">{error}</p>}{notice && <p className="auth-notice" role="status">{notice}</p>}
  </main>;
}
