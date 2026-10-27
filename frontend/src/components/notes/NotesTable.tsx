import React from 'react';
import type { ReconciliationResult } from '../../types/api';
import { ResultStatusBadge, formatCurrency } from '../results/ResultStatusBadge';
import { ChevronUp, ChevronDown } from 'lucide-react';
import { WideTableFrame } from '../results/WideTableFrame';

interface NotesTableProps {
  results: ReconciliationResult[];
  onRowClick: (result: ReconciliationResult) => void;
  sortField: string;
  sortOrder: 'asc' | 'desc';
  onSort: (field: string) => void;
  emptyMessage?: string;
}

export const NotesTable: React.FC<NotesTableProps> = ({ results, onRowClick, sortField, sortOrder, onSort, emptyMessage }) => {
  const SortIcon = ({ field }: { field: string }) => {
    if (sortField !== field) return <ChevronUp className="w-4 h-4 text-slate-300 inline-block ml-1" />;
    return sortOrder === 'asc' ? <ChevronUp className="w-4 h-4 inline-block ml-1" /> : <ChevronDown className="w-4 h-4 inline-block ml-1" />;
  };

  const thClass = "px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider cursor-pointer hover:bg-slate-100 transition-colors";
  const sortableHeader = (label: string, field: string) => (
    <th scope="col" aria-sort={sortField === field ? (sortOrder === 'asc' ? 'ascending' : 'descending') : undefined} className={thClass}>
      <button type="button" onClick={() => onSort(field)} className="inline-flex items-center rounded-sm text-left uppercase focus-visible:ring-2 focus-visible:ring-brand-500">
        {label} <SortIcon field={field} />
      </button>
    </th>
  );

  return (
    <WideTableFrame label="Notes reconciliation results. Scroll horizontally to view all columns.">
      <table className="w-full min-w-[1840px] table-fixed divide-y divide-slate-200">
        <colgroup>
          <col className="w-[220px]" /><col className="w-[160px]" /><col className="w-[170px]" /><col className="w-[190px]" />
          <col className="w-[130px]" /><col className="w-[110px]" /><col className="w-[110px]" /><col className="w-[110px]" />
          <col className="w-[105px]" /><col className="w-[105px]" /><col className="w-[105px]" /><col className="w-[170px]" />
          <col className="w-[220px]" /><col className="w-[80px]" />
        </colgroup>
        <thead className="bg-slate-50">
          <tr>
            <th scope="col" aria-sort={sortField === 'supplier' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : undefined} className={`${thClass} sticky left-0 z-20 bg-slate-50 shadow-[1px_0_0_0_rgb(226,232,240)]`}>
              <button type="button" onClick={() => onSort('supplier')} className="inline-flex items-center rounded-sm text-left uppercase focus-visible:ring-2 focus-visible:ring-brand-500">Supplier <SortIcon field="supplier" /></button>
            </th>
            {sortableHeader('GSTIN', 'gstin')}
            {sortableHeader('Accounts Debit Note', 'accounts_invoice')}
            {sortableHeader('GSTR-2B Credit Note', 'gstr_invoice')}
            {sortableHeader('Taxable Value', 'taxable_value')}
            {["GST IGST", "GST CGST", "GST SGST"].map(label => <th key={label} className={thClass}>{label}</th>)}
            <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">IGST Diff</th>
            <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">CGST Diff</th>
            <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">SGST Diff</th>
            {sortableHeader('Method', 'match_method')}
            {sortableHeader('Status', 'status')}
            <th className="sticky right-0 z-20 bg-slate-50 px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-slate-500 shadow-[-1px_0_0_0_rgb(226,232,240)]">Action</th>
          </tr>
        </thead>
        <tbody className="bg-white divide-y divide-slate-200">
          {results.length === 0 ? (
            <tr>
              <td colSpan={14} className="px-6 py-12 text-center text-slate-500">
                {emptyMessage || 'No notes found matching your criteria.'}
              </td>
            </tr>
          ) : (
            results.map((result) => (
              <tr 
                key={result.result_id} 
                onClick={() => onRowClick(result)}
                className="group cursor-pointer transition-colors hover:bg-brand-50"
              >
                <td className="sticky left-0 z-10 truncate bg-white px-4 py-3 text-sm text-slate-900 shadow-[1px_0_0_0_rgb(226,232,240)] group-hover:bg-brand-50" title={result.supplier}>{result.supplier}</td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-slate-500">{result.gstin}</td>
                <td className="px-4 py-3 text-sm text-slate-900">{result.accounts_invoice || '—'}</td>
                <td className="px-4 py-3 text-sm text-slate-900">
                  <span>{result.gst_invoice || '—'}</span>
                  {result.gstr_source_category && <span className="ml-2 rounded bg-violet-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-violet-700">{result.gstr_source_category}</span>}
                </td>
                <td className="px-4 py-3 text-sm text-slate-900">{formatCurrency(result.taxable_value)}</td>
                {[result.gst_igst, result.gst_cgst, result.gst_sgst].map((value, index) => <td key={index} className="px-4 py-3 text-sm tabular-nums text-slate-900">{formatCurrency(value)}</td> )}
                <td className="px-4 py-3 text-sm text-slate-500">{formatCurrency(result.igst_diff)}</td>
                <td className="px-4 py-3 text-sm text-slate-500">{formatCurrency(result.cgst_diff)}</td>
                <td className="px-4 py-3 text-sm text-slate-500">{formatCurrency(result.sgst_diff)}</td>
                <td className="px-4 py-3 text-sm text-slate-500 whitespace-nowrap">{result.match_method}</td>
                <td className="px-4 py-3 text-sm whitespace-nowrap"><ResultStatusBadge status={result.status} /></td>
                <td className="sticky right-0 z-10 bg-white px-4 py-3 text-right text-sm font-medium text-brand-600 shadow-[-1px_0_0_0_rgb(226,232,240)] group-hover:bg-brand-50"><button type="button" onClick={(event) => { event.stopPropagation(); onRowClick(result); }} className="rounded px-1 py-0.5 hover:underline focus-visible:ring-2 focus-visible:ring-brand-500">View</button></td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </WideTableFrame>
  );
};
