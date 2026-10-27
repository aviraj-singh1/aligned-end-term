from dataclasses import dataclass
import re
from typing import Dict, List, Optional, Tuple


def normalize_label(value) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    # GST portal workbooks decorate monetary/percentage headings with a unit,
    # for example ``Taxable Value (₹)``.  Remove only a terminal unit marker;
    # meaningful qualifiers such as ``Invoice (Amendment)`` stay intact.
    text = re.sub(r"\s*\((?:₹|rs\.?|inr|%)\)\s*$", "", text)
    text = text.replace("&", " and ")
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"[/_.\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


HEADER_ALIASES: Dict[str, str] = {
    "gstin": "gstin",
    "gstin uin": "gstin",
    "gstin of supplier": "gstin",
    "gstin of the supplier": "gstin",
    "gstin of isd": "gstin",
    "supplier gstin": "gstin",
    "supplier gstin uin": "gstin",
    "invoice number": "invoice_number",
    "invoice no": "invoice_number",
    "invoice date": "invoice_date",
    "invoice dt": "invoice_date",
    "document number": "document_number",
    "document no": "document_number",
    "document date": "document_date",
    "isd document number": "document_number",
    "isd document no": "document_number",
    "isd document date": "document_date",
    "eco document number": "document_number",
    "eco document no": "document_number",
    "eco document date": "document_date",
    "original document number": "original_document_number",
    "original document no": "original_document_number",
    "document number (original details)": "original_document_number",
    "document number (original)": "original_document_number",
    "original document date": "original_document_date",
    "document date (original details)": "original_document_date",
    "document date (original)": "original_document_date",
    "original isd document number": "original_document_number",
    "original isd document no": "original_document_number",
    "original isd document date": "original_document_date",
    "isd document number (original)": "original_document_number",
    "isd document no (original)": "original_document_number",
    "isd document date (original)": "original_document_date",
    "original eco document number": "original_document_number",
    "original eco document no": "original_document_number",
    "original eco document date": "original_document_date",
    "revised document number": "revised_document_number",
    "revised document no": "revised_document_number",
    "revised document date": "revised_document_date",
    "revised isd document number": "revised_document_number",
    "revised isd document no": "revised_document_number",
    "revised isd document date": "revised_document_date",
    "revised eco document number": "revised_document_number",
    "revised eco document no": "revised_document_number",
    "revised eco document date": "revised_document_date",
    "voucher number": "document_number",
    "voucher date": "document_date",
    "original invoice number": "original_invoice_number",
    "original invoice no": "original_invoice_number",
    "invoice number (original details)": "original_invoice_number",
    "invoice number (original)": "original_invoice_number",
    "original invoice date": "original_invoice_date",
    "invoice date (original details)": "original_invoice_date",
    "invoice date (original)": "original_invoice_date",
    "revised invoice number": "revised_invoice_number",
    "revised invoice no": "revised_invoice_number",
    "revised invoice date": "revised_invoice_date",
    "note refund number": "note_number",
    "note number": "note_number",
    "note refund no": "note_number",
    "note refund date": "note_date",
    "note date": "note_date",
    "original note number": "original_note_number",
    "original note no": "original_note_number",
    "note number (original details)": "original_note_number",
    "note number (original)": "original_note_number",
    "original note date": "original_note_date",
    "note date (original details)": "original_note_date",
    "note date (original)": "original_note_date",
    "revised note number": "revised_note_number",
    "revised note no": "revised_note_number",
    "revised note date": "revised_note_date",
    "taxable value": "taxable_value",
    "total taxable value": "taxable_value",
    "taxable amount": "taxable_value",
    "taxable": "taxable_value",
    "invoice value": "invoice_value",
    "invoice amount": "invoice_value",
    "total invoice value": "invoice_value",
    "note refund value": "note_value",
    "note value": "note_value",
    "refund value": "note_value",
    "igst": "igst",
    "igst amount": "igst",
    "integrated tax": "igst",
    "integrated tax paid": "igst",
    "amount of integrated tax": "igst",
    "integrated tax amount": "igst",
    "cgst": "cgst",
    "cgst amount": "cgst",
    "central tax": "cgst",
    "amount of central tax": "cgst",
    "central tax amount": "cgst",
    "sgst": "sgst",
    "sgst amount": "sgst",
    "state tax": "sgst",
    "state ut tax": "sgst",
    "amount of state ut tax": "sgst",
    "state ut tax amount": "sgst",
    "cess": "cess",
    "cess amount": "cess",
    "cess paid": "cess",
    "tax amount": "tax_amount",
    "month": "period",
    "return period": "period",
    "filing period": "period",
    "hsn": "hsn",
    "hsn sac": "hsn",
    "hsn sac of supply": "hsn",
    "bill of entry number": "bill_of_entry_number",
    "bill of entry no": "bill_of_entry_number",
    "boe number": "bill_of_entry_number",
    "boe no": "bill_of_entry_number",
    "bill of entry date": "bill_of_entry_date",
    "boe date": "bill_of_entry_date",
    "gstin of deductor": "gstin",
    "gstin of collector": "gstin",
}


FINANCIAL_FIELDS = (
    "taxable_value", "igst", "cgst", "sgst", "cess",
    "itc_reduction_igst", "itc_reduction_cgst", "itc_reduction_sgst", "itc_reduction_cess",
)


def canonical_header(value) -> str:
    normalized = normalize_label(value)
    if not normalized:
        return ""
    return HEADER_ALIASES.get(normalized, f"raw:{normalized}")


@dataclass(frozen=True)
class CategorySpec:
    key: str
    output_name: str
    aliases: Tuple[str, ...]
    required: Tuple[str, ...]
    identity: Tuple[str, ...] = ()
    minimum_known: int = 2


def header_mapping_from_existing(mapping: Dict[str, int], duplicates: List[str],
                                 spec: Optional[CategorySpec] = None) -> Tuple[Dict[str, int], List[str]]:
    mapping = dict(mapping)
    duplicates = list(duplicates)
    if spec:
        for generic_number, generic_date, original_number, revised_number, revised_date in (
            ("invoice_number", "invoice_date", "original_invoice_number", "revised_invoice_number", "revised_invoice_date"),
            ("note_number", "note_date", "original_note_number", "revised_note_number", "revised_note_date"),
            ("document_number", "document_date", "original_document_number", "revised_document_number", "revised_document_date"),
        ):
            required = set(spec.required)
            if original_number not in mapping or revised_number not in required:
                continue
            if revised_number not in mapping and generic_number in mapping:
                mapping[revised_number] = mapping.pop(generic_number)
            if revised_date not in mapping and generic_date in mapping:
                mapping[revised_date] = mapping.pop(generic_date)
    return mapping, duplicates


def header_mapping(values, spec: Optional[CategorySpec] = None) -> Tuple[Dict[str, int], List[str]]:
    """Return a canonical column map, with narrow amendment-sheet context.

    Some GST portal amendment exports label the current document simply as
    ``Invoice number`` beside ``Original Invoice number``.  Within a spec that
    explicitly requires a revised document, that generic current label is the
    revised value.  No remapping is performed on ordinary transaction sheets.
    """
    mapping: Dict[str, int] = {}
    duplicates: List[str] = []
    for index, value in enumerate(values):
        key = canonical_header(value)
        if not key:
            continue
        if key in mapping:
            duplicates.append(key)
        else:
            mapping[key] = index
    return header_mapping_from_existing(mapping, duplicates, spec)


def _aliases(*values: str) -> Tuple[str, ...]:
    return tuple(normalize_label(value) for value in values)


CATEGORY_SPECS: Tuple[CategorySpec, ...] = (
    CategorySpec("b2b", "B2B", _aliases("B2B"), ("invoice_number", "invoice_date", "taxable_value"), ("gstin", "invoice_number", "invoice_date"), 3),
    CategorySpec("b2ba", "B2BA", _aliases("B2BA"), ("original_invoice_number", "revised_invoice_number", "revised_invoice_date", "taxable_value"), ("gstin", "revised_invoice_number", "revised_invoice_date"), 4),
    CategorySpec("cdnr", "CDNR", _aliases("CDNR", "Note", "Notes", "Credit/Debit Notes"), ("note_number", "note_date", "taxable_value"), ("gstin", "note_number", "note_date"), 3),
    CategorySpec("cdnra", "CDNRA", _aliases("CDNRA"), ("original_note_number", "revised_note_number", "revised_note_date", "taxable_value"), ("gstin", "revised_note_number", "revised_note_date"), 4),
    CategorySpec("b2cl", "B2CL", _aliases("B2CL"), ("invoice_number", "invoice_date", "taxable_value"), ("invoice_number", "invoice_date"), 3),
    CategorySpec("b2cla", "B2CLA", _aliases("B2CLA"), ("original_invoice_number", "revised_invoice_number", "revised_invoice_date", "taxable_value"), ("revised_invoice_number", "revised_invoice_date"), 4),
    CategorySpec("b2cs", "B2CS", _aliases("B2CS"), ("taxable_value",), (), 2),
    CategorySpec("b2csa", "B2CSA", _aliases("B2CSA"), ("period", "taxable_value"), (), 2),
    CategorySpec("exp", "EXP", _aliases("EXP"), ("invoice_number", "invoice_date", "taxable_value"), ("invoice_number", "invoice_date"), 3),
    CategorySpec("expa", "EXPA", _aliases("EXPA"), ("original_invoice_number", "revised_invoice_number", "revised_invoice_date", "taxable_value"), ("revised_invoice_number", "revised_invoice_date"), 4),
    CategorySpec("at", "AT", _aliases("AT"), ("taxable_value",), (), 2),
    CategorySpec("ata", "ATA", _aliases("ATA"), ("revised_document_number", "revised_document_date", "taxable_value"), ("revised_document_number", "revised_document_date"), 3),
    CategorySpec("exemp", "EXEMP", _aliases("EXEMP", "EXEMPT"), (), (), 2),
    CategorySpec("hsn", "HSN", _aliases("HSN", "HSN Summary"), ("hsn",), (), 2),
    CategorySpec("tds", "TDS", _aliases("TDS"), ("gstin",), (), 2),
    CategorySpec("tcs", "TCS", _aliases("TCS"), ("gstin",), (), 2),
    CategorySpec("isd", "ISD", _aliases("ISD"), ("document_number", "document_date"), ("gstin", "document_number", "document_date"), 3),
    CategorySpec("impg", "IMPG", _aliases("IMPG"), ("bill_of_entry_number", "bill_of_entry_date", "taxable_value"), ("bill_of_entry_number", "bill_of_entry_date"), 3),
    CategorySpec("impg_sez", "IMPG SEZ", _aliases("IMPG SEZ", "IMPGSEZ"), ("bill_of_entry_number", "bill_of_entry_date", "taxable_value"), ("gstin", "bill_of_entry_number", "bill_of_entry_date"), 3),

    # Form GSTR-2B uses a separate worksheet for every document role and ITC
    # disposition.  Keep unique category keys so reversal/rejected rows can
    # never be mixed into the ordinary transaction sheets.
    CategorySpec("gstr2b_b2b_cdnr", "B2B-CDNR", _aliases("B2B-CDNR"), ("note_number", "note_date", "taxable_value"), ("gstin", "note_number", "note_date"), 3),
    CategorySpec("gstr2b_b2b_cdnra", "B2B-CDNRA", _aliases("B2B-CDNRA"), ("original_note_number", "revised_note_number", "revised_note_date", "taxable_value"), ("gstin", "revised_note_number", "revised_note_date"), 4),
    CategorySpec("gstr2b_eco", "ECO", _aliases("ECO"), ("document_number", "document_date", "taxable_value"), ("gstin", "document_number", "document_date"), 3),
    CategorySpec("gstr2b_ecoa", "ECOA", _aliases("ECOA"), ("original_document_number", "revised_document_number", "revised_document_date", "taxable_value"), ("gstin", "revised_document_number", "revised_document_date"), 4),
    CategorySpec("gstr2b_isda", "ISDA", _aliases("ISDA"), ("original_document_number", "revised_document_number", "revised_document_date"), ("gstin", "revised_document_number", "revised_document_date"), 4),
    CategorySpec("gstr2b_impga", "IMPGA", _aliases("IMPGA"), ("bill_of_entry_number", "bill_of_entry_date", "taxable_value"), ("bill_of_entry_number", "bill_of_entry_date"), 3),
    CategorySpec("gstr2b_impgseza", "IMPGSEZA", _aliases("IMPGSEZA", "IMPG SEZA"), ("bill_of_entry_number", "bill_of_entry_date", "taxable_value"), ("gstin", "bill_of_entry_number", "bill_of_entry_date"), 3),
    CategorySpec("gstr2b_b2b_reversal", "B2B (ITC Reversal)", _aliases("B2B (ITC Reversal)"), ("invoice_number", "invoice_date", "taxable_value"), ("gstin", "invoice_number", "invoice_date"), 3),
    CategorySpec("gstr2b_b2ba_reversal", "B2BA (ITC Reversal)", _aliases("B2BA (ITC Reversal)"), ("original_invoice_number", "revised_invoice_number", "revised_invoice_date", "taxable_value"), ("gstin", "revised_invoice_number", "revised_invoice_date"), 4),
    CategorySpec("gstr2b_b2b_dnr", "B2B-DNR", _aliases("B2B-DNR"), ("note_number", "note_date", "taxable_value"), ("gstin", "note_number", "note_date"), 3),
    CategorySpec("gstr2b_b2b_dnra", "B2B-DNRA", _aliases("B2B-DNRA"), ("original_note_number", "revised_note_number", "revised_note_date", "taxable_value"), ("gstin", "revised_note_number", "revised_note_date"), 4),
    CategorySpec("gstr2b_b2b_rejected", "B2B(Rejected)", _aliases("B2B(Rejected)", "B2B (Rejected)"), ("invoice_number", "invoice_date", "taxable_value"), ("gstin", "invoice_number", "invoice_date"), 3),
    CategorySpec("gstr2b_b2ba_rejected", "B2BA(Rejected)", _aliases("B2BA(Rejected)", "B2BA (Rejected)"), ("original_invoice_number", "revised_invoice_number", "revised_invoice_date", "taxable_value"), ("gstin", "revised_invoice_number", "revised_invoice_date"), 4),
    CategorySpec("gstr2b_b2b_cdnr_rejected", "B2B-CDNR(Rejected)", _aliases("B2B-CDNR(Rejected)", "B2B-CDNR (Rejected)"), ("note_number", "note_date", "taxable_value"), ("gstin", "note_number", "note_date"), 3),
    CategorySpec("gstr2b_b2b_cdnra_rejected", "B2B-CDNRA(Rejected)", _aliases("B2B-CDNRA(Rejected)", "B2B-CDNRA (Rejected)"), ("original_note_number", "revised_note_number", "revised_note_date", "taxable_value"), ("gstin", "revised_note_number", "revised_note_date"), 4),
    CategorySpec("gstr2b_eco_rejected", "ECO(Rejected)", _aliases("ECO(Rejected)", "ECO (Rejected)"), ("document_number", "document_date", "taxable_value"), ("gstin", "document_number", "document_date"), 3),
    CategorySpec("gstr2b_ecoa_rejected", "ECOA(Rejected)", _aliases("ECOA(Rejected)", "ECOA (Rejected)"), ("original_document_number", "revised_document_number", "revised_document_date", "taxable_value"), ("gstin", "revised_document_number", "revised_document_date"), 4),
    CategorySpec("gstr2b_isd_rejected", "ISD(Rejected)", _aliases("ISD(Rejected)", "ISD (Rejected)"), ("document_number", "document_date"), ("gstin", "document_number", "document_date"), 3),
    CategorySpec("gstr2b_isda_rejected", "ISDA(Rejected)", _aliases("ISDA(Rejected)", "ISDA (Rejected)"), ("original_document_number", "revised_document_number", "revised_document_date"), ("gstin", "revised_document_number", "revised_document_date"), 4),
)

CATEGORY_BY_KEY = {spec.key: spec for spec in CATEGORY_SPECS}
CATEGORIES_BY_ALIAS: Dict[str, Tuple[CategorySpec, ...]] = {}
for _spec in CATEGORY_SPECS:
    for _alias in _spec.aliases:
        CATEGORIES_BY_ALIAS[_alias] = CATEGORIES_BY_ALIAS.get(_alias, ()) + (_spec,)


def category_candidates(sheet_name: str) -> Tuple[CategorySpec, ...]:
    return CATEGORIES_BY_ALIAS.get(normalize_label(sheet_name), ())
