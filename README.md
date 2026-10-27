# Aligned GST Reconciliation — End-Term Edition

Aligned is a focused accounting-automation application that compares a purchase register with GSTR-2B, classifies matches and exceptions, supports manual resolution, and regenerates Excel or JSON reports from persisted reconciliation state.

The end-term deployment architecture is React 19 + TypeScript + Vite on Vercel, FastAPI on one Render web service, and a separate Supabase PostgreSQL/Auth project. Core reconciliation does not require AWS, S3, Redis, MSG91, a persistent disk, or a background worker.

## Core workflow

1. Register or sign in through Supabase Auth.
2. Select a workspace and client.
3. Upload purchase-register and GSTR-2B files.
4. Validate files and confirm detected column mappings.
5. Run reconciliation and review categorized results.
6. Resolve ambiguous matches manually.
7. Download regenerated Excel or JSON reports.
8. Reopen an available run from client history.

## Local setup

Requires Python 3.12, Node.js, npm, and a PostgreSQL database migrated to the Alembic head.

```text
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
copy .env.example .env.development
alembic upgrade head
.venv/Scripts/python -m uvicorn backend.app.main:app --reload --port 8000
```

In another terminal:

```text
cd frontend
npm ci
copy .env.example .env.development
npm run dev
```

Use `source .venv/bin/activate` and `cp` instead of the Windows commands on Linux/macOS. Configure only a disposable or dedicated database; never point development commands at the original Aligned database.

## Configuration

Backend essentials are `APP_ENV`, `AUTH_ENABLED`, `AUTH_SECRET`, `AUTH_FRONTEND_URL`, `CORS_ORIGINS`, `DATABASE_URL`, `DATABASE_ROLE`, `SESSION_STORE_BACKEND=postgres`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, and the backend-only `SUPABASE_SECRET_KEY` when account deletion is enabled. The end-term profile requires `OBJECT_STORAGE_BACKEND=local` and `REQUIRE_S3_OBJECT_STORAGE=false`.

Frontend essentials are `VITE_API_BASE_URL`, `VITE_AUTH_ENABLED=true`, `VITE_SUPABASE_URL`, and `VITE_SUPABASE_PUBLISHABLE_KEY`. Vite variables are public and must never contain a Supabase secret/service-role key.

## Tests

```text
.venv/Scripts/python -m pytest -q -p no:cacheprovider
cd frontend
npm test
npm run build
```

PostgreSQL-marked tests require `TEST_DATABASE_URL` pointing to a disposable database. See [testing documentation](docs/TESTING.md).

## Deployment and limitations

See [architecture](docs/ARCHITECTURE.md), [database](docs/DATABASE.md), [deployment](docs/DEPLOYMENT.md), and [demo](docs/DEMO.md). Render uses ephemeral files, so uploads are parsed and removed and reports are regenerated from PostgreSQL-backed session state. The demonstration should use small sanitized files and one expensive reconciliation at a time.

This preserved repository history contains previously committed private accounting artifacts and must not be published. See [ORIGIN.md](ORIGIN.md).

---

## Historical product documentation

The material below documents the broader source product and is retained for engineering context. Where it conflicts with the end-term documentation above, the end-term documentation is authoritative.

# ALIGNED

GST reconciliation for comparing Accounts purchase records with GSTR data, with a separate Accounts debit-note versus supplier credit-note workflow. This repository contains a React application and a FastAPI backend; it is a controlled pilot, not a complete accounting or compliance platform.

Documentation audit: **28 August 2026**. Code is authoritative. **VERIFIED FROM CODE** means inspected implementation; **VERIFIED FROM TESTS** means assertions in the regression suite, not a new test run for this documentation change; **OBSERVED IN PRODUCTION AUDIT** means the earlier capacity/production audit; **INFERENCE** and **RECOMMENDATION** identify analysis and future work. Local benchmark observations are explicitly labelled separately. No architecture recommendation here is implemented by this documentation change.

For scaling decisions, read [Scaling Architecture Report](docs/SCALING_ARCHITECTURE_REPORT.md).
For the end-term Vercel + Render + Supabase setup, use the authoritative [deployment guide](docs/DEPLOYMENT.md).

## 1. What ALIGNED Is

**VERIFIED FROM CODE.** ALIGNED helps accountants and CA firms identify document matches, financial differences, missing records, ambiguous candidates and source-data problems between supplied Accounts and GSTR files. It preserves evidence for review and produces Excel and JSON reports. “Accounts” is the UI terminology; internal names such as `tally`, `Not in Tally` and `RecordSource.TALLY` remain compatibility names.

It does not file returns, determine ITC eligibility, validate GST registration against a live service, connect directly to accounting systems, or provide SaaS organizations, user accounts or reconciliation history. The export explicitly disclaims ITC/compliance determination.

**RECOMMENDATION / future vision.** A broader accounting automation platform could add client workspaces, period-aware reconciliation, history and compliance intelligence. These are roadmap items, not existing capabilities.

## 2. Current Reconciliation Scope

**VERIFIED FROM CODE.** Two logically separate engines run:

| Workflow | Accounts side | GSTR side |
|---|---|---|
| Purchases | Purchase/unknown voucher records with an invoice identifier | Authoritative B2B/B2BA purchase records |
| Notes | Accounts debit notes | Confirmed supplier credit notes from the CDNR/CDNRA workflow |

Upload Accounts and GSTR files; an additional Accounts debit-notes file is optional. Embedded note handling depends on the detected/selected Accounts data, not arbitrary cross-sheet matching. GST debit notes and uncertain note directions must not be assumed to be supplier credit notes.

Accepted upload formats are legacy Excel `.xls`, modern Excel `.xlsx`, and `.json`. The backend reads `.xls` with `xlrd` and retains the established `openpyxl` path for `.xlsx`; both Excel formats are converted into the same canonical worksheet rows before detection and mapping. Extensionless files can be recognized by content. JSON input supports an array of row objects or a workbook-like object whose sheet names map to arrays of row objects. This is **not** a promise to accept every nested GST portal JSON schema, CSV, PDF or every accounting export layout. Review detected mappings before proceeding.

Outputs are an Excel workbook, structured downloadable JSON and paginated API results. Inputs and exports have different JSON schemas.

## 3. End-to-End User Workflow

**VERIFIED FROM CODE.** [App.tsx](frontend/src/App.tsx) owns the upload, review and results stages:

1. **Upload:** select required Accounts/GSTR files and optional debit notes. The UI checks combined size and preserves selected file bytes with a stable `File` snapshot.
2. **Inspect / Review Data:** `POST /api/inspect` uploads multipart data. The backend detects worksheets, headers and semantic columns, returns warnings, mappings and up to ten preview rows per preview category. Accounts preview excludes rows without invoice identifiers but retains actual cancelled vouchers for visibility.
3. **Map:** inspect and adjust Accounts/GSTR column mappings. Reconciliation reparses stored canonical worksheet rows using overrides; it does not require the original upload to remain on the same server instance.
4. **Reconcile:** `POST /api/reconcile?session_id=...` performs parsing, amendment resolution, matching, export generation and session publication synchronously. An already reconciled session returns its existing summary.
5. **Review Results:** summary/count response comes first; the dashboard fetches pages separately. Open the comparison drawer for side-by-side values, dates, evidence and provenance.
6. **Manual Review / Manual Match:** select one of the backend-provided candidates and confirm. The server validates IDs, reviewability and consumption; counts/pages refresh after success.
7. **Export:** download Excel or JSON. Oversized JSON receives a controlled warning directing the user to Excel. Starting over deletes the current session through the session API.

Session expiry requires uploading again. There is no persistent user history or guaranteed browser-reload recovery flow.

## 4. Reconciliation Logic

**VERIFIED FROM CODE:** [purchase engine](backend/app/services/reconciliation_engine.py), [Notes engine](backend/app/services/notes_reconciliation_engine.py), [manual match service](backend/app/services/manual_match_service.py). Tests include [adversarial matching](backend/tests/test_adversarial_matching.py), [reconciliation](backend/tests/test_reconciliation.py), [taxable missing](backend/tests/test_taxable_value_missing.py) and [Notes](backend/tests/test_notes_reconciliation.py).

### Purchase eligibility and supplier identity

Accounts purchase/unknown vouchers need a nonempty original invoice identifier. Explicitly cancelled records are partitioned out before matching. Accounts duplicates are grouped by **normalized GSTIN + normalized invoice number**, without date in the duplicate key, and are not automatically consumed. Invalid nonblank financial values produce review behavior; invalid GST candidates are excluded from automatic eligibility, with exact-identity data-quality candidates surfaced for review. Accounts missing-taxable precedence is described below.

When a normalized Accounts GSTIN is nonempty, candidates must have that exact normalized GSTIN. A malformed but nonempty GSTIN does not unlock supplier-name fallback. When GSTIN is absent, normalized supplier names use RapidFuzz `token_sort_ratio >= 75`; all passing candidates enter the pool, not just the highest-scoring supplier. This is heuristic supplier evidence, not registration validation.

The following layers run in order against unused, financially eligible GST aggregates. A unique accepted automatic match consumes one aggregate. More than one eligible candidate at a layer produces Needs Review / Ambiguous and stops the chain without consumption.

| Layer | Identity / eligibility | Date requirement | Financial role | Result / consumption |
|---|---|---|---|---|
| Exact invoice | Nonempty trimmed originals equal within supplier pool | No equality gate | Classification after selection | Unique: consume; Perfect/Minor/Major subject to missing-taxable precedence |
| Normalized invoice | Nonempty normalized identifiers equal within supplier pool | No equality gate | Classification after selection | Same uniqueness and consumption rule |
| Invoice Last 4, exact GSTIN | Last four normalized characters equal; suffix length at least four | Both dates present and exactly equal | **Not a financial eligibility gate**; classify differences separately | Unique: consume, including Major or Taxable Value Missing |
| Invoice Last 4, missing GSTIN | Same suffix within supplier-name fallback pool | Both present, exactly equal | Numeric taxable and each tax-head difference must qualify as Perfect/Minor | Unique: consume; preserve missing-taxable warning if applicable |
| Invoice Last 2 | Last two normalized characters equal; length at least two; supplier pool rules unchanged | Both present, exactly equal | Perfect/Minor numeric gate in every supplier context | Unique: consume; never describe missing taxable as financially confirmed |
| Invoice Number Mismatch | Earlier invoice layers failed; exact GSTIN; exactly one Accounts row with the complete GSTIN/date/taxable/IGST/CGST/SGST signature in the full active Accounts input | Both present, exactly equal | All four financial values exactly equal; no rounding tolerance | One GST candidate: consume and Invoice Number Mismatch; multiple GST candidates: review; nonunique Accounts signature: no mismatch selection |

The suffix fields are the last **characters of the separator-stripped normalized identifier**, not necessarily its numeric suffix. A separate trailing-number field exists but does not replace these suffix keys.

**Missing-value nuance:** some weak-identity and mismatch helpers compare canonical numeric values without testing taxable presence separately. Presence is retained independently, and `_build_result` overrides the primary purchase status to Taxable Value Missing. Do not infer that a missing source value was confirmed merely because numeric comparisons admitted a candidate.

**Strong Evidence fallback:** only when the supplier pool has no available candidates, Accounts GSTIN is missing and a normalized invoice exists. Search globally for unused eligible GST records with that normalized invoice. Exactly one must exist **before** checking Perfect/Minor financial agreement. The candidate remains unconsumed and is presented for review, not auto-matched. Equal dates strengthen the explanation; different or missing dates add a warning. Missing-taxable precedence can replace the primary Needs Review status while retaining candidate evidence.

### Financial classification and dates

Differences are signed **Accounts minus GSTR**. Classification uses the maximum absolute difference among taxable value, IGST, CGST and SGST, not a netted total:

| Classification | Rule |
|---|---|
| Perfectly Matched | All four differences equal zero |
| Minor Rounding Difference | Maximum difference is greater than zero and at most `1.00` |
| Major Difference | Any one difference exceeds `1.00` |

Accounts taxable absence is a separate presence flag. The purchase result builder preserves canonical match evidence, then sets primary **Taxable Value Missing** and a source warning. It is not a zero-value match. API/export presentation uses null for missing/invalid values where the serializers specify it.

Date Mismatch is a cross-cutting flag when both linked dates exist and differ; the day difference is absolute. Missing dates are not fabricated and do not themselves set this flag. Exact/normalized matches can therefore remain financially Perfect while appearing in Date Mismatches. Suffix purchase matches require exact dates. There is no FY or return-period eligibility check.

### Unmatched, duplicate and cancelled results

**Missing in GSTR-2B:** no eligible link was selected for the Accounts record, subject to primary status overrides. **Not in Tally / Not in Accounts:** an unconsumed GST aggregate; this includes a candidate still awaiting manual confirmation. **Potential Duplicate:** repeated Accounts GSTIN+normalized invoice identity; no automatic consumption. Counts of date signals are not a disjoint partition of primary status counts.

**Cancelled:** explicit source cancellation evidence is retained separately. For purchases, a GST aggregation identity containing a cancelled member is excluded from active matching. Zero monetary values alone do not establish cancellation. Cancelled source rows and reasons are exported; cancellation-counterpart provenance can explain active unmatched rows. Do not assume the Notes engine implements the same cancellation partition.

### Notes are a separate rule set

Accounts debit notes and canonical GST credit notes use GSTIN exact / missing-GSTIN supplier fallback, then exact note, normalized note, Last-4, Last-2. Exact/normalized note layers require uniqueness but **do not require equal dates or financial tolerance** before linking. Both Notes suffix layers require taxable difference at most `1.00` and each tax-head difference at most `5.00`; **they do not impose the purchase suffix date gate**. After linking, Notes classify using the ordinary `1.00` maximum-difference rule, so a suffix-selected note can be Major.

Invalid financial records require review; Accounts note duplicates use GSTIN+normalized number; ambiguity does not consume; unconsumed GST notes are Not in Tally. Notes do not implement the purchase Strong Evidence or Invoice Number Mismatch fallback. Automatic Notes construction is not identical to purchase missing-taxable/date-signal handling. Preserve this distinction in future work rather than silently unifying engines.

### Manual Match

Only backend-listed candidates for a reviewable result can be confirmed. Invalid financial source values block manual matching. An already consumed candidate is rejected; consumption conflicts return HTTP 409. The service assigns `Manual Match`, evidence `Manually matched by user`, recomputes financial/date fields and summary counts, removes the corresponding synthetic Not-in-Accounts representation, and invalidates export paths for regeneration. Missing Accounts taxable remains Taxable Value Missing with a distinct warning. Current removal predicates use GSTIN+normalized number; changes to date-key semantics would require separate correctness review.

## 5. GSTR Aggregation

**VERIFIED FROM CODE.** B2B/B2BA purchase rows aggregate by `(normalized GSTIN, normalized invoice number, invoice date)`. Taxable, IGST, CGST and SGST are summed with Decimal; all contributing source rows and financial issues are preserved. Other metadata comes from the first row; notably the stored total invoice value is not independently summed.

Credit notes aggregate by `(normalized GSTIN, normalized note number, source note_date)`, using the note number or invoice-number fallback. The aggregate's displayed date can fall back to invoice date, but the grouping key uses `note_date`. Financial values sum and source rows remain available.

Before either aggregation, B2BA/CDNRA supersession uses complete GSTIN/original-number/original-date lineage. Regular rows with that key are replaced by amendment rows. Complete orphan amendments remain authoritative. Incomplete lineage or conflicting revised identities fail explicitly; source-row order is not treated as “latest revision.” See [amendment resolver](backend/app/services/amendment_resolver.py).

One-to-one consumption applies **after supported aggregation**. There is no general one-to-many/many-to-one Accounts posting matcher, split-invoice solver or arbitrary cross-document consolidation.

## 6. Data Parsing and Normalization

**VERIFIED FROM CODE:** [file parser](backend/app/services/file_parser.py), [header detector](backend/app/services/header_detector.py), [column detector](backend/app/services/column_detector.py), [sheet detector](backend/app/services/sheet_detector.py), [normalizer](backend/app/services/normalizer.py).

- Excel uses openpyxl with `data_only=True`, `read_only=False`, then materializes worksheet rows. Formula results depend on stored workbook values; the server does not calculate Excel formulas.
- Worksheet categories include Purchase Register, Debit Note Register, B2B, B2BA, CDNR, CDNRA, Party (`party`) and Unknown. Detection and column aliases cover supported layouts, not every workbook. Supported GST layouts also flatten multirow headers, preserving parent context for repeated leaf headings.
- Header detection defaults to scanning the first 30 rows for semantic aliases. Canonical columns separate source header spelling from invoice, GSTIN, date and monetary meanings. Mapping overrides reparse persisted rows.
- Invoice normalization retains original text, handles whole numeric Excel values without `.0`, uppercases and removes supported separators. It separately retains normalized text, trailing number, last four and last two characters. Literal identifiers must not be dropped just because they resemble placeholder text.
- Dates accept Python date/datetime values, supported strings and bounded Excel serial dates. String formats are tried in order, with day-first formats before US fallback. Unparseable values retain their original string with no parsed date. The implementation does **not** reject every ambiguous day/month string despite a stronger docstring claim.
- Money converts through strings to Decimal, rounds to two places with `ROUND_HALF_UP`, accepts commas/rupee symbols/parenthetical negatives, and distinguishes missing taxable presence from numeric zero. Invalid nonblank money is retained as a financial parse issue rather than treated as trustworthy zero. Rate metadata uses a separate parser.
- GSTIN normalization removes whitespace and uppercases; a regex reports format validity, without registry lookup. Supplier normalization removes punctuation and expands configured abbreviations.
- Provenance retains source side/system, sheet, row identifiers, aggregated GST row references, amendment metadata and explicit cancellation evidence. Raw upload files are deleted after canonical row persistence; this is not archival preservation of the source workbook.

## 7. Result Model

**VERIFIED FROM CODE:** [models](backend/app/models/reconciliation.py), [API serialization](backend/app/services/result_pagination.py), [provenance](backend/app/services/result_provenance.py).

| Concept | Meaning / canonical source |
|---|---|
| Primary status | `result.status`: current outcome/review category |
| Cross-cutting signals | Date mismatch, taxable absence, invoice mismatch and source-quality/provenance signals; not interchangeable with a status partition |
| Match method | `result.match_method`: selection route, such as Invoice Last 4 or Manual Match |
| Match evidence | `result.audit.explanation`: why records were linked or why a candidate was surfaced |
| Warning / remarks | `result.remarks`: status/source context |
| Provenance | Source records and `result_provenance(...)`: where the data came from |

The results API exposes `evidence` directly from `audit.explanation`; it does not reconstruct matching rules. The drawer displays distinct Match evidence and Warning, suppresses duplicate text, and falls back cleanly when only one text exists. Display terminology may replace Tally/GST with Accounts/GSTR-2B; canonical evidence is unchanged. Excel's Invoice No Remarks explanation similarly composes distinct evidence and remarks.

For invoice `60882` linked to `06CFIP0675660882`, a Taxable Value Missing warning must coexist with Last-4/exact-GSTIN/exact-date identity evidence and method Invoice Last 4. Equal CGST/SGST do not justify claiming full financial confirmation when Accounts taxable is absent.

## 8. Outputs

**VERIFIED FROM CODE:** [Excel exporter](backend/app/services/excel_exporter.py) creates these nine sheets in this order:

1. `Reconciliation Summary`
2. `Missing & Differences`
3. `Taxable Value Missing`
4. `Date Mismatches`
5. `Invoice Mismatches`
6. `Cancelled Vouchers`
7. `Invoice No Remarks`
8. `Not in Tally`
9. `Notes`

Sheets are different views, not mutually exclusive sets. Invoice No Remarks covers normalized/Last-4/Last-2 methods with evidence and warning context. Source values, signed differences, missing-value presentation and provenance follow the exporter rather than frontend reconstruction.

[Downloadable JSON](backend/app/services/json_exporter.py) contains top-level `metadata`, `counts`, `reconciliation_summary`, `invoice_mismatches`, `missing_in_gstr`, `major_differences`, `taxable_value_missing`, `invoice_number_remarks`, `not_in_tally`, `notes`, `cancelled_vouchers`. Results are repeated in relevant subsets. Decimal values serialize as strings, dates as ISO strings, enums as values. It already contains evidence/reason/remarks fields where defined; the additive results-API evidence field did not redefine this export schema. Current orchestration uses a generic reporting-period label rather than FY-aware reporting.

Both reports are generated into temporary files during reconciliation. Missing files can be regenerated from surviving session state. Manual matching invalidates reports; subsequent download regenerates them. The JSON size guard controls download, not report generation.

## 9. Frontend Architecture

**VERIFIED FROM CODE.** [package.json](frontend/package.json) declares React 19, TypeScript 6, Vite 8, Axios, Lucide icons, Tailwind CSS 4, `clsx` and `tailwind-merge`. [Vite configuration](frontend/vite.config.ts) configures React/Tailwind and Vitest/jsdom tests.

| Responsibility | Main location |
|---|---|
| Workflow/session state | `frontend/src/App.tsx` |
| Upload and stable file selection | `components/upload/FileUploader.tsx`, `utils/stableUploadFile.ts` |
| Review | `components/mapping/MappingTable.tsx`, `components/preview/PreviewTable.tsx` |
| Results / grouping / pages | `components/results/ResultsDashboard.tsx`, `ResultsTabs.tsx`, `ResultsTable.tsx` |
| Evidence and comparison | `components/results/InvoiceComparisonDrawer.tsx`, `ResultComparison.tsx` |
| Candidate confirmation | `components/review/AmbiguousReview.tsx`, `ConfirmMatchDialog.tsx` |
| Notes / special categories | `components/notes/NotesTable.tsx`, taxable-missing and cancelled tables |
| Summary / exports | `components/export/SummaryPanel.tsx`, `ExportPanel.tsx` |
| API and contracts | `services/api.ts`, `types/api.ts` |

Results are grouped into All, Matched, Differences, Missing, Review and Notes, with individual category filters. Search, sorting and pagination go to the server. Default API page size is 25, maximum 100. Reconcile/manual responses contain summary/count information with empty bulk result arrays; pagination supplies the rows.

Toasts, upload error helpers, session-expiry handling, keyboard/focus utilities and accessible labels support review. Downloads currently receive an Axios Blob and create/revoke an object URL in the browser. This still buffers file bytes. JSON HTTP 413 is interpreted as a pilot transport warning, including when the error body arrives as a Blob.

## 10. Backend Architecture

**VERIFIED FROM CODE.** [main.py](backend/app/main.py) wires FastAPI, explicit CORS, sensitive-response headers and cleanup lifecycle hooks.

```text
React browser
  | multipart files / mapping overrides / candidate IDs
  v
FastAPI routes + Pydantic schemas
  |-- inspect -> file/header/column/sheet parsers -> canonical worksheet rows
  |-- reconcile -> reparse mappings -> amendment resolver
  |                                  |-- purchase engine
  |                                  +-- Notes engine
  |                                      -> IDs/provenance/summary
  |                                      -> Excel + JSON temp files
  |-- results -> category/filter/sort -> page serialization
  |-- manual-match -> validated session mutation -> invalidate exports
  |-- export -> existing or regenerated temp report -> FileResponse
  +-- session get/delete
          |
       SessionManager -> SessionStore
                         |-- memory (local)
                         |-- JSON file (local tests)
                         +-- Upstash Redis (production)
```

| HTTP route | Responsibility |
|---|---|
| `GET /api/health` | Health response, not a full capacity/readiness certification |
| `POST /api/inspect` | Multipart upload and inspection |
| `POST /api/reconcile?session_id=...` | Synchronous reconciliation |
| `POST /api/manual-match?session_id=...` | Candidate confirmation |
| `GET /api/session/{session_id}/results` | Category/search/sort/page |
| `GET /api/export/{session_id}/excel` | Excel download |
| `GET /api/export/{session_id}/json` | JSON download with size guard |
| `GET /api/session/{session_id}` | Session status |
| `DELETE /api/session/{session_id}` | Session deletion |

Schemas live in [schemas.py](backend/app/api/schemas.py). Orchestration coordinates engines/exporters; engines do not call the browser. Expected validation/expiry/conflict failures use 400/404/409; upload/export limits use 413; unsupported uploads use 415. Unexpected route failures generally return sanitized messages rather than raw exceptions. Legacy reconciliation remains synchronous; GSTR Consolidation uses its separate PostgreSQL-backed worker lifecycle and is disabled by default pending manual infrastructure rollout.

## 11. Persistence Architecture

**Database foundation added after the original audit:** an opt-in PostgreSQL/SQLModel
schema and Alembic migration workflow now live under `backend/app/db/` and
`backend/alembic/`. They model Individual/Firm workspaces and durable business
ownership, but are not connected to the existing reconciliation routes yet.
The subsequent [local authentication milestone](docs/LOCAL_AUTH_DEVELOPMENT.md)
now connects registration/login/verification/recovery to PostgreSQL and the UI.
It is not production auth: client/run authorization is unfinished, financial
routes are blocked in auth mode, and email is captured privately on disk.
See [Database Development](docs/DATABASE_DEVELOPMENT.md) for
setup, migration safety and the real-PostgreSQL test gate. The operational session
architecture below remains unchanged.

**VERIFIED FROM CODE:** [session manager](backend/app/services/session_manager.py), [stores](backend/app/services/session_store.py).

- `InMemorySessionStore`: process-local dictionary with an `RLock`; useful locally, not durable across instances.
- `JsonFileSessionStore`: explicit JSON encoding, temporary-file replacement and process-local locks for local/restart tests. It is not a distributed transactional database.
- `UpstashRedisSessionStore`: required when `VERCEL` is set or `APP_ENV=production`. Keys use `gst-reconciliation:session:<uuid>`; default TTL is 3,600 seconds and the manager uses a 60-minute inactivity timeout.

The allowlisted serializer encodes dataclasses/enums, Decimal strings, dates, datetimes and paths; default dataclass fields are omitted. It recursively serializes values, not a general shared-reference graph. Upstash stores compact JSON compressed with zlib level 6 and base64, prefixed `z1:`; legacy uncompressed payloads remain readable.

Normal `SessionManager.get` is a **mutation**: load/decode the full session, refresh access time, reconcile local report paths, encode/compress the full replacement, then Lua compare-and-swap against the original payload. Lua sets the replacement with renewed TTL only if the original still matches. Five retries precede a mutation conflict. Direct store `get` alone does not refresh TTL. The Lua arguments include both old and new payloads; frequent paging is not a metadata-only Redis read.

Inspection persists canonical rows, drops parsed-record copies and deletes raw uploads. Reconciliation compacts source data after provenance extraction, stores authoritative results/candidates and drops reparsing inputs. Redis preserves sessions across backend instances while they remain alive; temporary reports are disposable and regenerable. Redis TTL is **not** a durable reconciliation-history or compliant retention policy. Local temporary-directory cleanup is opportunistic, not guaranteed erasure on every instance at the exact TTL boundary.

## 12. Current Deployment Architecture

**VERIFIED FROM CODE / OBSERVED IN PRODUCTION AUDIT.** The audited deployment shape is browser → Vercel frontend → Vercel FastAPI backend → Upstash. [pyproject.toml](pyproject.toml) identifies `backend.app.main:app` as the Vercel entrypoint and requires Python `>=3.12,<3.13`. No checked-in `vercel.json` establishes the actual function duration, memory or Fluid Compute settings. Production project settings must be verified separately; deployment URLs are not a stable contract in repository configuration.

Files currently travel through a backend multipart request into instance-local temporary storage. Exports travel through backend `FileResponse`, then the browser download helper. Neither direct object uploads nor object-backed exports are implemented.

| Safeguard | Actual constant / behavior |
|---|---|
| Combined upload | `3,500,000` bytes across Accounts + GSTR + optional debit notes |
| Backend enforcement | Shared cumulative byte counter; over-limit upload fails with 413 and cleanup |
| Frontend enforcement | Combined validation in App/API plus individual file rejection |
| JSON download | File size above `3,500,000` bytes returns a small 413 response |
| Excel download | No equivalent explicit size guard in the current endpoint |

See [backend limits](backend/app/transport_limits.py) and [frontend limits](frontend/src/utils/transportLimits.ts). Decimal MB is used, not MiB. Upload byte checks do not limit expanded workbook cells, row count or parser memory.

Vercel documents a 4.5 MB Function request/response payload limit; the application safeguard leaves headroom but is not a new platform capability. Actual duration/memory limits depend on project/runtime/plan configuration. See [Vercel Function limits](https://vercel.com/docs/functions/limitations).

CORS requires explicit production origins; no wildcard or credentialed browser requests are enabled. API responses have no-store/private, nosniff and no-referrer headers. Session UUIDs are capability-like identifiers; there is no user/tenant authorization layer or application rate limiting. **CORS is not authentication.** Do not expose sessions or financial data in public links/logs. Review commercial plan eligibility before a firm pilot; [Vercel Hobby](https://vercel.com/docs/plans/hobby) is intended for personal, non-commercial use.

## 13. Current Production Capacity Evidence

**OBSERVED IN PRODUCTION AUDIT, 28 August 2026:** synthetic runs of 500+500 and 1,000+1,000 invoices completed; reconciliation took approximately 2.3 and 3.5 seconds respectively. These are observations, not an SLA or concurrency test.

**LOCAL AUDIT OBSERVATION:** synthetic exact-match runs reached 10,000 Accounts + 10,000 GSTR records, approximately 97 seconds and 355 MiB peak process working set. At 5,000+5,000, approximately 21 seconds and 215 MiB were observed. That harness used local in-memory sessions on Windows/Python 3.10, unlike the declared production Python 3.12 runtime and Redis path.

The UI statement **“Production tested with approximately 1,000 invoices per source”** is not an engine maximum. There is no hard row cap, but **10,000 is not production-supported by this evidence**. The upload guard is 25 MB combined; the separate downloadable JSON response guard remains 3.5 MB. See the report for full measurements, workload caveats and transport sizes.

## 14. Publication-safe fixture validation

The public test suite uses programmatically generated records and three small synthetic legacy-XLS fixtures. It covers parsing, mapping, matching, manual resolution, provenance, exports, cleanup and restart behavior without requiring private workbooks. Historical customer-derived QA workbooks, filenames, frozen financial totals and associated local-only regression scripts were deliberately excluded from the sanitized repository.

## 15. Testing

**VERIFIED FROM CODE / TESTS.** From repository root, using the supported Python environment:

```powershell
python -m pytest backend/tests -q
python -m pytest backend/tests/test_adversarial_matching.py backend/tests/test_manual_match_integrity.py backend/tests/test_session_persistence.py -q
```

From `frontend/`:

```powershell
npm test
npm test -- src/ResultDrawer.test.tsx
npm run build
npm run lint
```

`npm test` runs Vitest once; build runs TypeScript project checking then Vite. Coverage includes parsing/mappings, missing/invalid money, amendments, suffix ambiguity/date gates, cancellation, Notes direction, provenance, pagination, manual consumption, persistence/TTL/CAS, transport limits, drawer evidence and export errors. Redis unit tests use controlled clients; local fixture/synthetic tests are not live production load tests.

For changes, run relevant targeted tests, full suites/build as appropriate and `git diff --check`. No new suite run is claimed for this documentation-only audit, and no moving test-count total is hard-coded here.

## 16. Local Development

**VERIFIED FROM CODE.** Use Python 3.12 as declared by the project. Use a Node release satisfying the installed Vite/package engine requirements; the repository does not pin one in a runtime-version file.

Backend, PowerShell from repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
$alignedDependencies = python -c "import pathlib, tomllib; print('\n'.join(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['dependencies']))"
python -m pip install $alignedDependencies
python -m uvicorn backend.app.main:app --reload --port 8000
```

Frontend, a separate terminal:

```powershell
cd frontend
npm ci
npm run dev
```

These are setup instructions derived from manifests, not commands executed during the documentation audit. The backend command installs dependencies directly from `pyproject.toml`; it does not assume this flat repository has been configured as an installable Python distribution. Backend defaults to `http://localhost:8000`; frontend development origin is `http://localhost:5173`. FastAPI exposes its usual `/docs` UI. If the frontend port changes, explicitly configure the corresponding allowed origin.

| Variable | Use |
|---|---|
| `VITE_API_BASE_URL` | Frontend build/dev API origin; defaults to `http://localhost:8000`; see `.env.example` |
| `CORS_ORIGINS` | Comma-separated exact frontend origins; mandatory in production |
| `SESSION_STORE_BACKEND` | `memory` locally by default; `json_file` for local persistence tests; must be `upstash` in production |
| `SESSION_STORE_PATH` | Required local directory for `json_file` |
| `UPSTASH_REDIS_REST_URL` | Backend-only Redis REST endpoint |
| `UPSTASH_REDIS_REST_TOKEN` | Backend-only secret; never a `VITE_` variable |
| `APP_ENV` / `VERCEL` | Production detection; activates strict store/CORS requirements |

Supply backend variables through the shell or deployment environment; automatic loading of a root `.env` is not implemented here. Do not place credentials in Markdown, frontend bundles or committed environment files. Production must configure Upstash and the correct CORS origins before startup.

## 17. Current Known Limitations

**VERIFIED FROM CODE unless explicitly labelled otherwise.**

- Backend-proxied file transport with a 25 MB combined application upload guard and a separate 3.5 MB JSON download safeguard; Vercel Functions still impose their lower platform request limit, and Excel remains exposed to platform response limits.
- Production capacity verified only around 1,000 invoices/source, with no demonstrated multi-user load envelope, forced cold-start test or production memory profile.
- Legacy reconciliation and export generation remain synchronous. Durable worker processing currently applies only to GSTR Consolidation; it has lease recovery and deletion but no user-facing cancellation action.
- Repeated full-list matching scans; pagination limits response size but still serializes category rows before slicing.
- Entire session stored/read/recompressed through Redis; normal reads renew it through CAS. No bounded metadata-only read path.
- Workbook/JSON parsing, exports and browser downloads materialize data; compressed input size does not bound expanded memory.
- Temporary local reports, finite session TTL and no durable history or retention guarantee.
- No general split/consolidated posting layer; purchase and Notes rules intentionally differ in current implementation.
- No FY/return-period awareness. Amendment supersession is not a substitute for it.
- No SaaS auth, tenant isolation, organizations, client workspaces, application rate limiting or billing.
- No compliance/ITC eligibility engine, return filing or direct accounting-system integration.
- Private fixture tests may skip on a fresh checkout. Production runtime/settings cannot be inferred from a successful local test.

## 18. Roadmap

**RECOMMENDATION — NOT IMPLEMENTED.** Freeze reconciliation semantics while deciding infrastructure changes in the [architecture report](docs/SCALING_ARCHITECTURE_REPORT.md).

Near-term priorities are private scalable upload/export transport, phase-level measurements, a controlled 5,000/source production benchmark, then a measured 10,000/source target. Add bounded background execution and result-storage changes when the latency/concurrency gates require them. Product polish and a controlled CA pilot also need access controls, data-handling policy and deployment-plan review. FY / Period Awareness is a separately approved future workstream, not part of this audit.

Later product work may add general split/consolidated postings, authentication, organizations, client workspaces, reconciliation history, durable job processing, explicit storage/retention, billing, broader accounting automation and ITC/compliance intelligence. Their order is a product decision; none should be advertised as implemented today.
