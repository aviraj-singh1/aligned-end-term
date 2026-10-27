import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { InvoiceComparisonDrawer } from './components/results/InvoiceComparisonDrawer';
import { AmbiguousReview } from './components/review/AmbiguousReview';
import { ResultsTable } from './components/results/ResultsTable';
import { NotesTable } from './components/notes/NotesTable';
import type { ReconciliationResult } from './types/api';

const result: ReconciliationResult = {
  result_id: 'result-1', status: 'Major Difference', supplier: 'Example Supplier', gstin: '27AAAAA0000A1Z5',
  accounts_invoice: 'ACC-10', gst_invoice: 'GST-10', accounts_date: '2026-04-12', gst_date: '2026-04-16',
  taxable_value: '100.00', accounts_taxable: '100.00', gst_taxable: '120.00',
  accounts_igst: null, gst_igst: '0.00', igst_diff: null,
  accounts_cgst: '9.00', gst_cgst: '10.00', cgst_diff: '-1.00',
  accounts_sgst: '9.00', gst_sgst: '10.00', sgst_diff: '-1.00',
  date_mismatch: true, date_difference_days: 4, invoice_mismatch: true,
  match_method: 'Normalized Invoice', remarks: 'Authoritative reconciliation reason.', candidates: [],
};

const withResult = (overrides: Partial<ReconciliationResult>): ReconciliationResult => ({ ...result, ...overrides });

describe('release fidelity tables', () => {
  it('shows GSTR source badges for purchase and note results', () => {
    const { unmount } = render(<ResultsTable results={[withResult({ gstr_source_category: 'b2ba' })]}
      onRowClick={vi.fn()} sortField="supplier" sortOrder="asc" onSort={vi.fn()} />);
    expect(screen.getByText('b2ba')).toBeInTheDocument();
    unmount();
    render(<NotesTable results={[withResult({ gstr_source_category: 'cdnr' })]}
      onRowClick={vi.fn()} sortField="supplier" sortOrder="asc" onSort={vi.fn()} />);
    expect(screen.getByText('cdnr')).toBeInTheDocument();
  });

  it('shows the signed backend taxable difference explaining a synthetic Major result', () => {
    render(<ResultsTable results={[withResult({ accounts_invoice: '374/2025-26',
      taxable_difference: '-1.42', igst_diff: '0.00', cgst_diff: '-0.13', sgst_diff: '-0.13',
      status: 'Major Difference', date_mismatch: false })]} onRowClick={vi.fn()}
      sortField="supplier" sortOrder="asc" onSort={vi.fn()} />);
    expect(screen.getByRole('columnheader', { name: 'Taxable Diff' })).toBeInTheDocument();
    expect(screen.getByText('-₹1.42')).toBeInTheDocument();
    expect(screen.getByText('Major Difference')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /Taxable Value/ })).toBeInTheDocument();
  });

  it('shows unmatched credit-note taxes but no fabricated zero differences', () => {
    render(<NotesTable results={[withResult({ accounts_invoice: '', accounts_taxable: null,
      status: 'Not in Tally', gst_igst: '0.00', gst_cgst: '800.85', gst_sgst: '800.85',
      igst_diff: null, cgst_diff: null, sgst_diff: null })]} onRowClick={vi.fn()}
      sortField="supplier" sortOrder="asc" onSort={vi.fn()} />);
    expect(screen.getAllByText('₹800.85')).toHaveLength(2);
    const cells = within(screen.getAllByRole('row')[1]).getAllByRole('cell');
    expect(cells.slice(8, 11).map(cell => cell.textContent)).toEqual(['—', '—', '—']);
  });

  it('keeps GSTR-only taxes and date visible in the drawer', () => {
    render(<InvoiceComparisonDrawer isOpen onClose={vi.fn()} type="note" result={withResult({
      status: 'Not in Tally', accounts_invoice: '', gst_date: '2025-05-10', gst_igst: '0.00',
      gst_cgst: '800.85', gst_sgst: '800.85', igst_diff: null, cgst_diff: null, sgst_diff: null,
    })} />);
    expect(screen.getAllByText('₹800.85')).toHaveLength(2);
    expect(screen.getByText('2025-05-10')).toBeInTheDocument();
  });

  it('explains a cancelled counterpart without changing the Missing status', () => {
    const remarks = 'Matching GSTR invoice was found but is marked cancelled and was excluded from active reconciliation.';
    render(<InvoiceComparisonDrawer isOpen onClose={vi.fn()} result={withResult({
      status: 'Missing in GSTR-2B', cancelled_gstr_counterpart: true, remarks,
      gst_invoice: null, gst_taxable: null, igst_diff: null, cgst_diff: null, sgst_diff: null,
    })} />);
    expect(screen.getAllByText(remarks)).toHaveLength(2);
    expect(screen.queryByText('No corresponding GSTR-2B invoice was found.')).not.toBeInTheDocument();
  });
});

describe('result detail drawer', () => {
  const last4Evidence = "Matched by unique Last-4 invoice identity using exact GSTIN and exact invoice date. Financial differences are reported separately: Tally '60882' ↔ GST '06CFIP0675660882'";
  const displayedLast4 = last4Evidence.replace("Tally '", "Accounts '").replace("GST '", "GSTR-2B '");

  it('shows canonical Last-4 evidence and the distinct taxable-missing warning', () => {
    const warning = 'Taxable value is blank in the Accounts source.';
    const input = withResult({ status: 'Taxable Value Missing', match_method: 'Invoice Last 4',
      accounts_invoice: '60882', gst_invoice: '06CFIP0675660882',
      accounts_date: '2025-04-30', gst_date: '2025-04-30', date_mismatch: false,
      invoice_mismatch: false, accounts_taxable: null, gst_taxable: '1209.32',
      accounts_cgst: '108.84', gst_cgst: '108.84', accounts_sgst: '108.84', gst_sgst: '108.84',
      cgst_diff: '0.00', sgst_diff: '0.00', evidence: last4Evidence, remarks: warning });
    render(<InvoiceComparisonDrawer isOpen onClose={vi.fn()} result={input} />);
    expect(screen.getByText('Match evidence').nextElementSibling).toHaveTextContent(displayedLast4);
    expect(screen.getByText('Warning').nextElementSibling).toHaveTextContent(warning);
    expect(screen.getByText('Match method').nextElementSibling).toHaveTextContent('Invoice Last 4');
    expect(screen.getAllByText(displayedLast4)).toHaveLength(1);
    expect(screen.getAllByText(warning)).toHaveLength(1);
    expect(input.evidence).toBe(last4Evidence);
    expect(input.remarks).toBe(warning);
  });

  it.each([
    ['Major Difference', 'Invoice Last 4', last4Evidence, displayedLast4],
    ['Perfectly Matched', 'Exact Invoice', 'Exact invoice number match', 'Exact invoice number match'],
    ['Minor Rounding Difference', 'Exact Invoice', 'Exact invoice number match', 'Exact invoice number match'],
    ['Invoice Number Mismatch', 'Exact GSTIN + Date + Financials', 'Exact GSTIN, date and financial evidence', 'Exact GSTIN, date and financial evidence'],
    ['Needs Review / Ambiguous', 'Strong Evidence Fallback', 'Strong Review Candidate: Exact invoice, unique candidate, matching financials, and exact date match.', 'Strong Review Candidate: Exact invoice, unique candidate, matching financials, and exact date match.'],
    ['Perfectly Matched', 'Manual Match', 'Manually matched by user', 'Manually matched by user'],
  ])('preserves %s / %s canonical evidence without duplication', (status, method, evidence, displayed) => {
    render(<InvoiceComparisonDrawer isOpen onClose={vi.fn()} result={withResult({
      status, match_method: method, evidence, remarks: ` ${evidence} `,
      date_mismatch: false, invoice_mismatch: status === 'Invoice Number Mismatch',
    })} />);
    expect(screen.getByText('Match evidence').nextElementSibling).toHaveTextContent(displayed);
    expect(screen.getAllByText(displayed)).toHaveLength(1);
    expect(screen.queryByText('Warning')).not.toBeInTheDocument();
    expect(screen.getByText('Match method').nextElementSibling).toHaveTextContent(method);
    if (status === 'Major Difference') {
      expect(screen.getByRole('dialog')).not.toHaveTextContent(/taxable \+ tax confirmation|tax confirmation|financial confirmation/i);
    }
  });

  it.each([
    { evidence: undefined, remarks: 'Legacy explanation', label: 'Evidence', text: 'Legacy explanation' },
    { evidence: '   ', remarks: 'Warning only', label: 'Evidence', text: 'Warning only' },
    { evidence: 'Evidence only', remarks: '  ', label: 'Match evidence', text: 'Evidence only' },
    { evidence: '', remarks: '', label: null, text: null },
  ])('keeps single or absent explanations clean: $text', ({ evidence, remarks, label, text }) => {
    render(<InvoiceComparisonDrawer isOpen onClose={vi.fn()} result={withResult({ evidence, remarks })} />);
    if (label && text) {
      expect(screen.getByText(label).nextElementSibling).toHaveTextContent(text);
      expect(screen.getAllByText(text)).toHaveLength(1);
    } else {
      expect(screen.queryByText('Match evidence')).not.toBeInTheDocument();
      expect(screen.queryByText('Evidence')).not.toBeInTheDocument();
    }
    expect(screen.queryByText('Warning')).not.toBeInTheDocument();
  });

  it('shows the authoritative reason and comparison matrix without changing difference signs or missing values', () => {
    render(<InvoiceComparisonDrawer isOpen onClose={vi.fn()} result={result} />);
    expect(screen.getByRole('dialog', { name: 'Result details' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Why this result?' })).toBeInTheDocument();
    expect(screen.getByText('Authoritative reconciliation reason.')).toBeInTheDocument();
    expect(screen.getByText('The financial comparison is classified as a major difference.')).toBeInTheDocument();
    expect(screen.getByText('The Accounts and GSTR-2B invoice dates differ by 4 days.')).toBeInTheDocument();
    expect(screen.getByText('Date Mismatch')).toBeInTheDocument();
    for (const header of ['Field', 'Accounts', 'GSTR-2B', 'Difference']) {
      expect(screen.getByRole('columnheader', { name: header })).toBeInTheDocument();
    }
    for (const field of ['Invoice', 'Date', 'Taxable Value', 'IGST', 'CGST', 'SGST']) {
      expect(screen.getByRole('rowheader', { name: field })).toBeInTheDocument();
    }
    expect(screen.getByText('-₹20.00')).toBeInTheDocument();
    const igstRow = screen.getByRole('rowheader', { name: 'IGST' }).closest('tr') as HTMLTableRowElement;
    expect(within(igstRow).getAllByText('—')).toHaveLength(2);
    expect(within(igstRow).getByText('₹0.00')).toBeInTheDocument();
    expect(screen.getByRole('rowheader', { name: 'Date' }).closest('tr')).toHaveClass('bg-status-warning-soft/60');
    expect(screen.getByRole('rowheader', { name: 'Invoice' }).closest('tr')).toHaveClass('bg-status-warning-soft/60');
  });

  it('keeps a primary Minor status and cross-cutting Date Mismatch visible together', () => {
    render(<InvoiceComparisonDrawer isOpen onClose={vi.fn()} result={withResult({
      status: 'Minor Rounding Difference', invoice_mismatch: false, match_method: 'Exact Invoice',
      remarks: 'Exact invoice number match', date_mismatch: true, date_difference_days: 44,
    })} />);
    expect(screen.getByText('Minor Rounding Difference')).toBeInTheDocument();
    expect(screen.getByText('Date Mismatch')).toBeInTheDocument();
    expect(screen.getByText('The financial comparison is classified as a minor rounding difference.')).toBeInTheDocument();
    expect(screen.getByText('The Accounts and GSTR-2B invoice dates differ by 44 days.')).toBeInTheDocument();
    expect(screen.getByText('Exact invoice number match')).toBeInTheDocument();
  });

  it('renders legacy evidence with source-neutral labels without mutating result data', () => {
    const legacyRemarks = "Normalized invoice match: Tally 'KT-1093' ↔ GST 'KT1093'; GSTIN unchanged";
    const evidenceResult = withResult({ remarks: legacyRemarks, date_mismatch: false, invoice_mismatch: false });
    render(<InvoiceComparisonDrawer isOpen onClose={vi.fn()} result={evidenceResult} />);
    expect(screen.getByText("Normalized invoice match: Accounts 'KT-1093' ↔ GSTR-2B 'KT1093'; GSTIN unchanged")).toBeInTheDocument();
    expect(evidenceResult.remarks).toBe(legacyRemarks);
  });

  it.each([
    ['Normalized Invoice', 'Normalized match'],
    ['Invoice Last 4', 'Last 4 match'],
    ['Invoice Last 2', 'Last 2 match'],
  ])('shows an accepted %s without a generic mismatch', (matchMethod, expected) => {
    render(<InvoiceComparisonDrawer isOpen onClose={vi.fn()} result={withResult({
      status: 'Perfectly Matched', invoice_mismatch: false, date_mismatch: false,
      accounts_invoice: 'KT-1093', gst_invoice: 'KT1093', match_method: matchMethod,
    })} />);
    const invoiceRow = screen.getByRole('rowheader', { name: 'Invoice' }).closest('tr') as HTMLTableRowElement;
    expect(within(invoiceRow).getByText(expected)).toBeInTheDocument();
    expect(within(invoiceRow).queryByText('Mismatch')).not.toBeInTheDocument();
  });

  it('continues to show Mismatch for an actual invoice-number mismatch classification', () => {
    render(<InvoiceComparisonDrawer isOpen onClose={vi.fn()} result={withResult({
      status: 'Invoice Number Mismatch', invoice_mismatch: true, date_mismatch: false,
      match_method: 'Exact GSTIN + Date + Financials',
    })} />);
    const invoiceRow = screen.getByRole('rowheader', { name: 'Invoice' }).closest('tr') as HTMLTableRowElement;
    expect(within(invoiceRow).getByText('Mismatch')).toBeInTheDocument();
  });

  it('suppresses placeholder zeros when the GSTR counterpart is absent', () => {
    render(<InvoiceComparisonDrawer isOpen onClose={vi.fn()} result={withResult({
      status: 'Missing in GSTR-2B', gst_invoice: null, gst_date: null,
      gst_taxable: '0.00', gst_igst: '0.00', gst_cgst: '0.00', gst_sgst: '0.00',
      igst_diff: '0.00', cgst_diff: '9.00', sgst_diff: '9.00', date_mismatch: false, invoice_mismatch: false,
    })} />);
    for (const field of ['Taxable Value', 'IGST', 'CGST', 'SGST']) {
      const cells = within(screen.getByRole('rowheader', { name: field }).closest('tr') as HTMLTableRowElement).getAllByRole('cell');
      expect(cells[1]).toHaveTextContent('—');
      expect(cells[2]).toHaveTextContent('—');
    }
  });

  it('preserves explicit zero values when the source record exists', () => {
    render(<InvoiceComparisonDrawer isOpen onClose={vi.fn()} result={withResult({
      gst_invoice: 'GST-ZERO', gst_date: null, gst_igst: '0.00', igst_diff: '0.00',
      date_mismatch: false, invoice_mismatch: false,
    })} />);
    const igstRow = screen.getByRole('rowheader', { name: 'IGST' }).closest('tr') as HTMLTableRowElement;
    expect(within(igstRow).getAllByText('₹0.00')).toHaveLength(2);
  });

  it('preserves close-button behavior', () => {
    const onClose = vi.fn();
    render(<InvoiceComparisonDrawer isOpen onClose={onClose} result={result} />);
    fireEvent.click(screen.getByRole('button', { name: 'Close result details' }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('uses Accounts terminology for a GSTR-2B record with no accounting match', () => {
    render(<InvoiceComparisonDrawer isOpen onClose={vi.fn()} type="notin" result={{
      supplier: 'GSTR Supplier', gstin: '27BBBBB0000B1Z5', gst_invoice: 'GST-1', taxable_value: '500.00',
    }} />);
    expect(screen.getByText('No corresponding Accounts record was found for this GSTR-2B invoice.')).toBeInTheDocument();
    expect(screen.queryByText(/Tally/)).not.toBeInTheDocument();
  });

  it('shows a clear empty state when Manual Match has no eligible candidates', () => {
    render(<AmbiguousReview isOpen onClose={vi.fn()} result={{ ...result, status: 'Needs Review / Ambiguous', candidates: [] }} onManualMatch={vi.fn()} />);
    expect(screen.getByText('No eligible GSTR-2B candidates found.')).toBeInTheDocument();
  });
});
