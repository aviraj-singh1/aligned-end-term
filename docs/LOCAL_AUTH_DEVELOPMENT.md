# Local authentication milestone

Built on the existing FastAPI, SQLModel, PostgreSQL and React code.
**This is not a production authentication release.**

## Implemented

- Individual/Firm registration creates a user, Argon2id credential, workspace,
  initial ADMIN membership, login session and audit entry in one transaction.
  A successful new registration opens Dashboard immediately; client data remains
  blocked until the contact email is verified. Duplicate signup never signs in
  to an existing account or replaces its password.
- Login, PostgreSQL-backed opaque cookie sessions, `/api/me`, logout/logout-all.
- Email verification/resend and password reset with session revocation.
- CSRF/origin validation, local throttling, redacted validation errors, expiry
  and single-use action tokens, including concurrent consumption checks.
- Existing forms submit to these APIs when `VITE_AUTH_ENABLED=true`.

## Start locally

Ignored local configuration is stored in the repository-root `.env` and
`.env.development`. `.env` contains Vite's public build-time settings;
`.env.development` contains backend development settings and secrets. Never
commit either file. Provide a disposable test database URL through the shell
when running database tests.

Backend, from the repository root:

```powershell
.\.venv\Scripts\python.exe -m backend.dev
```

This explicitly loads `.env.development` without overriding shell variables,
binds to 127.0.0.1:8000 in one process. Restart after backend source changes.
Optional `--reload` watches `backend/app`; if the Windows watcher stalls, restart
manually. Stop older uvicorn servers first.
The previous bare uvicorn command does NOT automatically load this configuration.

Frontend, in a separate terminal:

```powershell
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open `http://127.0.0.1:5173/#/sign-up`. Do not mix localhost and 127.0.0.1 across
the two services. Restart the relevant server after changing environment values.

## Verification and reset messages

No internet email is sent. Messages are written privately to **scratch/auth-mail**.
Open the newest **`.txt`** file for your email address in a text editor. Copy its
complete `http://127.0.0.1:5173/#/...` link into the browser and confirm the action.
The `.txt` is a readable companion to `.eml`. Old raw `.eml` files can contain
quoted-printable encoding (`=3D` and soft line breaks), which corrupts a link
copied without MIME decoding. Never shorten the link or copy only its first line
from encoded mail. Request new instructions if you have no readable copy.
The API never returns these tokens; the backend still requires the original
valid, unexpired, unused token. Clearing the browser URL after opening a link is
intentional; refreshing that cleared URL requires reopening the message link.

The files contain live single-use credentials: do not share, commit or expose
the folder through HTTP. Remove expired local messages when no longer needed;
there is no automatic retention worker yet. An email write failure is logged
without secrets; fix the folder configuration and use the resend flow.

## Databases and migrations

This setup created separate `aligned_auth_dev` and `aligned_auth_test` databases.
Each has a dedicated owner. Runtime access uses another non-superuser role with
explicit auth-write/workspace-create grants, no DDL, no create-role or create-DB
rights. Audit tables have append-only triggers. No existing user DB was migrated
or deleted. Synthetic browser-test accounts may remain in the development DB;
do not remove audit records to hide test history.

Only the new development database has baseline through `20260901_05` applied. For a
future reviewed local migration, explicitly load the environment:

```powershell
.\.venv\Scripts\python.exe -c "from dotenv import load_dotenv; from alembic import command; from alembic.config import Config; load_dotenv('.env.development'); command.upgrade(Config('alembic.ini'), 'head')"
```

The app never migrates automatically. Review runtime grants with future table
additions; migration success does not grant the app access to a new table.
Never point schema tests at the development or production database.

## Deliberate security boundary

With `AUTH_ENABLED=true`, financial APIs return 503 unless the separate local
`AUTH_RECONCILIATION_ENABLED=true` gate is enabled. The gate defaults to false;
the current ignored development configuration enables it after migration and
runtime grants. Enabled routes enforce verified identity and current client/run
access before parsing uploads or touching temporary financial state. The
authenticated shell never mounts the anonymous pilot. Old anonymous sessions
cannot be adopted by presenting their IDs.

Auth-disabled mode preserves the earlier anonymous pilot; it is not a secure
fallback or deployment recommendation. DB/auth errors never switch modes
automatically. Partial auth activation is refused for production/VERCEL.
No matching rules, engine logic or downloaded JSON/Excel schema changed.

Clients and Team now enforce workspace membership, verified email and client
assignments. Owned runs and operation recovery are implemented locally. Remaining
work: real Redis recovery verification, production email with durable retry/retention, distributed
rate limiting, broader password screening, HTTPS cookies/domain validation,
operational monitoring, backups and rollout rehearsal. Local HTTP cookies are
intentionally non-Secure; local throttling is process-local. Do not deploy this
milestone as a production auth system.

## Verification

The test DB must be empty and disposable; its fixture upgrades and downgrades it:

```powershell
$env:TEST_DATABASE_URL='postgresql+psycopg://aligned_test:CHANGE_ME@localhost:5432/aligned_test'
.\.venv\Scripts\python.exe -m pytest backend/tests --require-postgres -q
cd frontend
npm test
npm run build
```

Targeted suites are `backend/tests/test_auth_api.py`, database schema/config tests,
`backend/tests/test_workspace_api.py`, and `frontend/src/workspace/` tests. Browser checks cover real
registration, verification, login, reload persistence, logout, password reset
and a subsequent login with the new password, including mobile layout.

## Clients and Team workflow (local milestone)

Signed-in navigation hides Sign in/Create account and enables Clients and Team.
Direct links to sign-in/sign-up return authenticated users to Settings. Both
management pages require a verified email before loading business data.

- **Clients:** select a workspace; create/search/page through clients; edit their
  name, legal name and PAN; add GSTINs; activate/deactivate registrations;
  archive/restore clients. GSTIN format and PAN consistency are checked. GSTINs
  stay unique within a workspace, including inactive registrations. Records are
  archived rather than deleted. Restoring a client restores its existing assignments.
- **Individual:** sole ADMIN manages all clients. Team offers an explicit,
  confirmed, one-way conversion to FIRM that keeps the workspace ID and records.
- **Firm:** ADMIN manages invitations, member roles/status and assignments.
  ACCOUNTANT can read only active assigned clients and cannot edit clients or
  manage the team. Suspension/removal and role changes clear assignments;
  reactivation does not silently grant them back. The last active ADMIN cannot
  be demoted/removed/suspended. Workspace locks serialize competing changes.
- **Invite:** send an invitation from Team. Open its local `.txt` message and
  share the invitation only with the intended colleague. This build has no
  internet email delivery. Existing users sign in on the invitation page. New
  users choose Create invited account (no unrelated workspace is created), open
  their verification `.txt`, verify, then reopen the original invitation, sign
  in, and explicitly accept it. Email must match and be verified. Invitations
  expire after seven days, are single-use, and can be revoked from Team.
- **Switching:** selectors use memberships returned by `/api/me`. Switching
  aborts queued requests and unmounts old data/forms; IDs are never authorization.
  Lists use 25-item pages; client details load separately. Server errors render
  retry controls, not misleading empty lists.

The tables already existed; no schema migration was needed for this slice.
The local runtime role received only these additional grants, applied by the
owner to `aligned_auth_dev` (never grant DDL or owner credentials to runtime):

```sql
GRANT SELECT, INSERT, UPDATE ON clients, client_gstins, workspace_invites,
  client_assignments TO aligned_auth_app;
GRANT UPDATE ON workspaces, workspace_memberships TO aligned_auth_app;
GRANT DELETE ON client_assignments TO aligned_auth_app;
```

All writes enforce CSRF/Origin, reload current membership, scope resource IDs,
and commit an audit event in the same PostgreSQL transaction. Cross-workspace
resources return 404; permission failures return 403. Invitation raw tokens
never appear in listing APIs. Registration and invitation acceptance have no
automatic access shortcut. Invitation delivery occurs after commit; on local
mail failure, the API reports the saved invitation and asks the admin to revoke
and retry. Durable email dispatch remains production work.

Financial routes remain fail-closed unless the owned local workflow is enabled.
This is not a production authorization release: real Redis verification,
production email, distributed throttling, HTTPS and operational gates are still required. Subsidiary hierarchy
and inherited permissions remain out of scope until their meaning is defined.

## Client-owned reconciliation (local milestone)

Refresh the browser after starting the updated backend. Open Reconciliation,
choose a workspace and active client, optionally select an active GST registration,
then choose New reconciliation. Existing upload, mapping, reconciliation, result
review, manual matching and Excel/JSON downloads run within that client context.
Run history can reopen a mapping review or completed result while its temporary
state remains available. Deleting results removes the published financial snapshot
and blocks API access, retaining a receipt tombstone, SQL run and audit history.
This is logical deletion: local upload/export files can remain in private temporary
directories. It is not secure erasure; automated file retention/cleanup is still
a production requirement.

Migration `20260831_03` adds run storage versions/deletion timestamps and the
`run_operations` journal. In the local development database the owner also applied:

```sql
GRANT SELECT, INSERT, UPDATE ON reconciliation_runs, run_operations TO aligned_auth_app;
```

Inspection requires `X-Workspace-ID`, `X-Client-ID` and optional
`X-Client-GSTIN-ID`. Mutations require CSRF, a UUID `Idempotency-Key` and, after
inspection, `X-Run-Version`. Successful mutations return the next version in
`X-Run-Version`. The UI supplies these headers. Financial result representations,
downloaded JSON and Excel formats, matching rules and FY behavior are unchanged.

Each operation commits a SQL intent, computes privately using the existing engine,
publishes a single temporary-store snapshot with a receipt, then finalizes SQL
versions and audit together. A retry after an uncertain failure keeps the same key;
a matching receipt finalizes SQL without consuming a manual candidate again.
History's Recover action can finalize a stored receipt or cancel an unpublished
operation with a storage fence. Missing evidence produces UNRESOLVED, never an
automatic replay. SQL and temporary storage are not one distributed transaction.

Availability is bounded to one hour after a successful change. Reads do not extend
the SQL deadline. Local memory storage also loses results on backend restart;
PostgreSQL preserves ownership/history, not uploaded files or financial snapshots.
Expired history is not a promise that reports can be regenerated. Durable object
storage and automated retention/cleanup remain separate production work.

Verification includes `backend/tests/test_owned_reconciliation.py`, using real
PostgreSQL and a command-level Redis test double for CAS/failure injection, plus
frontend workspace/API tests and a synthetic browser upload-to-delete flow.
The real Upstash test is gated on shell-supplied `TEST_UPSTASH_REDIS_REST_URL`
and `TEST_UPSTASH_REDIS_REST_TOKEN`. Use a dedicated, disposable test database;
the test writes and deletes only its unique key prefix.
These credentials are not configured locally, so this gate is skipped and
production activation remains blocked. Do not substitute production Redis credentials.

## Product shell and managed firm accounts (2026-09-01)

### Browser account synchronization

Tabs/windows in one browser profile share the session cookie (including incognito
windows within the same incognito session). They cannot maintain different accounts
independently. Use separate browser profiles, or normal versus incognito, for that.
The UI refreshes its profile on focus/visibility and permission/account-change errors.
Unchanged profiles retain unsaved client forms; changed identities clear old workspace
components and pending request state. Requests are never automatically retried as the
new account.

Protected frontend requests include `X-Expected-User-ID`, derived from the displayed
profile. The backend compares this hint with the authenticated cookie user before
workspace/financial access. A mismatch returns HTTP 409 with `ACCOUNT_CHANGED` and
does not perform the operation. This optional compatibility header only restricts
requests; it never selects a user or grants permissions. PostgreSQL membership
checks remain authoritative. Profile discovery (`/api/me`) is deliberately unbound
so the page can learn the actual signed-in account. No roles are changed by this fix.

### Local authentication throttling

Limits use a rolling five-minute window and expire individual attempts. Each
action has its own counter: CSRF bootstrap and verification/team activity do not
consume the sign-in budget. Sign-in allows 15 attempts per submitted login
identifier and 60 requests per peer IP in that window, including successful
sign-ins. CSRF bootstrap has a separate 300-request IP budget. Other throttled
actions retain 60 requests per IP and, where applicable, 15 per identifier.

Normal and private browser windows on the same machine still share the IP limit;
this is flood protection, not a simultaneous-session limit. HTTP 429 responses
include the remaining `Retry-After` seconds, exposed through CORS and displayed
by the frontend. Rejected requests do not extend the bucket's expiry. The limiter
is process-local and resets on backend restart; production still requires a
distributed limiter. This change does not enable production authentication.

The public home/landing, sign-in and sign-up pages do not render the workspace
sidebar. Signed-in users start on Dashboard, with separate Overview, Work,
Management and Account navigation groups. The old sidebar promotional card is
removed. Reconciliation uses a compact client context and the shared product palette.

Dashboard reads PostgreSQL aggregates, never invented sample statistics: active
accessible clients, active activated accountants (ADMIN only), run totals,
completed run totals, seven days of run creation in UTC, and eight recent runs.
Counts include retained run history after temporary results expire or are deleted.
Accountants see only currently assigned active clients; unverified accounts see
onboarding instead of business analytics. These are workflow counts, not financial
matching rates or compliance metrics.

Migration 20260901_04 adds a firm login namespace and membership login IDs/activation
state. Existing firm memberships are backfilled. Contact emails are unchanged.
The firm creator receives admin@firm-name-<suffix>.aligned.com; new colleagues get
username@firm-name-<suffix>.aligned.com. The stable suffix prevents two firms with
the same display name sharing IDs. These are login identifiers, NOT hosted email
inboxes or provisioned DNS subdomains. Sign-in accepts the ID or real contact email.
No internet email delivery, DNS configuration or external service was provisioned.

Team > Create a colleague account creates the user, unusable random credential,
membership, activation token and audit in one transaction. The colleague opens
the local activation message and chooses their own password. Activation verifies
the contact email, is single-use, and fails for suspended/removed memberships.
Admins can resend activation or manage membership access. Pending administrators
do not satisfy last-active-administrator protection. Existing account holders use
the secondary consent-based invitation flow; their passwords are never replaced.
Only ADMIN and ACCOUNTANT permission roles exist; no undefined agent privileges
were introduced. Firm administrators assign clients through existing controls.

Account activation and invited-account registration reject requests with an active
signed-in session (HTTP 409), including the administrator who created the account.
The UI waits for the session check and hides password setup when signed in. Rejected
requests leave the activation token, password, membership and current login intact.
Existing-account invitation acceptance still requires the matching verified user.
An expired/invalid session is treated as signed out by the existing session validator.

This prevents accidental setup in the wrong browser account, but activation remains
a bearer-token flow: anyone holding the link in a signed-out browser can use it.
The shared local scratch mailbox is development-only, not proof of recipient-only
delivery. Production must deliver the link privately to the colleague. Previously
activated accounts are not rolled back by this guard; their intended owners should
use password reset to choose a private password (which revokes existing sessions).

Creating a client immediately saves its business record. A separate client portal
login is not enabled: its access scope needs confirmation before exposing financial
data to clients. Clients are not silently turned into firm team members.

The local database uses the existing table grants for the additive columns; no
new runtime DDL or administrator privileges are needed. Production activation,
real Redis recovery verification, secure file retention, delivery infrastructure
and operational readiness gates remain outstanding. No deployment was performed.

## Protected workspace ownership (2026-09-01)

Migration `20260901_05` promotes each workspace's recorded original creator to
`OWNER`, restoring an active creator membership if another administrator previously
demoted or removed it. The migration refuses to guess when a creator membership is
missing, pending activation, or its user is disabled. It writes `OWNER_BACKFILLED`
audit evidence with the prior role and status. A partial unique index permits only
one Owner per workspace, and a check constraint requires that Owner to remain active
and activated. Application member-management endpoints always reject changes to the
Owner, including self-demotion. Owner and Administrator retain the same business
management permissions; only the Owner can initiate/cancel ownership transfer.

Transfer accepts only an active, activated, email-verified Administrator. It is a
two-party, registered-email flow: the current Owner receives a 24-hour confirmation
link first; after confirmation, the recipient gets a separate consent link. Ownership
changes atomically only after recipient acceptance. The old Owner becomes an active
Administrator, the recipient becomes Owner, both sessions are revoked, and request,
owner-confirmation, cancellation/completion events are audited. Pending transfer
state is unique per workspace and can be cancelled by the Owner. Tokens are stored
as SHA-256 digests and are never returned through team APIs.

The configured mail transport remains the private local `scratch/auth-mail` sink;
it does not deliver to internet inboxes. Production ownership consent remains blocked
until recipient-private transactional email delivery is configured.

The local migration owner also applied the narrow runtime grant required by the
new service:

```sql
GRANT SELECT, INSERT, UPDATE ON ownership_transfers TO aligned_auth_app;
```

Signed-in users resend verification to the contact email already stored on their
account; the resend form does not ask them to retype it. Signed-out users are asked
to sign in because the server cannot safely infer an identity. Successful verification
shows confirmation for 2.5 seconds, then goes to Dashboard when the verifying browser
has a valid session or Sign in when it does not.
