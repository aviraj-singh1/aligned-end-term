import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { vi, describe, beforeEach, it, expect } from 'vitest';
import App from './App';
import * as api from './services/api';

vi.mock('./services/api');

let currentPageData: any;

describe('App Phase 6B Integration Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    currentPageData = mockReconciliationResponse;
    (api.healthCheck as any).mockResolvedValue({ status: 'ok', service: 'GST Tool' });
    window.alert = vi.fn();
    (api.getResultPage as any).mockImplementation((_session: string, params: any) => {
      let items: any[] = currentPageData.results;
      if (params.category === 'Not in Tally') items = currentPageData.not_in_tally;
      else if (params.category === 'Notes') items = currentPageData.notes;
      else if (params.category === 'Cancelled Vouchers') items = currentPageData.cancelled_vouchers || [];
      else if (params.category !== 'All' && params.category !== 'Date Mismatches' && params.category !== 'Invoice Mismatches') {
        items = items.filter(item => item.status === params.category);
      }
      if (params.search) {
        const query = params.search.toLowerCase();
        items = items.filter(item => [item.supplier, item.gstin, item.accounts_invoice, item.gst_invoice]
          .some(value => String(value ?? '').toLowerCase().includes(query)));
      }
      const start = (params.page - 1) * params.pageSize;
      return Promise.resolve({ items: items.slice(start, start + params.pageSize), total: items.length, page: params.page, page_size: params.pageSize, total_pages: Math.ceil(items.length / params.pageSize), category: params.category });
    });
  });

  const setupToReviewState = async () => {
    render(<App />);
    const fileInputs = document.querySelectorAll('input[type="file"]');
    const accFile = new File(['dummy'], 'accounts.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    const gstrFile = new File(['dummy'], 'gstr.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    
    fireEvent.change(fileInputs[0], { target: { files: [accFile] } });
    fireEvent.change(fileInputs[1], { target: { files: [gstrFile] } });

    (api.inspectFiles as any).mockResolvedValue({
      session_id: 'sess-123',
      files: {
        accounts: { original_filename: 'accounts.xlsx', row_count: 5, detected_sheets: ['Sheet1'] },
        gstr: { original_filename: 'gstr.xlsx', row_count: 5, detected_sheets: ['Sheet1'] }
      },
      mappings: {
        accounts: { 'Invoice Number': 'Voucher No.', 'Taxable Value': 'Value' },
        gstr: { 'Invoice Number': 'Invoice No.', 'Taxable Value': 'Taxable Value' }
      },
      available_columns: {
        accounts: ['Voucher No.', 'Value', 'Particulars'],
        gstr: ['Invoice No.', 'Party Name', 'Taxable Value']
      },
      preview: { accounts: [], gstr: [] },
      warnings: []
    });

    await waitFor(() => expect(screen.getByText('Inspect Files')).not.toBeDisabled());
    fireEvent.click(screen.getByText('Inspect Files'));
    await waitFor(() => {
      expect(screen.getByText('Review source data')).toBeInTheDocument();
    });
  };

  const mockReconciliationResponse = {
    session_id: 'sess-123',
    counts: {
      accounts_invoices: 100,
      gstr_invoices: 90,
      perfectly_matched: 80,
      minor_rounding_difference: 5,
      major_difference: 2,
      missing_in_gstr_2b: 10,
      not_in_tally: 3,
      needs_review_ambiguous: 1,
      potential_duplicate: 0,
      notes_matched: 0
    },
    results: [
      {
        result_id: 'res-1',
        status: 'Perfectly Matched',
        accounts_invoice: 'INV-001',
        gst_invoice: 'INV-001',
        supplier: 'Test Supplier A',
        gstin: '27AAAAA0000A1Z5',
        taxable_value: '1000.00',
        accounts_taxable: '1000.00',
        gst_taxable: '1000.00',
        match_method: 'Exact Invoice',
        remarks: '',
        candidates: null
      },
      {
        result_id: 'res-2',
        status: 'Needs Review / Ambiguous',
        accounts_invoice: 'INV-002',
        gst_invoice: null,
        supplier: 'Test Supplier B',
        gstin: '27BBBBB0000B1Z5',
        accounts_supplier: 'Original Tally Supplier',
        accounts_gstin: '27BBBBB0000B1Z5',
        taxable_value: '2000.00',
        accounts_taxable: '2000.00',
        accounts_igst: '360.00',
        accounts_cgst: '180.00',
        accounts_sgst: '180.00',
        accounts_date: '2025-04-02',
        gst_taxable: null,
        match_method: 'Ambiguous',
        remarks: 'Multiple candidates found',
        candidates: [
          {
            candidate_id: 'cand-1',
            gst_invoice: 'INV-002',
            supplier: 'Test Supplier B',
            gstin: '27BBBBB0000B1Z5',
            invoice_date: '2025-04-01',
            taxable_value: '2000.00',
            cgst: '180.00',
            sgst: '180.00',
            igst: '0.00'
          }
        ]
      },
      {
        result_id: 'res-3',
        status: 'Major Difference',
        accounts_invoice: 'INV-003',
        gst_invoice: 'INV-003',
        supplier: 'Test Supplier C',
        gstin: '27CCCCC0000C1Z5',
        taxable_value: '3000.00',
        accounts_taxable: '3000.00',
        gst_taxable: '3000.00',
        igst_diff: '100.00',
        cgst_diff: '0.00',
        sgst_diff: '0.00',
        match_method: 'Normalized Invoice',
        remarks: '', date_mismatch: false, date_difference_days: null
      }
    ],
    not_in_tally: [
      {
        gst_invoice: 'INV-004',
        supplier: 'Test Supplier D',
        gstin: '27DDDDD0000D1Z5',
        taxable_value: '4000.00'
      }
    ],
    notes: [],
    summary_text: 'Test Summary',
    has_ambiguous_matches: true
  };

  it('Run Reconciliation calls API and transitions to dashboard', async () => {
    await setupToReviewState();

    let resolveReconcile: any;
    const reconcilePromise = new Promise((resolve) => { resolveReconcile = resolve; });
    (api.reconcileSession as any).mockReturnValue(reconcilePromise);

    const runBtn = screen.getByText('Run Reconciliation');
    fireEvent.click(runBtn);

    expect(screen.getByText('Reconciling records...')).toBeInTheDocument();
    
    resolveReconcile(mockReconciliationResponse);

    await waitFor(() => {
      expect(screen.getByText('Reconciliation Results')).toBeInTheDocument();
    });

    // Check KPI Cards
    expect(screen.getByText('Perfect')).toBeInTheDocument();
    expect(screen.getAllByText('80')[0]).toBeInTheDocument();

    // Check Table Headers
    expect(await screen.findByText('Supplier')).toBeInTheDocument();

    // Check Results Rows
    expect(await screen.findByText('Test Supplier A')).toBeInTheDocument();
    expect(screen.getByText('Test Supplier B')).toBeInTheDocument();
    expect(screen.getByText('Test Supplier C')).toBeInTheDocument();
  });

  it('handles reconciliation failure', async () => {
    await setupToReviewState();

    (api.reconcileSession as any).mockRejectedValue({ response: { data: { detail: 'Session expired' } } });

    fireEvent.click(screen.getByText('Run Reconciliation'));

    await waitFor(() => {
      expect(screen.getByText('Your session may have expired. Please upload the files again.')).toBeInTheDocument();
    });
  });

  it('filters results by tabs', async () => {
    await setupToReviewState();
    (api.reconcileSession as any).mockResolvedValue(mockReconciliationResponse);
    fireEvent.click(screen.getByText('Run Reconciliation'));

    await waitFor(() => {
      expect(screen.getByText('Reconciliation Results')).toBeInTheDocument();
    });

    // Go to Review, whose safe default is Needs Review.
    fireEvent.click(screen.getByRole('button', { name: 'Review' }));
    
    await waitFor(() => {
      expect(screen.queryByText('Test Supplier A')).not.toBeInTheDocument(); // Perfect match, shouldn't be here
      expect(screen.getByText('Test Supplier B')).toBeInTheDocument(); // Ambiguous match
    });

    // Go to Missing, then select the source-neutral Not in Accounts filter.
    fireEvent.click(screen.getByRole('button', { name: 'Missing' }));
    fireEvent.click(screen.getByRole('button', { name: /^Not in Accounts, 3 results$/i }));

    await waitFor(() => {
      expect(screen.getByText('Test Supplier D')).toBeInTheDocument();
    });
  });

  it('search filter works across table', async () => {
    await setupToReviewState();
    (api.reconcileSession as any).mockResolvedValue(mockReconciliationResponse);
    fireEvent.click(screen.getByText('Run Reconciliation'));

    await waitFor(() => {
      expect(screen.getByText('Reconciliation Results')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/Search supplier/i);
    fireEvent.change(searchInput, { target: { value: 'Supplier C' } });

    await waitFor(() => {
      expect(screen.queryByText('Test Supplier A')).not.toBeInTheDocument();
      expect(screen.getByText('Test Supplier C')).toBeInTheDocument();
    });
  });

  it('opens comparison drawer and review candidate modal correctly', async () => {
    await setupToReviewState();
    (api.reconcileSession as any).mockResolvedValue(mockReconciliationResponse);
    fireEvent.click(screen.getByText('Run Reconciliation'));

    await waitFor(() => {
      expect(screen.getByText('Reconciliation Results')).toBeInTheDocument();
    });

    // Click on a perfect match
    fireEvent.click(await screen.findByText('Test Supplier A'));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Result details' })).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: 'Why this result?' })).toBeInTheDocument();
    });

    // Close it
    fireEvent.click(document.querySelector('.fixed.inset-0') as Element);

    // Click on Ambiguous
    fireEvent.click(await screen.findByText('Test Supplier B'));

    await waitFor(() => {
      expect(screen.getByText('Select the corresponding GSTR-2B invoice')).toBeInTheDocument();
      // Candidate should be shown
      expect(screen.getByText('Select This Invoice')).toBeInTheDocument();
    });
  });

  it('uses the authoritative manual-match response without a stale session refresh', async () => {
    await setupToReviewState();
    (api.reconcileSession as any).mockResolvedValue(mockReconciliationResponse);
    fireEvent.click(screen.getByText('Run Reconciliation'));

    await waitFor(() => {
      expect(screen.getByText('Reconciliation Results')).toBeInTheDocument();
    });

    // Click on Ambiguous
    fireEvent.click(await screen.findByText('Test Supplier B'));

    await waitFor(() => {
      expect(screen.getByText('Select This Invoice')).toBeInTheDocument();
    });

    // Mock API
    const updatedResponse = structuredClone(mockReconciliationResponse);
    updatedResponse.counts.perfectly_matched = 2;
    updatedResponse.counts.needs_review_ambiguous = 0;
    updatedResponse.counts.not_in_tally = 0;
    updatedResponse.results[1].status = 'Perfectly Matched';
    updatedResponse.results[1].match_method = 'Manual Match';
    updatedResponse.results[1].candidates = [];
    updatedResponse.not_in_tally = [];
    currentPageData = updatedResponse;
    (api.manualMatch as any).mockResolvedValue(updatedResponse);

    // Click select
    fireEvent.click(screen.getByText('Select This Invoice'));

    // Confirm dialog appears
    await waitFor(() => {
      expect(screen.getAllByText('Confirm Match')[0]).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Confirm Match' }));

    // Wait for drawer to close (implied by the fetch of updated data)
    await waitFor(() => {
      expect(api.manualMatch).toHaveBeenCalledWith('sess-123', 'res-2', 'cand-1');
      expect(screen.getByText('Manual match applied successfully.')).toBeInTheDocument();
    });
    
    // Check that it's no longer ambiguous in the UI
    // wait for UI to re-render
    await waitFor(() => {
      expect(screen.queryByText('Select the corresponding GSTR-2B invoice')).not.toBeInTheDocument();
    });
  });

  it('shows authoritative source values and updates candidates without changing Tally values', async () => {
    await setupToReviewState();
    const response = structuredClone(mockReconciliationResponse);
    response.results[1].candidates!.push({
      candidate_id: 'cand-2', gst_invoice: 'GST-ORIGINAL-002-B', supplier: 'Candidate B',
      gstin: '27CCCCC0000C1Z5', invoice_date: '2025-04-03', taxable_value: '2100.00',
      igst: '18.00', cgst: '90.00', sgst: '90.00'
    });
    currentPageData = response;
    (api.reconcileSession as any).mockResolvedValue(response);
    fireEvent.click(screen.getByText('Run Reconciliation'));
    await screen.findByText('Reconciliation Results');
    fireEvent.click(await screen.findByText('Test Supplier B'));

    const tally = await screen.findByTestId('tally-source');
    expect(within(tally).getByText('Original Tally Supplier')).toBeInTheDocument();
    expect(within(tally).getByText('INV-002')).toBeInTheDocument();
    expect(within(tally).getByText('02-04-2025')).toBeInTheDocument();
    for (const value of ['₹2,000.00', '₹360.00']) expect(within(tally).getByText(value)).toBeInTheDocument();
    expect(within(tally).getAllByText('₹180.00')).toHaveLength(2);

    const first = screen.getByTestId('gstr-candidate-cand-1');
    expect(within(first).getByText('INV-002')).toBeInTheDocument();
    expect(within(first).getByText('01-04-2025')).toBeInTheDocument();
    expect(within(first).getByText('₹0.00')).toBeInTheDocument();
    const second = screen.getByTestId('gstr-candidate-cand-2');
    expect(within(second).getByText('GST-ORIGINAL-002-B')).toBeInTheDocument();
    expect(within(second).getByText('03-04-2025')).toBeInTheDocument();
    expect(within(second).getByText('₹2,100.00')).toBeInTheDocument();

    fireEvent.click(within(first).getByRole('button', { name: 'Select This Invoice' }));
    expect((await screen.findByTestId('confirm-match-message')).textContent).toContain('GSTR-2B invoice INV-002');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(within(second).getByRole('button', { name: 'Select This Invoice' }));
    expect((await screen.findByTestId('confirm-match-message')).textContent).toContain('GSTR-2B invoice GST-ORIGINAL-002-B');
    expect(within(tally).getByText('₹360.00')).toBeInTheDocument();
  });

  it('distinguishes unavailable source values from actual zero', async () => {
    await setupToReviewState();
    const response = structuredClone(mockReconciliationResponse);
    (response.results[1] as any).accounts_igst = null;
    currentPageData = response;
    (api.reconcileSession as any).mockResolvedValue(response);
    fireEvent.click(screen.getByText('Run Reconciliation'));
    await screen.findByText('Reconciliation Results');
    fireEvent.click(await screen.findByText('Test Supplier B'));
    const tally = await screen.findByTestId('tally-source');
    expect(within(tally).getAllByText('—').length).toBeGreaterThan(0);
    expect(within(screen.getByTestId('gstr-candidate-cand-1')).getByText('₹0.00')).toBeInTheDocument();
  });

  it('shows an actionable conflict error when a candidate was already consumed', async () => {
    await setupToReviewState();
    (api.reconcileSession as any).mockResolvedValue(mockReconciliationResponse);
    fireEvent.click(screen.getByText('Run Reconciliation'));
    await screen.findByText('Reconciliation Results');
    fireEvent.click(await screen.findByText('Test Supplier B'));
    await screen.findByText('Select This Invoice');
    (api.manualMatch as any).mockRejectedValue({ response: { status: 409 } });
    fireEvent.click(screen.getByText('Select This Invoice'));
    await screen.findByRole('button', { name: 'Confirm Match' });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Match' }));
    expect(await screen.findByText('That GSTR-2B candidate has already been matched.')).toBeInTheDocument();
    expect(screen.getByText('Select the corresponding GSTR-2B invoice')).toBeInTheDocument();
  });

});
