import React from 'react';
import type { ReconciliationResult } from '../../types/api';
import { formatCurrency } from './ResultStatusBadge';

const formatDate = (value: string | null | undefined): string => {
  if (!value) return '—';
  const [year, month, day] = value.split('-');
  return year && month && day ? `${day}-${month}-${year}` : value;
};

const financialDifference = (accounts: string | null, gst: string | null): string => {
  if (accounts === null || gst === null || accounts.trim() === '' || gst.trim() === '') return '—';
  const accountsValue = Number(accounts);
  const gstValue = Number(gst);
  return Number.isFinite(accountsValue) && Number.isFinite(gstValue)
    ? formatCurrency(accountsValue - gstValue)
    : '—';
};

const hasNonZeroDifference = (value: string | null): boolean => {
  if (value === null || value.trim() === '') return false;
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric !== 0;
};

export const formatEvidenceForDisplay = (evidence: string): string => evidence
  .replace(/\bTally\b/gi, 'Accounts')
  .replace(/\bGST\b/gi, 'GSTR-2B');

export const resultExplanation = (result: ReconciliationResult): string => {
  if (result.cancelled_gstr_counterpart) return result.remarks;
  switch (result.status) {
    case 'Perfectly Matched': return 'The financial comparison is classified as perfectly matched.';
    case 'Minor Rounding Difference': return 'The financial comparison is classified as a minor rounding difference.';
    case 'Major Difference': return 'The financial comparison is classified as a major difference.';
    case 'Invoice Number Mismatch': return 'The records align on supporting fields, but the invoice numbers do not match.';
    case 'Taxable Value Missing': return 'A taxable value is missing from the source data and requires review.';
    case 'Missing in GSTR-2B': return 'No corresponding GSTR-2B invoice was found.';
    case 'Not in Tally': return 'No corresponding Accounts record was found.';
    default: return 'Review the comparison and match evidence for this classification.';
  }
};

export const dateMismatchExplanation = (result: ReconciliationResult): string | null => {
  if (!result.date_mismatch) return null;
  return result.date_difference_days === null
    ? 'The Accounts and GSTR-2B invoice dates differ.'
    : `The Accounts and GSTR-2B invoice dates differ by ${result.date_difference_days} days.`;
};

const invoiceDifference = (result: ReconciliationResult): string => {
  if (!result.accounts_invoice || !result.gst_invoice || result.accounts_invoice === result.gst_invoice) return '—';
  if (result.invoice_mismatch || result.status === 'Invoice Number Mismatch' || result.match_method === 'Exact GSTIN + Date + Financials') return 'Mismatch';
  const acceptedMethods: Record<string, string> = {
    'Normalized Invoice': 'Normalized match',
    'Invoice Last 4': 'Last 4 match',
    'Invoice Last 2': 'Last 2 match',
    'Exact Invoice': 'Exact match',
    'Manual Match': 'Manual match',
    'Supplier Name Fallback': 'Supplier match',
    'Strong Evidence Fallback': 'Evidence match',
  };
  return acceptedMethods[result.match_method] || '—';
};

interface ComparisonProps {
  result: ReconciliationResult;
}

export const ResultComparison: React.FC<ComparisonProps> = ({ result }) => {
  const invoiceDiff = invoiceDifference(result);
  const dateDiff = result.date_mismatch && result.date_difference_days !== null ? `${result.date_difference_days} days` : '—';
  const hasAccountsSource = Boolean(result.accounts_invoice || result.accounts_date);
  const hasGstrSource = Boolean(result.gst_invoice || result.gst_date);
  const bothSourcesPresent = hasAccountsSource && hasGstrSource;
  const taxableDiff = bothSourcesPresent ? (result.taxable_difference !== undefined ? formatCurrency(result.taxable_difference) : financialDifference(result.accounts_taxable, result.gst_taxable)) : '—';
  const grossDiff = bothSourcesPresent ? formatCurrency(result.gross_total_difference ?? null) : '—';
  const isMajor = result.status === 'Major Difference';
  const sourceCurrency = (sourcePresent: boolean, value: string | null) => sourcePresent ? formatCurrency(value) : '—';
  const differenceCurrency = (value: string | null) => bothSourcesPresent ? formatCurrency(value) : '—';
  const rows = [
    { label: 'Invoice', accounts: result.accounts_invoice || '—', gst: result.gst_invoice || '—', difference: invoiceDiff, emphasize: invoiceDiff === 'Mismatch' },
    { label: 'Date', accounts: formatDate(result.accounts_date), gst: formatDate(result.gst_date), difference: dateDiff, emphasize: result.date_mismatch },
    { label: 'Taxable Value', accounts: sourceCurrency(hasAccountsSource, result.accounts_taxable), gst: sourceCurrency(hasGstrSource, result.gst_taxable), difference: taxableDiff, emphasize: result.status === 'Taxable Value Missing' || (isMajor && taxableDiff !== '—' && taxableDiff !== '₹0.00') },
    { label: 'Gross Total / Invoice Value', accounts: sourceCurrency(hasAccountsSource, result.accounts_gross_total ?? null), gst: sourceCurrency(hasGstrSource, result.gst_invoice_value ?? null), difference: grossDiff, emphasize: isMajor && grossDiff !== '—' && grossDiff !== '₹0.00' },
    { label: 'IGST', accounts: sourceCurrency(hasAccountsSource, result.accounts_igst), gst: sourceCurrency(hasGstrSource, result.gst_igst), difference: differenceCurrency(result.igst_diff), emphasize: isMajor && bothSourcesPresent && hasNonZeroDifference(result.igst_diff) },
    { label: 'CGST', accounts: sourceCurrency(hasAccountsSource, result.accounts_cgst), gst: sourceCurrency(hasGstrSource, result.gst_cgst), difference: differenceCurrency(result.cgst_diff), emphasize: isMajor && bothSourcesPresent && hasNonZeroDifference(result.cgst_diff) },
    { label: 'SGST', accounts: sourceCurrency(hasAccountsSource, result.accounts_sgst), gst: sourceCurrency(hasGstrSource, result.gst_sgst), difference: differenceCurrency(result.sgst_diff), emphasize: isMajor && bothSourcesPresent && hasNonZeroDifference(result.sgst_diff) },
  ];

  return (
    <section aria-labelledby="comparison-heading">
      <h3 id="comparison-heading" className="mb-2 text-sm font-semibold text-slate-900">Comparison</h3>
      <div role="region" aria-label="Source comparison table" tabIndex={0} className="overflow-x-auto rounded-lg border border-slate-200 focus-visible:outline-2 focus-visible:outline-brand-500 focus-visible:-outline-offset-2">
        <table className="w-full min-w-[440px] border-collapse text-xs">
          <thead className="bg-slate-50 text-slate-500">
            <tr>
              <th scope="col" className="px-3 py-2 text-left font-medium">Field</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">Accounts</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">GSTR-2B</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Difference</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white">
            {rows.map(row => (
              <tr key={row.label} className={row.emphasize ? 'bg-status-warning-soft/60' : undefined}>
                <th scope="row" className="whitespace-nowrap px-3 py-2.5 text-left font-medium text-slate-600">{row.label}</th>
                <td className="whitespace-nowrap px-3 py-2.5 text-slate-900 tabular-nums">{row.accounts}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-slate-900 tabular-nums">{row.gst}</td>
                <td className={`whitespace-nowrap px-3 py-2.5 text-right font-medium tabular-nums ${row.emphasize ? 'text-status-warning' : 'text-slate-600'}`}>{row.difference}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-slate-500">Financial differences are Accounts − GSTR-2B.</p>
    </section>
  );
};
