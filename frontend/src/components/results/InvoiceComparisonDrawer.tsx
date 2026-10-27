import React, { useRef } from 'react';
import { useModalFocus } from '../common/useModalFocus';
import { X } from 'lucide-react';
import type { ReconciliationResult, NotInTallyResult } from '../../types/api';
import { ResultStatusBadge, formatCurrency } from './ResultStatusBadge';
import { ResultComparison, dateMismatchExplanation, formatEvidenceForDisplay, resultExplanation } from './ResultComparison';

interface DrawerProps {
  isOpen: boolean;
  onClose: () => void;
  result: ReconciliationResult | NotInTallyResult | null;
  type?: 'purchase' | 'note' | 'notin';
}

export const InvoiceComparisonDrawer: React.FC<DrawerProps> = ({ isOpen, onClose, result, type = 'purchase' }) => {
  const dialogRef = useRef<HTMLElement>(null);
  useModalFocus(dialogRef, isOpen && !!result, onClose);

  if (!isOpen || !result) return null;

  const isNotInAccounts = type === 'notin' || ('status' in result && result.status === 'Not in Tally');
  const status = 'status' in result && result.status ? result.status : 'Not in Tally';
  const supplier = result.supplier || '—';
  const gstin = result.gstin || '—';

  return (
    <>
      <div className="fixed inset-0 z-50 bg-slate-900/20 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <aside
        role="dialog"
        ref={dialogRef}
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby="result-drawer-title"
        className="fixed inset-y-0 right-0 z-[55] flex w-full max-w-[32rem] flex-col bg-white shadow-xl [overflow-wrap:anywhere]"
      >
        <header className="shrink-0 border-b border-slate-200 bg-white px-5 py-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 id="result-drawer-title" className="text-base font-semibold text-slate-900">Result details</h2>
                <ResultStatusBadge status={status} />
                {'date_mismatch' in result && result.date_mismatch && (
                  <span className="inline-block w-fit rounded-full border border-status-warning-line bg-status-warning-soft px-2.5 py-1 text-xs font-semibold text-status-warning">Date Mismatch</span>
                )}
              </div>
              <p className="mt-2 truncate text-base font-semibold text-slate-900" title={supplier}>{supplier}</p>
              <p className="mt-0.5 text-xs text-slate-500">GSTIN {gstin}</p>
            </div>
            <button type="button" onClick={onClose} aria-label="Close result details"
              className="rounded-md p-2 text-slate-500 transition-colors hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
              <X className="h-5 w-5" />
            </button>
          </div>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5">
          {isNotInAccounts ? (
            <>
              <section aria-labelledby="why-result-heading" className="rounded-lg border border-status-review-line bg-status-review-soft p-3.5">
                <h3 id="why-result-heading" className="text-sm font-semibold text-status-review">Why this result?</h3>
                <p className="mt-1 text-sm leading-5 text-slate-700">No corresponding Accounts record was found for this GSTR-2B invoice.</p>
              </section>
              <section aria-labelledby="gstr-detail-heading">
                <h3 id="gstr-detail-heading" className="mb-2 text-sm font-semibold text-slate-900">GSTR-2B record</h3>
                <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-2 rounded-lg border border-slate-200 p-3.5 text-sm">
                  <dt className="text-slate-500">Invoice</dt><dd className="font-medium text-slate-900">{result.gst_invoice || '—'}</dd>
                  <dt className="text-slate-500">Date</dt><dd>{result.gst_date || "—"}</dd>
                  {[ ["IGST", result.gst_igst], ["CGST", result.gst_cgst], ["SGST", result.gst_sgst] ].map(([label, value]) => <React.Fragment key={label}><dt className="text-slate-500">{label}</dt><dd className="tabular-nums">{formatCurrency(value)}</dd></React.Fragment>)}
                  <dt className="text-slate-500">Taxable Value</dt><dd className="font-medium tabular-nums text-slate-900">{formatCurrency(result.taxable_value)}</dd>
                </dl>
              </section>
            </>
          ) : (
            <StandardResultDetails result={result as ReconciliationResult} type={type} />
          )}
        </div>
      </aside>
    </>
  );
};

const StandardResultDetails: React.FC<{ result: ReconciliationResult; type: 'purchase' | 'note' | 'notin' }> = ({ result }) => {
  const dateExplanation = dateMismatchExplanation(result);
  const evidence = formatEvidenceForDisplay(result.evidence?.trim() || '');
  const remarks = formatEvidenceForDisplay(result.remarks?.trim() || '');
  const warning = remarks && remarks !== evidence ? remarks : '';
  return <>
    <section aria-labelledby="why-result-heading" className="rounded-lg border border-slate-200 bg-slate-50 p-3.5">
      <h3 id="why-result-heading" className="text-sm font-semibold text-slate-900">Why this result?</h3>
      <p className="mt-1 text-sm leading-5 text-slate-700">{resultExplanation(result)}</p>
      {dateExplanation && <p className="mt-1 text-sm leading-5 text-status-warning">{dateExplanation}</p>}
      <dl className="mt-3 grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5 border-t border-slate-200 pt-3 text-xs">
        {evidence && <><dt className="text-slate-500">Match evidence</dt><dd className="font-medium text-slate-800">{evidence}</dd></>}
        {warning && <><dt className="text-slate-500">{evidence ? 'Warning' : 'Evidence'}</dt><dd className="font-medium text-slate-800">{warning}</dd></>}
        <dt className="text-slate-500">Match method</dt>
        <dd className="font-medium text-slate-800">{result.match_method || '—'}</dd>
      </dl>
    </section>
    <ResultComparison result={result} />
  </>;
};
