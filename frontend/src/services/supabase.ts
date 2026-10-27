import { createClient, type Session, type User } from '@supabase/supabase-js';

const url = import.meta.env.VITE_SUPABASE_URL || '';
const publishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY || '';
export const supabaseConfigured = Boolean(url && publishableKey);
export const supabase = supabaseConfigured
  ? createClient(url, publishableKey, { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true } })
  : null;

function client() {
  if (!supabase) throw new Error('Supabase authentication is not configured.');
  return supabase;
}


export async function getSupabaseSession(): Promise<Session | null> {
  const { data, error } = await client().auth.getSession();
  if (error) throw error;
  return data.session;
}

export async function signInWithSupabasePassword(email: string, password: string): Promise<Session> {
  const { data, error } = await client().auth.signInWithPassword({ email, password });
  if (error) throw error;
  if (!data.session) throw new Error('Supabase did not create a sign-in session.');
  return data.session;
}

export async function signUpWithSupabasePassword(email: string, password: string): Promise<{ user: User; session: Session | null }> {
  const { data, error } = await client().auth.signUp({ email, password });
  if (error) throw error;
  if (!data.user) throw new Error('Supabase did not create the account.');
  return { user: data.user, session: data.session };
}

export async function startSupabaseGoogle(intent: 'sign-in' | 'sign-up'): Promise<void> {
  sessionStorage.setItem('aligned_supabase_auth_intent', intent);
  const redirectTo = `${window.location.origin}${window.location.pathname}`;
  const { error } = await client().auth.signInWithOAuth({ provider: 'google', options: { redirectTo } });
  if (error) throw error;
}

export async function clearSupabaseSession(): Promise<void> {
  if (supabase) await supabase.auth.signOut();
}

export async function getSupabaseUser(): Promise<User | null> {
  const { data, error } = await client().auth.getUser();
  if (error) throw error;
  return data.user;
}

export function supabaseProviderIds(user: User | null): string[] {
  if (!user) return [];
  const metadataProviders = Array.isArray(user.app_metadata?.providers)
    ? user.app_metadata.providers.map(String)
    : [];
  const identityProviders = (user.identities || []).map(identity => identity.provider);
  return [...new Set([...metadataProviders, ...identityProviders])];
}

export async function linkSupabaseGoogle(): Promise<void> {
  sessionStorage.setItem('aligned_supabase_auth_intent', 'sign-in');
  const redirectTo = `${window.location.origin}${window.location.pathname}`;
  const { error } = await client().auth.linkIdentity({ provider: 'google', options: { redirectTo } });
  if (error) throw error;
}

export async function sendSupabasePasswordReset(email: string): Promise<void> {
  const redirectTo = `${window.location.origin}${window.location.pathname}?authAction=reset-password`;
  const { error } = await client().auth.resetPasswordForEmail(email, { redirectTo });
  if (error) throw error;
}

export async function updateSupabasePassword(password: string, currentPassword?: string): Promise<void> {
  const attributes: { password: string; current_password?: string } = { password };
  if (currentPassword) attributes.current_password = currentPassword;
  const { error } = await client().auth.updateUser(attributes);
  if (error) throw error;
}
