import type { GstrMergeRun } from '../../types/api';

const LABELS: Record<GstrMergeRun['status'], string> = {
  UPLOADING: 'Uploading files',
  QUEUED: 'Queued for processing',
  PROCESSING: 'Validating and merging GSTR data',
  COMPLETED: 'Completed',
  FAILED: 'Processing failed',
  EXPIRED: 'Result expired',
  DELETED: 'Run deleted',
};

export function GstrMergeProgress({ run }: { run: GstrMergeRun }) {
  return <section className="management-panel gstr-progress" aria-live="polite">
    <div className="gstr-status-line"><span className={`gstr-status-dot is-${run.status.toLowerCase()}`} aria-hidden="true" /><div><p className="auth-eyebrow">CURRENT STATUS</p><h2>{LABELS[run.status]}</h2></div></div>
    <p>{run.status === 'QUEUED' ? 'Your files are safely queued. You can leave this page and return later.'
      : run.status === 'PROCESSING' ? 'Aligned is validating the workbooks, consolidating supported categories, and verifying the output.'
        : run.status === 'UPLOADING' ? 'Upload every required workbook before submitting the run.' : `Financial year ${run.financial_year} · ${run.mode === 'QUARTERLY' ? 'Quarterly' : 'Monthly'}`}</p>
  </section>;
}
