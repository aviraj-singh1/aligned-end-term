import React, { useRef, useState } from 'react';
import { useModalFocus } from '../common/useModalFocus';
import { X, CheckCircle2 } from 'lucide-react';
import type { ReconciliationResult, CandidateModel } from '../../types/api';
import { formatCurrency, ResultStatusBadge } from '../results/ResultStatusBadge';
import { ResultComparison, dateMismatchExplanation, formatEvidenceForDisplay, resultExplanation } from '../results/ResultComparison';
import { ConfirmMatchDialog } from './ConfirmMatchDialog';

interface AmbiguousReviewProps {
  isOpen: boolean;
  onClose: () => void;
  result: ReconciliationResult | null;
  onManualMatch: (resultId: string, candidateId: string) => Promise<void>;
}

const formatDate = (value: string | null | undefined): string => {
  if (!value) return '—';
  const [year, month, day] = value.split('-');
  return year && month && day ? `${day}-${month}-${year}` : '—';
};

const displayText = (value: string | null | undefined): string => value || '—';

export const AmbiguousReview: React.FC<AmbiguousReviewProps> = ({ isOpen, onClose, result, onManualMatch }) => {
  const [selectedCandidate, setSelectedCandidate] = useState<CandidateModel | null>(null);
  const [isConfirming, setIsConfirming] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const dialogRef = useRef<HTMLElement>(null);
  useModalFocus(dialogRef, isOpen && !!result, onClose, !isConfirming && !isSubmitting);

  if (!isOpen || !result) return null;

  const handleSelect = (candidate: CandidateModel) => {
    setSelectedCandidate(candidate);
    setIsConfirming(true);
  };

  const handleConfirm = async () => {
    if (!selectedCandidate) return;
    setIsSubmitting(true);
    try {
      await onManualMatch(result.result_id, selectedCandidate.candidate_id);
      setIsConfirming(false);
      onClose();
    } catch {
      // The parent owns the actionable error message; keep this drawer open.
    } finally {
      setIsSubmitting(false);
    }
  };

  const supplier = result.accounts_supplier || result.supplier || '—';
  const gstin = result.accounts_gstin || result.gstin || '—';
  const dateExplanation = dateMismatchExplanation(result);

  return (
    <>
      <div className="fixed inset-0 z-50 bg-slate-900/30 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <aside ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="manual-result-title"
        className="fixed inset-y-0 right-0 z-[55] flex w-full max-w-[35rem] flex-col bg-white shadow-xl [overflow-wrap:anywhere]">
        <header className="shrink-0 border-b border-slate-200 bg-white px-5 py-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 id="manual-result-title" className="text-base font-semibold text-slate-900">Result details</h2>
                <ResultStatusBadge status={result.status} />
                {result.date_mismatch && <span className="inline-block w-fit rounded-full border border-status-warning-line bg-status-warning-soft px-2.5 py-1 text-xs font-semibold text-status-warning">Date Mismatch</span>}
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
          <section aria-labelledby="manual-why-heading" className="rounded-lg border border-slate-200 bg-slate-50 p-3.5">
            <h3 id="manual-why-heading" className="text-sm font-semibold text-slate-900">Why this result?</h3>
            <p className="mt-1 text-sm leading-5 text-slate-700">{resultExplanation(result)}</p>
            {dateExplanation && <p className="mt-1 text-sm leading-5 text-status-warning">{dateExplanation}</p>}
            <dl className="mt-3 grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5 border-t border-slate-200 pt-3 text-xs">
              {result.remarks?.trim() && <><dt className="text-slate-500">Evidence</dt><dd className="font-medium text-slate-800">{formatEvidenceForDisplay(result.remarks.trim())}</dd></>}
              <dt className="text-slate-500">Match method</dt>
              <dd className="font-medium text-slate-800">{result.match_method || '—'}</dd>
            </dl>
          </section>

          <div data-testid="tally-source">
            <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
              <span><span className="font-medium text-slate-700">Accounts source:</span> {displayText(result.accounts_supplier)}</span>
              <span>{displayText(result.accounts_gstin)}</span>
            </div>
            <ResultComparison result={result} />
          </div>

          <section aria-labelledby="manual-match-heading" className="border-t border-slate-200 pt-5">
            <h3 id="manual-match-heading" className="text-base font-semibold text-slate-900">Manual Match</h3>
            <p className="mt-1 text-sm text-slate-600">Select the correct GSTR-2B record if this invoice should be linked manually.</p>
            <h4 className="mt-4 text-sm font-semibold text-slate-800">Select the corresponding GSTR-2B invoice</h4>
            <p className="mt-0.5 text-xs text-slate-500">{result.candidates?.length || 0} candidate{result.candidates?.length === 1 ? '' : 's'} available</p>

            <div className="mt-3 space-y-2.5">
              {result.candidates?.map(candidate => {
                const selected = selectedCandidate?.candidate_id === candidate.candidate_id;
                return (
                  <div key={candidate.candidate_id} data-testid={`gstr-candidate-${candidate.candidate_id}`}
                    className={`rounded-lg border p-3 transition-colors ${selected ? 'border-brand-400 bg-brand-50' : 'border-slate-200 bg-white hover:border-brand-300'}`}>
                    <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                      <div className="col-span-2"><p className="text-slate-500">Supplier</p><p title={candidate.supplier} className="truncate font-medium text-slate-900">{displayText(candidate.supplier)}</p></div>
                      <div><p className="text-slate-500">GSTIN</p><p className="font-medium text-slate-900">{displayText(candidate.gstin)}</p></div>
                      <div><p className="text-slate-500">Invoice</p><p className="font-medium text-slate-900">{displayText(candidate.gst_invoice)}</p></div>
                      <div><p className="text-slate-500">Date</p><p className="font-medium text-slate-900">{formatDate(candidate.invoice_date)}</p></div>
                      <div><p className="text-slate-500">Taxable Value</p><p className="font-medium tabular-nums text-slate-900">{formatCurrency(candidate.taxable_value)}</p></div>
                      <div><p className="text-slate-500">IGST</p><p className="font-medium tabular-nums text-slate-900">{formatCurrency(candidate.igst)}</p></div>
                      <div><p className="text-slate-500">CGST / SGST</p><p className="font-medium tabular-nums text-slate-900">{formatCurrency(candidate.cgst)} / {formatCurrency(candidate.sgst)}</p></div>
                    </div>
                    <button type="button" aria-pressed={selected} onClick={() => handleSelect(candidate)}
                      className="mt-3 inline-flex w-full items-center justify-center rounded-md border border-brand-200 bg-white px-3 py-2 text-sm font-medium text-brand-700 transition-colors hover:bg-brand-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
                      <CheckCircle2 className="mr-2 h-4 w-4" />
                      {selected ? 'Selected' : 'Select This Invoice'}
                    </button>
                  </div>
                );
              })}
              {!result.candidates?.length && (
                <p className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-5 text-center text-sm text-slate-600">No eligible GSTR-2B candidates found.</p>
              )}
            </div>
          </section>
        </div>
      </aside>

      <ConfirmMatchDialog isOpen={isConfirming} onClose={() => setIsConfirming(false)} onConfirm={handleConfirm}
        isLoading={isSubmitting} tallyInvoice={result.accounts_invoice} candidate={selectedCandidate} />
    </>
  );
};
