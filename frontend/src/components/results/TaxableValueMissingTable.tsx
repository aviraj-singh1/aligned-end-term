import React from 'react';
import type { ReconciliationResult } from '../../types/api';
import { formatCurrency, ResultStatusBadge } from './ResultStatusBadge';

interface Props {
  results: ReconciliationResult[];
  onRowClick: (result: ReconciliationResult) => void;
  emptyMessage?: string;
}

export const TaxableValueMissingTable: React.FC<Props> = ({ results, onRowClick, emptyMessage }) => (
  <div className="overflow-x-auto">
    <table className="min-w-full divide-y divide-slate-200">
      <thead className="bg-slate-50"><tr>
        {['Supplier', 'GSTIN', 'Accounts Invoice', 'GSTR-2B Invoice', 'Accounts Date', 'GSTR-2B Taxable Value', 'IGST', 'CGST', 'SGST', 'Method', 'Status', 'Reason'].map(header => (
          <th key={header} className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">{header}</th>
        ))}
      </tr></thead>
      <tbody className="bg-white divide-y divide-slate-200">
        {results.map(result => (
          <tr key={result.result_id} onClick={() => onRowClick(result)} className="hover:bg-brand-50 cursor-pointer">
            <td className="px-4 py-3 text-sm"><button type="button" aria-label={`View ${result.supplier || result.accounts_invoice}`} onClick={(event) => { event.stopPropagation(); onRowClick(result); }} className="rounded text-left hover:underline focus-visible:ring-2 focus-visible:ring-brand-500">{result.supplier || 'View'}</button></td>
            <td className="px-4 py-3 text-sm text-slate-500">{result.gstin || '—'}</td>
            <td className="px-4 py-3 text-sm">{result.accounts_invoice || '—'}</td>
            <td className="px-4 py-3 text-sm">{result.gst_invoice || '—'}</td>
            <td className="px-4 py-3 text-sm">{result.accounts_date || '—'}</td>
            <td className="px-4 py-3 text-sm">{result.gst_invoice ? formatCurrency(result.gst_taxable) : '—'}</td>
            <td className="px-4 py-3 text-sm">{formatCurrency(result.accounts_igst)}</td>
            <td className="px-4 py-3 text-sm">{formatCurrency(result.accounts_cgst)}</td>
            <td className="px-4 py-3 text-sm">{formatCurrency(result.accounts_sgst)}</td>
            <td className="px-4 py-3 text-sm">{result.match_method}</td>
            <td className="px-4 py-3 text-sm"><ResultStatusBadge status={result.status} /></td>
            <td className="px-4 py-3 text-sm">{result.remarks}</td>
          </tr>
        ))}
        {!results.length && <tr><td colSpan={12} className="px-6 py-12 text-center text-slate-500">{emptyMessage || 'No records have a missing taxable value.'}</td></tr>}
      </tbody>
    </table>
  </div>
);
