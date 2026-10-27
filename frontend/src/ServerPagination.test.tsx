import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ResultsDashboard } from './components/results/ResultsDashboard';
import * as api from './services/api';

vi.mock('./services/api', () => ({
  getResultPage: vi.fn(),
  downloadExcel: vi.fn(),
  downloadNotesExcel: vi.fn(),
  downloadJson: vi.fn(),
}));

const row = {
  result_id: 'r-1', status: 'Perfectly Matched', supplier: 'Paged Supplier',
  gstin: '27AAAAA0000A1Z5', accounts_invoice: 'A-1', gst_invoice: 'G-1',
  taxable_value: '100.00', match_method: 'Exact Invoice', remarks: '', candidates: [],
};

const summary: any = {
  session_id: 'session-paged',
  counts: {
    all: 80, accounts_invoices: 80, gstr_invoices: 90, perfectly_matched: 80,
    minor_rounding_difference: 0, major_difference: 0, taxable_value_missing: 2,
    missing_in_gstr_2b: 0, not_in_tally: 3, needs_review_ambiguous: 1,
    potential_duplicate: 4, notes_matched: 0, total_notes: 0, date_mismatches: 2,
    invoice_mismatches: 0, cancelled_vouchers: 2,
  },
  results: [], not_in_tally: [], notes: [], cancelled_vouchers: [],
  summary_text: 'Authoritative summary', has_ambiguous_matches: true,
};

const renderDashboard = () => render(
  <ResultsDashboard data={summary} onManualMatch={vi.fn()} addToast={vi.fn()} onSessionExpired={vi.fn()} />
);

describe('server-paginated results dashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getResultPage).mockResolvedValue({
      items: [row], total: 80, page: 1, page_size: 25, total_pages: 4, category: 'all',
    });
  });

  it('requests page 1 and keeps global KPI counts independent of page rows', async () => {
    renderDashboard();
    expect(screen.getByRole('status')).toHaveTextContent('Loading results');
    expect(screen.getByText('Perfect')).toBeInTheDocument();
    expect(await screen.findByText('Paged Supplier')).toBeInTheDocument();
    expect(api.getResultPage).toHaveBeenCalledWith('session-paged', expect.objectContaining({ page: 1, pageSize: 25, category: 'All' }));
    expect(screen.getAllByText('80').length).toBeGreaterThan(1);
    expect(screen.getByText((_text, element) => element?.tagName === 'P' && element.textContent?.includes('of 80 results') === true)).toBeInTheDocument();
  });

  it('requests next pages and changed page sizes from the server', async () => {
    renderDashboard();
    await screen.findByText('Paged Supplier');
    fireEvent.click(screen.getAllByRole('button', { name: 'Next' })[0]);
    await waitFor(() => expect(api.getResultPage).toHaveBeenLastCalledWith('session-paged', expect.objectContaining({ page: 2 })));
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '50' } });
    await waitFor(() => expect(api.getResultPage).toHaveBeenLastCalledWith('session-paged', expect.objectContaining({ page: 1, pageSize: 50 })));
  });

  it('resets page and maps grouped navigation to valid backend categories', async () => {
    renderDashboard();
    await screen.findByText('Paged Supplier');
    fireEvent.click(screen.getAllByRole('button', { name: 'Next' })[0]);
    await waitFor(() => expect(api.getResultPage).toHaveBeenCalledWith('session-paged', expect.objectContaining({ page: 2 })));
    fireEvent.click(screen.getByRole('button', { name: 'Review' }));
    await waitFor(() => expect(api.getResultPage).toHaveBeenLastCalledWith('session-paged', expect.objectContaining({ category: 'Needs Review / Ambiguous', page: 1 })));
    fireEvent.click(screen.getByRole('button', { name: /Taxable Missing/ }));
    await waitFor(() => expect(api.getResultPage).toHaveBeenLastCalledWith('session-paged', expect.objectContaining({ category: 'Taxable Value Missing', page: 1 })));
    fireEvent.click(screen.getByRole('button', { name: /Cancelled, 2/ }));
    await waitFor(() => expect(api.getResultPage).toHaveBeenLastCalledWith('session-paged', expect.objectContaining({ category: 'Cancelled Vouchers', page: 1 })));
    fireEvent.click(screen.getByRole('button', { name: 'Differences' }));
    await waitFor(() => expect(api.getResultPage).toHaveBeenLastCalledWith('session-paged', expect.objectContaining({ category: 'Date Mismatches', page: 1 })));
    fireEvent.click(screen.getByRole('button', { name: 'Missing' }));
    await waitFor(() => expect(api.getResultPage).toHaveBeenLastCalledWith('session-paged', expect.objectContaining({ category: 'Not in Tally', page: 1 })));
  });

  it('debounces backend-wide search and sends whitelisted sort intent', async () => {
    renderDashboard();
    await screen.findByText('Paged Supplier');
    fireEvent.change(screen.getByPlaceholderText(/Search supplier/), { target: { value: 'needle' } });
    await waitFor(() => expect(api.getResultPage).toHaveBeenLastCalledWith('session-paged', expect.objectContaining({ search: 'needle' })), { timeout: 1000 });
    fireEvent.click(screen.getByRole('button', { name: /Supplier/ }));
    await waitFor(() => expect(api.getResultPage).toHaveBeenLastCalledWith('session-paged', expect.objectContaining({ sortBy: 'supplier', sortOrder: 'desc' })));
  });

  it('shows empty and table-local API error states without removing the dashboard', async () => {
    vi.mocked(api.getResultPage).mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 25, total_pages: 0, category: 'all' });
    const { unmount } = renderDashboard();
    expect(await screen.findByText('No reconciliation records found.')).toBeInTheDocument();
    expect(screen.getByText('Perfect')).toBeInTheDocument();
    unmount();
    vi.mocked(api.getResultPage).mockRejectedValueOnce({ response: { data: { detail: 'Page temporarily unavailable' } } });
    renderDashboard();
    expect(await screen.findByRole('alert')).toHaveTextContent('Page temporarily unavailable');
    expect(screen.getByText('Perfect')).toBeInTheDocument();
    vi.mocked(api.getResultPage).mockResolvedValueOnce({ items: [row], total: 1, page: 1, page_size: 25, total_pages: 1, category: 'all' });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Paged Supplier')).toBeInTheDocument();
  });

  it('sends the existing supported GSTR invoice sort key and resets the page for Results and Notes', async () => {
    renderDashboard();
    await screen.findByText('Paged Supplier');
    fireEvent.click(screen.getAllByRole('button', { name: 'Next' })[0]);
    await screen.findByRole('button', { name: 'GSTR-2B Invoice' });
    fireEvent.click(screen.getByRole('button', { name: 'GSTR-2B Invoice' }));
    await waitFor(() => expect(api.getResultPage).toHaveBeenLastCalledWith('session-paged', expect.objectContaining({ sortBy: 'gstr_invoice', sortOrder: 'asc', page: 1 })));
    fireEvent.click(screen.getByRole('button', { name: /^Notes/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'GSTR-2B Credit Note' }));
    await waitFor(() => expect(api.getResultPage).toHaveBeenLastCalledWith('session-paged', expect.objectContaining({ category: 'Notes All', sortBy: 'gstr_invoice', sortOrder: 'desc', page: 1 })));
  });

  it('distinguishes category, search and Notes empty states', async () => {
    vi.mocked(api.getResultPage).mockResolvedValue({ items: [], total: 0, page: 1, page_size: 25, total_pages: 0, category: 'all' });
    renderDashboard();
    expect(await screen.findByText('No reconciliation records found.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Differences' }));
    expect(await screen.findByText('No date mismatches found.')).toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox', { name: 'Search results' }), { target: { value: 'missing supplier' } });
    expect(await screen.findByText('No results match your search.', {}, { timeout: 1000 })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Notes/ }));
    expect(await screen.findByText('No Notes reconciliation records found.')).toBeInTheDocument();
  });
});
