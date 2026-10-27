# Database

PostgreSQL is the authoritative store. Alembic migrations under `backend/alembic/versions` remain the only supported schema installation path.

Important model groups are:

- `users`, `auth_identities`, `auth_sessions`, and security events for identity and application sessions.
- `workspaces`, `memberships`, `clients`, and `client_gstins` for tenant ownership and client selection.
- `reconciliation_runs` and `run_operations` for durable run history and idempotent operations.
- `reconciliation_session_state` for compressed normalized/reconciled state used after restarts.
- `audit_events` for append-only business activity.

Historical tables for invitations, ownership transfer, GSTR merge runs, and Tally exports remain for migration compatibility but are outside the active end-term workflow.

Use a new Supabase project and separate migration/runtime credentials. Never copy original production rows. PostgreSQL tests must use `TEST_DATABASE_URL` pointing to a disposable database. Startup validates the current Alembic revision but does not alter the schema.

Reconciliation state has a configured retention period and is intentionally not permanent archival storage. Original uploads and generated reports are not stored in PostgreSQL.
