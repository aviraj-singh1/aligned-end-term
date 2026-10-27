# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the Oxlint configuration

If you are developing a production application, we recommend enabling type-aware lint rules by installing `oxlint-tsgolint` and editing `.oxlintrc.json`:

```json
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  "plugins": ["react", "typescript", "oxc"],
  "options": {
    "typeAware": true
  },
  "rules": {
    "react/rules-of-hooks": "error",
    "react/only-export-components": ["warn", { "allowConstantExport": true }]
  }
}
```

See the [Oxlint rules documentation](https://oxc.rs/docs/guide/usage/linter/rules) for the full list of rules and categories.

## Workspace and authentication UI

Run `npm run dev` for local development. The root URL still opens the existing
reconciliation workflow. The sidebar also exposes these hash routes, which can
be loaded directly without server rewrite rules:

- `/#/sign-in`: password and Google sign-in with password reset.
- `/#/sign-up`: three-step identity, mobile verification and workspace/address flow.
- `/#/demo`: developer-only temporary reconciliation mode.
- `/#/settings`: profile/email, Google connection, audit access and data controls.
- `/#/reconcile`: the existing reconciliation workflow.

With `VITE_AUTH_ENABLED=true`, the forms connect to the local FastAPI auth API:
registration, login, verification, reset and logout. Set `VITE_API_BASE_URL` to the
same hostname used by the frontend (127.0.0.1 on both ports in the configured setup).
Passwords require at least 15 characters in connected mode. Cookie credentials
are not stored in browser storage. Recovery routes are `/#/verify-email`,
`/#/resend-verification`, `/#/forgot-password` and `/#/reset-password`.

Supabase Auth uses the `VITE_SUPABASE_*` variables listed in `.env.example`.
The FastAPI server verifies a Supabase access token once, then issues the
application session cookie. PostgreSQL remains the authorization and workspace
source of truth and can be hosted by Supabase, EC2, or another PostgreSQL host
through `DATABASE_URL`; changing that URL does not change the identity provider.
Configure Google, phone/SMS and allowed redirect URLs in Supabase Auth.

See [Local Auth Development](../docs/LOCAL_AUTH_DEVELOPMENT.md) for backend startup
and the private local email folder. The server blocks financial routes while
tenant/client ownership is unfinished; production activation is refused.
Without the flag, auth forms retain
their explicitly labeled, non-submitting design-preview behavior.

Only the legacy auth-disabled pilot preserves reconciliation state across sidebar
navigation. Auth mode does not mount that component or expose its financial data.
Logout clears identity and cancels the current API request scope. No durable
financial history is introduced. Tests live in `src/workspace/WorkspaceApp.test.tsx`; run `npm test`
and `npm run build` before shipping changes.

## Pilot launch transport checklist

- Combined selected files are limited to 3,500,000 bytes, leaving multipart headroom below the current Function transport ceiling.
- JSON downloads are limited to 3,500,000 bytes. An oversized JSON download does not invalidate reconciliation or disable Excel.
- Production tested with approximately 1,000 invoices per source; this is not a row hard-cap or a guarantee for every dataset.
- Manually confirm the hosting workspace plan before an external commercial/professional CA pilot. The audit observed a Hobby workspace but could not confirm the production projects' plan. Do not assume a plan upgrade removes the request/response payload limits.
- After approved deployment, verify the pilot upload text, all real fixtures, normal JSON/Excel downloads, CORS and session persistence. Exercise oversized payload rejection locally, not by sending above-platform-limit requests to production.
