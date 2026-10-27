import React from 'react';
import { clsx } from 'clsx';
import { Check, AlertTriangle } from 'lucide-react';
import type { FileInspection } from '../../types/api';

interface Props {
  title: string;
  detectedMappings: Record<string, string>;
  availableHeaders: string[];
  requiredFields: string[];
  fileInfo: FileInspection;
  onMappingChange?: (semanticField: string, newHeader: string) => void;
  sourceType?: 'accounts' | 'gstr';
}

export const MappingTable: React.FC<Props> = ({ title, detectedMappings, availableHeaders, requiredFields, fileInfo, onMappingChange, sourceType }) => {
  // semantic concepts we expect
  const concepts = [
    'Supplier', 'GSTIN', 'Invoice Number', 'Invoice Date', 
    'Taxable Value', sourceType === 'gstr' ? 'Invoice Value' : 'Gross Total',
    'IGST', 'CGST', 'SGST'
  ];
  const mappedCount = concepts.filter((concept) => Boolean(detectedMappings[concept])).length;
  const missingRequiredCount = requiredFields.filter((concept) => !detectedMappings[concept]).length;
  const isComplete = mappedCount === concepts.length;

  return (
    <section className="overflow-hidden rounded-panel border border-line bg-white">
      <div className="flex min-h-[92px] items-start justify-between gap-4 border-b border-line bg-surface-subtle px-5 py-4">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-brand-700">Source mapping</p>
          <h3 className="mt-0.5 text-base font-semibold text-ink">{title}</h3>
          <p className="mt-1 truncate text-xs text-muted" title={fileInfo.original_filename}>{fileInfo.original_filename}</p>
          <p className="mt-0.5 text-xs text-muted">
            {fileInfo.selected_sheet && <span>Sheet: {fileInfo.selected_sheet} · </span>}
            {fileInfo.row_count.toLocaleString('en-IN')} rows
          </p>
        </div>
        <div className="shrink-0 text-right">
          <p className={clsx("text-xs font-semibold", isComplete ? "text-status-perfect" : "text-slate-700")}>
            {mappedCount} of {concepts.length} mapped
          </p>
          {missingRequiredCount > 0 ? (
            <p className="mt-1 text-[11px] font-medium text-rose-700">
              {missingRequiredCount} required {missingRequiredCount === 1 ? 'field' : 'fields'} missing
            </p>
          ) : (
            <p className="mt-1 text-[11px] text-muted">Required fields ready</p>
          )}
        </div>
      </div>
      <div>
        <table className="w-full table-fixed text-left text-[13px]">
          <thead className="border-b border-line bg-white text-[11px] uppercase tracking-[0.05em] text-muted">
            <tr>
              <th className="w-[34%] px-5 py-2.5 font-semibold">Product field</th>
              <th className="px-5 py-2.5 font-semibold">Source column</th>
              <th className="w-16 px-3 py-2.5 text-center font-semibold">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {concepts.map((concept) => {
              const isRequired = requiredFields.includes(concept);
              const mappedValue = detectedMappings[concept] || '';
              
              const isResolved = mappedValue !== '';
              const isMissingRequired = isRequired && !isResolved;
              const fieldId = `${title}-${concept}`.toLowerCase().replace(/[^a-z0-9]+/g, '-');

              return (
                <tr key={concept} className={clsx("hover:bg-surface-subtle", isMissingRequired && "bg-rose-50/70 hover:bg-rose-50")}>
                  <td className="px-5 py-2.5 font-medium text-slate-700">
                    <label htmlFor={fieldId}>
                      {concept} {isRequired && <span className="text-rose-600" title="Required">*</span>}
                    </label>
                  </td>
                  <td className="px-5 py-2.5">
                    <select
                      id={fieldId}
                      aria-required={isRequired}
                      className={clsx(
                        "block h-9 w-full rounded-control border bg-white px-2.5 text-[13px] text-slate-700 focus:border-brand-500 focus:ring-brand-500",
                        isMissingRequired ? "border-rose-300" : "border-line-strong"
                      )}
                      value={mappedValue}
                      onChange={(e) => onMappingChange?.(concept, e.target.value)}
                    >
                      <option value="">-- Not mapped --</option>
                      {availableHeaders.map((h) => (
                        <option key={h} value={h}>{h}</option>
                      ))}
                    </select>
                  </td>
                  <td className="px-3 py-2.5 text-center">
                    {isResolved ? (
                      <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-status-perfect-soft text-status-perfect" title="Mapped">
                        <Check className="h-3 w-3" strokeWidth={2.5} aria-hidden="true" />
                        <span className="sr-only">Mapped</span>
                      </span>
                    ) : isMissingRequired ? (
                      <span className="inline-flex text-rose-700" title="Required field missing">
                        <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                        <span className="sr-only">Required field missing</span>
                      </span>
                    ) : (
                      <span className="text-slate-300" aria-label="Not mapped">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
};
