import React from 'react';
import type { CancelledVoucher } from '../../types/api';

export const CancelledVouchersTable: React.FC<{ results: CancelledVoucher[]; emptyMessage?: string }> = ({ results, emptyMessage }) => (
  <div role="region" aria-label="Cancelled vouchers table" tabIndex={0} className="overflow-x-auto focus-visible:outline-2 focus-visible:outline-brand-500 focus-visible:-outline-offset-2">
    <table className="min-w-full divide-y divide-slate-200">
      <thead className="bg-slate-50"><tr>
        {['Supplier', 'GSTIN', 'Invoice/Voucher No.', 'Date', 'Taxable Value', 'IGST', 'CGST', 'SGST', 'Source', 'Status', 'Reason'].map(header =>
          <th key={header} className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">{header}</th>
        )}
      </tr></thead>
      <tbody className="bg-white divide-y divide-slate-200">
        {results.map(result => <tr key={result.id} className="hover:bg-slate-50">
          <td className="px-4 py-3 text-sm text-slate-900">{result.supplier || '—'}</td>
          <td className="px-4 py-3 text-sm text-slate-500">{result.gstin || '—'}</td>
          <td className="px-4 py-3 text-sm text-slate-900">{result.invoice_number || '—'}</td>
          <td className="px-4 py-3 text-sm text-slate-700">{result.invoice_date || '—'}</td>
          <td className="px-4 py-3 text-sm text-slate-900">{result.taxable_value}</td>
          <td className="px-4 py-3 text-sm text-slate-900">{result.igst}</td>
          <td className="px-4 py-3 text-sm text-slate-900">{result.cgst}</td>
          <td className="px-4 py-3 text-sm text-slate-900">{result.sgst}</td>
          <td className="px-4 py-3 text-sm text-slate-700">{result.source_system}</td>
          <td className="px-4 py-3 text-sm"><span className="rounded-full bg-slate-100 px-2 py-1 font-medium text-slate-700">{result.status}</span></td>
          <td className="px-4 py-3 text-sm text-slate-700">{result.cancellation_reason}</td>
        </tr>)}
        {results.length === 0 && <tr><td colSpan={11} className="px-6 py-12 text-center text-slate-500">{emptyMessage || 'No cancelled vouchers found.'}</td></tr>}
      </tbody>
    </table>
  </div>
);
