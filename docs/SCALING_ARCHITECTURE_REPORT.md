# ALIGNED: Scaling Architecture Audit

**Audit date: 28 August 2026. Scope: documentation and decision support only.** No code, infrastructure, limits, exports, matching decisions or FY behavior were changed. The current product is documented in [README](../README.md).

## 1. Decision summary and evidence standard

**RECOMMENDATION.** Keep React on Vercel and FastAPI as the control API. Move file bytes to **private S3-compatible object storage, initially Cloudflare R2**, using scoped direct browser uploads/downloads. Progress toward **one managed Python container job per reconciliation, initially Cloud Run Jobs**, with bounded concurrency, immutable versioned result artifacts and small Redis session/job metadata. Preserve the existing engines and Excel/JSON contracts. Start with transport and measurements, not a rewrite or a queue cluster.

The next production acceptance target should be **5,000 Accounts + 5,000 GST invoices**, followed by 10,000 after latency, memory, session-size and concurrent-use gates pass. Neither target is supported today by the existing production evidence. Treat 25,000+ as a later engineering qualification, not a multiplier of the 1,000-row test.

Evidence labels throughout:

| Label | Interpretation |
|---|---|
| **VERIFIED FROM CODE** | Inspected repository implementation; not a production measurement |
| **VERIFIED FROM TESTS** | Existing test assertions/fixtures inspected; no new full suite run in this docs-only change |
| **OBSERVED IN PRODUCTION AUDIT** | Earlier HTTP audit; not reverified against production in this task |
| **LOCAL AUDIT OBSERVATION** | Local capacity artifacts, different runtime/store from production |
| **INFERENCE** | Complexity, likely risk or extrapolation without a measured guarantee |
| **RECOMMENDATION** | Proposed work, not an existing feature |

Provider facts are linked to official documentation. Prices, plans and runtime limits are time-sensitive and must be checked again before purchase/deployment. No actual monthly bill, configured production memory/duration, Redis plan limit or service-level objective was available for verification. No secrets or private workbook contents are included.

## 2. Current system and audit traceability

**VERIFIED FROM CODE.** The request path is synchronous and stateful:

```text
Browser: File snapshot -> multipart POST /api/inspect
                             |
Vercel FastAPI               v
  local temp files -> openpyxl/JSON parsing -> stored canonical worksheet rows
                             |                    |
                             |                    +--> Upstash full session
  POST /api/reconcile -> reparse overrides -> amendments -> purchase + Notes
                             |
                      results, IDs, provenance
                             |
                      Excel + JSON temp files
                             +------------------------> Upstash full session
  GET results -> full session CAS touch -> serialize/filter/sort -> page
  GET export  -> full session CAS touch -> file/regeneration -> browser Blob
```

| Area inspected | Authoritative files / observed consequences |
|---|---|
| Routing, schemas, safety | `backend/app/main.py`, `api/routes/{inspection,reconciliation,export,sessions,health}.py`, `api/schemas.py`, `transport_limits.py` |
| Input interpretation | `services/{file_parser,header_detector,column_detector,sheet_detector,normalizer,cancellation_detector,amendment_resolver}.py` |
| Orchestration and matching | `services/{reconciliation_orchestrator,reconciliation_engine,notes_reconciliation_engine,manual_match_service}.py` |
| Output semantics | `models/{records,reconciliation}.py`, `services/{result_provenance,result_pagination,summary_generator,excel_exporter,json_exporter}.py` |
| State | `services/{session_manager,session_store}.py`: full-session persistence, TTL, compression and CAS |
| UI | `frontend/src/App.tsx`, upload/mapping/preview/results/review/notes/export components, `services/api.ts`, `types/api.ts`, upload/download utilities |
| Validation/build | `backend/tests/`, frontend test files, `frontend/package.json`, `vite.config.ts`, TypeScript configuration, `pyproject.toml` |
| Existing documentation | No root README existed. `frontend/README.md` contains scaffold guidance plus the pilot-limit checklist; it is not a system specification |

`pyproject.toml` declares Python 3.12 and the Vercel FastAPI entrypoint. The checked-in project does not establish actual production duration/memory/Fluid settings. No object-storage adapter, job dispatcher, durable queue, auth/tenant layer or database result index exists. The frontend uses `VITE_API_BASE_URL`; backend CORS and Upstash settings are environment-driven. There is no need to infer a production URL from ignored local environment files.

## 3. Existing capacity evidence

### Method and limitations

**LOCAL AUDIT OBSERVATION.** The earlier capacity harness used synthetic unique exact invoice matches, one supplier/GSTIN, equal dates/financials and no Notes. Local calls used FastAPI TestClient with in-memory sessions on Windows/Python 3.10. This differs from the declared Python 3.12 deployment and Redis-backed production path. The one-supplier case makes the existing supplier bucket large, but does not stress fuzzy supplier matching, ambiguous candidate graphs, amendment density or manual contention.

Artifacts inspected: `local-500.json` through `local-10000.json`, `production-500.json`, `production-1000.json` and the capacity harness under the prior audit's temporary directory `aligned-pilot-audit-20260828`. They are not versioned benchmark infrastructure. The following table preserves their useful measurements without publishing session IDs. The user-provided audit brief corroborates the rounded timing/memory figures.

| Records per source | Local reconcile seconds | Local peak working set MiB | Local JSON bytes | Local Excel bytes | Multipart request bytes |
|---:|---:|---:|---:|---:|---:|
| 500 | 0.635 | 91.8 | 1,103,318 | 56,064 | 39,554 |
| 1,000 | 1.420 | 106.8 | 2,205,827 | 98,719 | 67,830 |
| 2,000 | 3.740 | 133.0 | 4,413,827 | 183,643 | 123,208 |
| 5,000 | 21.204 | 215.2 | 11,037,827 | 438,326 | 289,089 |
| 10,000 | 97.078 | 355.0 | 22,077,836 | 863,611 | 565,535 |

**OBSERVED IN PRODUCTION AUDIT:**

| Records per source | Inspect seconds | Reconcile seconds | JSON bytes | Excel bytes |
|---:|---:|---:|---:|---:|
| 500 | 5.140 | 2.268 | 1,073,783 | 56,064 |
| 1,000 | 5.748 | 3.532 | 2,146,792 | 98,723 |

These are individual observations, not averages or p95/p99 estimates. Production timings include HTTP/network/store effects. Export download timings from the artifacts are not exporter generation timings: initial exports were generated during reconcile. Different local/production serialized sizes must not be interpreted as a compression measurement or guaranteed identical deployment revision.

The large local JSON downloads preceded the present **3,500,000-byte JSON download guard**. Today those reports may be generated but their backend download would be refused. The 2,000/source local JSON is already above that guard. Repetitive XLSX compresses well: the 10,000/source request was only about 0.57 MB, while its JSON exceeded 22 MB. Invoice count and upload bytes are not interchangeable limits.

**Not established:** production 2k/5k/10k support, production peak memory, forced cold-start latency, multi-user concurrency, failure recovery during deployment, largest Redis session or worst-case workbook expansion. Earlier live health/CORS checks and real-file smoke runs do not close those gaps. The UI's approximately 1,000/source statement is an evidence boundary, not an engine maximum.

## 4. Ranked bottlenecks

**INFERENCE grounded in the code and measurements.** Rank reflects the next 5k–10k pilot, not generic cloud severity. Transport ranks first because it can deterministically block otherwise successful computation.

| Rank | Issue | Severity | Repository-specific reason / first response |
|---:|---|---|---|
| 1 | Export transport | **CRITICAL** | JSON exceeds the 3.5 MB guard around the observed 2k case. Excel has no equivalent guard and still uses a Function response. Store and download artifacts directly |
| 2 | Upload transport | **CRITICAL** | All three files traverse one multipart Function request. The application now allows 25 MB combined, but Vercel Functions enforce a lower 4.5 MB request-body ceiling before FastAPI can validate larger requests. Use direct private uploads for the full application limit. |
| 3 | Reconciliation algorithm | **HIGH** | Per-Accounts full GST scans remain even on exact matches; local 10k reconcile is ~97 seconds. Profile and index without changing decisions |
| 4 | Session storage size | **HIGH** | Redis holds complete canonical rows or results/candidates, not just session metadata. Bound and measure serialized state; separate immutable bulk artifacts |
| 5 | Redis serialization/compression | **HIGH** | Every normal manager read decodes/recompresses and CAS-writes the whole session. Old and new payloads travel in Lua arguments; retries multiply work |
| 6 | Synchronous HTTP execution | **HIGH** | Browser waits through reconciliation, export generation and publication. Disconnect/retry is not a durable job lifecycle |
| 7 | Result pagination | **HIGH** | Full session is loaded and the entire category serialized before search/sort/slice. Small response does not mean bounded backend work |
| 8 | JSON generation | **HIGH** | Nested report and indented JSON string are materialized; category subsets repeat full results. Generation cost remains even when download is blocked |
| 9 | Memory | **HIGH** | Normal-mode Excel workbook, worksheet lists, dataclasses, report dict/string and compressed session copies overlap. 355 MiB observed locally is not a worst-case ceiling |
| 10 | Concurrency | **HIGH** | CPU-heavy requests and repeated session touches compete; no admission control or per-job resource budget. Platform autoscaling does not prevent same-session conflicts or cost spikes |
| 11 | Function duration | **HIGH** | Runtime deadline bounds the entire request; configured production values unknown. Higher limits do not solve recovery or user waiting |
| 12 | Excel generation | **MEDIUM** | Standard openpyxl workbook with styling and column sizing retains cells; output compressibility hides construction memory. Profile separately before 25k |
| 13 | Temporary files | **MEDIUM** | Reports are instance-local and regenerable, not durable. Cold instances may redo both exporters; cleanup is opportunistic |
| 14 | Frontend/browser limits | **MEDIUM** | Stable uploads copy bytes; Axios Blob downloads buffer exports. Pagination helps rendering but not upload/download memory or long-request UX |
| 15 | Cold starts | **MEDIUM** | Python/dependency startup and absent temp reports can add latency. No isolated cold-start measurement; not proven primary |

No issue is ranked LOW merely because the synthetic benchmark did not hit it. For the current ~1k single-user workload, some HIGH items are latent rather than observed failures.

**External constraint:** Vercel documents 4.5 MB request/response payloads for Functions. Check the actual runtime and plan for duration/memory; this repository has no authoritative setting for them. Streaming or increasing a timeout must not be assumed to bypass the current FileResponse transport design. [Vercel limits](https://vercel.com/docs/functions/limitations).

## 5. Target scale and acceptance requirements

**RECOMMENDATION, not capacity claims.** “Per source” counts refer to invoice-level inputs/aggregates; tests must also record physical worksheet rows, cells, source bytes and ambiguous candidate counts.

| Tier | Transport / exports | Processing | Persistence / architecture | Required observability and acceptance |
|---|---|---|---|---|
| **5k + 5k** | Private direct upload and signed Excel/JSON download; keep export schema | Measure unchanged engine first; synchronous bridge only if bounded trials pass | Measure full Redis state; small manifest and artifacts prepared for worker migration | Phase timings, bytes, peak memory, repeat runs, real-layout fixtures and two concurrent sessions; no data loss/partial report |
| **10k + 10k** | Same object path; browser-native download; validate workbook expansion budget | Background job boundary recommended before promising support; exact scans likely need indexing | Versioned artifacts; metadata-only polling; bounded worker concurrency | Queue/startup/run/export timings, retry/kill tests, concurrent manual confirmation and stable pages; no inferred SLA from local 97s |
| **25k + 25k** | Multipart/resumable upload if warranted by bytes, lifecycle cleanup, artifact checksums | Profile all matching branches; job execution and admission control required for qualification | Indexed result store or bounded page artifacts; full Redis blob no longer default design | Mixed suppliers, dense ambiguity, amendments, Notes, exports, load/soak and provider quota tests; explicit CPU/memory budgets |
| **50k+ each** | Same storage abstraction; larger-file abuse/expansion controls; optional alternate exports | Proven indexed engine plus capacity-managed workers; partition only if semantics can be preserved | Queryable result persistence and durable history if product needs it; audit versions | Adversarial distribution tests, recovery drills, tail latency, retention verification and cost per reconciliation |

There is no measured 25k/50k runtime or memory estimate. Extrapolating the 10k time quadratically would illustrate risk but would not establish a deployment specification. Input cells, supplier distribution and candidate density can dominate row count.

**Proposed gate for the synchronous bridge:** p95 end-to-end reconcile below 30 seconds on a representative trial set, peak memory below half the verified per-instance budget at the allowed concurrency, and total execution below half the actual deadline. These are proposed operational targets, not existing guarantees. Any timeout, frequent retry, overlapping-user contention or need to recover after browser closure ends the bridge even below 5k. The observed 97-second 10k local request is already poor interactive behavior; do not wait for a hard Function timeout to introduce jobs.

## 6. Algorithm audit and semantics-preserving optimization

**VERIFIED FROM CODE:** [purchase engine](../backend/app/services/reconciliation_engine.py), [Notes engine](../backend/app/services/notes_reconciliation_engine.py). **INFERENCE:** complexity below. Let `A` be active Accounts purchase rows, `G` GST purchase aggregates, `B` the current supplier bucket, `D` Accounts debit notes, `C` GST credit-note aggregates and `K` a returned candidate list. Dictionary operations are expected O(1); fuzzy comparison also depends on name length.

### Current cost by layer

| Layer | Current pattern / likely cost |
|---|---|
| Aggregation and duplicate detection | Dictionary grouping, expected O(input rows); retains all source rows. Amendment grouping also linear in rows aside from key normalization |
| GSTIN pool | `_gstin_groups` / `_cn_by_gstin` already exist. Lookup O(1), but constructing eligible/unused lists costs O(B) per Accounts row |
| Exact purchase | Before exact lookup, `_match_single_tally_invoice` builds `invalid_exact`, `valid_exact`, `matchable_gst` with full-list comprehensions. Thus O(G) preprocessing per row plus O(B) exact scan; O(A*G) remains even with many small supplier buckets |
| Normalized purchase | Additional O(B) scan after exact fails |
| Last-4 / Last-2 | Additional O(B) scans, date checks and Decimal classification for gated candidates; fixed number of layers but still repeated work |
| Supplier fallback | Missing-GSTIN rows compare normalized supplier names across eligible G, repeatedly normalizing GST names and invoking RapidFuzz: O(A_missing*G*name_cost) |
| Strong Evidence | Global unused normalized-invoice scan O(G) when no supplier candidate remains; uniqueness checked before financial agreement |
| Invoice mismatch | Reciprocal signature counts scan full active Accounts input O(A) per attempt, then candidate scan O(B): potentially O(A² + A*G) across the workload |
| Ambiguity | List construction/counting and retained candidate references O(K); dense overlapping candidate sets can inflate serialized state beyond linear result count |
| Consumption | Flag test/set O(1); repeated filtering of lists is the expense. Deterministic Accounts iteration order affects which records remain unused |
| Notes | Same three full-list quality scans per debit note, supplier bucket scans and fuzzy fallback: O(D*C) worst case. No purchase reciprocal-mismatch/strong-evidence layers |
| Provenance/export | Source-row traversal, category duplication and cancellation-counterpart scans add work outside matching; cancellation-heavy cases warrant separate profiles |

**INFERENCE.** Repeated full GST scans plausibly explain much of the 10k slowdown even though all synthetic matches were exact. The observed 5k→10k increase is about 4.6x. That is consistent with substantial superlinear work, but reconcile timing also includes reparsing, exports and persistence. Do not attribute all 97 seconds to matching without a profiler.

### Concrete optimization candidates, not changes approved here

| Current pattern | Proposed structure | Expected improvement | Correctness risk and required test |
|---|---|---|---|
| Full invalid/valid exact scans each row | Build ordered maps `(GSTIN, normalized invoice) -> invalid/valid lists` once; preserve consumed filtering exactly | O(G) build then O(K) per lookup | Invalid candidate consumed handling differs from valid handling; test malformed exact candidate with/without valid alternatives and after consumption |
| Exact/normalized scans inside supplier group | Ordered `(GSTIN, original.strip())` and `(GSTIN, normalized)` maps; separate fallback pool membership | Expected O(A+G+sum K) for ordinary unique exact workloads | Do not choose first candidate, collapse same-number different-date candidates or change case sensitivity of original matching |
| Repeated suffix scan | Ordered maps `(GSTIN, last4, date)` / `(GSTIN, last2, date)` for purchase GSTIN path | O(G) build plus candidate-level checks | Do not add financial gating to exact-GSTIN Last-4; keep Last-2 and name-fallback financial gates; preserve missing-date rejection |
| Fuzzy normalization repeated | Cache normalized supplier strings and scores per distinct normalized Accounts name; expand all passing suppliers to ordered candidates | Reduce repeated name work; still worst-case all supplier-pair fuzzy scoring | No best-only selection, changed threshold, accent policy or narrowed supplier universe; compare every fallback candidate ID/order |
| Strong global scan | Ordered normalized-invoice index; filter unused before global uniqueness | O(K) lookup rather than O(G) | Must count globally before financial filtering, not “one candidate with agreeing money”; test same invoice across GSTINs |
| Reciprocal signature scan | Precompute counts over **the same full active Accounts list** keyed by GSTIN/date/taxable/IGST/CGST/SGST | O(A) setup instead of O(A) per mismatch attempt | Do not count only unmatched rows or change taxable-presence semantics; preserve Decimal equality and duplicate universe |
| Repeated available filtering | Retain canonical ordered candidate objects and consumed-ID set/flags | Avoid rebuilding unrelated pools; O(K) relevant checks | All indexes must see each consumption immediately. No parallel Accounts processing without proving decision equivalence |
| Notes full scans | Separate Notes indexes for quality, original/normalized number and suffix | Reduce D*C scans for common paths | Notes suffixes use taxable<=1, tax-head<=5 and no date gate; copying purchase keys would change results |
| Full category serialization per page | Filter/sort canonical lightweight fields then serialize only the selected page; later persisted query index | Reduce serialization to page size; sort still O(R log R) until indexed | Preserve current substring fields, numeric sort behavior, stable tie breakers, category counts and IDs |

O(A+G) is not a universal guarantee after indexing: large ambiguous buckets and fuzzy comparisons still cost time and storage. Memoized scoring also needs bounded cache size. Decimal/date values already normalized should be reused, not converted to float for engine keys.

### Matching freeze: required proof before accepting any optimization

**RECOMMENDATION.** Run old and proposed implementations on identical inputs and compare, row by row, not just totals:

1. Primary status, method, evidence, warning, signed monetary values, null/presence flags, dates and all cross-cutting signals.
2. Original identifiers and source provenance: sheet/row references, aggregation members, amendment lineage and cancellation evidence.
3. Candidate sets **and order**, uniqueness decisions, Accounts processing order and final consumed GST identities.
4. API IDs after stable normalization of intentionally random IDs; full page ordering/search/sort, summary counts and canonical JSON objects; Excel cells/sheet names/styles where changed code could affect them.
5. Publication-safe synthetic small, medium, large and manual fixtures plus adversarial suffix collisions, date splits, missing GSTIN/taxable, malformed values, cancelled rows and conflicting amendments.
6. Persist/reload/manual-match round trips, repeated confirmation, two conflicting confirmations and export regeneration. Shared in-memory references must not be assumed to survive recursive session serialization.

Existing publication-safe tests in `test_adversarial_matching.py`, `test_manual_match_integrity.py`, `test_financial_parse_validity.py`, `test_amendment_resolution.py` and `test_session_persistence.py` form the baseline. A new performance index is acceptable only with **zero unexplained semantic differences**. It is an implementation optimization, not permission to adjust thresholds or matching rules.

## 7. Three architecture paths

### Option A: Vercel synchronous compute with private objects

**RECOMMENDATION / transitional option.**

```text
Browser <------ signed PUT/GET ------> Private object storage
   | upload manifest / reconcile request           ^
   v                                               |
Vercel FastAPI -> parse + engines + export ----------+
   |
   +--> Upstash full session (initially)
```

**Pros:** smallest change; keeps current request lifecycle, Python engine and UI mostly intact; removes file-byte transport failure independently of matching. **Cons:** long requests, full-session costs and regeneration remain; reference upload does not make parsing bounded or async. Inspection itself can become a long request after larger uploads are admitted.

Complexity is low-to-medium relative to the other options, but private signing, manifest validation, deletion and export-version control are real security work. Cost adds object requests/storage to existing Function/Redis usage; repeated export generation and full-session reads persist. Operational burden is low. Migration risk is mainly access control and stale artifact publication. Expected lifetime is **a measured bridge to 5k, not a promised 10k/25k architecture**. End it when the gates in section 5 fail; do not assign a calendar lifetime without usage evidence.

### Option B: Managed job boundary, Vercel control API retained

**RECOMMENDATION / preferred next-stage target.**

```text
Browser <---- private signed transfers ----> R2
   | create/inspect/reconcile/status/page        ^
   v                                           |
Vercel FastAPI control API -> Cloud Run Job -----+
   |                           Python engine + exports
   +--> Redis job/session metadata + version/lease
   +--> bounded result pages / candidate data from artifact index
```

Use one container image from the same repository, not an engine rewrite. The job runs a frozen input/mapping version, publishes immutable artifacts and atomically advances a small manifest. Initially allow one active job per session and a small global concurrency cap. Cloud Run Jobs supplies managed task execution/retries; application idempotency and a recoverable dispatch protocol remain necessary. No separate queue product is mandatory for the first small pilot.

Complexity is medium-to-high: job states, polling UX, invocation credentials, versioned publication, retention and manual-match consistency must be implemented. Operational burden is medium but avoids managing a server/cluster. Costs shift CPU/memory to job lifetime, plus starts, object transfers, metadata and logs; startup can dominate short jobs. Cloud Run Jobs are billed for instance lifetime with a minimum billing period; price the chosen region and allocation, not local process time. [Cloud Run pricing](https://cloud.google.com/run/pricing).

**Pros:** browser disconnects need not kill work; separate resource/concurrency budgets; exports survive instance churn; compatible with Python. **Cons:** additional cloud/IAM boundary and cold starts; R2↔compute latency/egress must be measured; job retries are not automatically exactly once. Expected lifetime is through **5k–10k qualification and toward 25k after indexing/result storage**, with no 25k guarantee. This isolates future worker-provider changes behind the dispatcher rather than migrating the UI again.

### Option C: Persistent managed container API and worker

**RECOMMENDATION / alternative if sustained pilot traffic justifies it.**

```text
Vercel React -> managed FastAPI service -> bounded worker/queue
                    |                         |
                    +--> metadata/database <--+
                    +--> private objects <----+
```

Run API/worker on Render, Railway, Fly or an equivalent managed container service; retain the frontend on Vercel. A dedicated worker can consume a durable job queue, with explicit acknowledgement/recovery. A single API process with an in-memory background task is not equivalent.

Complexity and operational burden are medium-to-high: worker lifecycle, queue persistence, rolling deploys, resource sizing and monitoring. Cost has a standing-service floor but may be more predictable at sustained load; it cannot be declared cheaper without job frequency and concurrency. Migration risk includes new API origin/CORS, secret management and session compatibility. Engine compatibility is high because it remains Python.

**Pros:** predictable warm capacity and easier process profiling; API/read and worker CPU can be isolated. **Cons:** more always-on resources and queue operation than the small pilot needs; a bigger host alone retains O(A*G) matching. Expected lifetime extends to higher sustained usage **only after** algorithm/result-storage changes. Choose it if job-start latency or continuous traffic defeats Option B economics, not just to escape a payload limit.

## 8. Object storage comparison

**RECOMMENDATION informed by official provider documentation.** All options require private objects, authorization, bounded retention and backend-generated object keys. Do not expose permanent storage credentials or turn financial exports public for easier downloads.

| Dimension | Vercel Blob | Cloudflare R2 | AWS S3 |
|---|---|---|---|
| Direct browser upload | Client-token upload flow integrates well with Vercel | S3 API presigned PUT; browser CORS required | Presigned PUT or controlled upload policy; browser CORS required |
| Private access / download | Private storage exists; documented private delivery often goes through a Function. Validate a path that does not recreate export proxy limits | Private bucket + short-lived presigned GET directly to storage | Private bucket + short-lived presigned GET directly to storage |
| Python | Python SDK available; integration must be tested against installed versions | S3-compatible clients such as boto3 with endpoint configuration | Native boto3 ecosystem |
| Temporary retention | Application deletion/cleanup strategy must be designed | Lifecycle expiration plus explicit manifest/session deletion | Lifecycle rules plus explicit deletion |
| Vercel integration | Closest native integration | External credentials/signing; portable API | External credentials/IAM; portable SDK and mature policies |
| Cost factors | Storage, operations and transfer; private proxying can add Function transfer/work | Storage and Class A/B operations; no R2 egress charge, but compute-side traffic can still cost | Regional storage, requests, retrieval class and transfer; colocated AWS compute can change economics |
| Portability | Provider-specific API and token flow | S3-compatible subset; verify required operations | S3 API reference implementation; avoid unnecessary proprietary surrounding services |
| Fit here | Easy upload integration, but prove private large-download path first | Preferred small-pilot default if data-location requirements permit | Prefer when AWS compute, IAM or explicit regional controls are decisive |

Sources: [Blob client uploads](https://vercel.com/docs/vercel-blob/client-upload), [Blob private storage](https://vercel.com/docs/vercel-blob/private-storage), [Blob pricing](https://vercel.com/docs/vercel-blob/usage-and-pricing), [R2 presigned URLs](https://developers.cloudflare.com/r2/api/s3/presigned-urls/), [R2 compatibility](https://developers.cloudflare.com/r2/api/s3/), [S3 presigned upload](https://docs.aws.amazon.com/AmazonS3/latest/userguide/PresignedUrlUploadObject.html).

Python and retention references: [R2 boto3 example](https://developers.cloudflare.com/r2/examples/aws/boto3/), [R2 object lifecycles](https://developers.cloudflare.com/r2/buckets/object-lifecycles/), [S3 object lifecycle](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lifecycle-mgmt.html). Lifecycle deletion is asynchronous cleanup, not a guarantee that every byte disappears exactly when a session timer expires.

R2 Standard published pricing at audit time is $0.015/GB-month, $4.50/million Class A operations and $0.36/million Class B operations, with no egress charge. These are unit rates, not an ALIGNED budget; free allowances, taxes, worker traffic and retries affect bills. S3 prices depend on region/class/transfer; Blob also charges along multiple dimensions. [R2 pricing](https://developers.cloudflare.com/r2/pricing/), [S3 pricing](https://aws.amazon.com/s3/pricing/).

**RECOMMENDATION: private R2 behind a minimal object-store adapter**, with an S3-compatible path that can move to S3 if region/residency or colocated compute requirements change. Validate those requirements with pilot firms before selecting a bucket jurisdiction; do not treat this technical recommendation as a compliance determination.

### Transfer contract to design before coding

Issue short-lived upload permission for a server-generated immutable key bound to session, file role and upload version. Never accept an arbitrary user URL for backend fetching. On completion validate object ownership, actual byte size, permitted type, expected checksum where supported and workbook expansion/cell budget. A claimed size or MIME type is not sufficient. Enforce limits while downloading/parsing too; a presigned PUT is not inherently a complete content-validation mechanism.

After processing, publish immutable export keys and checksums for a result version, then mint short-lived GET URLs only after authorization. Download through the browser's native path rather than loading large exports into Axios Blob memory. Signed URLs are bearer capabilities: avoid logging them; expiry and deletion have distinct behavior. Use lifecycle rules as a cleanup backstop, with explicit deletion and an orphan reconciler. Choose retention with the user before launch; the current 60-minute session TTL does not automatically define object retention.

## 9. Background execution choices and timing

**RECOMMENDATION.** Async is not required to perform the **first transport phase** or an internal bounded 5k benchmark. It should be part of the next-stage target and should precede a promise of reliable 10k multi-user operation. The threshold is measured request duration, recovery and concurrency needs, not one magic invoice count.

| Choice | Fit / tradeoff for ALIGNED |
|---|---|
| Current synchronous FastAPI | Keep briefly for small controlled runs. No durable recovery after execution failure |
| Longer Vercel Function | Can buy measured headroom if the actual plan allows; does not remove request lifecycle, full-session work or file transport |
| Vercel Queues | Managed delivery can trigger processing; SDK quickstart is Node-oriented and HTTP/poll integration must be validated for Python. Delivery may repeat; consuming in a Function still leaves compute duration constraints. No reason to add it solely to move files |
| Dedicated container worker | Good for continuous demand; requires durable claim/ack, crash recovery, concurrency and deployment drain handling |
| Railway | Managed service/worker deployment can host Python; restart policy is not in-flight job recovery. Queue/claim semantics remain application work |
| Render | Background workers and one-off jobs are realistic Python options; standing workers need queue design and an ongoing service budget |
| Fly.io | Machines give VM/process control and can run jobs; more machine lifecycle/region operations than this pilot needs initially |
| Cloud Run Jobs | Preferred managed job runtime: reusable Python image, task timeouts/retries and bounded parallelism; startup and IAM/dispatch complexity must be budgeted |
| ECS/Fargate | Strong future AWS path using container task execution, particularly with S3; IAM/network/task-definition setup is excessive for this pilot absent AWS requirements |

Official references: [Vercel Queues](https://vercel.com/docs/queues), [Railway job patterns](https://docs.railway.com/guides/cron-workers-queues), [Render workers](https://render.com/docs/background-workers), [Render one-off jobs](https://render.com/docs/one-off-jobs), [Fly Machines](https://fly.io/docs/machines/overview/), [Cloud Run Jobs](https://cloud.google.com/run/docs/create-jobs), [ECS RunTask](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_RunTask.html).

Do not use FastAPI `BackgroundTasks`, a detached thread or `async def` around CPU work as a substitute for durable processing. The proposed API returns a job ID after a recoverable dispatch decision, and clients poll compact states. Repeated task delivery must be safe even if two executions overlap. One failed attempt must not overwrite a successful newer result.

## 10. Export design

**VERIFIED FROM CODE.** Current Excel uses a regular openpyxl workbook and nine fixed sheets. JSON constructs multiple serialized subsets and `json.dumps(..., indent=2)` into one large string. The JSON endpoint checks size only after generation. Manual confirmation clears export references; missing local files cause regeneration from session state.

**RECOMMENDATION.** Keep the existing Excel and downloadable JSON schemas initially. Move identical bytes to private object storage and expose versioned downloads. This addresses the first hard failure without breaking existing consumers.

| Format/path | Decision |
|---|---|
| Canonical single JSON | Retain for compatibility; object storage handles file size independently of Function responses |
| Gzipped canonical JSON | Optional additional download with explicit filename/content encoding; lower transfer/storage, but not lower uncompressed parser memory by itself |
| Streaming generation | Later reduce peak report-string memory by writing compatible JSON incrementally; prove field/order/value behavior and interruption cleanup. Streaming delivery through the existing Function is not the chosen transport fix |
| NDJSON | Useful optional machine-processing format; not a silent replacement for nested canonical JSON or its subsets/counts |
| Paginated API | Interactive navigation, not a substitute for complete export. Preserve filters/order and stable versioning |
| CSV | Optional flat machine export if demanded; lacks multi-sheet evidence structure and typing; guard spreadsheet formula injection in any future implementation |
| Excel | Keep as primary accountant-facing report; benchmark generation, style/width costs and opening behavior at 10k/25k/50k |

At 10k, the observed XLSX was below 1 MB but that is compressed disk size, not workbook memory. At 25k/50k, multiple sheets repeat results and wide provenance/candidate information changes cost. Profile write-only openpyxl or staged row writing only if needed; current tables/styles/auto-width behaviors may not transfer directly. Generate once per immutable result version, not on every download. A manifest must distinguish processing success from export-generation failure and never point to a partial object.

## 11. Session and result persistence

**VERIFIED FROM CODE.** Upstash stores the full session under one key with zlib/base64. The recursive serializer omits default fields but does not preserve shared object identity via references. Candidate-rich summaries can repeat data. Normal manager `get` calls `mutate`, which GETs, deserializes, touches state, serializes and EVALs a comparison of the original string before writing replacement with TTL. It retries up to five times. Raw store `get` is cheaper but is not the route-level path.

**INFERENCE.** Let `S` be compressed/base64 session bytes and `U` decoded JSON bytes. One successful normal touch transfers approximately `S` back from Redis plus old/new `~2S` arguments to CAS, excluding protocol overhead, and incurs O(U) decoding/encoding/compression work. A retry repeats this. Peak memory can include compressed, decompressed text, Python objects and replacement buffers simultaneously. Export JSON bytes are **not** a measurement of S or U.

Upstash plan limits include request/record size, storage, bandwidth and command throughput. The selected production plan was not established, so there is no defensible fixed “maximum practical session size” here. Measure actual EVAL request size as well as stored record size; old+new arguments can fail before a single record limit. Compare against the chosen plan with safety margin. [Upstash pricing and limits](https://upstash.com/pricing/redis), [request-size troubleshooting](https://upstash.com/docs/redis/troubleshooting/max_request_size_exceeded).

**RECOMMENDATION: split metadata from bulk state.** Redis should eventually hold job/session owner or access scope, input/mapping version, state, execution lease, result-manifest key, counts, current result version and expiry. Immutable objects hold source/parsed artifacts, canonical results and exports. Do not merely move the giant session to an object and fetch it for every page.

For the small pilot, a versioned result artifact plus bounded category/page index can serve ordinary pages; arbitrary search/sort must preserve existing semantics and may justify a small managed relational result store sooner. At 25k with frequent review, indexed database rows keyed by session/version/result ID are likely preferable. This is a measured decision, not an instruction to build a warehouse now. Candidate availability/manual consumption needs transactional authoritative state, not blind edits to unrelated objects.

Use version-based CAS and an immutable publish protocol: compute result version N; upload all artifacts; atomically change the manifest only if the expected session/input version still holds. Manual confirmation must advance the version and invalidate/replace dependent exports atomically from the user's perspective. Source data, candidates, pages and exports must never mix versions.

Maintain session expiry independently from durable jobs: a job must not vanish because its 60-minute browser session expired mid-run, and an expired browser capability must not retain indefinite access to artifacts. Specify deletion, job expiry, retries and retention as separate policies. A tiny manifest also reduces incidental CAS conflicts from polling, but this is not implemented today.

## 12. Recommended target and operating model

**RECOMMENDATION — one target, introduced in stages:**

```text
                       private PUT/GET (short-lived)
React on Vercel <------------------------------------> R2
   |                                                  ^
   | file manifest / mapping / reconcile / manual      |
   v                                                  |
FastAPI control API on Vercel                          |
   |-- authorization + validated object references     |
   |-- job submission / compact status / page API      |
   |                                                   |
   +--> Upstash: small session/job/version metadata     |
   |                                                   |
   +--> recoverable dispatcher -> Cloud Run Python Job -+
                                  parse + frozen engines
                                  immutable results/index
                                  Excel + canonical JSON
                                  versioned publication
```

Keep one repository and one engine implementation. Begin with one job per session and a deliberately small global execution cap; benchmark before raising it. Reuse inspect/reparse/mapping contracts. Inspection can initially stay synchronous with object references, but must also move behind a job boundary if larger workbook parsing exceeds its gate.

Access control is a release prerequisite for real firm files. Existing UUID sessions/CORS do not supply tenant authorization. A small controlled-pilot access mechanism can precede a full organizations/billing system, but every signing/status/page/manual endpoint needs consistent ownership checks before opening larger uploads to external users. Add byte/cell budgets and rate/concurrency quotas; do not log GSTINs, invoice amounts, source rows or signed URLs in performance telemetry.

**Cost model, not a quote:** per-month cost is control API requests + job CPU/memory/startup/retries + object storage/operations/transfer + metadata commands/bandwidth + logs. Model at least low-frequency pilot and burst-day workloads. Record cost per successful job and failed retry, not only per invoice. Compare R2↔Cloud Run transfer/latency against an S3/AWS alternative before committing to a region. Cloud Run filesystems remain ephemeral; use them only for bounded scratch space. [Cloud Run container contract](https://docs.cloud.google.com/run/docs/container-contract).

## 13. Phased migration plan

**RECOMMENDATION. All changes below require a separate implementation approval.** Paths are likely future touch points, not files changed in this audit.

### Phase A — Private file transport and export artifacts

**Objective:** remove Function file-byte transport as the hard blocker while preserving engine and output semantics. Define access, quotas and retention first. Add a small storage adapter, signed upload completion and versioned export URLs; initially retain existing parsing/reconcile lifecycle.

**Likely files:** `api/routes/inspection.py`, `export.py`, `api/schemas.py`, `services/reconciliation_orchestrator.py`, new storage service; frontend `FileUploader.tsx`, `App.tsx`, `services/api.ts`, `types/api.ts`, `ExportPanel.tsx`, upload/download utilities. Environment/deployment settings would be a separately reviewed change.

**Risk:** unauthorized object access, upload overwrite, arbitrary URL fetch, partial export publication and mistaken removal of all size limits. **Tests:** signed URL expiry/ownership, wrong role/key/version, failed upload, actual-byte/type/checksum validation, expansion limits, cleanup, same JSON objects/Excel cells, old small-upload compatibility and browser-native download. **Rollback:** feature-flag the object path; keep current guarded multipart/export behavior for small sessions. Drain or expire new-format sessions rather than pretending old code can read them. **Exit:** files above the old transport cap can be safely transferred in staging, unchanged fixture outcomes, private download bypass verified, no public data paths, and explicit cleanup/retention policy.

### Phase B — Measure unchanged engine and persistence at 5k

**Objective:** create repeatable phase-level profiling before optimization. Record parse/reparse/amendments, purchase/Notes matching, provenance, each exporter, session encode/compress/Redis, page/search/sort and download time separately.

**Likely files:** benchmark/test harness and optional timing instrumentation around orchestrator, engines, exporters and store; no rule edits. **Risk:** sensitive logging or a benchmark too favorable to represent firms. **Tests:** 1k/2k/5k/10k local/staging cases with multiple supplier distributions, invalid/missing values, ambiguity, Notes, multiline/amendments; cold/warm runs and two separate sessions concurrently. Capture runtime versions and configuration with each run. **Rollback:** disable instrumentation, retain original pilot limits if gates fail. **Exit:** reproducible percentile/memory/byte measurements, configured provider limits verified, bottleneck profile and an explicit 5k go/no-go. A failed gate is information, not permission to advertise capacity.

### Phase C — Optimize implementation only where profiling justifies it

**Objective:** remove repeated scans using ordered indexes and cached normalization; reduce page serialization work. **Likely files:** `reconciliation_engine.py`, `notes_reconciliation_engine.py`, `result_pagination.py` and associated tests; possibly provenance/export code only if profiles identify it.

**Risk:** silent change to uniqueness, priority, source order, missing-value interpretation or consumption. **Tests:** full semantic equivalence protocol in section 6, all backend/frontend tests, build and diff checks. Separate each optimization for diagnosis. **Rollback:** keep the previous implementation selectable during qualification; never switch an in-flight job between engine versions. **Exit:** zero unexplained row/candidate/provenance/consumption differences plus measured improvement on the actual expensive phase. Do not adjust thresholds as a performance shortcut.

### Phase D — Durable execution and smaller state

**Objective:** introduce Cloud Run Jobs, recoverable dispatch, compact polling, immutable artifacts and metadata-only Redis operations before reliable 10k multi-user support. Pull this phase ahead of C if timeouts/recovery are the immediate blocker; profiling still precedes rule-preserving optimization.

**Likely files:** new dispatcher/worker entrypoint/container definition; reconciliation/session routes and schemas; `session_store.py`, `session_manager.py`, orchestrator, manual match service, page storage; frontend App/dashboard/API/types/status UI; infrastructure configuration reviewed separately.

**Risk:** double dispatch, lost job, stale write, session TTL expiring mid-job and mixed-version manual/export results. **Tests:** kill at every state transition; repeated delivery; expired lease; dispatch-success/metadata-failure; delayed old job publication; concurrent manual matches; browser close/reopen; deleted/expired session; export failure after engine success. **Rollback:** stop accepting new jobs, drain/cancel explicitly, keep worker version available to finish existing jobs; route only compatible small sessions to synchronous flow. **Exit:** no lost successful job under injected failures, bounded concurrency, consistent result versions, compact status reads and documented incident/retry/deletion handling.

### Phase E — Qualify and release measured capacity

**Objective:** certify controlled 5k, then 10k; run 25k experiments only after the preceding gates. **Likely files:** load/golden/browser tests, benchmark documentation and capacity text; limit/config changes only after approval.

**Risk:** marketing row counts outrun data-distribution evidence or provider budgets. **Tests:** representative firm workbooks with consent, synthetic adversarial cases, concurrent users, prolonged review/manual edits, report regeneration/download, lifecycle cleanup, accessibility/error recovery and cost monitoring. **Rollback:** lower admission limits, retain old capacity statement and stop new large jobs without deleting completed reports unexpectedly. **Exit:** published workload envelope, runtime/region/concurrency, p95 timing/memory, error rate, cost and retention evidence. No 25k/50k support claim without its own acceptance run.

## 14. What not to build yet

**RECOMMENDATION.** Do not add Kubernetes, Kafka, a collection of microservices, active-active multi-region writes, event sourcing or distributed invoice-level matching. The immediate problems are file bytes, repeated scans and whole-session work; these tools do not resolve frozen matching semantics and add failure modes. One managed worker image and a small metadata protocol are sufficient for the next qualification stage.

Do not add a separate queue product before managed job dispatch/recovery requirements are understood. Do not shard Accounts processing by supplier casually: missing-GSTIN fallback and global normalized-invoice uniqueness cross supplier boundaries. Do not split work across arbitrary invoice chunks: consumption and reciprocal uniqueness depend on the full relevant input. Do not introduce NDJSON as a breaking export replacement or a database solely because it is fashionable. Add queryable storage when page/search/manual behavior demands it.

Full billing/organizations/history can wait for product scope, but private access control, quotas and retention cannot wait until after real financial files are exposed.

## 15. Contradictions, caveats and unresolved evidence

**VERIFIED FROM CODE.** The purchase `_find_last_n_match` headline docstring describes mandatory financial confirmation, while its implementation deliberately exempts exact-GSTIN Last-4. Its detailed bullets are more accurate. The new README follows actual branching; no source comment was edited.

The normalizer date docstring implies ambiguous dates are rejected, but the code tries day-first formats before US fallback. The README documents format order rather than claiming universal ambiguity rejection. Last-N wording in older comments says digits, but normalization stores the last characters of normalized identifiers. Notes and purchase suffix rules must not be described as identical.

`frontend/README.md` is mostly React/Vite scaffolding with a useful pilot-limit checklist, not architecture documentation. No previous root README needed replacement. Legacy Tally names remain in APIs/workbook sheets even when UI labels say Accounts; this is deliberate compatibility, not evidence of a separate product.

**OBSERVED / INFERENCE.** Pre-guard local large JSON downloads are not current production capability. Python 3.10 local capacity measurements cannot establish Python 3.12/Vercel/Redis timings. Live provider project configuration, subscription eligibility, Redis limits and production concurrency remain unverified. These are acceptance prerequisites, not values to invent in a report.

The biggest architectural risk is a seemingly small storage/job migration that retains giant per-page state reads or introduces mixed-version results. The biggest correctness risk is optimizing candidate lookup in a way that changes global uniqueness, ordering, financial gates or consumption. Count-only regression tests can miss both.

## 16. Final decision table

All future columns are **RECOMMENDATION**, not implemented capacity.

| Dimension | Current architecture | Recommended next architecture | Future higher-scale architecture |
|---|---|---|---|
| Supported scale | Observed production ~1k/source; local 10k is not support evidence | Qualify 5k, then 10k; 25k experimental until tested | Qualify 25k/50k+ with explicit workload envelope |
| Upload | Guarded multipart through Vercel | Private R2 signed direct upload + validated manifest | Same abstraction; resumable/multipart by byte needs |
| Execution | Synchronous request including exports | Synchronous transport bridge, then bounded Cloud Run Jobs | Capacity-managed indexed workers; sustained workers if measured demand favors them |
| Persistence | Full compressed Upstash session | Small metadata/version state + immutable bulk artifacts; bounded page index | Queryable result/history store where product/query load requires |
| Exports | Local temp files through API | Versioned private Excel/canonical JSON objects | Same compatibility, optional gzip/NDJSON/CSV and optimized generation |
| Complexity | Low deployment complexity, hidden state costs | Moderate storage/job/versioning work | Higher data lifecycle, query and operational requirements |
| Cost | Function time + full-session Redis traffic | Object operations + managed job resources + small metadata | Workload-dependent compute/storage/query budgets |
| Operations | Limited measurements; manual recovery | Job/state metrics, retry/cleanup procedures, bounded admission | Load/soak qualification, recovery drills, retention/cost controls |

## 17. Final answers: the next engineering decision

1. **Primary bottleneck today:** file transport, most concretely JSON export responses; observed ~2k JSON already exceeds the application guard.
2. **Secondary bottleneck:** repeated whole-input matching scans; full-session Redis serialization/paging is a closely related architecture risk, especially under multiple users.
3. **Is the engine the main bottleneck now?** Not the first hard production barrier. At 10k local, engine/orchestration cost is significant; a phase profile is needed before assigning all 97 seconds to matching.
4. **Change upload/export architecture first?** Yes: private direct transfers and versioned export objects, retaining canonical contracts and safety budgets.
5. **Optimize matching immediately?** No unprofiled rewrite. Measure next, then introduce ordered indexes only with exact semantic equivalence. Optimization is likely needed for reliable larger targets.
6. **Need background jobs immediately?** Not to complete Phase A or a bounded 5k trial. Plan them now; implement before promising reliable 10k multi-user processing, or sooner when the measured latency/recovery gate fails.
7. **Object storage?** Private R2 through a small S3-compatible adapter; choose S3 instead if verified region/IAM/compute requirements warrant it. Do not use public financial objects.
8. **Compute?** Keep Vercel React/FastAPI control plane; move long execution to managed Python Cloud Run Jobs with idempotent versioned publication and bounded concurrency.
9. **Next production target?** 5,000 invoices per source under a documented workload/concurrency envelope, then 10,000. Do not jump directly to a 25k support claim.
10. **Exact phase to start next?** Review and approve **Phase A: private upload/export transport**, including ownership, quotas, retention, compatibility tests and rollback. This report does not implement or authorize that phase.
