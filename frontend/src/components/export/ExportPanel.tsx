import React, { useState } from 'react';
import { Braces, Download, FileSpreadsheet, Loader2 } from 'lucide-react';
import { downloadExcel, downloadJson, downloadNotesExcel } from '../../services/api';
import { triggerBrowserDownload } from '../../utils/downloadHelper';
import type { ToastMessage } from '../common/Toast';

interface ExportPanelProps {
  sessionId: string;
  counts: { accounts_invoices: number; gstr_invoices: number };
  notesCount: number;
  addToast: (toast: Omit<ToastMessage, 'id'>) => void;
  onSessionExpired: () => void;
}

type ExportType = 'purchase' | 'notes' | 'json';

const apiErrorDetail = (error: unknown): string | null => {
  if (!error || typeof error !== 'object' || !('response' in error)) return null;
  const response = (error as { response?: { data?: { detail?: unknown } } }).response;
  return typeof response?.data?.detail === 'string' ? response.data.detail : null;
};

export const ExportPanel: React.FC<ExportPanelProps> = ({ sessionId, counts, notesCount, addToast, onSessionExpired }) => {
  const [exporting, setExporting] = useState<Partial<Record<ExportType, boolean>>>({});
  const [errors, setErrors] = useState<Partial<Record<ExportType, string>>>({});
  const [completed, setCompleted] = useState<Partial<Record<ExportType, boolean>>>({});

  const handleExport = async (type: ExportType) => {
    const isJson = type === 'json';
    setErrors(current => ({ ...current, [type]: undefined }));
    setCompleted(current => ({ ...current, [type]: false }));
    setExporting(current => ({ ...current, [type]: true }));

    try {
      const response = await (type === 'purchase' ? downloadExcel(sessionId) : type === 'notes' ? downloadNotesExcel(sessionId) : downloadJson(sessionId));
      triggerBrowserDownload(response.blob, response.filename);
      setCompleted(current => ({ ...current, [type]: true }));
      addToast({ message: `${type === 'purchase' ? 'Purchase' : type === 'notes' ? 'Notes' : 'JSON'} report downloaded.`, type: 'success' });
    } catch (error: unknown) {
      const responseStatus = error && typeof error === 'object' && 'response' in error
        ? (error as { response?: { status?: number } }).response?.status
        : undefined;
      if (responseStatus === 404) {
        addToast({ message: 'This reconciliation session has expired. Upload the files again to create a new report.', type: 'error' });
        onSessionExpired();
      } else {
        const fallback = `Unable to generate the ${type === 'purchase' ? 'Purchase' : type === 'notes' ? 'Notes' : 'JSON'} report. Please try again.`;
        // Axios downloads use responseType=blob, so do not depend on parsed
        // response.data.detail for the controlled JSON transport rejection.
        const message = isJson && responseStatus === 413
          ? 'JSON export is too large for the current pilot deployment. Excel export is still available.'
          : apiErrorDetail(error) || fallback;
        setErrors(current => ({ ...current, [type]: message }));
        addToast({ message, type: 'error' });
      }
    } finally {
      setExporting(current => ({ ...current, [type]: false }));
    }
  };

  return (
    <section className="mt-6 overflow-hidden rounded-panel border border-slate-200 bg-white shadow-sm" aria-labelledby="export-heading">
      <div className="border-b border-slate-200 bg-slate-50 px-5 py-4 sm:px-6">
        <h3 id="export-heading" className="text-lg font-semibold text-slate-900">Export reconciliation</h3>
        <p className="mt-1 text-sm text-slate-600">Your reconciliation is ready to download.</p>
        <p className="mt-2 text-xs text-slate-500">
          Reconciliation complete · {counts.accounts_invoices} Accounts invoices · {counts.gstr_invoices} GSTR-2B invoices
        </p>
      </div>

      <div className="grid gap-4 p-4 sm:p-5 lg:grid-cols-3">
        <article className="flex flex-col rounded-lg border border-brand-200 bg-white p-4 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-start gap-3">
              <span className="rounded-md bg-brand-50 p-2 text-brand-700"><FileSpreadsheet className="h-5 w-5" aria-hidden="true" /></span>
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h4 className="font-semibold text-slate-900">Purchase reconciliation</h4>
                  <span className="rounded border border-brand-200 bg-brand-50 px-1.5 py-0.5 text-[11px] font-semibold text-brand-700">Recommended</span>
                </div>
                <p className="mt-1 text-sm text-slate-600">Purchase-only workbook with summary, differences, missing invoices and supporting sheets.</p>
              </div>
            </div>
          </div>
          <div className="mt-4 flex flex-col items-start gap-2 sm:flex-row sm:items-center">
            <button type="button" onClick={() => handleExport('purchase')} disabled={!!exporting.purchase} aria-busy={!!exporting.purchase}
              aria-describedby={errors.purchase ? 'purchase-export-error' : undefined}
              className="inline-flex w-full items-center justify-center rounded-control bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2">
              {exporting.purchase ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" /> : <Download className="mr-2 h-4 w-4" aria-hidden="true" />}
              {exporting.purchase ? 'Preparing Purchase...' : 'Download Purchase Report'}
            </button>
            {completed.purchase && <span className="text-xs font-medium text-emerald-700" role="status">Purchase download started.</span>}
          </div>
          {errors.purchase && <div id="purchase-export-error" className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700" role="alert">{errors.purchase}</div>}
        </article>

        <article className="flex flex-col rounded-lg border border-violet-200 bg-white p-4 shadow-sm">
          <div className="flex items-start gap-3">
            <span className="rounded-md bg-violet-50 p-2 text-violet-700"><FileSpreadsheet className="h-5 w-5" aria-hidden="true" /></span>
            <div><h4 className="font-semibold text-slate-900">Notes reconciliation</h4>
              <p className="mt-1 text-sm text-slate-600">Notes-only audit workbook with category sheets and source provenance.</p></div>
          </div>
          <div className="mt-auto pt-4">
            <button type="button" onClick={() => handleExport('notes')} disabled={notesCount === 0 || !!exporting.notes} aria-busy={!!exporting.notes}
              aria-describedby={errors.notes ? 'notes-export-error' : undefined}
              className="inline-flex w-full items-center justify-center rounded-control border border-violet-300 bg-white px-4 py-2.5 text-sm font-semibold text-violet-800 hover:bg-violet-50 disabled:cursor-not-allowed disabled:opacity-50">
              {exporting.notes ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" /> : <Download className="mr-2 h-4 w-4" aria-hidden="true" />}
              {exporting.notes ? 'Preparing Notes...' : 'Download Notes Report'}
            </button>
            {notesCount === 0 && <p className="mt-2 text-xs text-slate-500">No Notes data was included in this run.</p>}
            {completed.notes && <p className="mt-2 text-xs font-medium text-emerald-700" role="status">Notes download started.</p>}
          </div>
          {errors.notes && <div id="notes-export-error" className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700" role="alert">{errors.notes}</div>}
        </article>

        <article className="flex flex-col rounded-lg border border-slate-200 bg-slate-50/40 p-4">
          <div className="flex items-start gap-3">
            <span className="rounded-md bg-slate-100 p-2 text-slate-600"><Braces className="h-5 w-5" aria-hidden="true" /></span>
            <div>
              <h4 className="font-semibold text-slate-900">JSON export</h4>
              <p className="mt-1 text-sm text-slate-600">Structured reconciliation data for integrations or technical workflows.</p>
            </div>
          </div>
          <div className="mt-auto pt-4">
            <button type="button" onClick={() => handleExport('json')} disabled={!!exporting.json} aria-busy={!!exporting.json}
              aria-describedby={errors.json ? 'json-export-error' : undefined}
              className="inline-flex w-full items-center justify-center rounded-control border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2">
              {exporting.json ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" /> : <Download className="mr-2 h-4 w-4" aria-hidden="true" />}
              {exporting.json ? 'Preparing JSON...' : 'Download JSON'}
            </button>
            {completed.json && <p className="mt-2 text-xs font-medium text-emerald-700" role="status">JSON download started.</p>}
          </div>
          {errors.json && <div id="json-export-error" className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700" role="alert">{errors.json}</div>}
        </article>
      </div>
    </section>
  );
};
