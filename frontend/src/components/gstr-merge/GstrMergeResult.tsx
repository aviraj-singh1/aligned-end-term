import { AlertTriangle, CheckCircle2, Download } from 'lucide-react';
import { gstrMergeDownloadUrl } from '../../services/api';
import type { GstrMergeRun } from '../../types/api';

export function GstrMergeResult({ workspaceId, clientId, run, gstin, onStartNew }: {
  workspaceId: string;
  clientId: string;
  run: GstrMergeRun;
  gstin: string;
  onStartNew: () => void;
}) {
  if (run.status === 'FAILED') return <section className="management-panel gstr-result is-failed">
    <AlertTriangle aria-hidden="true" /><div><p className="auth-eyebrow">CONSOLIDATION FAILED</p><h2>We couldn’t consolidate these workbooks</h2><p role="alert">{run.safe_error_message || 'The workbooks could not be processed safely.'}</p><button type="button" onClick={onStartNew}>Start New Consolidation</button></div>
  </section>;
  if (run.status === 'EXPIRED' || run.status === 'DELETED') return <section className="management-panel gstr-result is-failed">
    <AlertTriangle aria-hidden="true" /><div><h2>{run.status === 'EXPIRED' ? 'Result expired' : 'Run deleted'}</h2><p>This temporary result is no longer available. Start a new consolidation when needed.</p><button type="button" onClick={onStartNew}>Start New Consolidation</button></div>
  </section>;
  if (run.status !== 'COMPLETED') return null;

  const summary = run.result_summary;
  const states = summary?.metadata_verification_states || {};
  const warning = (summary?.duplicate_warning_count || 0) > 0
    || Object.values(states).some(status => status && status !== 'VERIFIED');
  return <section className={`management-panel gstr-result ${warning ? 'has-warnings' : 'is-success'}`}>
    {warning ? <AlertTriangle aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}
    <div className="gstr-result-body">
      <p className="auth-eyebrow">{warning ? 'SUCCESS WITH WARNINGS' : 'SUCCESS'}</p>
      <h2>GSTR consolidation complete</h2>
      <p>{warning ? 'Your verified workbook is ready. Review the metadata and duplicate warnings below.' : 'Your verified consolidated workbook is ready to download.'}</p>
      <dl className="gstr-summary-grid">
        <div><dt>Financial year</dt><dd>{run.financial_year}</dd></div>
        <div><dt>GSTIN</dt><dd>{gstin || 'Selected registration'}</dd></div>
        <div><dt>Files processed</dt><dd>{summary?.files_processed ?? run.files.length}</dd></div>
        <div><dt>Total rows</dt><dd>{summary?.total_rows_merged?.toLocaleString('en-IN') ?? '—'}</dd></div>
        <div><dt>Duplicate warnings</dt><dd>{summary?.duplicate_warning_count ?? 0}</dd></div>
        <div><dt>Integrity</dt><dd>{summary?.integrity_passed ? 'Passed' : 'Not reported'}</dd></div>
      </dl>
      {summary?.categories_merged?.length ? <div className="gstr-category-summary"><h3>Rows by category</h3>{summary.categories_merged.map(category => <span key={category}>{category}: {(summary.rows_merged_per_category?.[category] ?? 0).toLocaleString('en-IN')}</span>)}</div> : null}
      {Object.keys(states).length ? <div className="gstr-category-summary"><h3>Metadata verification</h3>{Object.entries(states).map(([key, value]) => <span key={key}>{key.replace('_', ' ')}: {value}</span>)}</div> : null}
      <div className="gstr-result-actions">
        {run.artifact_available ? <a className="management-primary" href={gstrMergeDownloadUrl(workspaceId, clientId, run.id)}><Download size={16} aria-hidden="true" /> Download consolidated workbook</a> : <span>Download is no longer available.</span>}
        <button type="button" onClick={onStartNew}>Start New Consolidation</button>
      </div>
    </div>
  </section>;
}
