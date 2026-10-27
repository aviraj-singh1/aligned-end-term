from backend.app.services.gstr_merge.gst_schema import (
    CATEGORY_BY_KEY,
    FINANCIAL_FIELDS,
    canonical_header,
    normalize_label,
)

FIELD_LABELS = {
    "taxable_value": "Taxable Value",
    "invoice_value": "Invoice Value",
    "igst": "IGST",
    "cgst": "CGST",
    "sgst": "SGST",
    "cess": "Cess",
    "tax_amount": "Tax Amount",
    "note_value": "Note/Refund Value",
    "invoice_date": "Invoice Date",
    "invoice_number": "Invoice Number",
    "note_number": "Note/Refund Number",
}


def normalize_header(value):
    return normalize_label(value)


def canonicalize_header(value):
    return canonical_header(value) or None


def is_transaction_like_header(value):
    field = canonicalize_header(value)
    if not field:
        return False
    return field in {
        "gstin",
        "invoice_value",
        "invoice_number",
        "invoice_date",
        "taxable_value",
        "igst",
        "cgst",
        "sgst",
        "cess",
        "tax_amount",
        "note_value",
        "note_number",
    }


def required_fields_for_sheet(sheet_name):
    spec = CATEGORY_BY_KEY.get(normalize_header(sheet_name))
    return spec.required if spec else ()


def annualize_metadata_value(label, value):
    """Apply the sole approved annual metadata transformation."""
    if normalize_header(label) == "months":
        return "All Months"
    return value
