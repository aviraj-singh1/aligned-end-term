from pydantic import BaseModel, Field, field_validator
from typing import Dict, List, Optional, Any

class HealthResponse(BaseModel):
    status: str
    service: str

class FileInspectionPreview(BaseModel):
    supplier: Optional[str]
    gstin: Optional[str]
    invoice_number: Optional[str]
    date: Optional[str]
    taxable_value: str
    igst: str
    cgst: str
    sgst: str

class FileInspectionInfo(BaseModel):
    original_filename: str
    detected_sheets: List[str]
    selected_sheet: Optional[str]
    row_count: int

class InspectionResponse(BaseModel):
    session_id: str
    files: Dict[str, FileInspectionInfo]
    mappings: Dict[str, Dict[str, str]]
    available_columns: Dict[str, List[str]]
    preview: Dict[str, List[FileInspectionPreview]]
    warnings: List[str]

class ReconciliationRequest(BaseModel):
    mapping_overrides: Optional[Dict[str, Dict[str, Optional[str]]]] = None

    @field_validator("mapping_overrides")
    @classmethod
    def canonical_mapping_keys(cls, mappings):
        if mappings is None:
            return None
        labels = {
            "Supplier": "supplier_name", "GSTIN": "gstin",
            "Invoice Number": "invoice_number", "Invoice Date": "invoice_date",
            "Taxable Value": "taxable_value", "Gross Total": "gross_total",
            "Invoice Value": "total_invoice_value", "IGST": "igst",
            "CGST": "cgst", "SGST": "sgst",
        }
        normalized = {}
        for source, columns in mappings.items():
            canonical = {key: value for key, value in columns.items() if key not in labels}
            # App retains detected semantic keys alongside edited display labels.
            # Explicit user choices win, including null/empty Not mapped values.
            for label, semantic in labels.items():
                if label in columns:
                    canonical[semantic] = columns[label]
            normalized[source] = canonical
        return normalized

class CountsModel(BaseModel):
    all: int = 0
    accounts_invoices: int
    gstr_invoices: int
    perfectly_matched: int
    minor_rounding_difference: int
    major_difference: int
    taxable_value_missing: int = 0
    missing_in_gstr_2b: int
    not_in_tally: int
    needs_review_ambiguous: int
    potential_duplicate: int
    notes_matched: int
    total_notes: int
    date_mismatches: int
    invoice_mismatches: int
    cancelled_vouchers: int = 0
    cancelled_accounts_vouchers: int = 0
    cancelled_gstr_vouchers: int = 0
    active_accounts_invoices: int = 0
    active_gstr_invoices: int = 0

class CancelledVoucherModel(BaseModel):
    id: str
    source_side: str
    source_system: str
    supplier: str
    gstin: str
    invoice_number: str
    invoice_date: Optional[str] = None
    taxable_value: str
    igst: str
    cgst: str
    sgst: str
    source_sheet: str
    source_row: int
    status: str
    cancellation_reason: str
    cancellation_evidence: str

class CandidateModel(BaseModel):
    gst_note_direction: Optional[str] = None
    candidate_id: str
    gst_invoice: str
    supplier: str
    gstin: str
    invoice_date: Optional[str] = None
    taxable_value: Optional[str] = None
    cgst: Optional[str] = None
    sgst: Optional[str] = None
    igst: Optional[str] = None

class ResultModel(BaseModel):
    reconciliation_type: str = "PURCHASE"
    taxable_difference: Optional[str] = None
    source_system: Optional[str] = None
    source_sheet: Optional[str] = None
    source_row: Optional[int] = None
    accounts_source_system: Optional[str] = None
    accounts_source_sheet: Optional[str] = None
    accounts_source_row: Optional[int] = None
    gstr_source_category: Optional[str] = None
    gstr_source_sheet: Optional[str] = None
    gstr_source_rows: List[int] = []
    cancelled_gstr_counterpart: bool = False
    cancelled_gstr_source_sheet: str = ""
    cancelled_gstr_source_rows: List[int] = []
    gst_note_direction: Optional[str] = None
    accounts_note_direction: Optional[str] = None
    result_id: str
    status: str
    accounts_invoice: str
    gst_invoice: Optional[str] = None
    supplier: str
    gstin: str
    accounts_supplier: Optional[str] = None
    accounts_gstin: Optional[str] = None
    taxable_value: Optional[str] = None
    accounts_taxable: Optional[str] = None
    gst_taxable: Optional[str] = None
    accounts_gross_total: Optional[str] = None
    gst_invoice_value: Optional[str] = None
    gross_total_difference: Optional[str] = None
    igst_diff: Optional[str] = None
    cgst_diff: Optional[str] = None
    sgst_diff: Optional[str] = None
    accounts_igst: Optional[str] = None
    accounts_cgst: Optional[str] = None
    accounts_sgst: Optional[str] = None
    gst_igst: Optional[str] = None
    gst_cgst: Optional[str] = None
    gst_sgst: Optional[str] = None
    accounts_date: Optional[str] = None
    gst_date: Optional[str] = None
    date_mismatch: bool = False
    date_difference_days: Optional[int] = None
    invoice_mismatch: bool = False
    match_method: str
    remarks: str
    evidence: str = ""
    candidates: Optional[List[CandidateModel]] = None

class ReconciliationResponse(BaseModel):
    session_id: str
    counts: CountsModel
    results: List[ResultModel] = []
    not_in_tally: List[Any] = []
    notes: List[ResultModel] = []
    cancelled_vouchers: List[CancelledVoucherModel] = []
    summary_text: str
    has_ambiguous_matches: bool
    purchase_summary: Dict[str, int] = Field(default_factory=dict)
    notes_summary: Dict[str, int] = Field(default_factory=dict)

class ResultsPageResponse(BaseModel):
    items: List[Dict[str, Any]]
    total: int
    page: int
    page_size: int
    total_pages: int
    category: str

class ManualMatchRequest(BaseModel):
    result_id: str
    candidate_id: str

class SessionStatusResponse(BaseModel):
    session_id: str
    created_at: str
    last_accessed: str
    has_inspection: bool
    has_reconciliation: bool
