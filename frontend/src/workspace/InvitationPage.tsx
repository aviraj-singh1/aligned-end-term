import { useState, type FormEvent } from 'react';
import { acceptInvitation, authErrorMessage, registerInvited, signIn } from '../services/api';
import { useAuth } from './AuthContext';

export function InvitationPage({ token }: { token: string }) {
  const auth = useAuth();
  const invalid = !/^[A-Za-z0-9_-]{43}$/.test(token);
  const [mode, setMode] = useState<'accept' | 'sign-in' | 'register'>('accept');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  async function accept() {
    if (busy || invalid) return; setBusy(true); setError('');
    try { const result = await acceptInvitation(token); setNotice(result.message); await auth.refresh(); }
    catch (failure) { setError(authErrorMessage(failure)); } finally { setBusy(false); }
  }
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); if (busy || invalid || auth.loading || auth.error || auth.profile) return;
    const form = e.currentTarget;
    const data = new FormData(form); setBusy(true); setError(''); setNotice('');
    try {
      if (mode === 'sign-in') {
        await signIn(String(data.get('email')), String(data.get('password'))); form.reset(); await auth.refresh(); setMode('accept');
      } else {
        const result = await registerInvited({ token, name: String(data.get('name')), email: String(data.get('email')), password: String(data.get('password')) }); setNotice(result.message); (form.elements.namedItem('password') as HTMLInputElement).value = '';
      }
    } catch (failure) { setError(authErrorMessage(failure)); } finally { setBusy(false); }
  }
  if (auth.loading) return <main className="workspace-settings" role="status">Checking your current session…</main>;
  if (auth.error) return <main className="workspace-settings"><p role="alert">We could not check your session. Invitation setup is unavailable.</p><button onClick={() => void auth.refresh()}>Retry account connection</button></main>;
  if (auth.profile && mode !== 'accept') return <main className="workspace-settings"><p role="alert">You are already signed in. Do not create an account for someone else using their invitation.</p><button onClick={() => setMode('accept')}>Back to invitation</button></main>;
  return <main className="workspace-settings recovery-page"><p className="auth-eyebrow">FIRM INVITATION</p><h1>Join a workspace</h1><p className="settings-intro">Invitations only grant access to their invited, verified email address.</p>
    {invalid && <p className="auth-notice" role="alert">This invitation link is incomplete or contains email-formatting characters. Copy its complete link from the matching .txt message.</p>}
    {notice && <p className="auth-notice" role="status">{notice}</p>}{error && <p className="auth-notice" role="alert">{error}</p>}
    {!invalid && mode === 'accept' && <section className="management-panel"><h2>{auth.profile ? `Continue as ${auth.profile.user.email}` : 'Sign in or create your invited account'}</h2><p>{auth.profile ? 'We will confirm that this is the verified email address named by the invitation.' : 'Existing users should sign in. New users can create an account from this invitation without creating an unrelated workspace.'}</p>{auth.profile ? <button className="management-primary" disabled={busy || !!notice} onClick={() => void accept()}>{busy ? 'Checking invitation…' : 'Accept invitation'}</button> : <div className="management-actions"><button className="management-primary" onClick={() => setMode('sign-in')}>Sign in to accept</button><button onClick={() => setMode('register')}>Create invited account</button></div>}{auth.profile && !auth.profile.user.email_verified && <p>Verify your email first, then reopen this invitation. <a href="#/resend-verification">Request verification instructions</a></p>}{notice && auth.profile && <p><a href="#/clients">Open clients</a></p>}</section>}
    {!invalid && mode !== 'accept' && <section className="management-panel"><button className="management-back" onClick={() => setMode('accept')}>Back</button><h2>{mode === 'sign-in' ? 'Sign in to accept' : 'Create your invited account'}</h2><form onSubmit={submit}><fieldset className="management-fields" disabled={busy}>{mode === 'register' && <label>Full name<input name="name" required maxLength={200} autoComplete="name" /></label>}<label>Email address<input name="email" type="email" required maxLength={254} autoComplete="username" /></label><label>Password<input name="password" type="password" required minLength={mode === 'register' ? 15 : undefined} maxLength={128} autoComplete={mode === 'register' ? 'new-password' : 'current-password'} /></label><button className="management-primary">{busy ? 'Please wait…' : mode === 'sign-in' ? 'Sign in' : 'Create account'}</button></fieldset></form></section>}
  </main>;
}
