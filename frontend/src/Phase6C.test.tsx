import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import App from './App';
import * as api from './services/api';
import * as downloadHelper from './utils/downloadHelper';

// Mock the API and download helper
vi.mock('./services/api', async () => {
  const actual = await vi.importActual('./services/api');
  return {
    ...actual as any,
    healthCheck: vi.fn(),
    inspectFiles: vi.fn(),
    reconcileSession: vi.fn(),
    getResultPage: vi.fn(),
    downloadExcel: vi.fn(),
    downloadNotesExcel: vi.fn(),
    downloadJson: vi.fn(),
    deleteSession: vi.fn()
  };
});
vi.mock('./utils/downloadHelper', () => ({
  triggerBrowserDownload: vi.fn()
}));

const mockInspectionData = {
  session_id: 'test-session-123',
  files: {
    accounts: { original_filename: 'Tally.xlsx', detected_sheets: [], selected_sheet: null, row_count: 10 },
    gstr: { original_filename: 'GSTR.xlsx', detected_sheets: [], selected_sheet: null, row_count: 15 }
  },
  mappings: { accounts: { 'Invoice Number': 'Inv', 'Taxable Value': 'Tax' }, gstr: { 'GSTIN': 'G', 'Invoice Number': 'I', 'Taxable Value': 'T' } },
  available_columns: { accounts: ['Inv', 'Tax'], gstr: ['G', 'I', 'T'] },
  preview: { accounts: [], gstr: [] },
  warnings: []
};

const mockReconciliationData = {
  session_id: 'test-session-123',
  counts: {
    accounts_invoices: 618,
    gstr_invoices: 846,
    perfectly_matched: 496,
    minor_rounding_difference: 66,
    major_difference: 5,
    taxable_value_missing: 2,
    missing_in_gstr_2b: 51,
    not_in_tally: 279,
    needs_review_ambiguous: 0,
    notes_matched: 0,
    total_notes: 71,
    date_mismatches: 0,
    invoice_mismatches: 0,
    potential_duplicate: 0
  },
  results: [
    {
      result_id: 'r1',
      status: 'Major Difference',
      supplier: 'Test Supplier',
      gstin: '07MOCKX1234A1Z5',
      accounts_invoice: '123',
      gst_invoice: '123',
      taxable_value: '100',
      match_method: 'exact',
      accounts_taxable: '100',
      gst_taxable: '200',
      accounts_date: '2025-05-10',
      gst_date: '2025-05-11',
      remarks: 'Test',
      date_mismatch: false,
      date_difference_days: null,
      candidates: null,
      igst_diff: null, cgst_diff: null, sgst_diff: null, accounts_igst: null, accounts_cgst: null, accounts_sgst: null, gst_igst: null, gst_cgst: null, gst_sgst: null
    }
  ],
  not_in_tally: [],
  notes: [],
  notes_summary: { total_debit_notes: 71, total_gstr_credit_notes: 71, perfect: 40, minor: 10, major: 5, missing_in_gstr: 6, missing_in_debit_notes: 7, needs_review: 2, potential_duplicate: 1 },
  summary_text: '618 Tally invoices were compared with 846 GSTR-2B invoices.',
  has_ambiguous_matches: false
};

describe('Phase 6C Integration Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.healthCheck).mockResolvedValue({ status: 'ok', service: 'GST Tool' });
    vi.mocked(api.getResultPage).mockImplementation(async (_session, params) => ({
      items: mockReconciliationData.results,
      total: mockReconciliationData.results.length,
      page: params.page,
      page_size: params.pageSize,
      total_pages: 1,
      category: params.category,
    }));
  });

  const setupReconciliation = async (reconciliationData = mockReconciliationData) => {
    vi.mocked(api.inspectFiles).mockResolvedValue(mockInspectionData);
    vi.mocked(api.reconcileSession).mockResolvedValue(reconciliationData);

    const { container } = render(<App />);

    // Upload step
    const fileInputs = container.querySelectorAll('input[type="file"]');
    const tallyFile = new File(['dummy'], 'tally.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    const gstrFile = new File(['dummy'], 'gstr.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    fireEvent.change(fileInputs[0], { target: { files: [tallyFile] } });
    fireEvent.change(fileInputs[1], { target: { files: [gstrFile] } });
    
    await waitFor(() => expect(screen.getByText('Inspect Files')).not.toBeDisabled());
    fireEvent.click(screen.getByText('Inspect Files'));
    await waitFor(() => expect(screen.getByText('Review source data')).toBeInTheDocument());

    // Review step -> Reconcile
    fireEvent.click(screen.getByText('Run Reconciliation'));
    await waitFor(() => expect(screen.getByText('Reconciliation Results')).toBeInTheDocument());

    return container;
  };

  it('renders Export panel after reconciliation', async () => {
    await setupReconciliation();
    expect(screen.getByText('Export reconciliation')).toBeInTheDocument();
    expect(screen.getByText('Recommended')).toBeInTheDocument();
    expect(screen.getByText(/integrations or technical workflows/i)).toBeInTheDocument();
    expect(screen.getByText('Download Purchase Report')).toBeInTheDocument();
    expect(screen.getByText('Download Notes Report')).toBeInTheDocument();
    expect(screen.getByText('Download JSON')).toBeInTheDocument();
  });

  it('shows action-specific export loading labels without cross-locking formats', async () => {
    await setupReconciliation();
    let resolveExcel: (value: { blob: Blob; filename: string }) => void = () => undefined;
    vi.mocked(api.downloadExcel).mockReturnValue(new Promise(resolve => { resolveExcel = resolve; }));
    fireEvent.click(screen.getByRole('button', { name: 'Download Purchase Report' }));
    expect(screen.getByRole('button', { name: 'Preparing Purchase...' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Download JSON' })).toBeEnabled();
    resolveExcel({ blob: new Blob(['excel']), filename: 'report.xlsx' });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Download Purchase Report' })).toBeEnabled());
    let resolveJson: (value: { blob: Blob; filename: string }) => void = () => undefined;
    vi.mocked(api.downloadJson).mockReturnValue(new Promise(resolve => { resolveJson = resolve; }));
    fireEvent.click(screen.getByRole('button', { name: 'Download JSON' }));
    expect(screen.getByRole('button', { name: 'Preparing JSON...' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Download Purchase Report' })).toBeEnabled();
    resolveJson({ blob: new Blob(['json']), filename: 'report.json' });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Download JSON' })).toBeEnabled());
  });

  it('shows the reconciliation-only product boundary on results', async () => {
    await setupReconciliation();
    expect(screen.getByRole('note')).toHaveTextContent(
      'Reconciliation results reflect data matching only and do not determine ITC eligibility or GST compliance.'
    );
    expect(screen.getByRole('note')).toHaveTextContent('Review eligibility separately before claiming ITC.');
    expect(screen.getByText('Perfect')).toBeInTheDocument();
  });

  it('keeps wide purchase and notes tables navigable without dropping columns', async () => {
    await setupReconciliation();
    expect(await screen.findByTestId('wide-results-table')).toBeInTheDocument();
    expect(screen.getByTestId('results-top-scrollbar')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: /Purchase reconciliation results/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Notes Reconciliation/i }));
    await waitFor(() => expect(screen.getByRole('region', { name: /Notes reconciliation results/i })).toBeInTheDocument());
    expect(screen.getByText('Scroll horizontally to compare every column')).toBeInTheDocument();
  });

  it('calls correct API on Excel download', async () => {
    await setupReconciliation();
    
    vi.mocked(api.downloadExcel).mockResolvedValue({
      blob: new Blob(['excel data']),
      filename: 'Test_Excel.xlsx'
    });

    fireEvent.click(screen.getByText('Download Purchase Report'));

    await waitFor(() => {
      expect(api.downloadExcel).toHaveBeenCalledWith('test-session-123');
      expect(downloadHelper.triggerBrowserDownload).toHaveBeenCalledWith(expect.any(Blob), 'Test_Excel.xlsx');
    });
  });

  it('calls correct API on JSON download', async () => {
    await setupReconciliation();
    
    vi.mocked(api.downloadJson).mockResolvedValue({
      blob: new Blob(['json data']),
      filename: 'Test_Json.json'
    });

    fireEvent.click(screen.getByText('Download JSON'));

    await waitFor(() => {
      expect(api.downloadJson).toHaveBeenCalledWith('test-session-123');
      expect(downloadHelper.triggerBrowserDownload).toHaveBeenCalledWith(expect.any(Blob), 'Test_Json.json');
    });
  });

  it('shows summary text and keeps the Notes category count accessible', async () => {
    await setupReconciliation();
    expect(screen.getByText('618 Accounts invoices were compared with 846 GSTR-2B invoices.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Notes Reconciliation/i })).toHaveTextContent('71');
  });

  it('downloads the Notes workbook separately', async () => {
    await setupReconciliation();
    vi.mocked(api.downloadNotesExcel).mockResolvedValue({
      blob: new Blob(['notes data']), filename: 'GSTR2B_Notes_Reconciliation.xlsx'
    });

    fireEvent.click(screen.getByText('Download Notes Report'));

    await waitFor(() => {
      expect(api.downloadNotesExcel).toHaveBeenCalledWith('test-session-123');
      expect(downloadHelper.triggerBrowserDownload).toHaveBeenCalledWith(
        expect.any(Blob), 'GSTR2B_Notes_Reconciliation.xlsx'
      );
    });
  });

  it('disables Notes export when the run has no Notes data', async () => {
    await setupReconciliation({
      ...mockReconciliationData,
      counts: { ...mockReconciliationData.counts, total_notes: 0 },
      notes_summary: { total_debit_notes: 0, total_gstr_credit_notes: 0, perfect: 0, minor: 0, major: 0, missing_in_gstr: 0, missing_in_debit_notes: 0, needs_review: 0, potential_duplicate: 0 },
    });
    expect(screen.getByRole('button', { name: 'Download Notes Report' })).toBeDisabled();
    expect(screen.getByText('No Notes data was included in this run.')).toBeInTheDocument();
  });

  it('explains oversized JSON Blob responses and leaves Excel usable', async () => {
    await setupReconciliation();
    vi.mocked(api.downloadJson).mockRejectedValue({ response: { status: 413,
      data: new Blob([JSON.stringify({ detail: 'JSON export is too large' })], { type: 'application/json' }) } });
    fireEvent.click(screen.getByText('Download JSON'));
    await waitFor(() => expect(document.getElementById('json-export-error')).toHaveTextContent(
      'JSON export is too large for the current pilot deployment. Excel export is still available.'
    ));
    expect(screen.queryByText('JSON export failed.')).not.toBeInTheDocument();
    vi.mocked(api.downloadExcel).mockResolvedValue({ blob: new Blob(['excel']), filename: 'report.xlsx' });
    expect(screen.getByText('Download Purchase Report')).toBeEnabled();
    fireEvent.click(screen.getByText('Download Purchase Report'));
    await waitFor(() => expect(api.downloadExcel).toHaveBeenCalledWith('test-session-123'));
    expect(screen.getByText('Export reconciliation')).toBeInTheDocument();
  });

  it('shows the Taxable Missing attention metric and Taxable Value Missing tab count', async () => {
    await setupReconciliation();
    expect(screen.getByText('Taxable Missing')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Review' }));
    expect(screen.getByRole('button', { name: /Taxable Missing, 2 results/ })).toBeInTheDocument();
    expect(screen.getAllByText('2').length).toBeGreaterThanOrEqual(2);
  });

  it('formats date correctly in detail drawer', async () => {
    await setupReconciliation();
    fireEvent.click(await screen.findByText('Test Supplier')); // Open drawer

    await waitFor(() => {
      expect(screen.getByText('10-05-2025')).toBeInTheDocument();
      expect(screen.getByText('11-05-2025')).toBeInTheDocument();
    });
  });

  it('handles negative currency formatting properly', async () => {
    const dataWithNegativeDiff = {
      ...mockReconciliationData,
      results: [{
        ...mockReconciliationData.results[0],
        accounts_taxable: '0',
        gst_taxable: '947.52'
      }]
    };
    vi.mocked(api.inspectFiles).mockResolvedValue(mockInspectionData);
    vi.mocked(api.reconcileSession).mockResolvedValue(dataWithNegativeDiff);
    vi.mocked(api.getResultPage).mockImplementation(async (_session, params) => ({
      items: dataWithNegativeDiff.results,
      total: dataWithNegativeDiff.results.length,
      page: params.page,
      page_size: params.pageSize,
      total_pages: 1,
      category: params.category,
    }));
    
    const { container } = render(<App />);
    const fileInputs = container.querySelectorAll('input[type="file"]');
    fireEvent.change(fileInputs[0], { target: { files: [new File([''], 'a.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })] } });
    fireEvent.change(fileInputs[1], { target: { files: [new File([''], 'b.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })] } });
    await waitFor(() => expect(screen.getByText('Inspect Files')).not.toBeDisabled());
    fireEvent.click(screen.getByText('Inspect Files'));
    await waitFor(() => expect(screen.getByText('Run Reconciliation')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Run Reconciliation'));
    await waitFor(() => expect(screen.getByText('Reconciliation Results')).toBeInTheDocument());

    fireEvent.click(await screen.findByText('Test Supplier')); // open drawer
    
    // 0 - 947.52 = -947.52
    await waitFor(() => {
      expect(screen.getByText('-₹947.52')).toBeInTheDocument();
    });
  });

  it('does not display session ID prominently', async () => {
    await setupReconciliation();
    expect(screen.queryByText(/Session ID: test-session-123/)).not.toBeInTheDocument();
  });

  it('stacks only the Results action and Attention heading on mobile while retaining wider-screen rows', async () => {
    await setupReconciliation();
    const reset = screen.getByRole('button', { name: 'Start New Reconciliation' });
    expect(reset.parentElement).toHaveClass('flex-col', 'items-start', 'sm:flex-row');
    expect(reset).toHaveClass('max-sm:whitespace-nowrap');
    expect(screen.getByRole('heading', { name: 'Reconciliation Results' }).parentElement?.parentElement).toBe(reset.parentElement);

    const attention = screen.getByRole('heading', { name: 'Attention', level: 2 });
    expect(attention.parentElement).toHaveClass('flex-col', 'gap-1', 'sm:flex-row', 'sm:items-baseline', 'sm:justify-between', 'sm:gap-4');
    expect(screen.getByText('Signals may overlap and should be reviewed individually').parentElement).toBe(attention.parentElement);
    expect(attention.parentElement?.nextElementSibling).toHaveClass('grid-cols-2', 'md:grid-cols-3', 'xl:grid-cols-6');
    expect(screen.getByRole('heading', { name: 'Outcome' }).parentElement).not.toHaveClass('flex-col');
  });

  it('deletes session when Start New Reconciliation is clicked', async () => {
    await setupReconciliation();
    
    // Mock window.confirm
    vi.spyOn(window, 'confirm').mockImplementation(() => true);
    
    fireEvent.click(screen.getByText('Start New Reconciliation'));
    
    await waitFor(() => {
      expect(api.deleteSession).toHaveBeenCalledWith('test-session-123');
      expect(screen.getByText('Upload source data')).toBeInTheDocument(); // Returns to upload step
    });
  });
  
  it('shows error toast on download failure', async () => {
    await setupReconciliation();
    
    vi.mocked(api.downloadExcel).mockRejectedValue(new Error('Export failed'));
    fireEvent.click(screen.getByText('Download Purchase Report'));
    
    await waitFor(() => {
      expect(document.querySelector('#purchase-export-error')).toHaveTextContent('Unable to generate the Purchase report. Please try again.');
    });
    expect(document.querySelector('#purchase-export-error')).toBeInTheDocument();
    vi.mocked(api.downloadExcel).mockResolvedValue({ blob: new Blob(['retry']), filename: 'retry.xlsx' });
    fireEvent.click(screen.getByRole('button', { name: 'Download Purchase Report' }));
    await waitFor(() => expect(api.downloadExcel).toHaveBeenCalledTimes(2));
  });
});
