import React from 'react';
import type { ReconciliationResult } from '../../types/api';
import { ResultStatusBadge, formatCurrency } from './ResultStatusBadge';
import { ChevronUp, ChevronDown, CalendarClock } from 'lucide-react';
import { WideTableFrame } from './WideTableFrame';

interface ResultsTableProps {
  results: ReconciliationResult[];
  onRowClick: (result: ReconciliationResult) => void;
  sortField: string;
  sortOrder: 'asc' | 'desc';
  onSort: (field: string) => void;
  emptyMessage?: string;
}

export const ResultsTable: React.FC<ResultsTableProps> = ({ results, onRowClick, sortField, sortOrder, onSort, emptyMessage }) => {
  const SortIcon = ({ field }: { field: string }) => {
    if (sortField !== field) return <ChevronUp className="w-4 h-4 text-slate-300 inline-block ml-1" />;
    return sortOrder === 'asc' ? <ChevronUp className="w-4 h-4 inline-block ml-1" /> : <ChevronDown className="w-4 h-4 inline-block ml-1" />;
  };

  const thClass = "px-3 py-2.5 text-left text-xs font-medium text-slate-500 uppercase tracking-wider";
  const sortableHeader = (label: string, field: string, align: 'left' | 'right' = 'left') => (
    <th scope="col" aria-sort={sortField === field ? (sortOrder === 'asc' ? 'ascending' : 'descending') : undefined} className={`${thClass} ${align === 'right' ? 'text-right' : ''}`}>
      <button type="button" onClick={() => onSort(field)}
        className={`inline-flex w-full items-center gap-0.5 rounded-sm hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${align === 'right' ? 'justify-end' : ''}`}>
        {label} <SortIcon field={field} />
      </button>
    </th>
  );

  return (
    <WideTableFrame label="Purchase reconciliation results. Scroll horizontally to view all columns.">
      <table className="w-full min-w-[1580px] table-fixed divide-y divide-slate-200">
        <colgroup>
          <col className="w-[220px]" /><col className="w-[160px]" /><col className="w-[150px]" /><col className="w-[180px]" />
          <col className="w-[130px]" /><col className="w-[120px]" /><col className="w-[105px]" /><col className="w-[105px]" />
          <col className="w-[105px]" /><col className="w-[170px]" /><col className="w-[220px]" /><col className="w-[80px]" />
        </colgroup>
        <thead className="bg-slate-50">
          <tr>
            <th scope="col" aria-sort={sortField === 'supplier' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : undefined} className={`${thClass} sticky left-0 z-20 bg-slate-50 shadow-[1px_0_0_0_rgb(226,232,240)]`}>
              <button type="button" onClick={() => onSort('supplier')} className="inline-flex w-full items-center gap-0.5 rounded-sm hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">Supplier <SortIcon field="supplier" /></button>
            </th>
            {sortableHeader('GSTIN', 'gstin')}
            {sortableHeader('Accounts Invoice', 'accounts_invoice')}
            {sortableHeader('GSTR-2B Invoice', 'gstr_invoice')}
            {sortableHeader('Taxable Value', 'taxable_value', 'right')}
            <th className={`${thClass} text-right`}>Taxable Diff</th>
            <th className={`${thClass} text-right`}>IGST Diff</th>
            <th className={`${thClass} text-right`}>CGST Diff</th>
            <th className={`${thClass} text-right`}>SGST Diff</th>
            {sortableHeader('Method', 'match_method')}
            {sortableHeader('Status', 'status')}
            <th className={`${thClass} sticky right-0 z-20 bg-slate-50 text-right shadow-[-1px_0_0_0_rgb(226,232,240)]`}>Action</th>
          </tr>
        </thead>
        <tbody className="bg-white divide-y divide-slate-200">
          {results.length === 0 ? (
            <tr>
              <td colSpan={12} className="px-6 py-12 text-center text-slate-500">
                {emptyMessage || 'No results found matching your criteria.'}
              </td>
            </tr>
          ) : (
            results.map((result) => (
              <tr 
                key={result.result_id} 
                onClick={() => onRowClick(result)}
                className="group cursor-pointer transition-colors hover:bg-brand-50"
              >
                <td className="sticky left-0 z-10 truncate bg-white px-3 py-2.5 text-[13px] font-medium text-slate-900 shadow-[1px_0_0_0_rgb(226,232,240)] group-hover:bg-brand-50" title={result.supplier}>{result.supplier}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-[13px] text-slate-500">{result.gstin}</td>
                <td className="px-3 py-2.5 text-[13px] text-slate-900">{result.accounts_invoice || '—'}</td>
                <td className="px-3 py-2.5 text-[13px] text-slate-900">
                  <span>{result.gst_invoice || '—'}</span>
                  {result.gstr_source_category && <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-600">{result.gstr_source_category}</span>}
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right text-[13px] tabular-nums text-slate-900">{formatCurrency(result.taxable_value)}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right text-[13px] tabular-nums text-slate-500">{formatCurrency(result.taxable_difference)}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right text-[13px] tabular-nums text-slate-500">{formatCurrency(result.igst_diff)}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right text-[13px] tabular-nums text-slate-500">{formatCurrency(result.cgst_diff)}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right text-[13px] tabular-nums text-slate-500">{formatCurrency(result.sgst_diff)}</td>
                <td className="px-3 py-2.5 text-[13px] text-slate-500 whitespace-nowrap">
                  <div className="flex items-center space-x-2">
                    <span>{result.match_method}</span>
                    {result.date_mismatch && (
                      <span title={`Date Difference: ${result.date_difference_days} days`} className="text-amber-500 flex items-center">
                        <CalendarClock className="w-4 h-4" />
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-3 py-2.5 text-[13px] whitespace-nowrap">
                  <div className="flex flex-col space-y-1">
                    {result.date_mismatch && (
                      <span className="px-2.5 py-1 text-xs font-semibold rounded-full border bg-amber-100 text-amber-800 border-amber-200 inline-block w-fit">
                        ⚠️ Date Mismatch
                      </span>
                    )}
                    <ResultStatusBadge status={result.status} prefix={result.date_mismatch ? "✓ Financials: " : ""} />
                  </div>
                </td>
                <td className="sticky right-0 z-10 bg-white px-3 py-2.5 text-right text-[13px] font-medium text-brand-600 shadow-[-1px_0_0_0_rgb(226,232,240)] group-hover:bg-brand-50"><button type="button" onClick={(event) => { event.stopPropagation(); onRowClick(result); }} className="rounded px-1 py-0.5 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">View</button></td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </WideTableFrame>
  );
};
