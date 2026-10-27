import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { authErrorMessage, clearRequestScope, getAuthProfile, setExpectedUserId, signOut, type AuthProfile } from '../services/api';
import { clearSupabaseSession } from '../services/supabase';
import { getSupabaseSession } from '../services/supabase';
import { supabaseSignIn } from '../services/api';

interface AuthState {
  enabled: boolean;
  profile: AuthProfile | null;
  loading: boolean;
  error: string;
  notice: string;
  refresh: (background?: boolean) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({ enabled: false, profile: null, loading: false, error: '', notice: '', refresh: async () => {}, logout: async () => {} });
// The hook and provider intentionally share this small context module.
// eslint-disable-next-line react/only-export-components
export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children, enabled = import.meta.env.VITE_AUTH_ENABLED === 'true' }: { children: ReactNode; enabled?: boolean }) {
  const [profile, setProfile] = useState<AuthProfile | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const epoch = useRef(0);
  const currentProfile = useRef<AuthProfile | null>(null);

  const refresh = useCallback(async (background = false) => {
    if (!enabled) return;
    const request = ++epoch.current;
    if (!background) setLoading(true);
    setError('');
    try {
      const next = await getAuthProfile();
      if (request === epoch.current) {
        if (currentProfile.current && currentProfile.current.user.id !== next?.user.id) {
          setNotice('The signed-in account changed in another tab or window. Check the account shown before continuing. Sign in as the administrator to add clients.');
        }
        if (JSON.stringify(currentProfile.current) !== JSON.stringify(next)) clearRequestScope();
        currentProfile.current = next;
        setExpectedUserId(next?.user.id ?? null);
        setProfile(next);
      }
    } catch (failure) {
      if (request === epoch.current) { clearRequestScope(); currentProfile.current = null; setExpectedUserId(null); setProfile(null); setError(authErrorMessage(failure)); }
    } finally {
      if (request === epoch.current) setLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    void refresh().then(async () => {
      const intent = sessionStorage.getItem('aligned_supabase_auth_intent');
      if (!intent || currentProfile.current) return;
      try {
        const session = await getSupabaseSession();
        if (!session) return;
        if (intent === 'sign-up') {
          if (!window.location.hash.startsWith('#/sign-up')) window.location.hash = '/sign-up';
          return;
        }
        sessionStorage.removeItem('aligned_supabase_auth_intent');
        await supabaseSignIn(session.access_token);
        await refresh();
        window.location.hash = '/dashboard';
      } catch (failure) {
        sessionStorage.removeItem('aligned_supabase_auth_intent');
        setError(authErrorMessage(failure));
        window.location.hash = '/sign-in';
      }
    });
    function expire() { ++epoch.current; clearRequestScope(); currentProfile.current = null; setExpectedUserId(null); setProfile(null); setLoading(false); window.location.hash = '/sign-in'; }
    function synchronize() { void refresh(true); }
    function visible() { if (document.visibilityState === 'visible') synchronize(); }
    window.addEventListener('aligned-session-expired', expire);
    window.addEventListener('aligned-account-changed', synchronize);
    window.addEventListener('focus', synchronize);
    document.addEventListener('visibilitychange', visible);
    // This is a request generation counter, not a DOM ref; invalidate its latest value.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return () => {
      ++epoch.current;
      window.removeEventListener('aligned-session-expired', expire);
      window.removeEventListener('aligned-account-changed', synchronize);
      window.removeEventListener('focus', synchronize);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [refresh]);

  async function logout() {
    await signOut();
    try {
      await clearSupabaseSession();
    } catch {
      // The PostgreSQL session is already closed; an identity-provider cleanup failure
      // must not leave stale ALIGNED profile data visible in this tab.
    }
    ++epoch.current;
    clearRequestScope();
    currentProfile.current = null;
    setExpectedUserId(null);
    setNotice('');
    setProfile(null);
    setError('');
    setLoading(false);
    window.location.hash = '/sign-in';
  }

  return <AuthContext.Provider value={{ enabled, profile, loading, error, notice, refresh, logout }}>{children}</AuthContext.Provider>;
}
