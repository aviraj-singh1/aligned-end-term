# PostgreSQL, authentication and authorization implementation plan

Date: 31 August 2026. Status: Phase 1 database foundation and the first local auth
slice are implemented on PostgreSQL 18 / Python 3.12. Registration, login/logout/me,
verification and recovery connect to the existing forms. Workspace/client
authorization, client/GSTIN management, firm conversion, invitations, member
controls, assignments and their UI now work locally. Owned reconciliation,
client run history and receipt-backed recovery are implemented behind a separate
local feature gate. Production activation remains blocked pending real Redis
verification and the remaining infrastructure/security requirements. See
[Local Auth Development](LOCAL_AUTH_DEVELOPMENT.md) for the implemented boundary.
No production database migration or deployment has been performed.

2026-09-01 product update: public landing/auth pages are separate from the private
dashboard, which uses scoped SQL workflow analytics. New registration creates a
login session immediately, without bypassing email verification. Migration
`20260901_04` adds firm login identifiers and pre-created colleague accounts with
recipient-controlled password activation. Existing identities still require an
invitation/consent flow. Client portal logins and any separate agent role await
explicit access requirements; creating a client currently saves its business
record only. See Local Auth Development for the current implemented contract.

Updated after the UI preview: section 10 is the concrete backend execution plan
using the existing code. Its proposed first-party cookie-session design replaces
the earlier managed-auth recommendation. Auth storage/lifecycle and UI connection
now work locally with private local mail and process-local request limits. The
later production requirements remain planned, not completed.

Phase 1 includes all ten SQLModel tables, named tenant constraints, bounded JSONB,
append-only audit triggers, explicit Alembic migrations, lazy database settings and
real-PostgreSQL tests. See [Database Development](DATABASE_DEVELOPMENT.md) for setup
and the dated verification record. Later phases below remain the implementation
plan, not descriptions of existing behavior.

Design input: the supplied `ALIGNED_AUTH_POSTGRES_SCHEMA_BLUEPRINT.md`. Its recommendations are inputs, not instructions to provision services or change the matching engine. This plan adapts them to the inspected React/Vite + FastAPI repository.

## 1. Decisions and boundaries

Adopt PostgreSQL for durable users, Individual/Firm workspaces, memberships, clients, assignments, run ownership and audit history. Use SQLModel for table mappings, SQLAlchemy for advanced constraints/transactions, Psycopg 3 for PostgreSQL connectivity and Alembic for reviewed, versioned migrations.

Updated product requirement: both individuals and firms can register and manage clients. A firm can also have accountants responsible for specific clients. Model this as one user identity system with two workspace types, `INDIVIDUAL` and `FIRM`, not two permanent user-account types. A person may operate an individual practice and belong to one or more firms with the same login.

The blueprint's proposed `organizations`, `organization_memberships`, `organization_invites` and `organization_id` are renamed in this plan to `workspaces`, `workspace_memberships`, `workspace_invites` and `workspace_id`. These are proposed schema names; no existing database tables or application routes have been renamed.

SQLModel is built on SQLAlchemy and Pydantic; it is not a migration tool. The closest comparison to Prisma is the combined SQLModel/SQLAlchemy + Alembic toolchain. Keep API request/response schemas separate from table models so internal identity fields cannot leak or be mass-assigned. [FastAPI database guide](https://fastapi.tiangolo.com/tutorial/sql-databases/).

Start with synchronous database sessions because the existing reconciliation routes are synchronous. Use a session per short database unit of work; never share sessions globally or hold a transaction/connection while parsing files, reconciling, generating reports, calling an auth provider or sending email. Async database access is not required to make this foundation correct.

Preserve purchase/Notes matching, financial values, status/evidence semantics, export schemas and current transport safeguards. FY logic, object storage, background compute, billing, custom roles and a general result-row database are separate phases. Authentication will intentionally change endpoint access requirements, but not reconciliation decisions.

Authentication proposal for the next implementation: first-party email/password authentication in FastAPI with opaque server-side sessions in PostgreSQL, as detailed in section 10. This fits the existing custom forms without introducing a provider SDK. It also makes password security, email delivery, recovery, abuse prevention and session revocation our responsibility. A managed identity service remains an alternative if the team prefers to delegate that responsibility; do not implement both mechanisms in v1.

## 2. Baseline integration points before the authentication slice

These describe the original unprotected pilot. The subsequent local auth mode,
API additions and fail-closed financial boundary are recorded in the local guide.

- `pyproject.toml`: Python 3.12. Phase 1 added SQLModel/SQLAlchemy, Psycopg and Alembic; the new `backend/app/db/` layer is opt-in and not wired into routes.
- `backend/app/main.py`: explicit CORS, currently GET/POST/DELETE/OPTIONS and Accept/Content-Type headers, no auth dependency.
- `api/routes/inspection.py`: creates a UUID session before inspection; no durable user/client owner.
- `api/routes/reconciliation.py`: reconcile, manual-match and paginated results trust possession of the session ID.
- `api/routes/export.py`: both report downloads must be protected, including regeneration paths.
- `api/routes/sessions.py`: status and deletion also need ownership checks.
- `services/session_manager.py`: ordinary reads touch Redis state and refresh expiry; authorize BEFORE calling these reads.
- `services/manual_match_service.py`: mutations happen in Redis CAS; PostgreSQL audit writes are not automatically atomic with them.
- `frontend/src/services/api.ts`: one Axios client, currently no token attachment; `App.tsx` has no authenticated workspace/client context.

The first security milestone is not a login page. It is verified identity plus authorization on EVERY path to financial data and session mutation.

## 3. Target separation

```text
React/Vite
  | email/password login; HttpOnly session cookie
  | CSRF token on mutations + selected workspace/client identifiers
  v
FastAPI
  validate identity -> resolve internal user -> check active membership
  -> check tenant/resource ownership -> check client assignment
       |
       +--> PostgreSQL: business records, ownership, permissions, audit
       +--> Upstash: existing temporary reconciliation session/results
       +--> existing temporary files/exporters

Later, separately approved: private object storage and managed jobs
```

The selected workspace is a requested context, never proof of membership. Business roles come from current database state, not a client-supplied role or a stale token claim. CORS is not authorization.

### Registration and onboarding

```text
Create account / sign in -> verify email
                  |
                  +--> Individual -> create INDIVIDUAL workspace
                  |                   + sole ADMIN membership
                  |                   -> create client -> add GSTIN -> reconcile
                  |
                  +--> Firm -> create FIRM workspace
                              + initial ADMIN membership
                              -> create clients -> invite accountants
                              -> assign clients -> reconcile

Returning user -> load active memberships -> select workspace if needed
Invited user   -> sign in / register -> accept verified invitation
                 -> join inviting FIRM; no unrelated workspace required
```

The firm is not itself a login identity: its founding person registers and creates the firm workspace. Both workspace types use the same client, GSTIN, run and audit tables. Data belongs to the workspace; `created_by` records the actor rather than granting exclusive access to that actor.

Proposed v1 capability boundary: an Individual workspace has one active ADMIN (its creator), can manage all its clients and run reconciliations, but cannot invite colleagues or assign clients to other people. A Firm workspace supports multiple members, team invitations and client assignments. Enforce this boundary in backend services, not merely by hiding Team navigation. No separate OWNER role is needed for the first version.

### Workspace conversion and switching

Design an explicit, audited `INDIVIDUAL -> FIRM` conversion, available only to the current authorized individual administrator. Retain the workspace UUID, existing ADMIN membership, clients, GSTINs, runs and audit history; do not copy financial records. Validate required firm details and perform the type change in a locked transaction. Invite/assignment capabilities become available only after conversion commits. Do not expose type changes through generic PATCH or support `FIRM -> INDIVIDUAL` in v1.

Creating a separate firm is different from converting an individual workspace: separate workspaces do not share clients or permissions. Switching workspace clears cached client/results views, aborts or ignores stale requests, and requires fresh scoped access checks. A user being ADMIN in one workspace grants no role in another.

### Subsidiaries: reserved design decision

The requirement mentions subsidiaries but does not yet distinguish these cases:

| Meaning | Possible future model | Access boundary |
|---|---|---|
| Branch/department of one accounting firm | Organizational unit within a FIRM workspace | Explicit branch/team scope if required; shared workspace alone does not settle branch visibility |
| Separate subsidiary accounting firm | Separate FIRM workspace with an explicit parent relationship | Separate tenant; no automatic parent-admin or member access |
| Subsidiary business of a client | Client relationship within the relevant workspace | Does not create a firm workspace or inherit client assignments |

Do not add a generic `parent_workspace_id`, subsidiary CRUD, hierarchy roles or automatic inherited permissions to the first migration. Confirm the business meaning before selecting the hierarchy model. Workspace type is independent of any future hierarchy.

## 4. Proposed schema

### Shared conventions

- Application-generated UUID primary keys. UUIDs do not replace authorization.
- Timezone-aware PostgreSQL `timestamptz`; server defaults for creation time, explicit update handling in services.
- Named foreign keys, unique constraints and CHECK constraints. Start with strings constrained to named allowed values rather than difficult-to-evolve PostgreSQL enum types.
- Workspace-scoped entities carry `workspace_id`. Composite foreign keys prove that related records belong together, not merely that each ID exists. PostgreSQL supports multicolumn uniqueness and foreign keys; row-dependent business rules still require transactions/services. [PostgreSQL constraints](https://www.postgresql.org/docs/18/ddl-constraints.html).
- Normalize application email keys with a documented trim/case-insensitive policy; preserve display email. Do not apply provider-specific dot/plus rewriting or automatically merge identities because their emails match.
- Archive/suspend business records instead of cascading away reconciliation/audit history. Explicit deletion/retention policy required before permanent deletion.
- JSONB for small, validated summary/audit metadata only. No passwords, bearer tokens, full source workbooks or giant result arrays in JSONB.

### Ten baseline tables

| Table | Core fields and constraints |
|---|---|
| `users` | `id`, `name`, `email`, `email_normalized UNIQUE`, `status` ACTIVE/DISABLED, `email_verified_at`, timestamps. No workspace ID, INDIVIDUAL/FIRM account type or global ADMIN role |
| `auth_identities` | `id`, `user_id`, `issuer`, `subject`, timestamps; `UNIQUE(issuer, subject)`. External identity is separate from profile and workspace membership |
| `workspaces` | `id`, `name`, `type` constrained to INDIVIDUAL/FIRM, optional `legal_name`, `created_by` -> user, ACTIVE/ARCHIVED, timestamps. No billing plan or subsidiary-parent column until needed |
| `workspace_memberships` | `id`, `workspace_id`, `user_id`, ADMIN/ACCOUNTANT, ACTIVE/SUSPENDED/REMOVED, `joined_at`, timestamps; `UNIQUE(workspace_id,user_id)` and `UNIQUE(workspace_id,id)` |
| `workspace_invites` | `id`, `workspace_id`, normalized target email, role, unique `token_hash`, `expires_at`, PENDING/ACCEPTED/REVOKED/EXPIRED, `invited_by`, optional `accepted_by`, acceptance/revocation timestamps, timestamps |
| `clients` | `id`, `workspace_id`, `name`, optional `legal_name`/`pan`, ACTIVE/ARCHIVED, `created_by`, timestamps; `UNIQUE(workspace_id,id)` |
| `client_gstins` | `id`, `workspace_id`, `client_id`, normalized `gstin`, ACTIVE/INACTIVE, timestamps; `UNIQUE(workspace_id,gstin)` and `UNIQUE(workspace_id,client_id,id)` |
| `client_assignments` | `id`, `workspace_id`, `client_id`, `membership_id`, `assigned_by`, timestamps; `UNIQUE(workspace_id,client_id,membership_id)` |
| `reconciliation_runs` | `id`, `workspace_id`, `client_id`, optional `client_gstin_id`, `created_by`, unique nullable `redis_session_id`, workflow status, `engine_version`, input/result versions, small optional `summary_json`, lifecycle timestamps, `session_expires_at`, `artifacts_available`, timestamps |
| `audit_events` | `id`, `workspace_id`, nullable actor user, optional client/run references, action, entity type/ID, bounded redacted metadata, optional `operation_id`/deduplication key, timestamp |

The extra table relative to the blueprint is `auth_identities`. We should not implement two authentication systems implicitly by putting both nullable password and provider columns on `users`.

All client-related tables apply to both workspace types. Individual client access comes from the sole active ADMIN membership; it does not need one assignment row per client. `workspace_invites` and `client_assignments` are Firm-only capabilities in v1. Because workspace type/member-count rules span tables, simple row CHECK constraints cannot enforce them: membership/invite/assignment/conversion services must lock and validate the workspace before writing. Test every mutation path, including invite acceptance and reactivation. Restrict direct application writes to these services; any later database trigger defense requires its own migration tests.

### Database-enforced tenant consistency

Implement these as composite SQLAlchemy `ForeignKeyConstraint` definitions in the SQLModel metadata and migrations:

```text
client_gstins (workspace_id, client_id)
    -> clients (workspace_id, id)

client_assignments (workspace_id, client_id)
    -> clients (workspace_id, id)
client_assignments (workspace_id, membership_id)
    -> workspace_memberships (workspace_id, id)

reconciliation_runs (workspace_id, client_id)
    -> clients (workspace_id, id)
reconciliation_runs (workspace_id, client_id, client_gstin_id)
    -> client_gstins (workspace_id, client_id, id)
```

Use corresponding composite keys for optional audit client/run references. When an audit event has a run reference, require its client context as well, so nullable columns cannot bypass the intended consistency check. Actors may be system/global identities; actor foreign keys alone do not prove authorization. Services validate their active membership when performing the action.

These constraints reject cross-workspace relationships even through a faulty write path. They do not automatically filter reads or prove that a membership is active; scoped queries and authorization services remain mandatory. PostgreSQL RLS is a possible additional defense, not a claim about this first migration. If added, test transaction-local tenant context with the real pooled app role, including owner/BYPASSRLS behavior.

### Deliberate changes from the blueprint

1. Assign clients to a workspace membership, not a globally identified user alone. Removed/suspended members immediately lose access even if assignment records remain.
2. Proposed GSTIN uniqueness is within the workspace, not only within the client, for both Individual and Firm types. This prevents accidentally duplicating a registration across two clients in one workspace, while allowing separate workspaces to represent the same business. This is a product policy to confirm, not a matching-engine change.
3. Invitations are separate from memberships; create/reactivate membership only after valid acceptance. An invited email need not already have a user.
4. At most one PENDING invite per `(workspace_id,email_normalized)` through a partial unique index. Do not use `now()` in an index predicate. Mark an expired pending invite EXPIRED transactionally before reissuing; acceptance always checks `expires_at` regardless of cleanup.
5. Protect the last active ADMIN using a workspace row lock and a same-transaction count on all removal/demotion/suspension paths. Creating either workspace type and its initial ADMIN membership is one transaction. Individual workspaces prohibit additional active members and creator transfer in v1; global user disablement still immediately denies access and requires an explicit recovery policy rather than silently promoting another user.
6. Derive state code from GSTIN rather than independently editable duplicated state fields. Store normalized identifiers and validate format; no online GST validation or new financial rules.
7. Run workflow status is distinct from invoice reconciliation status. Start with DRAFT/READY/PROCESSING/COMPLETED/FAILED, adding only supported transitions. Session expiry/artifact availability must not erase the historical fact that a run completed. Do not expose cancellation before cancellation exists.
8. Leave FY/period and object-storage references out of the first migration. They can be added when their behavior is designed. Never persist temporary filesystem paths or expiring signed URLs as durable artifact references.
9. Audit data is append-only to the normal application role. An append-only table is not cryptographically tamper-proof. Configure separate migration/application roles; define retention and privileged maintenance explicitly.

### Initial query indexes

Reuse unique-constraint indexes rather than duplicate them. Add membership lookup by `(user_id,status)`, clients by `(workspace_id,status)`, assignments by `(workspace_id,membership_id)`, runs by `(workspace_id,client_id,created_at,id)` and audit events by `(workspace_id,created_at,id)` and scoped run/time. Confirm all supporting foreign-key indexes during migration review. Avoid indexing unqueried JSONB payloads.

## 5. Authentication decision and full lifecycle

Current proposal: the first-party lifecycle in section 10. Keep workspace roles and assignments in our database. Individual and Firm onboarding share one identity system. The provider design below is an alternative, not an additional v1 implementation. Do not provision a provider or purchase a plan without choosing it with the team.

For a provider flow: Authorization Code + PKCE via its supported SPA SDK; FastAPI validates API access tokens using configured issuer, audience, algorithms, signature, expiry and other required claims. Cache signing keys with bounded refresh and test key rotation. Do not accept a browser ID token as an API access token. Map `(issuer,subject)` to the internal user; linking an existing email requires a verified, explicit flow. [Token validation reference](https://auth0.com/docs/secure/tokens/access-tokens/validate-access-tokens).

Define signup, verification, login, logout, recovery, disabled-user behavior, token expiry/renewal and revocation semantics before declaring auth complete. Logout must clear client state; immediate server revocation requires provider support or additional application checks, not just deleting a browser token. Membership/assignment revocation is checked from current PostgreSQL state on protected requests.

Section 10 specifies separate `password_credentials`, `auth_sessions` and single-use verification/reset token tables. Use Argon2id for passwords and random opaque session tokens; no JWT/refresh-token system in v1. Add rate limits, generic account-discovery responses, email delivery, password-reset revocation and abuse monitoring. This is a larger security responsibility than adding a `/login` route. [OWASP password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).

Browser/API transport must follow the chosen auth mechanism: bearer tokens require explicit Authorization CORS support; workspace/client editing also requires PATCH support. If HttpOnly cookies are chosen, design CSRF protection, secure/same-site cookie settings and exact credentialed origins. Current separate frontend/backend origins make that a real design choice. Never combine permissive CORS with credentials or persist long-lived credentials in ordinary localStorage by default.

## 6. Authorization contract

Build explicit dependencies/services, for example `get_current_user`, `require_membership`, `require_workspace_admin`, `require_client_access`, `require_run_access`. Return validated context objects, not raw request IDs passed through as trusted data.

| Action | Individual ADMIN | Firm ADMIN | Firm ACCOUNTANT |
|---|---|---|---|
| Manage workspace details | Own workspace | Own workspace | Denied |
| Manage team/invites | Unavailable until conversion | Own firm | Denied |
| Create/edit/archive clients and registrations | Own workspace | Own firm | Denied initially |
| Assign/unassign accountants | Unavailable until conversion | Own firm | Denied |
| List/view clients and runs | All own-workspace clients | All own-firm clients | Assigned clients only |
| Inspect/reconcile/review/manual-match/export | Own active clients | Own active clients | Assigned active clients |
| Delete temporary reconciliation session | Own accessible run | Own accessible run | Assigned accessible run |
| Convert Individual to Firm | Authorized sole ADMIN | Not applicable | Denied |

“Own” means within the validated workspace, not necessarily created by the current user. A firm member can work across all explicitly assigned clients; there is no implicit subsidiary, parent-workspace or personal-workspace access. Both capability (workspace type) and permission (membership/role/assignment) must pass. Removing an accountant's access does not remove the firm's clients, source data or reconciliation history.

Disabled users, archived workspaces and inactive memberships are denied. Proposed archived-client policy: read-only historical metadata, no new runs or manual edits; any retained downloads still require access. Keep these rules centralized and test them, rather than scattered UI checks. A non-member should receive a non-enumerating resource response, while an authenticated member requesting an unauthorized admin action can receive 403. Invalid/missing identity returns 401. DB/auth outages fail closed with sanitized availability errors.

## 7. Reconciliation ownership and audit consistency

Create a PostgreSQL run for an authorized client BEFORE allocating its Redis session. Link the opaque Redis ID to that run; verify this durable relationship on inspect/reconcile/results/manual-match/export/status/delete. Do not trust Redis ownership fields supplied by the browser. Authorize before touching Redis or regenerating reports. An arbitrary UUID, candidate ID or file reference is never an access grant.

Existing anonymous sessions cannot safely be assigned to whichever user first presents their ID. At cutover, expire or reject them and require a new authenticated upload. Document this intentional compatibility change. Do not leave legacy unprotected routes operating alongside the new routes.

Keep the current engine stateless with respect to auth: route/business services enforce access before handing it the existing canonical records. Add a versioned ownership wrapper rather than changing match eligibility.

PostgreSQL and Redis do not share one transaction. For run publication/manual confirmation, design an operation ID and recoverable operation journal before release: authorize and record intent, perform version-checked idempotent Redis mutation, record success/audit in PostgreSQL, then acknowledge completion. A failed final write must be repairable without applying the match again. Phase 5 may add a dedicated operation/outbox table once this protocol is specified. Do not hold a SQL transaction across the reconciliation engine or label independent SQL/Redis writes atomic.

PostgreSQL-only actions (workspace creation/conversion, assignments, invite acceptance) write business state and audit in the same transaction. Invite acceptance locks the workspace then invite in a consistent order, verifies that the workspace is an active FIRM, checks expiry/revocation/verified email/role and consumes it once; concurrent acceptance must not create duplicate memberships. Email delivery happens after commit, with retry/status handling rather than pretending delivery is transactional.

## 8. Implementation phases and acceptance

### Phase 1 — Database foundation and first migration

Implemented locally: SQLModel, Psycopg and Alembic with versions resolved/tested against Python 3.12 and current dependencies. Foundation files:

```text
backend/app/db/config.py
backend/app/db/session.py
backend/app/db/models/identity.py
backend/app/db/models/workspace.py
backend/app/db/models/client.py
backend/app/db/models/reconciliation_run.py
backend/app/db/models/audit.py
backend/alembic/env.py
backend/alembic/versions/<initial_revision>.py
alembic.ini
backend/tests/test_database_schema.py
```

Also update dependency declarations and safe environment examples; optionally add a local PostgreSQL Compose service after checking available Docker tooling. No real secrets in committed files.

Use `DATABASE_URL` for application access and `MIGRATION_DATABASE_URL` for migration privileges/direct connectivity where required. Production uses TLS with certificate verification appropriate to the chosen host. Choose pool sizing after the host is selected: external transaction pooler plus tested NullPool or a small bounded app pool; do not multiply a large default pool across serverless instances. Set connection/statement timeouts. [SQLAlchemy pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html).

Review generated migrations, especially composite FKs, checks, partial indexes and deletes. Never run `create_all` or migrations during request handling/serverless startup. [Alembic autogeneration caveats](https://alembic.sqlalchemy.org/en/latest/autogenerate.html).

Acceptance: clean PostgreSQL upgrade; schema inspection; DB-level cross-tenant write rejection between all combinations of Individual/Firm workspaces; valid workspace-type CHECK; email/GSTIN/invite uniqueness; UUID/timestamp correctness; rollback in a disposable empty database; no migration drift. Tests run on real PostgreSQL, not SQLite substitutes. Missing test DB must not silently count as a passing database release gate. Service-level single-member and Firm-only capability tests belong to Phase 3, not a claim that foreign keys enforce those policies. Existing application behavior stays unchanged at this phase.

### Phase 2 — Identity end to end

Implement the first-party flow and `/api/me`; React auth boundary and Axios integration; expiry/logout/verification/error handling. Registration presents Individual/Firm onboarding choices, not a client-controlled global user role. Workspace ADMIN privilege is granted only by transactional workspace creation or an authorized membership workflow. Acceptance includes invalid/expired/revoked sessions, disabled users, CSRF rejection, recovery-token replay, concurrent signup and browser/API flows. Provider-specific issuer/audience/signature tests apply only if the team selects the provider alternative. Mock identity only in tests, never a production bypass.

### Phase 3 — Individual/Firm onboarding, switching and team

Transactional workspace + initial ADMIN creation for both types; workspace switcher; Firm-only membership management and hashed-token invitation/revoke/accept flows; last-admin protection; explicit audited Individual-to-Firm conversion. Proposed routes under `/api/workspaces` and `/api/workspaces/{workspace_id}/memberships|invites`, with `POST /api/workspaces/{workspace_id}/convert-to-firm` for the dedicated conversion operation. Audit these actions from the start.

Acceptance includes both onboarding paths; one user in an Individual workspace and two firms with different roles; invited users joining without creating unrelated workspaces; concurrent acceptance and last-admin races; another workspace's invite; suspended-member access; invite email mismatch; and backend rejection of team APIs in Individual workspaces. Test conversion retaining IDs, clients and history, refusing unauthorized/downgrade operations, and preventing stale requests or concurrent membership writes from bypassing workspace-type checks. Registration retries must not accidentally create duplicate workspaces; use an idempotent onboarding operation.

### Phase 4 — Clients, GST registrations and assignments

Workspace-scoped CRUD/list APIs shared by both types, Firm assignments to active memberships, client selector and client-scoped dashboard. Both Individual and Firm ADMINs can register/manage clients; firm accountants see assigned clients. Show Team/Assignments only in Firm navigation but enforce that rule server-side. Add backend pagination and strict update schemas; never allow changing workspace/creator through generic PATCH. Acceptance includes Individual-to-Firm and Firm-to-Firm isolation, two accountants, guessed UUIDs, unassigned client lists and mismatched client/GSTIN IDs rejected by both services and DB.

### Phase 5 — Protect existing reconciliation and downloads

Bind every Redis session to a run, apply authorization to all existing routes, add operation/audit recovery, refresh scoped frontend queries on workspace/client changes and invalidate stale views. Keep engine/export payload semantics intact. Acceptance includes foreign session status/delete/download, unassigned-client manual match, export regeneration, revoked membership with a still-valid token, Redis expiry and fault injection between SQL and Redis writes. Run frozen real-fixture and row-level output comparisons.

### Phase 6 — Production readiness and controlled rollout

Real PostgreSQL backups/restore rehearsal, separate app/migration roles, TLS/pooling/concurrency checks, rate limiting, sanitized logging, email delivery, security headers and provider configuration. Add schema migrations as an explicit release step, then backend/frontend cutover together; deploy first to staging with synthetic data.

Run full backend/frontend tests, targeted PostgreSQL isolation tests, browser end-to-end tests, production build and `git diff --check`. Observe DB connection counts and authorization failures without logging tokens or financial source rows. No claim of production readiness until these pass.

Rollback must never restore anonymous access to authenticated financial sessions. Prefer disabling writes/maintenance mode or a previous secure version; use additive migrations and forward fixes once production data exists, not destructive downgrades. No public launch with only Phases 1–2 complete.

## 9. First deliverable and open decisions

Completed first coding deliverable: Phase 1 — database configuration, SQLModel tables, reviewed initial Alembic migration and real-PostgreSQL constraint tests. An isolated local test cluster was used for that phase. The subsequent auth slice created dedicated development/test databases in the existing local PostgreSQL service; no pre-existing business database was migrated. UI forms now connect to the local auth lifecycle and workspace/client management. The next slice is owned reconciliation and its recovery/security gates. No matching changes, paid service setup, production migration, commit, push or deployment was performed.

Updated defaults proposed for schema review: one user identity with memberships across Individual/Firm workspaces; both workspace types manage clients; an Individual workspace has a sole ADMIN and no team features until conversion; Firm ADMIN/ACCOUNTANT roles and client assignments; workspace-wide GSTIN uniqueness; first-party credentials separate from users; archived historical records; no FY fields yet. Retain `auth_identities` for future explicit provider integration, but do not invent provider identity rows for local passwords. Whether to limit the number of Individual workspaces per user remains a product decision; do not silently add that database uniqueness policy.

Subsidiary meaning remains open: accounting-firm branch, separate subsidiary firm, or client subsidiary business. None is implemented or assumed by the first migration. Parent relationships must never imply automatic permission inheritance. Client transfer between workspaces and Firm-to-Individual conversion also remain outside v1.

The proposed first-party default is detailed below; confirm any change to a managed provider before credential-specific migrations. Select the actual PostgreSQL host/region and test connection through a secure environment before integration testing/provisioning; never paste credentials into this document or chat. The provider-neutral Phase 1 model design does not need production credentials.

## 10. Backend execution plan after the UI preview

Scope: connect the existing React forms and FastAPI application, then complete
tenant authorization. Local auth storage/lifecycle and UI connection are now
implemented. This section retains the target architecture: production controls
and milestones C/D remain outstanding. The local guide records interim limits.

### 10.1 Reuse map

| Existing code | Planned use |
|---|---|
| `backend/app/db/config.py`, `session.py` | Reuse URL validation, engine construction and short SQLModel units of work; add startup/readiness checks for authenticated deployments |
| `db/models/identity.py` | Reuse users, normalized email, disabled state and verification timestamp; retain external identities unchanged |
| `db/models/workspace.py` | Reuse workspace types, memberships and hashed invitations; add transactional services |
| `db/models/client.py` | Reuse clients, GSTINs and membership assignments with their composite tenant constraints |
| `db/models/reconciliation_run.py`, `audit.py` | Reuse durable run ownership and business audit records; do not store full result rows here |
| `backend/alembic/` | Add new reviewed revisions; never rewrite the baseline migration |
| `backend/app/main.py` | Register new routers, configure exact credentialed CORS and CSRF enforcement |
| Existing inspection/reconciliation/export/session routers | Add identity and ownership dependencies without rewriting the engine |
| `frontend/src/workspace/AuthPage.tsx` | Replace the preview submit handler; retain the design and Individual/Firm controls |
| `frontend/src/workspace/WorkspaceApp.tsx` | Load real identity/workspaces, implement sign-out and remove anonymous pilot entry at secure cutover |
| `frontend/src/services/api.ts`, `types/api.ts` | Extend the existing Axios client and explicit API types, not a second transport layer |

### 10.2 Milestone A — credentials, session storage and configuration

Add `backend/app/db/models/auth.py` and a second Alembic revision with:

| New table | Minimum data and guarantees |
|---|---|
| `password_credentials` | Unique `user_id` FK, Argon2id encoded hash, password-change timestamp; no plaintext password |
| `auth_sessions` | User FK, unique digest of a cryptographically random opaque token, created/absolute-expiry/idle-expiry/last-seen/revoked timestamps; expiry indexes and a session-bound CSRF secret/digest |
| `auth_action_tokens` | User FK, purpose VERIFY_EMAIL/RESET_PASSWORD, unique token digest, expiry, consumed/revoked timestamps, target-email binding; atomically consume once |
| `auth_security_events` | Nullable user FK, allowlisted action/outcome/time and small redacted metadata; login/reset events cannot use the existing audit table because it requires a workspace |

Use normal UUIDs for row IDs, but generate session/action credentials from at
least 32 random bytes. Store only their digests. Do not reuse the reconciliation
Redis `session_id` as a login credential. Keep auth security events append-only
with a documented retention policy; do not record passwords, cookie values,
reset links or full financial payloads.

Add `backend/app/auth/config.py`, `passwords.py`, `sessions.py`, `csrf.py` and
`dependencies.py`. Configure durations explicitly; proposed initial values are
30-minute idle and 12-hour absolute login expiry, 30-minute reset tokens and
24-hour verification tokens. Test those values through an injected clock.
Use Argon2id with benchmarked resource limits and rehash-on-login support.
Propose a 15-character single-factor password minimum and 128-character maximum;
allow spaces, avoid composition rules and reject common passwords. Align the
current UI's 12-character preview rule with the server contract during wiring.

Authentication-enabled startup must reject missing/unsafe database, origin,
cookie and email settings. Readiness should check DB connectivity/schema version
without exposing connection details. Never run migrations automatically on startup.
Do not hold database connections while hashing passwords or sending mail.

Acceptance: real PostgreSQL upgrade/downgrade/drift checks; secret-redaction,
duplicate digest, expiry, revoke, hash/verify and transaction rollback tests.

### 10.3 Milestone B — registration and the full authentication lifecycle

Add `api/auth_schemas.py`, `api/routes/auth.py`, `services/auth_service.py`,
`services/workspace_service.py` and an email adapter. Request schemas forbid
extra privileged fields such as role, user ID, verified state and creator.
Map `fullName` and `firmName` from the existing form to one documented API casing.

| Endpoint | Contract |
|---|---|
| `GET /api/auth/csrf` | Bootstrap a short-lived anonymous CSRF context, or return a token bound to the current session; never cache |
| `POST /api/auth/register` | Name, email, password, INDIVIDUAL/FIRM and conditional firm name; generic 202 verification-pending response, no claim of login |
| `POST /api/auth/login` | Verify credentials; rotate any prior/pre-auth session into a new cookie session; unverified users get restricted account/verification access only |
| `GET /api/me` | Minimal current user, email-verification state and active workspace memberships; 401 without a valid session |
| `POST /api/auth/logout` | Revoke current session and expire cookie; idempotent response |
| `POST /api/auth/logout-all` | Revoke all current user's sessions with CSRF protection |
| `POST /api/auth/verification/request` | Throttled generic resend response; never mark mail as delivered merely because it was queued |
| `POST /api/auth/verification/confirm` | Consume valid token for its bound user/email; no automatic login through the link |
| `POST /api/auth/password/forgot` | Generic throttled response for existing and unknown addresses |
| `POST /api/auth/password/reset` | Consume single-use reset token, replace password and revoke all sessions/other reset tokens atomically; require a fresh login |

Registration hashes outside the transaction, then creates user + credential +
workspace + initial ADMIN + business audit + verification token in one short
transaction. An individual workspace can derive its display name from the user;
a firm requires an explicit firm name. Email remains unverified and financial
access is denied until verification. Unique normalized email plus transactional
rollback prevents repeated/concurrent signup creating extra workspaces. Do not
merge an existing user based only on an email match or use signup to join a firm.
Existing users create additional workspaces through an authenticated endpoint.

Authenticate generic failure paths without exposing whether an account exists;
use comparable password verification work for an unknown email. Add distributed
IP/account rate limiting before expensive hashing and token/email issuance,
bounded request sizes, retry guidance and sanitized errors. Use the existing
Redis client capability with a separate limiter namespace, not reconciliation
session records; process-local limits are not sufficient for production.

Email is sent after commit. For local tests/development, use a private SMTP sink
or test adapter; never return verification/reset secrets from production APIs.
For production, use retryable durable dispatch with encrypted transient email
payloads (including any action link) and retention limits; store delivery state,
not a plaintext token in an outbox. This requires a selected mail service and
worker/dispatch mechanism before launch, not a FastAPI background-task guarantee.
Link navigation must not consume a token on GET: scanners may follow email links.
Use an allowlisted application URL and a POST confirmation flow; strip tokens
from the URL promptly and keep them out of logs/referrers/analytics.

Acceptance: both registration types, duplicate/retried signup, email outage,
invalid login, unverified/disabled users, logout/restart persistence, expired and
concurrently replayed action tokens, and reset racing login. Lock/recheck the
credential version before session issuance so a password reset cannot race a
stale password verification into a valid new session.

### 10.4 Cookie transport and connecting the existing UI

Use an opaque HttpOnly cookie, with Secure in production, host-only scope,
Path=/ and SameSite=Lax on an approved same-site deployment. Prefer a same-origin
API proxy or same-site app/API domains. The current separate hosting origins
must be checked before rollout: unrelated hosting domains can make cookie auth
depend on blocked third-party cookies. Do not solve this with wildcard CORS or
by assuming SameSite=None always works.

In local development use the SAME hostname on both ports, e.g.
`localhost:5173` and `localhost:8000`, or `127.0.0.1` on both. The existing preview
uses 127.0.0.1 while Axios defaults to localhost, which must be aligned for cookies.
Use a distinct non-Secure development cookie name; production should use the
`__Host-` prefix. Never let a request parameter select weaker cookie settings.

Extend `main.py` to allow credentials, exact trusted origins, PATCH, and the
`X-CSRF-Token` header. Validate the CSRF token and Origin on every unsafe method,
including login/register/logout/reset; reject untrusted-origin bootstrap reads.
Tie pre-auth CSRF context to a short-lived server-verifiable cookie and rotate
it at login. SameSite and CORS supplement CSRF validation rather than replace it.

Set `withCredentials: true` in the current Axios client and attach the CSRF
header on mutations. Add an AuthProvider and API types for bootstrapping `/me`,
verification/recovery screens, field errors and loading states. No browser token
storage and no mock-success fallback. Avoid auth retry loops or replaying uploads
automatically after session expiry. Keep download requests on the authorized
client; binary exports need cookie/error handling too.

IMPORTANT: `WorkspaceApp` currently retains `App` using React Activity. That is
useful for sidebar navigation but unsafe across identity/workspace changes.
Unmount/remount scoped reconciliation state, clear data and abort/ignore in-flight
responses on logout, session expiry and workspace/client changes. No old results
may reappear after another user logs in. Bootstrap must not briefly expose the
anonymous pilot before authentication resolves.

Acceptance: browser signup -> verification -> login -> refresh -> `/me` -> logout;
cookie attributes, trusted/untrusted origins, CSRF rejection, expired sessions,
no credentials in browser storage, no stale data across users and real network
error feedback. Test actual proposed production domains, not only localhost.

### 10.5 Milestone C — workspace and client authorization

Local implementation status: client/GSTIN CRUD and archive/activation, scoped
lists/details, firm conversion, invited registration, invitation create/revoke/
accept, member role/status updates and assignment/unassignment are implemented
with connected Clients/Team UI. Reuses the existing tables; runtime grants were
extended only for these operations. Permission/tenant/audit/concurrency tests
and a real browser flow cover these boundaries. See `LOCAL_AUTH_DEVELOPMENT.md`
for the local mail workflow and remaining production requirements. Financial
endpoints now have the separate local Milestone D gate described below; this is
not production authorization approval.

Implement the section 6 permission matrix through reusable dependencies:
`get_current_user`, `require_verified_user`, `require_membership`,
`require_workspace_admin`, `require_client_access`, `require_run_access`.
Recheck current user, workspace, membership and assignment state on protected
requests; frontend selections and cookie claims do not grant roles.

Add routers/services for:

- `GET/POST /api/workspaces`; `GET/PATCH /api/workspaces/{workspace_id}`.
- Workspace-scoped member lists/updates and invite create/revoke/accept.
- Workspace-scoped client create/list/get/update/archive, GSTIN management and
  client assignment/unassignment APIs. Use bounded pagination and update schemas.
- `POST /api/workspaces/{workspace_id}/convert-to-firm` as an explicit transaction;
  no generic type update or reverse conversion.

Use workspace row locks for last-ADMIN protection, Individual sole-member rules
and firm invite acceptance; use consistent lock order and same-transaction audit.
Invited registration must be a separate verified flow that joins the target firm
without forcing a new individual/firm workspace. A link alone cannot grant access
to a different signed-in email. Do not infer subsidiary access.

Acceptance: multiple firms and an individual workspace, same user with different
roles, accountants with different assignments, guessed tenant/client UUIDs,
archived entities, revoked memberships, concurrent invite consumption and two
admins attempting to remove each other. Add minimal client/workspace selection
to the existing shell before protected reconciliation can be used end to end.

### 10.6 Milestone D — secure the existing reconciliation API

**Local implementation status (2026-08-31):** implemented behind default-off
`AUTH_RECONCILIATION_ENABLED`, with migration `20260831_03`, scoped run history,
client/GSTIN selection, resume/delete, and operation receipts/fencing. SQL states
are PENDING/FINALIZED/FAILED/UNRESOLVED; APPLIED is proven by the atomic temporary
snapshot receipt rather than a separate SQL flag. Tests exercise PostgreSQL and
Redis CAS through a command-level test double, including concurrent identical
manual confirmation and SQL failure recovery. A real Upstash test is present but
requires isolated test credentials; this release gate has NOT been verified.
Production activation remains blocked. See `LOCAL_AUTH_DEVELOPMENT.md` for setup,
expiry, recovery and runtime grants. The requirements below remain the contract.

Keep existing paths and result/export representations. Add required authorized
workspace/client context to inspection, create/link the durable run before
publishing a temporary session, and resolve later session IDs through that run.
The API input/access contract changes intentionally; matching logic does not.

The complete protection checklist is:

| Existing route | Required protection |
|---|---|
| `POST /api/inspect` | Verified user + active workspace/client access BEFORE application file processing/session allocation; also enforce existing ingress size limits |
| `POST /api/reconcile` | Run ownership/client access before reading or mutating temporary state |
| `POST /api/manual-match` | Same run checks, CSRF and existing version/CAS validation |
| `GET /api/session/{session_id}/results` | Scoped run access before any session read/TTL refresh |
| `GET/DELETE /api/session/{session_id}` | Scoped run access; CSRF for delete |
| `GET /api/export/{session_id}/excel` and `/json` | Scoped run access before reading files or regenerating artifacts |

Missing identity returns 401; valid identity with forbidden role actions gets
403; foreign or nonexistent scoped resources return the same non-enumerating
404 response. Backend/database outages fail closed. No anonymous fallback.

Reuse current Redis CAS. Implement the section 7 recoverable operation journal
in a new migration/service: tenant/run-scoped idempotency key, operation kind,
expected versions, payload fingerprint, PENDING/APPLIED/FINALIZED or failure
state, and a Redis-side operation receipt stored atomically with the mutation.
Reject key reuse with different input. Retries reconcile the receipt/version
before recording SQL audit; never repeat a manual match just because SQL failed.
Run the repair procedure after crash/failure and test expiry before repair.
If the receipt has expired and outcome cannot be proven, surface an unresolved
operation rather than inventing success or replaying it blindly. Include session
allocation/deletion and export availability in lifecycle recovery tests.

At secure cutover, reject existing anonymous sessions; never assign them to the
first user presenting their ID. No parallel public legacy route. Local milestones
can be developed separately, but partial auth is not an authenticated release.

### 10.7 Verification and release gate

Extend existing database tests/fixtures for migrations and constraints. Add focused
auth lifecycle, authorization and API integration tests where no current suite
covers them. Extend existing inspection/manual-match/pagination/export tests to
run with real scoped identities; preserve financial output assertions. Check a
route inventory so a future financial endpoint cannot accidentally omit auth.

Run targeted auth/schema/permission tests, the full backend suite with
`--require-postgres` against an empty disposable test DB, the full frontend suite,
production build, browser lifecycle checks and `git diff --check`. Include real
Redis integration/fault injection for journal recovery; mocked Redis alone is
not the concurrency/recovery release gate. Require production TLS, credential
isolation, mail delivery, distributed throttling, backup/restore and migration
rehearsals, safe error responses and a rollback that never reopens anonymous access.

First coding slice after approval: Milestone A plus registration/login/logout/me
and verification plumbing from B in local/test environments, using the existing
UI and transactionally creating Individual/Firm workspaces. Then complete recovery,
permissions and route protection before calling it ready for a live pilot.

Open infrastructure inputs: development DB URL supplied securely, production
PostgreSQL host, same-site application/API domain arrangement, email sender/service
and durable dispatch runtime. None authorizes provisioning or paid services.
Do not touch `Track/Doc.py`, FY, matching rules, financial values, JSON/Excel export
schemas, or unrelated uncommitted work. Do not stage, commit, push or deploy.

Security references used for this proposal: [OWASP session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html),
[CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html),
[password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html),
[authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html),
and [password recovery](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html).
