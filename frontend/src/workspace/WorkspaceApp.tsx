import { Activity, useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowUpRight, Building2, ChevronRight, LayoutDashboard, FileCheck2, LogIn, Menu, Settings2, UserPlus, Users, X } from 'lucide-react';
import App from '../App';
import { AuthPage } from './AuthPage';
import './workspace.css';
import { useAuth } from './AuthContext';
import { RecoveryPage, type RecoveryMode } from './RecoveryPage';
import { ManagementPage } from './ManagementPage';
import { ReconciliationWorkspace } from './ReconciliationWorkspace';
import { authErrorMessage } from '../services/api';
import { LandingPage } from './LandingPage';
import { DashboardPage } from './DashboardPage';
import { DataSettingsPage } from './DataSettingsPage';
import { ActivityLogsPage } from './ActivityLogsPage';
import './product.css';

type Page = 'home' | 'dashboard' | 'logs' | 'reconcile' | 'clients' | 'sign-in' | 'sign-up' | 'settings' | Exclude<RecoveryMode, 'activate-account'>;
const titles: Record<Page, string> = { home: 'Welcome', dashboard: 'Dashboard', logs: 'Activity logs', reconcile: 'Reconciliation', clients: 'Clients', 'sign-in': 'Sign in', 'sign-up': 'Create an account', settings: 'Settings', 'verify-email': 'Verify email', 'resend-verification': 'Resend verification', 'forgot-password': 'Forgot password', 'reset-password': 'Reset password' };
function currentPage(): Page {
  const path = window.location.hash.replace(/^#\/?/, '').split('?')[0];
  if (new URLSearchParams(window.location.search).get('authAction') === 'reset-password') return 'reset-password';
  if (['demo', 'gstr-merge', 'team', 'accept-invite', 'ownership-transfer', 'activate-account'].includes(path)) return 'dashboard';
  return Object.hasOwn(titles, path) ? path as Page : 'home';
}

function actionToken() { return new URLSearchParams(window.location.hash.split('?')[1] || '').get('token') || ''; }
function stripActionToken(page: Page) {
  if (actionToken()) window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#/${page}`);
}

export default function WorkspaceApp() {
  const [page, setPage] = useState(currentPage);
  const [menuOpen, setMenuOpen] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const [token, setToken] = useState(actionToken);
  const [logoutError, setLogoutError] = useState('');
  const [loggingOut, setLoggingOut] = useState(false);
  const auth = useAuth();

  useEffect(() => {
    function navigate() {
      const next = currentPage();
      setToken(actionToken());
      stripActionToken(next);
      setPage(next);
      setMenuOpen(false);
      contentRef.current?.focus({ preventScroll: true });
    }
    window.addEventListener('hashchange', navigate);
    stripActionToken(currentPage());
    return () => window.removeEventListener('hashchange', navigate);
  }, []);

  useEffect(() => { document.title = `${titles[page]} · ALIGNED`; }, [page]);

  useEffect(() => {
    if (auth.enabled && !auth.loading && auth.profile && ['home','sign-in','sign-up'].includes(page)) window.location.hash = '/dashboard';
  }, [auth.enabled, auth.loading, auth.profile, page]);

  async function logout() {
    setLoggingOut(true); setLogoutError('');
    try { await auth.logout(); }
    catch (error) { setLogoutError(authErrorMessage(error)); }
    finally { setLoggingOut(false); }
  }

  const navLink = (destination: Page, label: string, icon: ReactNode) => (
    <a href={`#/${destination}`} aria-current={page === destination ? 'page' : undefined} onClick={() => setMenuOpen(false)}>
      {icon}<span>{label}</span>{page === destination && <ChevronRight size={15} className="nav-chevron" aria-hidden="true" />}
    </a>
  );

  const recoveryPage = ['verify-email', 'resend-verification', 'forgot-password', 'reset-password'].includes(page);
  if (!auth.profile) return <div className="public-site"><header className="public-header"><a href="#/home" aria-label="ALIGNED home"><Brand /></a><nav aria-label="Public navigation"><a href="#/sign-in">Sign in</a><a className="primary-link" href="#/sign-up">Get started <ArrowUpRight size={15}/></a></nav></header>
    {auth.loading && !recoveryPage ? <main className="workspace-settings" role="status">Loading your account…</main> : auth.error ? <main className="workspace-settings"><p role="alert">{auth.error}</p><button onClick={()=>void auth.refresh()}>Retry account connection</button></main>
      : page === 'home' ? <LandingPage/> : recoveryPage ? <RecoveryPage key={`${page}:${token}`} mode={page as RecoveryMode} token={token}/> : !auth.enabled && page==='reconcile' ? <App/> : <AuthPage key={page} mode={page==='sign-up'?'sign-up':'sign-in'}/>}
  </div>;

  return (
    <div className="aligned-workspace" key={auth.profile.user.id}>
      <a className="workspace-skip" href="#workspace-content" onClick={event => { event.preventDefault(); contentRef.current?.focus(); }}>Skip to content</a>
      <div className="mobile-workspace-bar">
        <a href="#/reconcile" aria-label="ALIGNED reconciliation"><Brand /></a>
        <button type="button" aria-expanded={menuOpen} aria-controls="workspace-sidebar" onClick={() => setMenuOpen(!menuOpen)} aria-label={menuOpen ? 'Close navigation' : 'Open navigation'}>{menuOpen ? <X /> : <Menu />}</button>
      </div>
      <aside id="workspace-sidebar" className={`workspace-sidebar ${menuOpen ? 'is-open' : ''}`} onKeyDown={event => { if (event.key === 'Escape') setMenuOpen(false); }}>
        <a className="sidebar-brand" href="#/reconcile" aria-label="ALIGNED reconciliation"><Brand /></a>
        <div className="workspace-identity"><span className="workspace-avatar"><Building2 size={20} aria-hidden="true" /></span><div><strong>{auth.profile?.user.name || (auth.enabled ? 'ALIGNED account' : 'Pilot workspace')}</strong><span>{auth.profile ? (auth.profile.user.email_verified ? 'Email verified' : 'Email not yet verified') : 'No account connected'}</span></div></div>
        <nav aria-label="Workspace navigation">
          <p className="nav-caption">OVERVIEW</p>
          {navLink('dashboard', 'Dashboard', <LayoutDashboard size={19} aria-hidden="true" />)}
          <p className="nav-caption">WORK</p>
          {navLink('reconcile', 'Reconciliation', <FileCheck2 size={19} aria-hidden="true" />)}
          <p className="nav-caption">MANAGEMENT</p>
          {auth.enabled && auth.profile ? navLink('clients', 'Clients', <Users size={19} aria-hidden="true" />) : <div className="nav-unavailable"><Users size={19} aria-hidden="true" /><span>Clients</span><small>Sign in</small></div>}
          <p className="nav-caption account-caption">ACCOUNT</p>
          {!auth.profile && navLink('sign-in', 'Sign in', <LogIn size={19} aria-hidden="true" />)}
          {!auth.profile && navLink('sign-up', 'Create an account', <UserPlus size={19} aria-hidden="true" />)}
          {navLink('settings', 'Settings', <Settings2 size={19} aria-hidden="true" />)}
          {logoutError && <p className="sidebar-error" role="alert">{logoutError}</p>}
        </nav>
        <div className="sidebar-bottom">
          <span className="sidebar-account-email">{auth.profile.user.email}</span><button className="sidebar-signout" type="button" disabled={loggingOut} onClick={logout}>{loggingOut ? 'Signing out…' : 'Sign out'}</button>
        </div>
      </aside>
      <div className="workspace-content" id="workspace-content" ref={contentRef} tabIndex={-1}>
        {auth.notice && <p className="auth-notice" role="status">{auth.notice}</p>}
        {/* The legacy local pilot is never mounted beneath a partially protected auth session. */}
        {!auth.enabled && <Activity mode={page === 'reconcile' ? 'visible' : 'hidden'}><App /></Activity>}
        {auth.enabled && page === 'dashboard' && <DashboardPage/>}
        {auth.enabled && page === 'logs' && <ActivityLogsPage/>}
        {auth.enabled && (page === 'reconcile' || page === 'settings' || page === 'clients') && (
          auth.loading ? <main className="workspace-settings" role="status">Loading your account…</main>
            : auth.error ? <main className="workspace-settings"><p role="alert">{auth.error}</p><button className="auth-submit" onClick={() => void auth.refresh()}>Retry account connection</button></main>
              : !auth.profile ? <AuthPage mode="sign-in" /> : page === 'clients' ? <ManagementPage mode="clients" /> : page === 'reconcile' && auth.profile.reconciliation_available ? <ReconciliationWorkspace /> : <DataSettingsPage />
        )}
        {(page === 'sign-in' || page === 'sign-up') && !auth.profile ? <AuthPage key={page} mode={page} /> : null}
        {page === 'settings' && !auth.enabled && <SettingsPage />}
        {auth.enabled && recoveryPage && <RecoveryPage key={`${page}:${token}`} mode={page as RecoveryMode} token={token} />}
      </div>
    </div>
  );
}

function Brand() {
  return <span className="workspace-brand"><span className="brand-symbol" aria-hidden="true"><i /><i /><i /></span>aligned<span className="brand-period">.</span></span>;
}

function SettingsPage() {
  return <main className="workspace-settings">
    <p className="auth-eyebrow">YOUR WORKSPACE</p><h1>Settings</h1><p className="settings-intro">A clear view of your account and workspace.</p>
    <section className="settings-card"><div className="settings-icon"><UserPlus aria-hidden="true" /></div><div><h2>Account & access</h2><p>You’re using the pilot without a signed-in account. Sign-in and registration are design previews; authentication is not connected yet.</p><a className="auth-text-link" href="#/sign-in">View sign-in page <ArrowUpRight size={16} aria-hidden="true" /></a></div><span className="preview-badge">Not connected</span></section>
    <section className="settings-card"><div className="settings-icon sage"><Building2 aria-hidden="true" /></div><div><h2>Workspace type</h2><p>Planned: individuals manage their own clients. Firms can bring in accountants and assign access to specific clients. Team and client management are not available yet.</p><a className="auth-text-link" href="#/sign-up">Explore account types <ArrowUpRight size={16} aria-hidden="true" /></a></div></section>
    <section className="settings-card"><div className="settings-icon yellow"><FileCheck2 aria-hidden="true" /></div><div><h2>Reconciliation pilot</h2><p>Upload Accounts and GSTR-2B files to begin. The combined upload limit is 25 MB. This pilot does not provide a saved workspace history.</p><a className="auth-text-link" href="#/reconcile">Open reconciliation <ArrowUpRight size={16} aria-hidden="true" /></a></div></section>
  </main>;
}
