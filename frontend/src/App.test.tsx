import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, describe, beforeEach, it, expect } from 'vitest';
import App from './App';
import * as api from './services/api';

vi.mock('./services/api');

describe('App Phase 6A Integration Tests', () => {
  it.each([24_999_999, 25_000_000, 25_000_001])('checks combined bytes including optional notes before upload: %i', async total => {
    vi.mocked(api.inspectFiles).mockRejectedValue(new Error('Reached API'));
    render(<App />);
    const inputs = document.querySelectorAll('input[type="file"]');
    const sizes = [10_000_000, 10_000_000, total - 20_000_000];
    const extensions = ['xls', 'xlsx', 'json'];
    for (let i = 0; i < 3; i++) {
      fireEvent.change(inputs[i], { target: { files: [new File([new Uint8Array(sizes[i])], `input${i}.${extensions[i]}`)] } });
    }
    await waitFor(() => expect(screen.getByText('Inspect Files')).toBeEnabled());
    fireEvent.click(screen.getByText('Inspect Files'));
    if (total > 25_000_000) {
      expect(await screen.findByText(/The selected files exceed the 25 MB combined upload limit/)).toBeInTheDocument();
      expect(api.inspectFiles).not.toHaveBeenCalled();
    } else {
      await waitFor(() => expect(api.inspectFiles).toHaveBeenCalledOnce());
    }
  });

  it('allows the 3,561,475-byte Jai Medical file pair to reach inspection', async () => {
    vi.mocked(api.inspectFiles).mockRejectedValue(new Error('Reached API'));
    render(<App />);
    const inputs = document.querySelectorAll('input[type="file"]');
    const files = [
      new File([new Uint8Array(979_000)], 'Jai_Medical.xls', { type: 'application/vnd.ms-excel' }),
      new File([new Uint8Array(2_582_475)], 'JAI MEDICAL gstr.xls', { type: 'application/vnd.ms-excel' }),
    ];
    fireEvent.change(inputs[0], { target: { files: [files[0]] } });
    fireEvent.change(inputs[1], { target: { files: [files[1]] } });
    await waitFor(() => expect(screen.getByText('Inspect Files')).toBeEnabled());
    fireEvent.click(screen.getByText('Inspect Files'));
    await waitFor(() => expect(api.inspectFiles).toHaveBeenCalledOnce());
    expect(files[0].size + files[1].size).toBe(3_561_475);
  });
  beforeEach(() => {
    vi.clearAllMocks();
    (api.healthCheck as any).mockResolvedValue({ status: 'ok', service: 'GST Tool' });
    window.alert = vi.fn();
  });

  it('renders application and displays health success', async () => {
    render(<App />);
    expect(screen.getByRole('img', { name: 'Aligned' })).toBeInTheDocument();
    expect(screen.getByText('Reconciliation workspace')).toBeInTheDocument();
    
    // health check is called on mount
    await waitFor(() => {
      expect(screen.getByText('Connected')).toBeInTheDocument();
    });
  });

  it('displays health failure state', async () => {
    (api.healthCheck as any).mockRejectedValue(new Error('Network error'));
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText('Backend Unavailable')).toBeInTheDocument();
      expect(screen.getByText('Retry')).toBeInTheDocument();
    });
  });

  it('requires Accounts and GSTR files, Inspect button is disabled initially', async () => {
    render(<App />);
    const inspectBtn = screen.getByText('Inspect Files');
    expect(inspectBtn).toBeDisabled();
    
    // Both uploads are visible
    expect(screen.getByText('Accounts Purchase Data')).toBeInTheDocument();
    expect(screen.getByText('GSTR-2B')).toBeInTheDocument();
    expect(screen.getByText('Debit Notes')).toBeInTheDocument(); // Optional
  });

  it('unsupported file extension shows an inline error and is rejected', async () => {
    render(<App />);
    const fileInputs = document.querySelectorAll('input[type="file"]');
    const accountsInput = fileInputs[0] as HTMLInputElement;

    const file = new File(['hello'], 'invalid.txt', { type: 'text/plain' });
    fireEvent.change(accountsInput, { target: { files: [file] } });

    expect(screen.getByRole('alert')).toHaveTextContent('Unsupported file type');
    expect(screen.queryByText('invalid.txt')).not.toBeInTheDocument();
  });

  it('advertises all supported workbook and JSON formats', () => {
    render(<App />);
    const fileInputs = document.querySelectorAll('input[type="file"]');

    expect(fileInputs).toHaveLength(3);
    fileInputs.forEach((input) => {
      expect(input).toHaveAttribute('accept', expect.stringContaining('.xls'));
      expect(input).toHaveAttribute('accept', expect.stringContaining('application/vnd.ms-excel'));
    });
    expect(screen.getAllByText('.xls, .xlsx and .json · 25 MB combined upload limit')).toHaveLength(3);
    expect(screen.queryByText(/50 MB/)).not.toBeInTheDocument();
    expect(screen.getByText(/Production tested with approximately 1,000/)).toBeInTheDocument();
  });

  it('accepts a legacy .xls selection for backend content validation', async () => {
    render(<App />);
    const accountsInput = document.querySelectorAll('input[type="file"]')[0] as HTMLInputElement;
    const file = new File(['legacy'], 'accounts.xls', { type: 'application/vnd.ms-excel' });

    fireEvent.change(accountsInput, { target: { files: [file] } });

    expect(await screen.findByText('accounts.xls')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('selects valid files, enables Inspect button, and displays loading state on submit', async () => {
    render(<App />);
    const fileInputs = document.querySelectorAll('input[type="file"]');
    
    const accFile = new File(['dummy'], 'accounts.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    const gstrFile = new File(['dummy'], 'gstr.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });

    fireEvent.change(fileInputs[0], { target: { files: [accFile] } });
    fireEvent.change(fileInputs[1], { target: { files: [gstrFile] } });

    // File names should be visible
    expect(await screen.findByText('accounts.xlsx')).toBeInTheDocument();
    expect(await screen.findByText('gstr.xlsx')).toBeInTheDocument();

    const inspectBtn = screen.getByText('Inspect Files');
    expect(inspectBtn).not.toBeDisabled();

    // Mock API
    let resolveInspect: any;
    const inspectPromise = new Promise((resolve) => { resolveInspect = resolve; });
    (api.inspectFiles as any).mockReturnValue(inspectPromise);

    fireEvent.click(inspectBtn);

    expect(screen.getByText('Inspecting files...')).toBeInTheDocument();
    expect(inspectBtn).toBeDisabled();

    // resolve it
    resolveInspect({
      session_id: 'sess-123',
      files: {
        accounts: { original_filename: 'accounts.xlsx', row_count: 5, detected_sheets: ['Sheet1'] },
        gstr: { original_filename: 'gstr.xlsx', row_count: 5, detected_sheets: ['Sheet1'] }
      },
      mappings: {
        accounts: { 'Invoice Number': 'Voucher No.' },
        gstr: { 'Invoice Number': 'Invoice No.' }
      },
      available_columns: {
        accounts: ['Voucher No.'],
        gstr: ['Invoice No.']
      },
      preview: {
        accounts: [],
        gstr: []
      },
      warnings: ['A warning occurred']
    });

    await waitFor(() => {
      expect(screen.getByText('Review source data')).toBeInTheDocument();
    });
  });

  it('inspection error displays cleanly', async () => {
    render(<App />);
    const fileInputs = document.querySelectorAll('input[type="file"]');
    const accFile = new File(['dummy'], 'accounts.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    const gstrFile = new File(['dummy'], 'gstr.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    
    fireEvent.change(fileInputs[0], { target: { files: [accFile] } });
    fireEvent.change(fileInputs[1], { target: { files: [gstrFile] } });

    (api.inspectFiles as any).mockRejectedValue(new Error('Backend failed to parse'));
    
    await waitFor(() => expect(screen.getByText('Inspect Files')).not.toBeDisabled());
    fireEvent.click(screen.getByText('Inspect Files'));

    await waitFor(() => {
      expect(screen.getByText('Backend failed to parse')).toBeInTheDocument();
    });
  });

  it('displays detected mappings, warnings, and allows mapping change in Review state', async () => {
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
        gstr: { 'Invoice Number': 'Invoice No.' }
      },
      available_columns: {
        accounts: ['Voucher No.', 'Value', 'Particulars'],
        gstr: ['Invoice No.', 'Party Name']
      },
      preview: {
        accounts: [{ supplier: 'Test', invoice_number: '123', taxable_value: '1000' }],
        gstr: []
      },
      warnings: ['Multiple sheets detected']
    });

    await waitFor(() => expect(screen.getByText('Inspect Files')).not.toBeDisabled());
    fireEvent.click(screen.getByText('Inspect Files'));

    await waitFor(() => {
      expect(screen.getByText('Review source data')).toBeInTheDocument();
    });

    // Check warning
    expect(screen.getByText('Multiple sheets detected')).toBeInTheDocument();

    // Check Mapping
    // "Voucher No." should be selected for Invoice Number in Accounts
    const selects = screen.getAllByRole('combobox');
    // React Testing Library allows changing select
    expect(selects.length).toBeGreaterThan(0);

    // Check preview
    expect(screen.getByText('Accounts Source (5 rows)')).toBeInTheDocument();
    
    // Change Files button
    vi.spyOn(window, 'confirm').mockImplementation(() => true);
    fireEvent.click(screen.getByText('Change Files'));
    
    await waitFor(() => {
      expect(screen.getByText('Upload source data')).toBeInTheDocument();
    });
  });
});

describe('App Pre-fill Default Mappings Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.healthCheck as any).mockResolvedValue({ status: 'ok', service: 'GST Tool' });
    window.alert = vi.fn();
  });

  const uploadAndInspect = async (availableAccounts: string[], availableGstr: string[], initialMappings = { accounts: {}, gstr: {} }) => {
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
      mappings: initialMappings,
      available_columns: {
        accounts: availableAccounts,
        gstr: availableGstr
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

  it('1 & 2. Standard Tally and GSTR columns available -> all 8 expected mappings preselected', async () => {
    await uploadAndInspect(
      ['Particulars', 'GSTIN', 'Voucher No.', 'Date', 'Value', 'Igst', 'CGST', 'SGST'],
      ['Party Name', 'GSTIN/UIN', 'Invoice No.', 'Invoice Date', 'Taxable Value', 'IGST Amount', 'CGST Amount', 'SGST Amount']
    );

    const selects = screen.getAllByRole('combobox') as HTMLSelectElement[];
    const allValues = selects.map(s => s.value);
    
    expect(allValues).toContain('Particulars');
    expect(allValues).toContain('GSTIN');
    expect(allValues).toContain('Voucher No.');
    expect(allValues).toContain('Date');
    expect(allValues).toContain('Value');
    expect(allValues).toContain('Igst');
    expect(allValues).toContain('CGST');
    expect(allValues).toContain('SGST');
    
    expect(allValues).toContain('Party Name');
    expect(allValues).toContain('GSTIN/UIN');
    expect(allValues).toContain('Invoice No.');
    expect(allValues).toContain('Invoice Date');
    expect(allValues).toContain('Taxable Value');
    expect(allValues).toContain('IGST Amount');
    expect(allValues).toContain('CGST Amount');
    expect(allValues).toContain('SGST Amount');
  });

  it('3. Tally GSTIN column missing -> GSTIN stays Not mapped', async () => {
    await uploadAndInspect(
      ['Particulars', 'Voucher No.', 'Date', 'Value'],
      []
    );
    const selects = screen.getAllByRole('combobox') as HTMLSelectElement[];
    const allValues = selects.map(s => s.value);
    expect(allValues).not.toContain('GSTIN');
    expect(allValues).toContain(''); 
  });

  it('4 & 5. User changes a prefilled mapping -> user selection remains / GSTIN to Not mapped does not revert', async () => {
    await uploadAndInspect(
      ['Particulars', 'GSTIN', 'Voucher No.', 'Date', 'Value', 'Igst', 'CGST', 'SGST'],
      ['Party Name', 'GSTIN/UIN', 'Invoice No.', 'Invoice Date', 'Taxable Value', 'IGST Amount', 'CGST Amount', 'SGST Amount']
    );
    
    const selects = screen.getAllByRole('combobox') as HTMLSelectElement[];
    const gstinSelect = selects.find(s => s.value === 'GSTIN');
    expect(gstinSelect).toBeDefined();
    
    fireEvent.change(gstinSelect!, { target: { value: '' } });
    expect(gstinSelect!.value).toBe('');
    
    fireEvent.click(screen.getByText(/GSTR-2B \(/));
    
    expect(gstinSelect!.value).toBe('');
  });

  it('6. Change Files + new structure -> mappings initialize using new file', async () => {
    await uploadAndInspect(
      ['Particulars', 'GSTIN'],
      ['Party Name']
    );
    
    let selects = screen.getAllByRole('combobox') as HTMLSelectElement[];
    let allValues = selects.map(s => s.value);
    expect(allValues).toContain('Particulars');
    
    vi.spyOn(window, 'confirm').mockImplementation(() => true);
    fireEvent.click(screen.getByText('Change Files'));
    
    await waitFor(() => {
      expect(screen.getByText('Upload source data')).toBeInTheDocument();
    });
    
    const fileInputs = document.querySelectorAll('input[type="file"]');
    fireEvent.change(fileInputs[0], { target: { files: [new File(['dummy'], 'a.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })] } });
    fireEvent.change(fileInputs[1], { target: { files: [new File(['dummy'], 'b.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })] } });

    (api.inspectFiles as any).mockResolvedValue({
      session_id: 'sess-124',
      files: {
        accounts: { original_filename: 'a.xlsx', row_count: 5, detected_sheets: ['Sheet1'] },
        gstr: { original_filename: 'b.xlsx', row_count: 5, detected_sheets: ['Sheet1'] }
      },
      mappings: { accounts: {}, gstr: {} },
      available_columns: {
        accounts: ['Voucher No.'],
        gstr: ['Invoice No.']
      },
      preview: { accounts: [], gstr: [] },
      warnings: []
    });

    await waitFor(() => expect(screen.getByText('Inspect Files')).not.toBeDisabled());
    fireEvent.click(screen.getByText('Inspect Files'));

    await waitFor(() => {
      expect(screen.getByText('Review source data')).toBeInTheDocument();
    });
    
    selects = screen.getAllByRole('combobox') as HTMLSelectElement[];
    allValues = selects.map(s => s.value);
    expect(allValues).not.toContain('Particulars');
    expect(allValues).toContain('Voucher No.');
  });

  it('7. Non-standard column names -> do not select nonexistent standard columns', async () => {
    await uploadAndInspect(
      ['WeirdName', 'SomeCol'],
      ['StrangeCol']
    );
    const selects = screen.getAllByRole('combobox') as HTMLSelectElement[];
    const allValues = selects.map(s => s.value);
    expect(allValues).not.toContain('Particulars');
    expect(allValues).not.toContain('GSTIN');
  });

  it('8. Required field missing -> existing validation still blocks reconciliation', async () => {
    await uploadAndInspect(
      ['Particulars', 'Date', 'Value'],
      ['Party Name', 'Invoice Date', 'Taxable Value']
    );
    const reconcileBtn = screen.getByText('Run Reconciliation');
    fireEvent.click(reconcileBtn);
    
    expect(window.alert).toHaveBeenCalledWith(expect.stringContaining('required fields'));
  });

  it('9. Alias-aware mapping initializes Cgst/Sgst correctly and preserves manual overrides', async () => {
    await uploadAndInspect(
      ['Igst', 'Cgst', 'Sgst'],
      ['IGST Amount', 'CGST Amount', 'SGST Amount']
    );
    
    const selects = screen.getAllByRole('combobox') as HTMLSelectElement[];
    const allValues = selects.map(s => s.value);
    
    // Should select the available alias columns
    expect(allValues).toContain('Igst');
    expect(allValues).toContain('Cgst');
    expect(allValues).toContain('Sgst');
    
    // Change one selection
    const cgstSelect = selects.find(s => s.value === 'Cgst');
    expect(cgstSelect).toBeDefined();
    
    fireEvent.change(cgstSelect!, { target: { value: '' } });
    expect(cgstSelect!.value).toBe('');
    
    // Change tab and ensure it doesn't reset
    fireEvent.click(screen.getByText(/GSTR-2B \(/));
    expect(cgstSelect!.value).toBe('');
  });

  it('10. BUSY mappings use backend semantics and preserve physical headers', async () => {
    const busyHeaders = [
      'Date', 'Vch/Bill No', 'Account', 'TIN/GSTIN No.', 'Type',
      'Total Amount', 'Purc. Amount', 'Taxable Amt.', 'IGST', 'CGST',
      'SGST', 'Other Amount.', 'Notes'
    ];
    await uploadAndInspect(
      busyHeaders,
      [],
      {
        accounts: {
          supplier_name: 'Account',
          gstin: 'TIN/GSTIN No.',
          invoice_number: 'Vch/Bill No',
          invoice_date: 'Date',
          taxable_value: 'Taxable Amt.',
          igst: 'IGST',
          cgst: 'CGST',
          sgst: 'SGST'
        },
        gstr: {}
      }
    );

    const accountValues = (screen.getAllByRole('combobox') as HTMLSelectElement[])
      .slice(0, 9)
      .map(select => select.value);
    expect(accountValues).toEqual([
      'Account', 'TIN/GSTIN No.', 'Vch/Bill No', 'Date',
      'Taxable Amt.', 'Total Amount', 'IGST', 'CGST', 'SGST'
    ]);
  });

  it('11. ambiguous exact aliases remain Not mapped instead of choosing first', async () => {
    await uploadAndInspect(
      [
        'Particulars', 'Account',
        'GSTIN', 'TIN/GSTIN No.',
        'Voucher No.', 'Vch/Bill No',
        'Value', 'Taxable Amt.'
      ],
      []
    );

    const accountValues = (screen.getAllByRole('combobox') as HTMLSelectElement[])
      .slice(0, 8)
      .map(select => select.value);
    expect(accountValues.slice(0, 5)).toEqual(['', '', '', '', '']);
  });

  it('12. authoritative DayBook and official GSTR mappings override fallback aliases', async () => {
    const accountHeaders = [
      'Date', 'Column B (blank header)', 'Column C (blank header)',
      'Supplier Invoice No.', 'GSTIN/UIN', 'Value', 'Gross Total', 'I-GST', 'CGST', 'SGST'
    ];
    const gstrHeaders = [
      'GSTIN of supplier', 'Trade/Legal name', 'Invoice number', 'Invoice Date',
      'Invoice Value(₹)', 'Taxable Value (₹)', 'Integrated Tax(₹)',
      'Central Tax(₹)', 'State/UT Tax(₹)'
    ];
    await uploadAndInspect(accountHeaders, gstrHeaders, {
      accounts: {
        supplier_name: 'Column B (blank header)', gstin: 'GSTIN/UIN',
        invoice_number: 'Supplier Invoice No.', invoice_date: 'Date',
        taxable_value: 'Value', gross_total: 'Gross Total', igst: 'I-GST', cgst: 'CGST', sgst: 'SGST'
      },
      gstr: {
        supplier_name: 'Trade/Legal name', gstin: 'GSTIN of supplier',
        invoice_number: 'Invoice number', invoice_date: 'Invoice Date',
        taxable_value: 'Taxable Value (₹)', total_invoice_value: 'Invoice Value(₹)', igst: 'Integrated Tax(₹)',
        cgst: 'Central Tax(₹)', sgst: 'State/UT Tax(₹)'
      }
    });

    const values = (screen.getAllByRole('combobox') as HTMLSelectElement[]).map(select => select.value);
    expect(values.slice(0, 9)).toEqual([
      'Column B (blank header)', 'GSTIN/UIN', 'Supplier Invoice No.', 'Date',
      'Value', 'Gross Total', 'I-GST', 'CGST', 'SGST'
    ]);
    expect(values.slice(9, 18)).toEqual([
      'Trade/Legal name', 'GSTIN of supplier', 'Invoice number', 'Invoice Date',
      'Taxable Value (₹)', 'Invoice Value(₹)', 'Integrated Tax(₹)', 'Central Tax(₹)', 'State/UT Tax(₹)'
    ]);
    expect(values).toContain('Invoice Value(₹)');
  });
});
