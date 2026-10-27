# Aligned — Project Context

- **Last verified date:** 24 September 2026
- **Last verified Git commit:** `e99b931` plus the uncommitted Tally Export frontend integration described here.
- **Working tree status:** **DIRTY.** The Tally Export frontend, its tests, and these two living-document updates are uncommitted. The pre-existing untracked `scratch/` directory was not touched.
- **Verification rule:** This living document describes inspected implementation and configuration. Code wins over plans, historical documents, remembered deployments, and assumptions.
- **Verification scope:** Frontend Tally tests, the complete frontend suite and the production frontend build were run for this integration; backend regression results are recorded in the implementing task report.

## Status vocabulary

- **IMPLEMENTED** — present in the current repository.
- **VALIDATED** — covered by repository tests or a recorded, current validation result. Test presence is not the same as a fresh test run.
- **FEATURE-FLAGGED** — implemented but gated by frontend or backend configuration.
- **DEPLOYED** — confirmed running in a named environment. Repository configuration alone does not prove deployment.
- **PARTIAL** — present, but with a material limitation or missing operational component.
- **PENDING** — required current work is identifiable but not complete.
- **EXTERNAL CONFIGURATION** — implemented in code but dependent on infrastructure, credentials, provider settings, or process supervision outside this repository.

## 1. Product overview

Aligned is a multi-tenant GST reconciliation application for accounting practices and their clients. Its current implemented product scope is:

- compare Accounts/Tally purchase data with GSTR-2B data;
- reconcile purchase invoices and GST credit/debit notes using separate engines;
- inspect unfamiliar workbooks and allow column-mapping corrections;
- review categorized results and make supported manual matches;
- persist workspace/client ownership and reconciliation run history;
- export reconciliation reports;
- export selected durable “Not in Tally” purchase invoices as validated TallyPrime 7.1 accounting-mode XML;
- consolidate strict monthly or quarterly GSTR workbooks asynchronously.

The integrated application is `Yuvraj-Tool/`. The sibling `GSTR File Merging Tool/` repository is a standalone predecessor/reference and is not the source of truth for the current product.

Tally export is an offline workflow: Aligned generates and securely downloads XML, while the user imports it manually into TallyPrime. The web application does not connect to or confirm acceptance from a local Tally installation.

## 2. Current product scope

| Capability | Status | Current reality |
|---|---|---|
| Public landing and account UI | **IMPLEMENTED** | React workspace shell includes landing, sign-in, sign-up, recovery and authenticated pages. |
| Supabase email/password and Google identity | **IMPLEMENTED / EXTERNAL CONFIGURATION** | Browser authenticates with Supabase; backend verifies the Supabase access token during account connection. |
| Backend application sessions | **IMPLEMENTED** | Opaque HttpOnly cookie backed by PostgreSQL `auth_sessions`. |
| Workspace, client and team management | **IMPLEMENTED** | Individual/Firm workspaces, roles, invites, managed accounts, assignments and ownership transfer are present. |
| Purchase reconciliation | **IMPLEMENTED** | Dedicated parser, normalizer, matching engine, classification, pagination and exports. |
| Notes reconciliation | **IMPLEMENTED** | Separate note-specific matching engine and report. |
| Manual matching | **IMPLEMENTED** | Candidate selection and guarded state mutation are present. |
| Durable reconciliation ownership/history | **IMPLEMENTED** | PostgreSQL run metadata and operation receipts wrap versioned session snapshots. |
| GSTR consolidation | **IMPLEMENTED / FEATURE-FLAGGED / EXTERNAL CONFIGURATION** | UI, APIs, PostgreSQL run lifecycle, private upload, worker and output download are present; both UI and server gates default off. |
| Production object storage | **IMPLEMENTED / EXTERNAL CONFIGURATION** | Private S3 adapter, optional signed S3/CloudFront delivery and local development adapter exist. |
| Production process definitions | **PARTIAL** | The worker entry point and deployment runbook exist, but no committed systemd, container, Nginx or infrastructure-as-code definitions exist. |
| Production email delivery | **PARTIAL** | Managed-account and ownership flows use a local private mail adapter; no external transactional email provider is implemented. |
| TallyPrime voucher export | **IMPLEMENTED / VALIDATED** | Selected Not-in-Tally purchase invoices are configured, mapped, server-validated, rendered as TallyPrime 7.1 accounting-mode XML, persisted privately, downloaded securely and retained in durable export history. CGST/SGST, IGST and multi-rate Purchase imports have recorded real TallyPrime 7.1 validation. |
| Verified live deployment state | **EXTERNAL CONFIGURATION** | The repository expresses an intended topology but cannot prove what is currently deployed. |

### Validation evidence

The repository contains broad pytest coverage for parsing, matching, exports, security, authentication, workspaces, owned reconciliation, PostgreSQL schema behavior, object storage and the GSTR API/worker/core. PostgreSQL-only tests use the `postgres` marker and disposable `TEST_DATABASE_URL` contract. The frontend uses Vitest/Testing Library for workspace, upload, result, management, GSTR and API-client behavior.

Because this documentation task did not run those suites, the current working tree as a whole is not newly labeled **VALIDATED** here. In particular, the pre-existing Excel exporter changes require their own test run before being treated as validated.

## 3. Current modules

### Frontend

- Public landing and developer-demo entry points.
- Supabase-backed sign-in/sign-up/recovery UI.
- Workspace dashboard and workspace selector.
- Client, GSTIN, team, managed-account and assignment management.
- Ownership transfer, activity logs and data-management settings.
- Reconciliation upload, inspection, mapping, results, review and export UI.
- Not-in-Tally candidate selection plus a role-aware Tally setup, exact ledger mapping, validation, generation/download and durable-history workflow.
- Feature-gated GSTR Consolidation upload, queue/progress and result UI.

### Backend

- FastAPI application with authentication, origin/CSRF and tenant boundaries.
- PostgreSQL/SQLAlchemy/SQLModel persistence and Alembic migrations.
- Workspace, client, role, assignment and data-management services.
- Reconciliation parsing, normalization, matching, notes, manual matching and exporters.
- Durable Tally candidate/profile/mapping/batch persistence, pre-export validation and TallyPrime 7.1 Purchase XML generation APIs.
- Configurable reconciliation session store and object storage.
- Durable GSTR run APIs and a separate worker process.

## 4. GST reconciliation

**IMPLEMENTED.** The reconciliation workflow accepts an Accounts file and a GSTR file, plus an optional debit-notes file. Supported source formats are `.xls`, `.xlsx` and `.json`.

At a high level it:

1. streams and bounds the uploads;
2. detects sheets, headers and semantic columns;
3. stores inspection data and canonical rows in a session snapshot;
4. accepts mapping overrides;
5. reparses from persisted canonical rows where needed;
6. runs purchase and notes reconciliation separately;
7. publishes versioned results and generated artifacts;
8. exposes categorized, searchable, sortable and paginated results.

Reconciliation engine behavior is more detailed than this context file. Preserve the established business rules in `README.md` and the regression tests when changing it.

## 5. Purchase reconciliation

**IMPLEMENTED.** `backend/app/services/reconciliation_engine.py` coordinates matching and classification using supporting normalization, amendment, cancellation and provenance services.

The current engine includes:

- GSTIN and invoice-number normalization;
- exact and normalized invoice matching;
- constrained suffix/last-digit matching;
- date and financial evidence;
- invoice-number mismatch handling;
- taxable/date/financial classifications;
- missing-in-Accounts and missing-in-GSTR categories;
- potential duplicates, cancelled vouchers and review states;
- source provenance and financial parse issues.

Do not silently combine purchase and notes semantics: their rules intentionally differ.

## 6. Notes reconciliation

**IMPLEMENTED.** `backend/app/services/notes_reconciliation_engine.py` reconciles Accounts debit notes against GSTR CDNR/CDNRA records. It maintains separate counts, classifications and an optional notes-specific Excel report.

Notes do not simply reuse every purchase fallback. Changes must preserve note direction, original/amended note metadata, duplicate behavior and note-specific missing-record handling.

## 7. Manual matching

**IMPLEMENTED.** Ambiguous results can expose candidates to the UI. A user selects a candidate and submits `/api/manual-match`; the backend validates the result and candidate identifiers, applies the decision through `manual_match_service.py`, recomputes affected output and publishes a new versioned session state.

In authenticated mode, manual changes pass through tenant authorization, operation receipts, optimistic version checks and idempotency handling. The browser must not be treated as the authority for a match.

## 8. GSTR consolidation

**IMPLEMENTED / FEATURE-FLAGGED / EXTERNAL CONFIGURATION.** The integrated workflow is not the synchronous standalone merger.

Current lifecycle states are:

`UPLOADING → QUEUED → PROCESSING → COMPLETED | FAILED`

Runs may also become `EXPIRED` or `DELETED`.

Current behavior:

- monthly mode requires exactly 12 `.xlsx` files;
- quarterly mode requires exactly 4 `.xlsx` files;
- the selected client and GSTIN must be active and accessible to the user;
- the API creates a tenant-owned `gstr_merge_runs` row and upload manifest;
- S3 mode returns short-lived presigned PUT targets; local development uses an authenticated PUT adapter;
- submission verifies object existence, size, content type and issued metadata before queueing;
- a separate worker claims the run, validates workbook packages and metadata, merges, independently verifies the output and stores it privately;
- the UI lists durable runs and polls selected-run status;
- completed output is downloaded through an authorized backend route.

The browser flag is `VITE_GSTR_CONSOLIDATION_ENABLED`; the authoritative server flag is `GSTR_CONSOLIDATION_ENABLED`. Both default to `false` in `.env.example`.

## 9. Upload, inspection and mapping flow

**IMPLEMENTED.** Legacy reconciliation uploads pass through FastAPI multipart handling. The combined request limit is `25,000,000` bytes. Filenames are sanitized, temporary files stay below the session scratch root, and supported extensions are enforced.

Inspection performs sheet/header/column detection and returns previews and mappings. Mapping overrides are applied server-side to persisted canonical sheet rows before reconciliation; the system does not rely on the browser to reinterpret financial source data.

GSTR consolidation deliberately uses a different upload path: direct private-object upload in S3 mode, followed by server-side verification before a run can enter the queue.

## 10. Export capabilities

**IMPLEMENTED:**

- purchase reconciliation Excel workbook;
- structured reconciliation JSON;
- notes-specific Excel workbook when notes results exist;
- consolidated GSTR `.xlsx` output;
- workspace/member/activity data exports through workspace APIs.
- TallyPrime 7.1 accounting-mode Purchase voucher XML for selected durable Not-in-Tally candidates, with secure artifact download and durable batch/item history.

The JSON reconciliation download is bounded to `3,500,000` bytes in the current pilot transport. Larger JSON responses are rejected with guidance to use Excel.

**Tally Export V1 support:** TallyPrime 7.1 only; purchase invoices; intra-state CGST/SGST, inter-state IGST and multi-rate purchases; accounting-mode offline XML import. It does not support TallyPrime 7.0, Debit Note export, inventory/item-level vouchers, automatic ledger creation/discovery, live synchronization, automatic import-status confirmation, or transactions blocked by the backend for unsupported RCM, cess, amendment or cancellation conditions.

## 11. Authentication

**IMPLEMENTED / EXTERNAL CONFIGURATION.** Authentication has two identity paths:

1. Normal customer accounts use Supabase Auth for email/password or Google identity. The backend verifies the access token with Supabase, maps `(issuer, subject)` through `auth_identities`, and issues its own application session.
2. Managed first-party accounts use local password credentials, primarily for firm-created `.aligned.com` users.

Business API calls use the backend session cookie, not the Supabase token. This prevents every business request from depending on Supabase availability.

Application sessions have absolute and idle expiry, are stored as hashes in PostgreSQL, can be revoked, and are issued as HttpOnly cookies. Mutations use origin checks and a signed CSRF cookie/header token tied to the current application session.

Phone verification uses the MSG91 widget. The backend verifies the MSG91 token and binds a short-lived signed phone receipt to the verified Supabase subject before registration completion.

## 12. Workspaces, clients and roles

**IMPLEMENTED.** Workspace types are `INDIVIDUAL` and `FIRM`. Membership roles are `OWNER`, `ADMIN` and `ACCOUNTANT`.

- OWNER and ADMIN have workspace-wide client access.
- ACCOUNTANT access is limited to active assigned clients.
- Inactive clients and inactive GSTINs are rejected for new financial work.
- Suspended or removed memberships do not authorize access.
- Client IDs, GSTIN IDs, runs and artifacts are checked within the selected workspace.
- Ownership transfers protect the sole owner and use a two-party lifecycle.
- Important mutations emit `audit_events`.

The authorization service, not the frontend menu, is the security boundary.

## 13. Database and persistence

**IMPLEMENTED.** PostgreSQL is accessed through SQLAlchemy/SQLModel. Alembic has a linear chain from `20260831_01` through current head `20260923_14`.

Current table groups include:

- identity/auth: `users`, `auth_identities`, `password_credentials`, `auth_sessions`, `auth_action_tokens`, `auth_security_events`;
- tenancy: `workspaces`, `workspace_memberships`, `workspace_invites`, `ownership_transfers`;
- clients: `clients`, `client_gstins`, `client_assignments`;
- reconciliation: `reconciliation_runs`, `run_operations`, `reconciliation_session_state`;
- GSTR: `gstr_merge_runs`;
- Tally Export: profiles, exact party/purchase-ledger mappings, durable candidate snapshots, export batches and immutable export items;
- auditing: `audit_events`.

Reconciliation run ownership and history are durable, while the detailed working snapshot has an expiry and lives in the configured session store. Production configuration permits only PostgreSQL or Upstash session stores; `.env.example` selects PostgreSQL.

The application validates a supported database revision at startup when authentication is enabled. It does not run migrations automatically.

## 14. Storage

**IMPLEMENTED / EXTERNAL CONFIGURATION.** `backend/app/services/object_storage.py` supports:

- `local` private storage beneath controlled scratch paths for development;
- private `s3` object storage for production;
- server-side encryption configuration;
- tenant/run-derived object keys and reference validation;
- presigned GSTR uploads;
- backend-streamed downloads;
- optional presigned S3 or signed CloudFront delivery;
- explicit artifact cleanup.

When `REQUIRE_S3_OBJECT_STORAGE=true`, backend startup fails rather than silently retaining data locally.

S3 bucket policy, CORS, lifecycle rules, credentials and optional CloudFront keys are external configuration and must not be committed.

## 15. Background workers

**IMPLEMENTED / EXTERNAL CONFIGURATION.** The GSTR worker starts with:

```text
python -m backend.app.workers.gstr_merge_worker
```

It is a distinct long-running process. It validates database/storage configuration, recovers expired leases, claims queued rows with database locking, renews leases, downloads inputs to isolated temporary storage, runs deterministic validation/merge/integrity checks, uploads the result and commits a guarded terminal state.

Retries are bounded and configured through environment variables. Lease token and version checks prevent a stale worker from publishing over a newer attempt.

No committed process supervisor definition exists; EC2 service supervision is deployment work outside the Python entry point.

## 16. Current deployment and infrastructure

The current repository-supported production topology is:

- **Frontend:** Vite static build with `frontend/vercel.json`, intended for Vercel.
- **API:** FastAPI process intended to run on EC2.
- **Worker:** separate GSTR worker process intended to run on EC2.
- **Database/Auth:** Supabase PostgreSQL and Supabase Auth.
- **Private artifacts:** AWS S3.
- **Artifact acceleration:** optional CloudFront signed delivery.

**DEPLOYED status is not verifiable from the repository.** Treat this as the current intended/supported production configuration, not proof that each component is live.

Only the frontend Vercel configuration and Alembic configuration are committed. There is no committed Dockerfile, systemd unit, Nginx configuration or infrastructure-as-code stack.

Local development uses Vite on `localhost:5173`, FastAPI on `localhost:8000`, an explicitly configured PostgreSQL database, and may use local private-object storage.

## 17. External integrations

| Integration | Status | Purpose |
|---|---|---|
| Supabase Auth | **IMPLEMENTED / EXTERNAL CONFIGURATION** | Customer email/password and Google identity; backend token verification and account administration. |
| Supabase PostgreSQL | **IMPLEMENTED / EXTERNAL CONFIGURATION** | Application database and supported reconciliation session state. |
| MSG91 | **IMPLEMENTED / EXTERNAL CONFIGURATION** | Indian mobile OTP/widget verification during onboarding. |
| AWS S3 | **IMPLEMENTED / EXTERNAL CONFIGURATION** | Private uploads, reconciliation artifacts and GSTR input/output objects. |
| CloudFront | **OPTIONAL / EXTERNAL CONFIGURATION** | Signed direct delivery of private artifacts. |
| Upstash Redis | **OPTIONAL** | Supported alternate shared reconciliation session store; not the selected production example. |
| Vercel | **CONFIGURED** | Frontend static build/hosting configuration. |

## 18. Current feature flags

| Variable | Layer | Effect |
|---|---|---|
| `AUTH_ENABLED` | Backend | Enables authentication configuration and boundaries. |
| `VITE_AUTH_ENABLED` | Frontend | Enables credentialed API behavior. |
| `AUTH_RECONCILIATION_ENABLED` | Backend | Enables authenticated owned reconciliation. |
| `GSTR_CONSOLIDATION_ENABLED` | Backend | Authoritative switch for creating/submitting new GSTR work. |
| `VITE_GSTR_CONSOLIDATION_ENABLED` | Frontend | Shows GSTR navigation/workflow; requires a frontend rebuild when changed. |
| `ALLOW_DEMO_MODE` | Backend | Allows development-only demo financial requests. |
| `VITE_DEMO_MODE` | Frontend | Exposes the demo entry point. |
| `ALLOW_PHONE_VERIFICATION_SKIP` | Backend | Development-only onboarding bypass. |
| `VITE_ALLOW_PHONE_VERIFICATION_SKIP` | Frontend | Displays the matching development-only bypass UI. |

Frontend flags are not security controls. Backend flags and authorization remain authoritative.

## 19. Known issues and technical gaps

- **PARTIAL:** Live production/deployment state cannot be derived from Git.
- **PARTIAL:** EC2 process supervision, reverse-proxy and infrastructure definitions are not committed.
- **PARTIAL:** Account mail is written to private local files; production email delivery is absent.
- **FEATURE-FLAGGED:** GSTR consolidation is disabled by default and needs database, worker and S3 readiness before enablement.
- **EXTERNAL CONFIGURATION:** Supabase, MSG91, S3, CORS and optional CloudFront must be configured consistently across environments.
- **LIMIT:** Reconciliation detailed state expires according to the session store TTL even though run metadata/history is durable.
- **LIMIT:** JSON reconciliation export has a 3.5 MB transport cap.
- **LIMIT:** Current capacity documentation is historical; the repository does not establish a fresh production-scale benchmark.
- **LIMIT:** Tally export is offline XML generation and download. Aligned does not discover ledgers from Tally, initiate the local import, or receive automatic acceptance/rejection feedback.
- **LIMIT:** Tally Export V1 supports TallyPrime 7.1 Purchase vouchers only; unsupported transaction conditions remain backend blockers.
- **WORKING TREE:** The frontend Tally integration and living-document updates are not committed yet; `scratch/` remains pre-existing and untouched.

## 20. Current pending work

Only evidence-backed operational work is listed here:

- **PENDING / EXTERNAL CONFIGURATION:** provision and verify the intended EC2 API/worker processes and their restart/supervision policy;
- **PENDING / EXTERNAL CONFIGURATION:** configure production Supabase, database roles, S3 policy/CORS/lifecycle and optional CloudFront delivery;
- **PENDING:** complete the manual end-to-end browser/download/import checklist against the intended deployed environment and TallyPrime 7.1 before final product sign-off;
- **PENDING:** enable both GSTR feature gates only after migration, worker and private-storage checks succeed;
- **PENDING:** select and implement production transactional email delivery if managed accounts and ownership transfer are to operate outside local development.

Do not convert historical roadmap proposals into current pending work without new approval and implementation evidence.

## 21. Important constraints and things not to break

1. Authorize workspace/client/GSTIN ownership before reading session state, run metadata or object references.
2. OWNER/ADMIN access and assigned ACCOUNTANT access must remain distinct; inactive or suspended entities must fail closed.
3. Do not trust workspace, client, GSTIN, run or artifact identifiers merely because the browser supplied them.
4. Keep CSRF validation, origin validation, secure cookie behavior and authentication request size/content-type limits intact.
5. Preserve idempotency keys and optimistic version checks for owned reconciliation and GSTR mutations.
6. Never allow a stale GSTR worker to publish without the final lease-token/version guard.
7. Keep private object keys tenant/run scoped and validate references before download or deletion.
8. Never expose Supabase secret keys, MSG91 auth keys, AWS credentials or CloudFront private keys to Vite variables or committed files.
9. Do not run Alembic automatically from API or worker startup; migrations use the migration role and runtime uses the application role.
10. Preserve workbook package-safety limits, duplicate detection, row-limit preflight and independent merged-output verification.
11. Preserve source provenance, invalid-financial-value handling and deterministic reconciliation classifications.
12. Do not unify purchase and notes engines without explicitly proving their different business semantics remain correct.
13. Do not replace durable database ownership with anonymous session IDs.
14. Keep production session state on a shared supported backend (`postgres` or `upstash`), never process memory or JSON files.
15. Tally generation must reload durable candidates, revalidate on the server and preserve HTTP idempotency; the browser is never the accounting-validation authority.
16. Tally artifacts remain private and are downloaded only through tenant-authorized backend routes.

## 22. Important code locations

| Area | Location |
|---|---|
| FastAPI composition and middleware | `backend/app/main.py` |
| API routes | `backend/app/api/routes/` |
| Authentication | `backend/app/auth/`, `backend/app/services/auth_service.py` |
| Supabase adapter | `backend/app/auth/supabase.py`, `frontend/src/services/supabase.ts` |
| MSG91 | `backend/app/services/msg91_phone_service.py`, `frontend/src/services/msg91.ts` |
| Database models | `backend/app/db/models/` |
| Alembic migrations | `backend/alembic/versions/` |
| Workspace/client authorization | `backend/app/services/workspace_service.py` |
| Owned reconciliation | `backend/app/services/owned_reconciliation.py` |
| Upload and inspection | `backend/app/api/routes/inspection.py`, `backend/app/services/file_parser.py` |
| Purchase engine | `backend/app/services/reconciliation_engine.py` |
| Notes engine | `backend/app/services/notes_reconciliation_engine.py` |
| Manual matching | `backend/app/services/manual_match_service.py` |
| Orchestration | `backend/app/services/reconciliation_orchestrator.py` |
| Exports | `backend/app/services/excel_exporter.py`, `backend/app/services/json_exporter.py` |
| Session state | `backend/app/services/session_manager.py`, `backend/app/services/session_store.py` |
| Object storage | `backend/app/services/object_storage.py` |
| GSTR APIs/lifecycle | `backend/app/api/routes/gstr_merges.py`, `backend/app/services/gstr_merge_lifecycle_service.py` |
| GSTR core | `backend/app/services/gstr_merge/` |
| GSTR worker | `backend/app/services/gstr_merge_worker_service.py`, `backend/app/workers/gstr_merge_worker.py` |
| Tally Export backend | `backend/app/api/routes/tally_exports.py`, `backend/app/services/tally_export_*`, `backend/app/services/tally_voucher_exporter.py` |
| Frontend shell/navigation | `frontend/src/workspace/WorkspaceApp.tsx` |
| Frontend auth state | `frontend/src/workspace/AuthContext.tsx`, `frontend/src/workspace/AuthPage.tsx` |
| Frontend reconciliation | `frontend/src/workspace/ReconciliationWorkspace.tsx`, `frontend/src/components/` |
| Frontend GSTR workflow | `frontend/src/workspace/GstrMergeWorkspace.tsx`, `frontend/src/components/gstr-merge/` |
| Frontend Tally workflow | `frontend/src/components/tally-export/`, `frontend/src/components/results/ResultsDashboard.tsx` |
| Frontend API client | `frontend/src/services/api.ts`, `frontend/src/services/tallyExportApi.ts` |
| Safe configuration reference | `.env.example` |
| Frontend deployment config | `frontend/vercel.json` |

## 23. Related existing documentation

- `README.md` remains useful for detailed reconciliation behavior, but several product, persistence and deployment statements are historical.
- `docs/deployment.md` is the focused current deployment/rollout runbook.
- `docs/MSG91_OTP_WIDGET.md` is the focused MSG91/Supabase phone-verification contract.
- `docs/AUTH_DATABASE_IMPLEMENTATION_PLAN.md` is design and implementation history, not current status.
- `docs/DATABASE_DEVELOPMENT.md` documents early database foundations and migration safety; its schema snapshot is outdated.
- `docs/LOCAL_AUTH_DEVELOPMENT.md` records local auth/workspace milestones and predates the complete current hybrid identity architecture.
- `docs/SCALING_ARCHITECTURE_REPORT.md` is a historical audit/proposal. Its Cloud Run/R2/Vercel-backend architecture is not the current implementation.
- `docs/CURRENT_ARCHITECTURE.md` is the living technical description paired with this context file.
