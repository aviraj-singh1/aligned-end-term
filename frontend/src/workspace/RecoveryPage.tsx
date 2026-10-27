import { useEffect, useState, type FormEvent } from 'react';
import { activateAccount, authErrorMessage, resendCurrentVerification, verifyEmail } from '../services/api';
import { sendSupabasePasswordReset, updateSupabasePassword } from '../services/supabase';
import { useAuth } from './AuthContext';

export type RecoveryMode = 'verify-email' | 'resend-verification' | 'forgot-password' | 'reset-password' | 'activate-account';
const headings: Record<RecoveryMode, string> = { 'verify-email': 'Verify your email', 'resend-verification': 'Resend verification', 'forgot-password': 'Forgot your password?', 'reset-password': 'Choose a new password', 'activate-account':'Your account is ready' };

export function RecoveryPage({ mode, token }: { mode: RecoveryMode; token: string }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [complete, setComplete] = useState(false);
  const auth = useAuth();
  const needsPassword = mode === 'reset-password' || mode === 'activate-account';
  const needsToken = mode === 'verify-email' || mode === 'activate-account';
  // The API issues 32 random bytes as a 43-character unpadded base64url token.
  const invalidToken = needsToken && !/^[A-Za-z0-9_-]{43}$/.test(token);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (invalidToken || busy || complete || (mode === 'activate-account' && (auth.loading || auth.error || auth.profile))) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true); setError(''); setMessage('');
    try {
      const result = mode === 'verify-email' ? await verifyEmail(token)
        : mode === 'activate-account' ? await activateAccount(token, String(data.get('password')))
        : mode === 'reset-password' ? await (async()=>{await updateSupabasePassword(String(data.get('password')));return {message:'Password changed. You can now sign in with your new password.'};})()
          : mode === 'forgot-password' ? await (async()=>{await sendSupabasePasswordReset(String(data.get('email')));return {message:'If this email has password sign-in, Supabase has sent reset instructions.'};})()
            : await resendCurrentVerification();
      setMessage(result.message); setComplete(true); form.reset();
      await auth.refresh(mode === 'verify-email');
    } catch (failure) { setError(authErrorMessage(failure)); }
    finally { setBusy(false); }
  }
  useEffect(() => {
    if (mode !== 'verify-email' || !complete) return;
    const destination = auth.profile ? '/dashboard' : '/sign-in';
    const timer = window.setTimeout(() => { window.location.hash = destination; }, 2500);
    return () => window.clearTimeout(timer);
  }, [auth.profile, complete, mode]);
  if (mode === 'activate-account' && (auth.loading || auth.error || auth.profile)) {
    return <main className="workspace-settings recovery-page"><p className="auth-eyebrow">ALIGNED ACCOUNT</p><h1>Account activation</h1>
      {auth.loading ? <p role="status">Checking your current session…</p> : auth.error ? <><p role="alert">We could not check your session. Account activation is unavailable until this check succeeds.</p><button onClick={() => void auth.refresh()}>Retry account connection</button></> : <><p role="alert">You are already signed in as {auth.profile?.user.email}. This setup link is for the invited colleague. Do not set their password or activate their account.</p><p>The colleague should open their own invitation while signed out. Nothing has been changed by opening this page.</p><a href="#/dashboard">Back to dashboard</a></>}
    </main>;
  }
  return <main className="workspace-settings recovery-page"><p className="auth-eyebrow">ALIGNED ACCOUNT</p><h1>{headings[mode]}</h1>
    <p className="settings-intro">{mode === 'activate-account' ? 'Your firm has already created your account. Set your own password to activate it. No separate registration is needed.' : mode === 'verify-email' ? 'Confirm below to verify the email address linked to this request.' : mode === 'resend-verification' ? (auth.profile ? `Send verification to your registered email ${auth.profile.user.email}.` : 'Sign in so we can send verification to the registered email on your account.') : 'Account recovery for your local ALIGNED workspace.'}</p>
    {mode === 'resend-verification' && !auth.profile ? <p className="auth-notice" role="alert">We cannot identify an email address while you are signed out. <a href="#/sign-in">Sign in</a>; we will not ask you to type it again.</p> :
    <form onSubmit={submit} aria-busy={busy}>
      {needsToken && !token ? <div role="alert" className="auth-notice">This link is missing its token. Open the original email or request a new link.</div> : null}
      {invalidToken && token ? <div role="alert" className="auth-notice">This link is incomplete or contains email-formatting characters. Open the matching .txt message in scratch/auth-mail and copy its complete link, or request new instructions.</div> : null}
      <fieldset disabled={busy || complete || invalidToken} className="auth-fields">
        {mode === 'forgot-password' && <div className="auth-field"><label htmlFor="recovery-email">Email address</label><input id="recovery-email" type="email" name="email" required autoComplete="email" /></div>}
        {needsPassword && <div className="auth-field"><label htmlFor="new-password">New password</label><input id="new-password" name="password" type="password" required minLength={15} maxLength={128} autoComplete="new-password" /><p className="field-hint">At least 15 characters.</p></div>}
        <button className="auth-submit" type="submit">{busy ? 'Please wait…' : mode === 'verify-email' ? 'Verify email' : mode === 'activate-account' ? 'Activate account' : mode === 'reset-password' ? 'Save new password' : 'Send instructions'}</button>
      </fieldset>
      {message && <p className="auth-notice" role="status">{message}</p>}
      {error && <p className="auth-notice" role="alert">{error}</p>}
    </form>}
    {mode === 'verify-email' && complete && <p>Redirecting to {auth.profile ? 'your dashboard' : 'sign in'} in a moment. You may close this tab.</p>}
    <p className="auth-switch"><a href="#/sign-in">Back to sign in</a><a href="#/resend-verification">Request a verification link</a></p>
    <p className="auth-preview-note">{mode==='forgot-password'?'Password reset messages are delivered by Supabase to accounts with password sign-in.':'Local email-verification and managed-account invitation links are saved in scratch/auth-mail.'}</p>
  </main>;
}
