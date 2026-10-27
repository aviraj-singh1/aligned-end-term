# End-Term Architecture

## System boundary

The browser runs a React/Vite application hosted by Vercel. It uses Supabase Auth for identity and exchanges a confirmed Supabase access token with FastAPI. FastAPI creates an opaque, HttpOnly application-session cookie and authorizes all business APIs against PostgreSQL workspace membership.

FastAPI is one Render web service. PostgreSQL is the authoritative store for users, workspaces, clients, run metadata, serialized reconciliation state, manual decisions, and audit events. Local files are temporary processing artifacts only.

## Reconciliation flow

Uploads are streamed in bounded chunks to a server-owned UUID session directory. The combined request is limited to 25 MB. User filenames are retained only as display metadata and never used as filesystem paths. Container signatures are inspected before parser selection.

The parser normalizes purchase-register and GSTR-2B records. Once canonical parser state is serialized into the configured session store, original temporary uploads are removed. Existing matching, discrepancy classification, duplicate handling, candidate evidence, and manual-match integrity rules remain unchanged.

Completed summaries and manual decisions live in PostgreSQL-backed reconciliation session state. Report downloads rebuild Excel or JSON output in a temporary directory, stream it, and delete the served file after the response. A backend restart therefore does not require the original uploads or a previous report file.

## Excluded execution paths

The web application does not register GSTR consolidation or Tally export routers. Its deployed workspace router exposes only dashboard, client/GSTIN management, audit logs, and reconciliation history/recovery; team, invitation, ownership-transfer, managed-account, assignment, workspace-reset, and bulk workspace-export endpoints remain compatibility-only. It does not start a worker. Frontend navigation does not expose GSTR merging, Tally history, team invitations, ownership transfer, phone verification, or developer demo mode. Compatibility models, migrations, and inactive modules remain so the established schema can migrate cleanly.

## Security boundary

Production CORS accepts explicit origins only. Credentialed requests use secure cookies with `SameSite=None`, and mutations require a CSRF token. Supabase secret keys are backend-only. Production requires PostgreSQL session storage. Financial input values are not logged.
