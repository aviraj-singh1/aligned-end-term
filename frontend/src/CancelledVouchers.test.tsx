import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ResultsTabs } from './components/results/ResultsTabs';
import { CancelledVouchersTable } from './components/results/CancelledVouchersTable';
import { NotesTable } from './components/notes/NotesTable';

const populatedGroupCases: Array<{ group: string; counts: Record<string, number>; category: string }> = [
  {
    group: 'Differences',
    counts: { major_difference: 0, date_mismatches: 7, invoice_mismatches: 1 },
    category: 'Date Mismatches',
  },
  {
    group: 'Missing',
    counts: { missing_in_gstr_2b: 0, not_in_tally: 84 },
    category: 'Not in Tally',
  },
  {
    group: 'Review',
    counts: { needs_review_ambiguous: 0, potential_duplicate: 0, taxable_value_missing: 16, cancelled_vouchers: 0 },
    category: 'Taxable Value Missing',
  },
];

describe('Cancelled Vouchers UI', () => {
  it('exposes cancelled vouchers through the Review group', () => {
    const onChange = vi.fn();
    const { rerender } = render(<ResultsTabs activeTab="All" onChange={onChange} counts={{ cancelled_vouchers: 2 }} />);
    fireEvent.click(screen.getByRole('button', { name: 'Review' }));
    expect(onChange).toHaveBeenCalledWith('Cancelled Vouchers');
    rerender(<ResultsTabs activeTab="Needs Review / Ambiguous" onChange={onChange} counts={{ cancelled_vouchers: 2 }} />);
    fireEvent.click(screen.getByRole('button', { name: /Cancelled, 2/ }));
    expect(onChange).toHaveBeenCalledWith('Cancelled Vouchers');
  });

  it.each(populatedGroupCases)('selects the first populated authoritative category for $group', ({ group, counts, category }) => {
    const onChange = vi.fn();
    render(<ResultsTabs activeTab="All" onChange={onChange} counts={counts} />);
    fireEvent.click(screen.getByRole('button', { name: group }));
    expect(onChange).toHaveBeenCalledWith(category);
  });

  it('falls back to the first configured category when a group is empty', () => {
    const onChange = vi.fn();
    render(<ResultsTabs activeTab="All" onChange={onChange} counts={{ major_difference: 0, date_mismatches: 0, invoice_mismatches: 0 }} />);
    fireEvent.click(screen.getByRole('button', { name: 'Differences' }));
    expect(onChange).toHaveBeenCalledWith('Major Difference');
  });

  it('uses source-neutral copy for the Notes accounts column', () => {
    render(<NotesTable results={[]} onRowClick={vi.fn()} sortField="supplier" sortOrder="asc" onSort={vi.fn()} />);
    expect(screen.getByRole('columnheader', { name: /Accounts Debit Note/ })).toBeInTheDocument();
    expect(screen.queryByText(/Tally Debit Note/)).not.toBeInTheDocument();
  });

  it('keeps purchase categories accessible and exposes Notes as a separate engine', () => {
    const onChange = vi.fn();
    const onDomainChange = vi.fn();
    const { rerender } = render(<ResultsTabs domain="purchase" activeTab="All" onDomainChange={onDomainChange} onChange={onChange} counts={{}} />);
    for (const group of ['All', 'Matched', 'Differences', 'Missing', 'Review']) {
      expect(screen.getByRole('button', { name: new RegExp(`^${group}`) })).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole('button', { name: /Notes Reconciliation/ }));
    expect(onDomainChange).toHaveBeenCalledWith('notes');

    const cases: Array<{ group: 'Matched' | 'Differences' | 'Missing' | 'Review'; active: any; labels: RegExp[] }> = [
      { group: 'Matched', active: 'Perfectly Matched', labels: [/Perfect/, /Minor/] },
      { group: 'Differences', active: 'Major Difference', labels: [/Major/, /Date Mismatch/, /Invoice Mismatch/] },
      { group: 'Missing', active: 'Missing in GSTR-2B', labels: [/Missing in GSTR-2B/, /Not in Accounts/] },
      { group: 'Review', active: 'Needs Review / Ambiguous', labels: [/Needs Review/, /Potential Duplicate/, /Taxable Missing/, /Cancelled/] },
    ];
    cases.forEach(({ active, labels }) => {
      rerender(<ResultsTabs domain="purchase" activeTab={active} onDomainChange={onDomainChange} onChange={onChange} counts={{}} />);
      labels.forEach(label => expect(screen.getByRole('button', { name: label })).toBeInTheDocument());
    });
  });

  it('renders cancelled identity, source, status and reason', () => {
    render(<CancelledVouchersTable results={[{
      id: 'cancel-1', source_side: 'ACCOUNTS', source_system: 'BUSY',
      supplier: 'Supplier', gstin: '06ABCDE1234F1Z5', invoice_number: 'A1',
      invoice_date: '2025-04-01', taxable_value: '0.00', igst: '0.00',
      cgst: '0.00', sgst: '0.00', source_sheet: 'Sheet1', source_row: 6,
      status: 'Cancelled BUSY Voucher', cancellation_reason: 'Explicit marker',
      cancellation_evidence: 'Account: CANCELLED',
    }]} />);
    expect(screen.getByText('Cancelled BUSY Voucher')).toBeInTheDocument();
    expect(screen.getByText('BUSY')).toBeInTheDocument();
    expect(screen.getByText('Explicit marker')).toBeInTheDocument();
  });
});
