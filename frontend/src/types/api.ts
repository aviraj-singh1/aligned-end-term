export interface HealthResponse {
  status: string;
  service: string;
}

export interface PreviewRecord {
  supplier: string | null;
  gstin: string | null;
  invoice_number: string;
  date: string | null;
  taxable_value: string;
  igst: string;
  cgst: string;
  sgst: string;
}

export interface FileInspection {
  original_filename: string;
  detected_sheets: string[];
  selected_sheet: string | null;
  row_count: number;
}

export interface ReconciliationResponse {
  session_id: string;
  counts: {
    all?: number;
    accounts_invoices: number;
    gstr_invoices: number;
    perfectly_matched: number;
    minor_rounding_difference: number;
    major_difference: number;
    taxable_value_missing?: number;
    missing_in_gstr_2b: number;
    not_in_tally: number;
    needs_review_ambiguous: number;
    potential_duplicate: number;
    notes_matched: number;
    total_notes: number;
    date_mismatches: number;
    invoice_mismatches: number;
    cancelled_vouchers?: number;
    cancelled_accounts_vouchers?: number;
    cancelled_gstr_vouchers?: number;
    active_accounts_invoices?: number;
    active_gstr_invoices?: number;
  };
  results: ReconciliationResult[];
  not_in_tally: NotInTallyResult[];
  notes: ReconciliationResult[];
  cancelled_vouchers?: CancelledVoucher[];
  summary_text: string;
  has_ambiguous_matches: boolean;
  purchase_summary?: Record<string, number>;
  notes_summary?: Record<string, number>;
}

export type GstrMergeMode = 'QUARTERLY' | 'MONTHLY';
export type GstrMergeStatus = 'UPLOADING' | 'QUEUED' | 'PROCESSING' | 'COMPLETED' | 'FAILED' | 'EXPIRED' | 'DELETED';
export type GstrUploadState = 'PENDING' | 'UPLOADED' | 'DELETED';

export interface GstrMergeFileInput {
  period_code: string;
  filename: string;
  size_bytes: number;
}

export interface GstrMergeUploadTarget {
  period_code: string;
  provider: 's3' | 'local';
  method: 'PUT';
  url: string;
  headers: Record<string, string>;
  expires_at: string;
}

export interface GstrMergeInputFile {
  period_code: string;
  filename: string;
  expected_size_bytes: number;
  actual_size_bytes: number | null;
  upload_state: GstrUploadState;
}

export interface GstrMetadataVerificationState {
  gstin?: string;
  financial_year?: string;
  period?: string;
}

export interface GstrMergeResultSummary {
  mode?: string;
  files_processed?: number;
  categories_merged?: string[];
  rows_merged_per_category?: Record<string, number>;
  total_rows_merged?: number;
  duplicate_warning_count?: number;
  metadata_verification_states?: GstrMetadataVerificationState;
  integrity_passed?: boolean;
}

export interface GstrMergeRun {
  id: string;
  workspace_id: string;
  client_id: string;
  client_gstin_id: string;
  mode: GstrMergeMode;
  financial_year: string;
  status: GstrMergeStatus;
  version: number;
  attempt_count: number;
  files: GstrMergeInputFile[];
  declared_total_size_bytes: number;
  uploaded_total_size_bytes: number | null;
  result_summary: GstrMergeResultSummary | null;
  artifact_available: boolean;
  error_code: string | null;
  safe_error_message: string | null;
  queued_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  failed_at: string | null;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface GstrMergeCreatePayload {
  client_gstin_id: string;
  financial_year: string;
  mode: GstrMergeMode;
  files: GstrMergeFileInput[];
}

export interface GstrMergeCreateResponse {
  run: GstrMergeRun;
  upload_targets: GstrMergeUploadTarget[];
}

export interface ResultsPageResponse {
  items: Array<ReconciliationResult | NotInTallyResult | CancelledVoucher>;
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  category: string;
}

export interface ReconciliationResult {
  reconciliation_type?: 'PURCHASE' | 'NOTE';
  gstr_source_category?: string | null;
  gstr_source_sheet?: string | null;
  taxable_difference?: string | null;
  cancelled_gstr_counterpart?: boolean;
  gst_note_direction?: string | null;
  accounts_note_direction?: string | null;
  result_id: string;
  status: string;
  accounts_invoice: string;
  gst_invoice: string | null;
  supplier: string;
  gstin: string;
  accounts_supplier?: string | null;
  accounts_gstin?: string | null;
  taxable_value: string | null;
  accounts_taxable: string | null;
  gst_taxable: string | null;
  accounts_gross_total?: string | null;
  gst_invoice_value?: string | null;
  gross_total_difference?: string | null;
  igst_diff: string | null;
  cgst_diff: string | null;
  sgst_diff: string | null;
  accounts_igst: string | null;
  accounts_cgst: string | null;
  accounts_sgst: string | null;
  gst_igst: string | null;
  gst_cgst: string | null;
  gst_sgst: string | null;
  accounts_date: string | null;
  gst_date: string | null;
  date_mismatch: boolean;
  date_difference_days: number | null;
  invoice_mismatch?: boolean;
  match_method: string;
  remarks: string;
  evidence?: string;
  candidates: CandidateModel[] | null;
}

export interface NotInTallyResult {
  gst_date?: string | null;
  gst_igst?: string | null;
  gst_cgst?: string | null;
  gst_sgst?: string | null;
  gst_invoice: string;
  supplier: string;
  gstin: string;
  taxable_value: string;
}

export interface CandidateModel {
  candidate_id: string;
  gst_invoice: string;
  supplier: string;
  gstin: string;
  invoice_date?: string | null;
  taxable_value: string | null;
  cgst: string | null;
  sgst: string | null;
  igst: string | null;
}

export interface CancelledVoucher {
  id: string;
  source_side: 'ACCOUNTS' | 'GSTR';
  source_system: string;
  supplier: string;
  gstin: string;
  invoice_number: string;
  invoice_date: string | null;
  taxable_value: string | null;
  igst: string;
  cgst: string;
  sgst: string;
  source_sheet: string;
  source_row: number;
  status: string;
  cancellation_reason: string;
  cancellation_evidence: string;
}

export interface SessionStatusResponse {
  session_id: string;
  created_at: string;
  last_accessed: string;
  has_inspection: boolean;
  has_reconciliation: boolean;
}

export interface InspectionResponse {
  session_id: string;
  files: {
    accounts: FileInspection;
    gstr: FileInspection;
    debit_notes?: FileInspection;
  };
  mappings: {
    accounts: Record<string, string>;
    gstr: Record<string, string>;
  };
  available_columns: {
    accounts: string[];
    gstr: string[];
    debit_notes?: string[];
    credit_notes?: string[];
  };
  preview: {
    accounts: PreviewRecord[];
    gstr: PreviewRecord[];
    debit_notes?: PreviewRecord[];
    credit_notes?: PreviewRecord[];
  };
  warnings: string[];
}
