# End-Term Deployment

No deployment is performed by this repository preparation.

## 1. Supabase

Create a new project dedicated to the submission. Configure email/password Auth and optionally Google OAuth. Add the final Vercel URL and its password-recovery URL to the allowed redirects. Keep the publishable key public and the secret/service-role key only in Render.

Run `alembic upgrade head` once from a trusted machine using `MIGRATION_DATABASE_URL`. Do not run Alembic automatically during normal web startup. The runtime database role must be represented by `DATABASE_ROLE` and use SSL as described by the existing database configuration.

## 2. Render

Create the service from `render.yaml` or configure it manually from the repository root.

```text
Build: pip install -r requirements.txt
Start: uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT
Health: /api/health
```

Set every `sync: false` value in the Render dashboard. `AUTH_FRONTEND_URL` and `CORS_ORIGINS` must equal the exact stable HTTPS Vercel origin. Generate an `AUTH_SECRET` with at least 48 random characters. Use `SESSION_STORE_BACKEND=postgres`, `OBJECT_STORAGE_BACKEND=local`, and `REQUIRE_S3_OBJECT_STORAGE=false`.

The service has no persistent disk and no worker. Cold starts are expected; `/api/health` is a cheap liveness check and does not query the database.

## 3. Vercel

Use `frontend` as the project root, `npm run build` as the build command, and `dist` as output. Configure the four variables in `frontend/.env.example`; set `VITE_API_BASE_URL` to the Render HTTPS origin. Every Vite-variable change requires a new build.

## 4. Verification

After deployment, test registration, email confirmation, login, logout, cookie persistence, expired-session behavior, client creation, upload, mapping, reconciliation, manual matching, report download, server restart recovery, and an incognito-browser session. Do not claim live readiness before this verification succeeds.
