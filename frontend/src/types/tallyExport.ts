export type WorkspaceRole = 'OWNER' | 'ADMIN' | 'ACCOUNTANT';

export interface TallyTarget { workspaceId: string; clientId: string; gstinId: string }
export interface TallyProfile {
  id: string; company_name: string; tally_version: '7.1'; purchase_voucher_type: string;
  debit_note_voucher_type: string | null; igst_ledger_name: string; cgst_ledger_name: string;
  sgst_ledger_name: string; numbering_strategy: 'TALLY_AUTO'; status: 'ACTIVE' | 'INACTIVE'; version: number;
  created_at: string; updated_at: string;
}
export interface PartyMapping { supplier_gstin: string; last_source_supplier_name: string; tally_party_ledger_name: string; status: 'ACTIVE'|'INACTIVE'; version: number }
export interface PurchaseMapping { transaction_kind: 'purchase_invoice'; gst_rate: string; tally_purchase_ledger_name: string; status: 'ACTIVE'|'INACTIVE'; version: number }
export interface TallyCandidate {
  candidate_id: string; supplier_gstin: string; supplier_name: string; document_number: string;
  document_date: string | null; taxable_value: string; igst: string; cgst: string; sgst: string; cess: string;
  invoice_total: string | null; gst_rates: string[]; tax_structure: string; party_mapping_status: 'MAPPED'|'UNMAPPED';
  required_purchase_rates: string[]; previously_exported: boolean; financial_parse_issues: {field:string;reason:string}[];
}
export interface TallyCandidateList {
  session_id: string; reconciliation_run_id: string; run_version: number; items: TallyCandidate[];
  requirements: { unresolved_suppliers: {supplier_gstin:string;supplier_name:string;candidate_count:number}[];
    unresolved_purchase_rates: {transaction_kind:'purchase_invoice';gst_rate:string;candidate_count:number}[] };
  page:number; page_size:number; total:number;
}
export interface TallyIssue { code:string; message:string }
export interface TallyValidationItem { candidate_id:string; eligible:boolean; blockers:TallyIssue[]; warnings:TallyIssue[] }
export interface TallyValidation { exportable_count:number; blocked_count:number; warning_count:number; items:TallyValidationItem[] }
export interface TallyBatchSummary {
  batch_id:string; status:'PENDING'|'GENERATED'|'FAILED'; created_at:string; reconciliation_run_id:string; session_id:string|null;
  candidate_count:number; generated_count:number; failed_count:number; checksum_sha256:string|null; artifact_available:boolean; download_url:string|null;
}
export interface TallyBatchCreate extends TallyBatchSummary { reused:boolean }
export interface TallyBatchItem { candidate_id:string; supplier_gstin:string|null; supplier_name:string|null; document_number:string|null; document_date:string|null; taxable_value:string|null; total_tax:string|null; invoice_total:string|null; gst_rates:string[]; outcome:string; warning_codes:string[] }
export interface TallyBatchDetail { batch:TallyBatchSummary; failure_code:string|null; failure_message:string|null; items:TallyBatchItem[] }
