# Source and provenance

This end-term project was derived from the independently developed Aligned application in `Yuvraj-Tool` at source commit `cd065c78701ff13083a8ee2c60ff2cf14b6f705c`.

The source was developed throughout the academic term and includes the reconciliation engine, parsing and normalization rules, manual matching, PostgreSQL models and migrations, authentication foundation, workspace/client management, exports, React interface, and automated tests.

End-term-specific work narrows that product to GST reconciliation and exception management. Active GSTR consolidation, its worker, Tally export history, S3/CloudFront delivery, Redis/Upstash, MSG91 phone verification, invitations, ownership transfer, team administration, and developer demo navigation are excluded from the application presented for evaluation. Reports are regenerated from persisted reconciliation state and served from temporary files.

The deployment target is Vercel for the React frontend, one Render FastAPI web service, and a new Supabase PostgreSQL/Auth project.

## Publication warning

The preserved Git history contains previously committed spreadsheet inputs, temporary sessions, generated reports, customer-identifying filenames, and removed environment files. It must not be pushed to a public remote. A later approved step must create a sanitized publication repository or explicitly rewrite only this independent repository after review and backup.

No original source repository or cloud infrastructure is modified by this end-term repository.
