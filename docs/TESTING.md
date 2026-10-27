# Testing

## Backend

Run from the repository root with Python 3.12 dependencies installed:

```text
python -m pytest -q -p no:cacheprovider
```

Tests marked `postgres` require a disposable PostgreSQL database through `TEST_DATABASE_URL`; without it they are skipped. Relevant coverage includes parsing, validation, reconciliation categories, manual-match integrity, session serialization, authorization, exports, upload limits, and the end-term route/runtime profile.

The retained source repository also contains compatibility suites for excluded GSTR-worker, Tally, MSG91, Upstash and cloud-object-storage features. Those are not part of the deployed end-term profile. One pre-existing adversarial assertion (`TestPart5Duplicates::test_42_same_inv_different_dates`) expects ambiguity even though the preserved engine selects the unique exact-date candidate; Phase 2 deliberately does not alter that matching rule. Run the retained-core suite with those compatibility files excluded and that assertion reported separately.

## Frontend

Run from `frontend`:

```text
npm ci
npm test
npm run build
```

The production build is required because TypeScript checks can find unused or unreachable excluded-feature imports that runtime tests miss.

## Manual integration

Use only sanitized fixtures. Complete registration, client creation, two-file inspection, mapping, reconciliation, exception resolution, report download, and history reopening. Restart the API between completion and reopening to confirm that PostgreSQL state—not local files—supports recovery.

Live Supabase, Render, and Vercel tests are deferred until those independent services are explicitly created and configured.
