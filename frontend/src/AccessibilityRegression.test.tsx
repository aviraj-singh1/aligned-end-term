import { useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { InvoiceComparisonDrawer } from './components/results/InvoiceComparisonDrawer';
import { AmbiguousReview } from './components/review/AmbiguousReview';
import { ConfirmMatchDialog } from './components/review/ConfirmMatchDialog';
import { NotesTable } from './components/notes/NotesTable';
import { ResultsTable } from './components/results/ResultsTable';
import { TaxableValueMissingTable } from './components/results/TaxableValueMissingTable';
import { Pagination } from './components/common/Pagination';
import { PreviewTable } from './components/preview/PreviewTable';
import { ToastContainer } from './components/common/Toast';
import { SummaryPanel } from './components/export/SummaryPanel';
import { FileUploader } from './components/upload/FileUploader';
import { WorkflowIndicator } from './components/layout/WorkflowIndicator';
import { CancelledVouchersTable } from './components/results/CancelledVouchersTable';
import type { CandidateModel, ReconciliationResult } from './types/api';

const candidate: CandidateModel = { candidate_id: 'c1', supplier: 'Supplier', gstin: '27AAAAA0000A1Z5', gst_invoice: 'GSTR-1', taxable_value: '100', igst: '0', cgst: '9', sgst: '9' };
const result: ReconciliationResult = {
  result_id: 'r1', status: 'Needs Review / Ambiguous', supplier: 'Supplier', gstin: candidate.gstin,
  accounts_invoice: 'ACC-1', gst_invoice: 'GSTR-1', accounts_date: null, gst_date: null,
  taxable_value: '100', accounts_taxable: '100', gst_taxable: '100',
  accounts_igst: '0', gst_igst: '0', igst_diff: '0', accounts_cgst: '9', gst_cgst: '9', cgst_diff: '0',
  accounts_sgst: '9', gst_sgst: '9', sgst_diff: '0', date_mismatch: false, date_difference_days: null,
  match_method: 'Strong Evidence Fallback', remarks: 'Review this record.', candidates: [candidate],
};

describe('8B.8 accessibility regression', () => {
  it.each([['Notes', NotesTable, 'GSTR-2B Credit Note'], ['Results', ResultsTable, 'GSTR-2B Invoice']] as const)(
    '%s exposes semantic sort controls, direction, and the supported GSTR sort key', (_name, Table, label) => {
      const onSort = vi.fn();
      const onRowClick = vi.fn();
      render(<Table results={[result]} onRowClick={onRowClick} sortField="gstr_invoice" sortOrder="desc" onSort={onSort} />);
      expect(screen.getByRole('columnheader', { name: label })).toHaveAttribute('aria-sort', 'descending');
      const expected = [['Supplier', 'supplier'], ['GSTIN', 'gstin'], [_name === 'Notes' ? 'Accounts Debit Note' : 'Accounts Invoice', 'accounts_invoice'], [label, 'gstr_invoice'], ['Taxable Value', 'taxable_value'], ['Method', 'match_method'], ['Status', 'status']];
      for (const [name, field] of expected) {
        const button = screen.getByRole('button', { name });
        button.focus();
        expect(button).toHaveFocus();
        fireEvent.click(button);
        expect(onSort).toHaveBeenLastCalledWith(field);
      }
      fireEvent.click(screen.getByRole('button', { name: 'View' }));
      expect(onRowClick).toHaveBeenCalledExactlyOnceWith(result);
    },
  );

  it('makes taxable-missing details reachable without replacing table semantics', () => {
    const onRowClick = vi.fn();
    render(<TaxableValueMissingTable results={[result]} onRowClick={onRowClick} />);
    expect(screen.getAllByRole('columnheader')).toHaveLength(12);
    const view = screen.getByRole('button', { name: 'View Supplier' });
    view.focus();
    expect(view).toHaveFocus();
    fireEvent.click(view);
    expect(onRowClick).toHaveBeenCalledExactlyOnceWith(result);
  });

  it('names the page-size selector and keeps its existing values', () => {
    const onPageSizeChange = vi.fn();
    render(<Pagination currentPage={1} totalItems={100} pageSize={25} onPageChange={vi.fn()} onPageSizeChange={onPageSizeChange} />);
    const select = screen.getByRole('combobox', { name: 'Rows per page' });
    expect(within(select).getAllByRole('option').map(option => option.getAttribute('value'))).toEqual(['25', '50', '100']);
    fireEvent.change(select, { target: { value: '50' } });
    expect(onPageSizeChange).toHaveBeenCalledWith(50);
  });

  it('contains drawer focus, supports Escape, restores the trigger and body scrolling', () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return <><button onClick={() => setOpen(true)}>Open record</button><button>Background action</button><InvoiceComparisonDrawer isOpen={open} result={result} onClose={() => setOpen(false)} /></>;
    }
    const originalOverflow = document.body.style.overflow;
    render(<Harness />);
    const trigger = screen.getByRole('button', { name: 'Open record' });
    trigger.focus();
    fireEvent.click(trigger);
    const close = screen.getByRole('button', { name: 'Close result details' });
    expect(close).toHaveFocus();
    expect(document.body.style.overflow).toBe('hidden');
    screen.getByRole('button', { name: 'Background action' }).focus();
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: 'Tab', shiftKey: true });
    const comparison = screen.getByRole('region', { name: 'Source comparison table' });
    expect(comparison).toHaveFocus();
    fireEvent.keyDown(comparison, { key: 'Tab' });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(document.body.style.overflow).toBe(originalOverflow);
  });

  it('keeps focus in nested confirmation and returns it to the candidate on cancel', () => {
    const onClose = vi.fn();
    render(<AmbiguousReview isOpen result={result} onClose={onClose} onManualMatch={vi.fn()} />);
    const candidateButton = screen.getByRole('button', { name: 'Select This Invoice' });
    candidateButton.focus();
    fireEvent.click(candidateButton);
    const confirmation = screen.getByRole('dialog', { name: 'Confirm Match' });
    const cancel = within(confirmation).getByRole('button', { name: 'Cancel' });
    expect(cancel).toHaveFocus();
    screen.getByRole('button', { name: 'Close result details' }).focus();
    expect(cancel).toHaveFocus();
    fireEvent.keyDown(cancel, { key: 'Tab', shiftKey: true });
    expect(within(confirmation).getByRole('button', { name: 'Confirm Match' })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Confirm Match' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Selected' })).toHaveFocus();
    expect(onClose).not.toHaveBeenCalled();
    expect(document.body.style.overflow).toBe('hidden');
  });

  it('keeps submission locked, announced and focusable when both confirmation buttons are disabled', () => {
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    render(<ConfirmMatchDialog isOpen isLoading candidate={candidate} tallyInvoice="ACC-1" onClose={onClose} onConfirm={onConfirm} />);
    const dialog = screen.getByRole('dialog', { name: 'Confirm Match' });
    expect(dialog).toHaveAttribute('aria-busy', 'true');
    expect(dialog).toHaveAccessibleDescription('Confirm that GSTR-2B invoice GSTR-1 corresponds to Accounts invoice ACC-1?');
    expect(dialog).toHaveFocus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(dialog).toHaveFocus();
    fireEvent.keyDown(dialog, { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: 'Applying match...' }));
    expect(onClose).not.toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
    expect(candidate.gst_invoice).toBe('GSTR-1');
  });

  it('makes the static preview scroll area keyboard reachable without changing density', () => {
    render(<PreviewTable type="accounts" records={[{ supplier: 'Supplier', gstin: candidate.gstin, invoice_number: 'ACC-1', date: null, taxable_value: '100', igst: '0', cgst: '9', sgst: '9' }]} />);
    const region = screen.getByRole('region', { name: 'Data preview table' });
    expect(region).toHaveAttribute('tabindex', '0');
    expect(region).toHaveClass('max-h-[420px]', 'overflow-auto');
    region.focus();
    expect(region).toHaveFocus();
  });

  it('bounds long error toasts to the viewport without hiding their text', () => {
    const message = 'x'.repeat(500);
    render(<ToastContainer toasts={[{ id: '1', type: 'error', message }]} removeToast={vi.fn()} />);
    const text = screen.getByText(message);
    expect(text).toHaveClass('[overflow-wrap:anywhere]');
    expect(screen.getByRole('alert').parentElement?.parentElement).toHaveClass('max-w-[calc(100vw-2rem)]');
  });

  it('translates generated summary labels only and copies the untouched authoritative summary', async () => {
    const summary = '618 Tally invoices were compared with 846 GSTR-2B invoices.\nPresent in GSTR-2B but not in Tally: 4\n  GSTR: 2\n- Invoice Tally-1 from Tally Supplier';
    const writeText = vi.fn().mockResolvedValue(undefined);
    const previousClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    try {
      render(<SummaryPanel summaryText={summary} counts={{}} onNavigateToTab={vi.fn()} />);
      expect(screen.getByText(/618 Accounts invoices/)).toHaveTextContent('Present in GSTR-2B but not in Accounts: 4');
      expect(screen.getByText(/618 Accounts invoices/)).toHaveTextContent('GSTR-2B: 2');
      expect(screen.getByText(/618 Accounts invoices/)).toHaveTextContent('Invoice Tally-1 from Tally Supplier');
      fireEvent.click(screen.getByRole('button', { name: 'Copy Summary' }));
      await waitFor(() => expect(writeText).toHaveBeenCalledExactlyOnceWith(summary));
    } finally {
      if (previousClipboard) Object.defineProperty(navigator, 'clipboard', previousClipboard);
      else Reflect.deleteProperty(navigator, 'clipboard');
    }
  });

  it('distinguishes keyboard browse controls by source without changing validation', () => {
    const onFileSelect = vi.fn();
    render(<><FileUploader title="Accounts Purchase Data" description="Accounts" required file={null} onFileSelect={onFileSelect} /><FileUploader title="GSTR-2B" description="GSTR-2B" required file={null} onFileSelect={onFileSelect} /></>);
    for (const title of ['Accounts Purchase Data', 'GSTR-2B']) {
      const browse = screen.getByRole('button', { name: `Browse ${title} file` });
      browse.focus();
      expect(browse).toHaveFocus();
    }
    expect(onFileSelect).not.toHaveBeenCalled();
  });

  it('makes noninteractive workflow and cancelled-table scrolling keyboard reachable', () => {
    render(<><WorkflowIndicator currentStep="export" /><CancelledVouchersTable results={[]} /></>);
    for (const region of [screen.getByRole('list', { name: 'Workflow steps' }), screen.getByRole('region', { name: 'Cancelled vouchers table' })]) {
      region.focus();
      expect(region).toHaveFocus();
      expect(region).toHaveClass('overflow-x-auto');
    }
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
  });

  it('does not steal focus on a drawer rerender and releases nested scroll locks on unmount', () => {
    const { rerender, unmount } = render(<AmbiguousReview isOpen result={result} onClose={vi.fn()} onManualMatch={vi.fn()} />);
    const select = screen.getByRole('button', { name: 'Select This Invoice' });
    select.focus();
    rerender(<AmbiguousReview isOpen result={result} onClose={vi.fn()} onManualMatch={vi.fn()} />);
    expect(select).toHaveFocus();
    fireEvent.click(select);
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
    unmount();
    expect(document.body.style.overflow).not.toBe('hidden');
  });
});
