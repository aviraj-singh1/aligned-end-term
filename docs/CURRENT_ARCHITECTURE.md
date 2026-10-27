# Aligned — Current Architecture

- **Last verified date:** 24 September 2026
- **Last verified Git commit:** `e99b931` plus the uncommitted Tally Export frontend integration described here.
- **Working tree status:** **DIRTY.** The Tally Export frontend, its tests, and these two living-document updates are uncommitted. The pre-existing untracked `scratch/` directory was not touched.
- **Source-of-truth rule:** The inspected implementation and safe committed configuration are authoritative. Historical plans and older documentation do not override the code.
- **Verification scope:** Frontend Tally tests, the complete frontend suite, the production frontend build, PostgreSQL-backed Tally/API/schema tests, the complete backend suite and backend compilation were run for this integration.

## 1. High-level system architecture

```text
User browser
    |
    | HTTPS UI and API calls; HttpOnly application session cookie
    v
React + TypeScript + Vite frontend
    |                               \
    | Axios API                       \ Supabase email/password or Google OAuth
    v                                  v
FastAPI application API <-------- Supabase Auth
    |
    +--> authentication and CSRF boundary
    +--> workspace/client authorization
    +--> upload inspection and reconciliation orchestration
    +--> GSTR run lifecycle API
    |
    +----------------+----------------------+---------------------+
    |                |                      |                     |
    v                v                      v                     v
PostgreSQL       Private object        MSG91 API           GSTR worker process
  - identity       storage             phone OTP                  |
  - sessions       - local dev                                    |
  - tenants        - AWS S3                                      v
  - runs           - optional                              validate / merge /
  - audit            CloudFront                            verify / publish
  - job state
```

The synchronous reconciliation API and asynchronous GSTR Consolidation lifecycle share authentication, tenancy, PostgreSQL and private-object infrastructure, but they use different processing lifecycles.

The sibling `GSTR File Merging Tool/` repository is an older standalone application. The current integrated architecture is the implementation under `Yuvraj-Tool/backend/app/services/gstr_merge/` plus the durable API, database and worker layers in `Yuvraj-Tool`.

## 2. Repository structure

```text
Yuvraj-Tool/
├── backend/
│   ├── alembic/
│   │   └── versions/              Ordered PostgreSQL migrations
│   ├── app/
│   │   ├── api/                   Schemas and FastAPI route modules
│   │   ├── auth/                  Auth config, middleware, dependencies, security
│   │   ├── db/                    Engine, models and revision validation
│   │   ├── models/                Reconciliation domain dataclasses/enums
│   │   ├── services/              Domain and integration services
│   │   │   └── gstr_merge/        Framework-independent workbook merge core
│   │   ├── workers/               Executable worker entry points
│   │   └── main.py                FastAPI composition and startup checks
│   └── tests/                     Backend unit/integration/PostgreSQL tests
├── frontend/
│   ├── src/
│   │   ├── components/            Reconciliation and GSTR UI components
│   │   ├── services/              API, Supabase and MSG91 adapters
│   │   ├── workspace/             Product shell and authenticated pages
│   │   ├── types/                 Shared frontend response/request types
│   │   └── utils/                 Download, upload and transport helpers
│   └── vercel.json                Vite static deployment configuration
├── docs/                           Focused and living documentation
├── local_test_data/                Sample/test workbooks
├── scratch/                        Local runtime/test artifacts; not source
├── temp_sessions/                  Local session artifacts; not durable production state
├── .env.example                    Safe configuration contract
├── alembic.ini                     Migration configuration
└── pyproject.toml                  Python package and test configuration
```

Generated workbooks, scratch contents and temporary sessions are not architecture definitions.

## 3. Frontend architecture

### Framework and composition

The frontend uses React 19, TypeScript, Vite 8 and Tailwind CSS. `frontend/src/main.tsx` mounts the application; `frontend/src/workspace/WorkspaceApp.tsx` is the product shell.

Navigation is a lightweight hash-based router implemented in the workspace shell rather than a third-party routing library. Current page states include public/home/demo, authentication and recovery, dashboard, reconciliation, GSTR consolidation, clients, team, settings, logs, invitations and ownership transfer.

### API layer

`frontend/src/services/api.ts` owns the Axios client and typed API helpers. It:

- resolves `VITE_API_BASE_URL` against the browser origin;
- sends credentials when frontend authentication is enabled;
- obtains CSRF tokens before mutations;
- supplies idempotency keys and expected run versions;
- supplies workspace, client and optional GSTIN context headers for reconciliation;
- handles expired/changed sessions and maps safe backend errors to UI messages;
- contains workspace, reconciliation, export and GSTR lifecycle calls.

`frontend/src/services/tallyExportApi.ts` builds on those shared credential, CSRF and download helpers. It provides typed tenant/GSTIN-scoped calls for profiles, exact ledger mappings, durable candidates, validation, idempotent batch generation, history, detail and secure XML download. Components do not scatter raw Tally API calls.

### Authentication/session handling

`frontend/src/services/supabase.ts` wraps Supabase email/password, Google OAuth, recovery and identity linking. `AuthContext.tsx` reconnects the browser to the backend application session and owns the current Aligned user/workspace state.

The Supabase browser session is an identity bootstrap. Authenticated business requests rely on the backend application cookie, not on attaching the Supabase access token to every request.

### Product UI

- `workspace/ManagementPage.tsx` and `workspace/management.ts` cover client/team operations.
- `workspace/ReconciliationWorkspace.tsx` owns the authenticated reconciliation workflow and run history.
- `components/upload`, `components/mapping`, `components/results`, `components/review` and `components/export` implement the major reconciliation stages.
- `workspace/GstrMergeWorkspace.tsx` and `components/gstr-merge/` implement GSTR client/GSTIN selection, upload slots, durable run listing, polling, failure/result views and download.
- `components/results/ResultsDashboard.tsx` uses durable backend candidate IDs for selection in the Not-in-Tally category.
- `components/tally-export/TallyExportFlow.tsx` implements the focused selected-invoices → profile → exact ledger mappings → backend validation → generation/download flow.
- `components/tally-export/TallyExportHistory.tsx` exposes durable batch and immutable item history independently of temporary reconciliation-session availability.

GSTR navigation is hidden unless `VITE_GSTR_CONSOLIDATION_ENABLED=true`. This is a presentation gate, not an authorization boundary.

## 4. Backend architecture

### Application composition

`backend/app/main.py` creates the FastAPI application and registers:

- `/api/health`;
- authentication and profile routes;
- workspace/client/team/data-management routes;
- GSTR Consolidation routes;
- tenant-scoped Tally Export profile, mapping, candidate, validation, generation, history, detail and secure-download routes;
- `/api/inspect`, `/api/reconcile`, `/api/manual-match` and paginated result routes;
- session status/deletion routes;
- Excel, JSON and notes Excel export routes.

Startup validates object-storage configuration. When authentication is enabled it also validates auth configuration and a supported database revision; enabling GSTR raises the required revision to the GSTR migration contract.

### Middleware and boundaries

The backend applies:

- configured CORS origins, with production refusing an empty origin list;
- an authentication boundary that protects API families and authorizes financial requests before session access;
- sanitized validation errors for authentication/workspace requests;
- no-store, no-sniff and referrer-protection headers on API responses;
- centralized handling of account-database failures for safe `503` responses.

Mutating auth, workspace, GSTR and Tally Export routers use the CSRF dependency. Route services still perform resource-specific authorization; middleware presence alone is not treated as tenancy proof.

### Service organization

Route modules are intentionally thin. Business behavior lives in services:

- authentication and phone verification;
- workspace/client/team/ownership/data management;
- upload parsing and detection;
- reconciliation and notes engines;
- manual match mutation;
- owned run orchestration and recovery;
- session and object storage;
- exporters;
- GSTR lifecycle and worker processing.

Domain reconciliation values are Python dataclasses/enums under `backend/app/models/`. Durable relational records are SQLModel models under `backend/app/db/models/`.

## 5. Authentication architecture

### Supabase customer identity

```text
Browser
  -> Supabase email/password or Google OAuth
  -> Supabase access token
  -> POST /api/auth/supabase/login or /register
  -> backend GETs Supabase /auth/v1/user
  -> map (issuer, subject) in auth_identities
  -> create/resolve Aligned user and workspace
  -> issue opaque HttpOnly Aligned session cookie
```

Supabase is contacted when exchanging or administering identity, not for each business API call. The backend stores only the external identity mapping needed to associate the Supabase subject with an Aligned user.

### Managed first-party accounts

Firm administrators can create managed accounts. These use `password_credentials` with Argon2 hashes and the first-party backend login path. The current frontend directs managed `.aligned.com` login IDs to that backend path while normal customer email/password login uses Supabase.

### Application sessions

`auth_sessions` stores a SHA-256 token digest, user, absolute expiry, idle expiry, last-seen time and revocation state. The raw opaque token exists only in the cookie/browser exchange. Default configuration enforces a 30-minute idle and 12-hour absolute lifetime.

Production cookies are secure, HttpOnly and `SameSite=None`; local cookies use loopback-safe development settings.

### CSRF and request security

`GET /api/auth/csrf` issues a signed HttpOnly CSRF cookie and returns a header token derived from that cookie and the current application session. Mutations must send `X-CSRF-Token`. Origin validation and bounded authentication request size/content type are enforced separately.

Authentication endpoints include rate limiting and deliberately sanitize errors that could contain credentials or tokens.

### MSG91 phone verification

```text
Browser MSG91 widget
  -> MSG91 access token
  -> backend verifies Supabase subject and MSG91 result
  -> backend returns short-lived signed phone receipt
  -> registration consumes receipt for the same Supabase subject
```

The public widget token is a Vite setting. `MSG91_AUTH_KEY` is backend-only. The phone-verification skip is development-only and requires matching frontend/backend switches.

## 6. Workspace and tenant architecture

### Core hierarchy

```text
User
  └── WorkspaceMembership -> Workspace (INDIVIDUAL or FIRM)
                              ├── Clients
                              │    ├── ClientGSTINs
                              │    ├── ClientAssignments
                              │    ├── ReconciliationRuns
                              │    └── GstrMergeRuns
                              ├── WorkspaceInvites
                              ├── OwnershipTransfers
                              └── AuditEvents
```

Roles are `OWNER`, `ADMIN` and `ACCOUNTANT`. Membership status and user/workspace status must be active.

- OWNER and ADMIN operate across clients in their workspace.
- ACCOUNTANT must have an active `client_assignments` row for an active client.
- New financial work requires an active client and, when selected, an active GSTIN owned by that client/workspace.
- Removed/suspended membership, archived client, inactive GSTIN and cross-workspace identifiers are denied.

Composite foreign keys and scoped queries reduce accidental cross-tenant association. Authorization remains application-layer service logic; this architecture does not claim PostgreSQL row-level security.

Ownership transfer protects the current owner from direct demotion/removal and uses explicit pending/completed/cancelled/expired states. Audit events capture important workspace, reconciliation and GSTR lifecycle actions without storing arbitrary unbounded payloads.

## 7. Database architecture

The application uses PostgreSQL through SQLAlchemy 2 and SQLModel. Schema changes use Alembic. The current linear migration chain is:

```text
20260831_01 -> 20260831_02 -> 20260831_03
            -> 20260901_04 -> 20260901_05 -> 20260901_06
            -> 20260902_07 -> 20260902_08 -> 20260905_09
            -> 20260910_10 -> 20260910_11 -> 20260914_12
            -> 20260923_13 -> 20260923_14
```

Major table groups are:

- identity and authentication: `users`, `auth_identities`, `password_credentials`, `auth_sessions`, `auth_action_tokens`, `auth_security_events`;
- tenancy: `workspaces`, `workspace_memberships`, `workspace_invites`, `ownership_transfers`;
- clients/access: `clients`, `client_gstins`, `client_assignments`;
- reconciliation: `reconciliation_runs`, `run_operations`, `reconciliation_session_state`;
- GSTR worker lifecycle: `gstr_merge_runs`;
- Tally Export configuration and lifecycle: profiles, party-ledger mappings, purchase-ledger mappings, durable candidate snapshots, export batches and immutable export items;
- accountability: `audit_events`.

### Reconciliation persistence

`reconciliation_runs` is durable ownership/history metadata. `run_operations` records mutation intent, expected version, fingerprint and publication/failure state. The detailed reconciliation snapshot is serialized, compressed and kept in the configured `SessionStore`.

Supported stores are:

- memory — local/test only;
- JSON file — explicit local/restart testing only;
- Upstash Redis — shared production-capable optional adapter with bounded compare-and-swap retries;
- PostgreSQL — shared production-capable store using `reconciliation_session_state` and row locks.

Production refuses memory and JSON-file session backends. `.env.example` selects PostgreSQL.

### Database roles and migrations

Runtime and migration database URLs/roles are separate. Migrations define grants needed by the runtime role. API and worker startup validate the database revision but never apply migrations automatically.

## 8. File upload and storage architecture

### Reconciliation uploads

```text
Browser multipart upload
  -> FastAPI /api/inspect
  -> bounded stream to isolated session scratch directory
  -> persist private source artifact (local or S3)
  -> parse and persist canonical inspection rows in session state
  -> delete raw local working files after inspection
```

The combined reconciliation upload limit is 25,000,000 bytes. `.xls`, `.xlsx` and `.json` are accepted by the parser. File suffix, filename safety, structure and parsing errors are checked server-side.

### Storage backends

`OBJECT_STORAGE_BACKEND` is either `local` or `s3`.

- Local storage resolves beneath a controlled scratch directory and rejects arbitrary paths.
- S3 state stores opaque `s3://bucket/key` references, not AWS credentials or public URLs.
- Keys are generated from configured prefix plus session or tenant/client/run ownership.
- S3 uploads can request server-side encryption.
- Exact references are validated before stat, download, publication or deletion.

`REQUIRE_S3_OBJECT_STORAGE=true` makes startup fail if the backend is not S3.

### GSTR direct uploads

In S3 mode the API creates a short-lived presigned PUT for each period. Issued targets include run/period/expected-size metadata. Submission performs HEAD/stat checks and verifies metadata, content type, individual size and aggregate size before setting `QUEUED`.

Local development instead exposes an authenticated streaming PUT route. It is unavailable when the object backend is S3 or the environment is not development.

### Downloads

All download requests are first authorized through application routes. Delivery can then be:

- backend file/stream response;
- `307` redirect to a short-lived presigned S3 GET URL;
- `307` redirect to a signed CloudFront URL.

Choosing direct delivery changes transport, not authorization. Objects remain private.

## 9. GST reconciliation architecture

```text
Accounts + GSTR-2B (+ optional debit notes)
  -> upload transport validation
  -> sheet/header/column inspection
  -> canonical ParsedInvoiceRecord values and source provenance
  -> optional user mapping overrides and server-side reparse
  -> purchase reconciliation engine
  -> notes reconciliation engine
  -> categorized summaries and manual-match candidates
  -> versioned session publication + durable run metadata
  -> paginated UI results / Excel / JSON / notes Excel
```

`reconciliation_orchestrator.py` owns the end-to-end session transformation. Engines do not call UI code or object-storage APIs directly. Export generation is separated from matching, although the orchestrator requests and persists the artifacts.

Authenticated processing uses `owned_reconciliation.py`, which stages work in an isolated attempt directory/store, records an operation receipt, and publishes a complete snapshot only after guarded validation. Recovery endpoints resolve pending/unresolved operations without adopting anonymous legacy sessions.

## 10. Purchase reconciliation engine

The purchase path begins with source detection and canonical parsing in `file_parser.py`. Supporting services handle sheet/header/column detection, normalization, amendments, cancellations and provenance.

At a useful architectural level the engine:

1. separates Accounts purchase records and applicable GSTR invoice categories;
2. resolves amendments/cancellations according to source metadata;
3. groups and indexes candidates by normalized identities;
4. applies ordered matching strategies rather than unconstrained fuzzy pairing;
5. compares dates, taxable values, totals and tax components;
6. preserves parse-invalid/missing financial evidence instead of coercing it into trustworthy zeroes;
7. classifies matched, mismatched, missing, duplicate, cancelled and review results;
8. attaches an audit trail containing source rows and explanation.

Detailed thresholds and matching semantics belong in the engine, tests and the focused root README. Architecture changes must not reorder or broaden matching without regression evidence.

## 11. Notes reconciliation engine

The notes path is implemented separately in `notes_reconciliation_engine.py`. It compares Accounts debit notes with GSTR CDNR/CDNRA credit-note records, including amended-note metadata and note direction.

It produces its own summary, missing-note sets and export. It intentionally does not inherit every purchase fallback. Reusing purchase infrastructure is acceptable only where note-specific behavior remains explicit.

## 12. Manual matching architecture

```text
Ambiguous result in versioned session
  -> backend exposes bounded candidates
  -> user selects result_id + candidate_id
  -> POST /api/manual-match with CSRF/idempotency/version context
  -> tenant/run authorization
  -> candidate and current-state validation
  -> manual match mutation
  -> counts/results/exports refreshed
  -> new versioned snapshot published
```

Candidate IDs refer to server-generated current state. A client cannot submit arbitrary financial rows or bypass candidate validation.

## 13. GSTR Consolidation architecture

```text
GSTR workspace UI
  -> create tenant-owned run (UPLOADING)
  -> API returns one private upload target per required period
  -> browser PUTs 4 quarterly or 12 monthly .xlsx files
  -> submit with idempotency key + expected version
  -> API verifies private objects and marks QUEUED
  -> worker claims row and marks PROCESSING
  -> download to isolated worker temp directory
  -> package safety + workbook inspection
  -> GSTIN/FY/period/cross-file validation
  -> source manifest + Excel row-limit preflight
  -> write-only merge
  -> reopen and independently verify output
  -> upload private result
  -> lease/version-guarded COMPLETED publication
  -> authorized download
```

The integrated processor is `backend/app/services/gstr_merge/processor.py`. It supplies expected client GSTIN, financial year and period values to validation; it does not fabricate missing workbook metadata.

`gstr_merge_runs` is the durable lifecycle record and includes tenant identifiers, mode, FY, status, engine version, version counter, manifest, output metadata, attempts, lease fields, safe failure information and retention/deletion timestamps.

The standalone `GSTR File Merging Tool/` instead accepts multipart files and performs the merge synchronously in one request. It is useful for historical/core context only and must not be used to describe current integrated orchestration.

## 14. Background worker architecture

The worker entry point is `python -m backend.app.workers.gstr_merge_worker`.

### Claim and lease

- `claim_next_run` selects eligible queued work with `FOR UPDATE SKIP LOCKED`.
- Claiming assigns a unique lease token, lease expiry, increments attempt count and lifecycle version, and changes status to `PROCESSING`.
- A heartbeat extends the lease without changing the lifecycle version.

### Processing and publication

Each attempt uses a unique OS temporary directory. Inputs are streamed from private storage while hashing. The processor validates and merges them, and the output is uploaded to a server-owned reference.

Completion or failure locks the run and confirms status, lease token and expected version. This fence prevents a worker that lost its lease from publishing stale output.

### Retry and recovery

Retryable infrastructure failures return the run to `QUEUED` with a configured backoff while attempts remain. Deterministic workbook failures become terminal `FAILED`. Expired `PROCESSING` leases are requeued or terminally failed by recovery logic.

The API never performs the CPU-heavy merge. It creates and observes jobs; the worker processes them.

## 15. Export architecture

Reconciliation exports are generated from the authoritative session summaries:

- `excel_exporter.py` creates the purchase reconciliation workbook;
- `json_exporter.py` creates the structured reconciliation JSON;
- the Excel exporter creates a separate notes workbook when notes results exist;
- `reconciliation_orchestrator.py` writes temporary artifacts and publishes them through `object_storage.persist_artifact`;
- export routes authorize the run/session and return an artifact response.

The current working tree contains pre-existing, uncommitted Excel schema/format changes. This architecture describes the export pipeline, not those changes as a committed contract.

GSTR output is generated by the worker as a separately verified consolidated `.xlsx` and delivered from its `gstr_merge_runs.output_object_ref`.

### Tally Export V1

```text
Purchase reconciliation “Not in Tally” result
  -> immutable durable purchase candidate snapshot
  -> user selects stable candidate IDs
  -> TallyPrime 7.1 profile + exact party/purchase-ledger mappings
  -> server reloads candidates and validates the complete selection
  -> idempotent export batch generation
  -> accounting-mode TallyPrime 7.1 Purchase XML
  -> private local/S3 artifact
  -> tenant-authorized secure download
  -> manual TallyPrime import
  -> durable batch and immutable item history
```

The React UI presents validation results but does not reproduce accounting rules. Generation reloads authoritative durable candidates and revalidates immediately; one blocked item blocks the complete selected batch. HTTP `Idempotency-Key`, a durable request fingerprint and database constraints prevent duplicate artifacts on uncertain retries. The same key conflicts if the effective selection, profile or resolved mappings changed.

OWNER and ADMIN can configure the profile and purchase-ledger mappings. An assigned ACCOUNTANT sees profile and purchase mappings read-only, can confirm party mappings, and can validate, generate, download and inspect history. The backend remains the authorization boundary.

Generated XML is stored privately and downloaded through the authorized backend artifact response. The frontend never constructs an S3/object path. Batch history and immutable item display snapshots remain available after temporary reconciliation details expire.

Supported V1 is deliberately narrow: TallyPrime 7.1, Purchase invoices, CGST/SGST, IGST, multi-rate purchases and accounting-mode offline XML import. TallyPrime 7.0, Debit Notes, inventory/item vouchers, automatic ledger discovery/creation, live synchronization and automatic import confirmation are not supported. RCM, cess, amendment and cancellation cases remain blocked when required by backend validation.

## 16. Deployment architecture

### Repository-supported production topology

```text
Vercel
  └── Vite frontend build
         |
         v
AWS EC2
  ├── FastAPI API process
  └── GSTR worker process
         |
         +--> Supabase PostgreSQL
         +--> Supabase Auth
         +--> MSG91
         +--> private AWS S3
                └── optional signed CloudFront delivery
```

This is the current intended/supported production architecture expressed by `.env.example`, `frontend/vercel.json`, the worker entry point and `docs/deployment.md`.

**Deployment state:** repository inspection cannot confirm that any specific Vercel, EC2, Supabase, MSG91, S3 or CloudFront environment is currently live. Do not label it deployed without checking the external environment.

### Committed deployment material

- `frontend/vercel.json` builds the Vite frontend to `dist`.
- `alembic.ini` and migration files define database migration behavior.
- `.env.example` defines safe environment contracts.
- `docs/deployment.md` describes migration, feature-gate, worker and rollout steps.

No Dockerfile, Compose file, systemd unit, Nginx configuration, Terraform or other infrastructure-as-code is committed. EC2 process management and ingress configuration are external operational responsibilities.

### Local development

Local development normally uses Vite on port 5173 and FastAPI/Uvicorn on port 8000. PostgreSQL must be configured explicitly. Local object storage stays beneath scratch; development may enable the authenticated local GSTR upload adapter. The demo bypass is accepted only in development when both code conditions and configuration allow it.

### Optional configuration

- Upstash Redis may replace PostgreSQL for shared reconciliation session snapshots.
- S3 presigned GET or CloudFront signed URLs may replace backend-streamed artifact delivery.
- These options do not change database tenancy or route authorization.

Historical Cloud Run, R2 and Vercel-backend proposals are not current architecture.

## 17. External integrations

### Supabase

- Auth: browser identity, token verification and account administration.
- PostgreSQL: application database and supported session-state backend.
- Provider credentials and database URLs are external secrets/configuration.

### MSG91

- Browser widget handles OTP interaction.
- Backend verifies the provider token using a private auth key.
- Verification produces an Aligned-signed receipt bound to the Supabase subject.

### AWS S3

- Private reconciliation uploads/reports and GSTR inputs/outputs.
- Presigned browser PUT for GSTR.
- Optional presigned GET delivery.
- Bucket policy, CORS, lifecycle and IAM are external.

### CloudFront

- Optional signed private-artifact delivery.
- Requires an HTTPS base URL, key-pair ID and backend-only private key file.

### Upstash Redis

- Optional shared reconciliation session-state backend using REST and compare-and-swap mutation.
- Not required when PostgreSQL session storage is selected.

### Vercel

- Committed configuration covers the frontend static build only.
- The current architecture does not treat Vercel as the FastAPI or worker host.

## 18. Security and trust boundaries

1. **Browser to identity provider:** Supabase and MSG91 tokens are untrusted until backend/provider verification succeeds.
2. **Identity to application session:** The backend maps a verified external identity to an active Aligned user and issues an independent opaque session.
3. **Browser to API:** Cookies establish session presence; origin and CSRF checks protect mutations. CORS is not authorization.
4. **API to tenant data:** Workspace membership, role, assignment, client/GSTIN status and resource ownership are checked before financial state access.
5. **API to PostgreSQL:** The runtime role should have only migration-granted access; the migration role is separate.
6. **API to object storage:** Shared state carries opaque private references. The backend validates reference ownership before stat/download/delete.
7. **Browser to S3:** Presigned GSTR PUT URLs are short-lived and constrained by exact key/content type/metadata. Submission performs independent verification.
8. **API to worker:** PostgreSQL job state is the handoff. Lease tokens and optimistic versions fence processing/publication.
9. **Worker scratch:** Workbooks are processed in per-attempt OS temporary directories and cleaned after processing.
10. **Artifact delivery:** Authorization happens before backend streaming or a signed direct-delivery redirect.
11. **Configuration:** Secrets belong in backend/deployment environment settings, never Markdown, source control or `VITE_*` variables.
12. **Tally Export:** The browser supplies stable IDs and exact user-confirmed ledger names, but durable candidates and backend validation remain authoritative. Private artifacts are never addressed directly by the browser.

Application authorization is required even if database constraints, S3 IAM and provider policies also protect data. No single layer is treated as the sole tenant boundary.

## 19. Important current data flows

### Authentication

```text
Email/password or Google
  -> Supabase session
  -> backend token exchange
  -> Supabase /auth/v1/user verification
  -> auth_identities + users + memberships
  -> auth_sessions digest
  -> HttpOnly Aligned cookie
  -> /api/me and tenant-scoped business APIs
```

Managed first-party path:

```text
Managed login ID + password
  -> backend Argon2 credential verification
  -> active user/membership checks
  -> same Aligned application-session mechanism
```

### Reconciliation

```text
Authorized workspace/client context
  -> multipart source upload
  -> inspect/detect/parse
  -> persisted versioned session snapshot
  -> optional mapping overrides
  -> purchase + notes engines
  -> summaries, candidates and paginated results
  -> optional manual match mutation
  -> private Excel/JSON artifacts
  -> authorized download
```

### GSTR consolidation

```text
Authorized workspace/client/GSTIN
  -> create UPLOADING run
  -> direct private uploads
  -> submit and verify -> QUEUED
  -> worker claim -> PROCESSING
  -> validate/merge/verify/upload
  -> COMPLETED or FAILED
  -> authorized result download / expiry / deletion
```

### Tally Export

```text
Durable Not-in-Tally candidates
  -> stable candidate-ID selection
  -> profile and exact ledger mappings
  -> authoritative server validation
  -> idempotent TallyPrime 7.1 XML batch
  -> private artifact and secure download
  -> manual import into TallyPrime
  -> durable history independent of temporary session data
```

### File storage and download

```text
Local development:
API/worker -> controlled scratch path -> authorized FileResponse

Production S3:
browser --presigned PUT--> private S3 object
API/worker <---- SDK -----> private S3 object
authorized download -> backend stream
                    or short-lived signed S3/CloudFront redirect
```

## 20. Existing documentation relationship

- `docs/PROJECT_CONTEXT.md` is the paired living status/scope document. Read it before this file for current flags, limitations and pending operational work.
- `README.md` remains useful for detailed reconciliation business behavior, but parts of its product, persistence, limits and deployment narrative predate current code.
- `docs/deployment.md` is the focused rollout/runbook companion for Vercel frontend, EC2 API/worker, Supabase PostgreSQL/Auth and S3.
- `docs/MSG91_OTP_WIDGET.md` is the focused phone-verification integration contract.
- `docs/AUTH_DATABASE_IMPLEMENTATION_PLAN.md` records earlier design decisions and phase planning; it is not the current architecture source.
- `docs/DATABASE_DEVELOPMENT.md` documents initial migration safety and database foundations; its schema snapshot is historical.
- `docs/LOCAL_AUTH_DEVELOPMENT.md` records local authentication/workspace milestones and predates the complete Supabase hybrid flow.
- `docs/SCALING_ARCHITECTURE_REPORT.md` is a historical audit/proposal. Its Cloud Run, R2 and older Vercel-backend assumptions were not the architecture ultimately integrated.
- `GSTR File Merging Tool/README.md` documents the standalone merger core, not Aligned's durable tenant-owned worker workflow.

When any of these conflict with current code, update this document from inspected implementation and explicitly preserve historical documents as history rather than silently treating them as current.
