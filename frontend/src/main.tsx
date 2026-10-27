import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import WorkspaceApp from './workspace/WorkspaceApp.tsx'
import { AuthProvider } from './workspace/AuthContext.tsx'

// Keep one canonical loopback origin so Supabase OAuth redirects and the
// backend's site-bound development cookie always return to the same host.
if (import.meta.env.DEV && window.location.hostname === '127.0.0.1') {
  const localUrl = new URL(window.location.href)
  localUrl.hostname = 'localhost'
  window.location.replace(localUrl.toString())
} else {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <AuthProvider><WorkspaceApp /></AuthProvider>
    </StrictMode>,
  )
}
