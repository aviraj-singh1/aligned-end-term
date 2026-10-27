# Evaluator Demo

Prepare one confirmed evaluator account, one client, and small sanitized purchase-register and GSTR-2B files. Warm the Render health endpoint before starting.

## Three-to-five-minute flow

1. Sign in and show the authenticated dashboard.
2. Open Clients and select the prepared client/GSTIN.
3. Start a reconciliation and upload both sanitized files.
4. Show format validation, detected sheets, previews, and column mapping.
5. Run reconciliation and explain exact matches, tax differences, missing records, duplicates, and ambiguous candidates.
6. Open one ambiguous item and confirm a manual match.
7. Download an Excel or JSON report.
8. Return to run history and reopen the completed run.
9. Briefly show architecture and automated test results.

If Render is waking, explain the free-tier cold start and use the visible retry action. Do not retry a reconciliation or manual-match mutation blindly after an uncertain network response; the UI retains idempotency keys for explicit retries.

Do not use real customer data or claim production-scale, high-concurrency, or statutory-compliance certification.
