import React from 'react';
import type { PreviewRecord } from '../../types/api';

interface Props {
  records: PreviewRecord[];
  type: 'accounts' | 'gstr' | 'debit_notes' | 'credit_notes';
}

export const PreviewTable: React.FC<Props> = ({ records, type }) => {
  if (records.length === 0) {
    return (
      <div className="p-8 text-center border-t border-slate-100">
        <p className="text-slate-500 text-sm">Sheet detected successfully, but no transaction rows were found.</p>
      </div>
    );
  }

  const invoiceLabel = type.includes('notes') ? 'Note Number' : 'Invoice Number';
  const dateLabel = type.includes('notes') ? 'Note Date' : 'Invoice Date';

  const formatMoney = (val: string | null | undefined) => {
    if (val === null || val === undefined || val.trim() === '') return '—';
    const num = parseFloat(val);
    if (isNaN(num)) return val;
    return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(num);
  };

  const formatDate = (val: string | null) => {
    if (!val) return '—';
    try {
      // Basic formatting, assuming ISO from backend
      const date = new Date(val);
      if (isNaN(date.getTime())) return val;
      return date.toLocaleDateString('en-IN', { day: '2-digit', month: '2-digit', year: 'numeric' }).replace(/\//g, '-');
    } catch {
      return val;
    }
  };

  return (
    <div role="region" aria-label="Data preview table" tabIndex={0} className="max-h-[420px] overflow-auto focus-visible:outline-2 focus-visible:outline-brand-500 focus-visible:-outline-offset-2">
      <table className="w-full min-w-[920px] border-separate border-spacing-0 text-left text-[13px]">
        <thead className="text-[11px] uppercase tracking-[0.05em] text-muted">
          <tr>
            <th className="sticky top-0 z-10 border-y border-line bg-surface-subtle px-4 py-3 font-medium">Supplier</th>
            <th className="sticky top-0 z-10 border-y border-line bg-surface-subtle px-4 py-3 font-medium">GSTIN</th>
            <th className="sticky top-0 z-10 border-y border-line bg-surface-subtle px-4 py-3 font-medium">{invoiceLabel}</th>
            <th className="sticky top-0 z-10 border-y border-line bg-surface-subtle px-4 py-3 font-medium">{dateLabel}</th>
            <th className="sticky top-0 z-10 border-y border-line bg-surface-subtle px-4 py-3 text-right font-medium">Taxable</th>
            <th className="sticky top-0 z-10 border-y border-line bg-surface-subtle px-4 py-3 text-right font-medium">IGST</th>
            <th className="sticky top-0 z-10 border-y border-line bg-surface-subtle px-4 py-3 text-right font-medium">CGST</th>
            <th className="sticky top-0 z-10 border-y border-line bg-surface-subtle px-4 py-3 text-right font-medium">SGST</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {records.map((r, idx) => (
            <tr key={idx} className="hover:bg-slate-50">
              <td className="max-w-[220px] truncate px-4 py-2.5 text-ink" title={r.supplier || ''}>{r.supplier || '—'}</td>
              <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs text-slate-600">{r.gstin || '—'}</td>
              <td className="whitespace-nowrap px-4 py-2.5 font-medium text-ink">{r.invoice_number || '—'}</td>
              <td className="whitespace-nowrap px-4 py-2.5 tabular-nums text-slate-600">{formatDate(r.date)}</td>
              <td className="whitespace-nowrap px-4 py-2.5 text-right font-medium tabular-nums text-ink">{formatMoney(r.taxable_value)}</td>
              <td className="whitespace-nowrap px-4 py-2.5 text-right tabular-nums text-slate-600">{formatMoney(r.igst)}</td>
              <td className="whitespace-nowrap px-4 py-2.5 text-right tabular-nums text-slate-600">{formatMoney(r.cgst)}</td>
              <td className="whitespace-nowrap px-4 py-2.5 text-right tabular-nums text-slate-600">{formatMoney(r.sgst)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
