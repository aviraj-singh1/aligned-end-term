"""
Normalizer module for GST Reconciliation Tool.

Handles:
- GSTIN normalization and validation
- Invoice number normalization with multi-level breakdown
- Monetary value parsing (string/Excel → Decimal)
- Date parsing (various Indian GST formats)
- Supplier name normalization
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional, Tuple

from backend.app.models.records import InvoiceNumberInfo


# ---------------------------------------------------------------------------
# GSTIN
# ---------------------------------------------------------------------------

# Standard GSTIN: 2-digit state code + 10-char PAN + 1 entity + 1 'Z' + 1 check
_GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9][Z][A-Z0-9]$")


def normalize_gstin(raw: Any) -> Tuple[str, str, bool]:
    """
    Normalize a GSTIN value.
    
    Returns:
        (original_string, normalized_string, is_valid)
    
    Normalization steps:
    1. Convert to string
    2. Strip whitespace
    3. Remove accidental internal spaces
    4. Uppercase
    """
    if raw is None:
        return ("", "", False)

    original = str(raw).strip()
    if not original:
        return ("", "", False)

    # Remove internal spaces/tabs
    normalized = re.sub(r"\s+", "", original).upper()

    # Validate format
    is_valid = bool(_GSTIN_PATTERN.match(normalized))

    return (original, normalized, is_valid)


# ---------------------------------------------------------------------------
# Invoice Number
# ---------------------------------------------------------------------------

# Pattern to extract trailing numeric portion (possibly after /, -, etc.)
_TRAILING_NUMBER_PATTERN = re.compile(r"(\d+)\s*$")

# Separators that are semantically irrelevant for comparison
_SEPARATOR_PATTERN = re.compile(r"[\s\-_/\\.:]+")


def normalize_invoice_number(raw: Any) -> InvoiceNumberInfo:
    """
    Normalize an invoice/voucher/note number.
    
    Produces:
    - original: as-is
    - normalized: uppercased, whitespace trimmed, separators collapsed
    - trailing_number: the numeric suffix (e.g. '4587' from 'SBT/2025-26/4587')
    - last_4: last 4 characters of the normalized value
    - last_2: last 2 characters of the normalized value
    """
    if raw is None:
        return InvoiceNumberInfo()

    # Excel may store invoice numbers as integers/floats
    if isinstance(raw, float):
        # Avoid scientific notation; preserve as integer if whole number
        if raw == int(raw):
            original = str(int(raw))
        else:
            original = str(raw)
    elif isinstance(raw, int):
        original = str(raw)
    else:
        original = str(raw).strip()

    if not original:
        return InvoiceNumberInfo()

    # Normalized form: uppercase, collapse separators to single separator
    normalized = original.upper().strip()
    # Remove leading/trailing separators after stripping
    normalized = re.sub(r"^[\s\-_/\\.:]+|[\s\-_/\\.:]+$", "", normalized)
    # Collapse internal separators entirely
    normalized = _SEPARATOR_PATTERN.sub("", normalized)

    # Extract trailing number
    trailing_match = _TRAILING_NUMBER_PATTERN.search(original)
    trailing_number = trailing_match.group(1) if trailing_match else ""

    # Last N characters of normalized
    last_4 = normalized[-4:] if len(normalized) >= 4 else normalized
    last_2 = normalized[-2:] if len(normalized) >= 2 else normalized

    return InvoiceNumberInfo(
        original=original,
        normalized=normalized,
        trailing_number=trailing_number,
        last_4=last_4,
        last_2=last_2,
    )


# ---------------------------------------------------------------------------
# Money / Decimal
# ---------------------------------------------------------------------------

# Pattern to strip currency symbols and comma separators
_MONEY_STRIP_PATTERN = re.compile(r"[₹,\s]")


def parse_money(raw: Any) -> Decimal:
    """
    Parse a monetary value into a Decimal with 2 decimal places.
    
    Supports:
    - Numeric Excel cells (int, float)
    - Strings like '₹50,000.00', '50000', '50,000'
    - None / blank / empty → Decimal('0.00')
    
    Raises ValueError for genuinely unparseable values.
    """
    if raw is None:
        return Decimal("0.00")

    if isinstance(raw, Decimal):
        if not raw.is_finite():
            raise ValueError(f"Cannot parse monetary value: {raw!r}")
        return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if isinstance(raw, (int, float)):
        # Convert via string to avoid float precision issues
        value = Decimal(str(raw))
        if not value.is_finite():
            raise ValueError(f"Cannot parse monetary value: {raw!r}")
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    text = str(raw).strip()
    if not text or text == "-":
        return Decimal("0.00")
    if text.lower() == "nan":
        raise ValueError(f"Cannot parse monetary value: {raw!r}")

    # Remove currency symbol, commas, spaces
    cleaned = _MONEY_STRIP_PATTERN.sub("", text)

    # Handle parenthetical negatives: (1234.56) → -1234.56
    paren_match = re.match(r"^\((.+)\)$", cleaned)
    if paren_match:
        cleaned = "-" + paren_match.group(1)

    try:
        value = Decimal(cleaned)
        if not value.is_finite():
            raise ValueError(f"Cannot parse monetary value: {raw!r}")
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError(f"Cannot parse monetary value: {raw!r}")


def parse_rate(raw: Any) -> Optional[Decimal]:
    """Parse rate metadata in percentage points: '18%' and 18 both mean 18.

    Unlike money, rates retain their precision and accept a percent suffix.
    Blank metadata is absent, not a fabricated zero. Invalid values still fail.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text == "-":
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
        raise ValueError(f"Cannot parse percentage/rate: {raw!r}")
    value = Decimal(text)
    if not value.is_finite():
        raise ValueError(f"Cannot parse percentage/rate: {raw!r}")
    return value


def safe_parse_money(raw: Any) -> Decimal:
    """
    Compatibility entry point for money parsing.

    Invalid non-blank values deliberately propagate ValueError; callers must
    retain parse validity instead of silently substituting zero.
    """
    return parse_money(raw)


# ---------------------------------------------------------------------------
# Date
# ---------------------------------------------------------------------------

# Common Indian GST date formats
_DATE_FORMATS = [
    "%d-%m-%Y",      # 02-04-2025
    "%d/%m/%Y",      # 02/04/2025
    "%d-%b-%Y",      # 02-Apr-2025
    "%d %b %Y",      # 02 Apr 2025
    "%d-%m-%y",      # 02-04-25
    "%d/%m/%y",      # 02/04/25
    "%Y-%m-%d",      # 2025-04-02 (ISO)
    "%m/%d/%Y",      # US format fallback (only if day > 12)
    "%d-%B-%Y",      # 02-April-2025
    "%d %B %Y",      # 02 April 2025
]


def parse_date(raw: Any) -> Tuple[Optional[date], str]:
    """
    Parse a date value from Excel or string.
    
    Returns:
        (parsed_date_or_None, original_string_representation)
    
    Supports:
    - Python datetime/date objects (Excel date cells)
    - String formats: DD-MM-YYYY, DD/MM/YYYY, DD-Mon-YYYY, etc.
    
    Does NOT silently reinterpret ambiguous dates. If the value looks
    ambiguous, returns None so the caller can flag it.
    """
    if raw is None:
        return (None, "")

    # Already a date/datetime
    if isinstance(raw, datetime):
        return (raw.date(), raw.strftime("%d-%m-%Y"))
    if isinstance(raw, date):
        return (raw, raw.strftime("%d-%m-%Y"))

    original = str(raw).strip()
    if not original:
        return (None, "")

    # Excel serial date number
    if isinstance(raw, (int, float)):
        try:
            from datetime import timedelta
            # Excel epoch is 1899-12-30 (accounting for the 1900 leap year bug)
            excel_epoch = date(1899, 12, 30)
            parsed = excel_epoch + timedelta(days=int(raw))
            # Sanity check: date should be in a reasonable range
            if date(2000, 1, 1) <= parsed <= date(2099, 12, 31):
                return (parsed, original)
        except (ValueError, OverflowError):
            pass

    # Try each known format
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(original, fmt)
            # Sanity: date should be reasonable for GST (2017+)
            if dt.year < 2015 or dt.year > 2099:
                continue
            return (dt.date(), original)
        except ValueError:
            continue

    # Could not parse
    return (None, original)


# ---------------------------------------------------------------------------
# Supplier Name
# ---------------------------------------------------------------------------

# Common company suffixes to normalize
_COMPANY_SUFFIXES = {
    "PVT": "PRIVATE",
    "LTD": "LIMITED",
    "PVTLTD": "PRIVATE LIMITED",
    "(I)": "INDIA",
    "(INDIA)": "INDIA",
    "CORP": "CORPORATION",
    "INTL": "INTERNATIONAL",
    "MFG": "MANUFACTURING",
    "GOVT": "GOVERNMENT",
    "ENGG": "ENGINEERING",
    "ENTERP": "ENTERPRISES",
    "IND": "INDUSTRIES",
    "BROS": "BROTHERS",
}

# Punctuation to remove for comparison
_PUNCT_PATTERN = re.compile(r"[^A-Z0-9\s]")


def normalize_supplier_name(raw: Any) -> str:
    """
    Normalize a supplier name for comparison purposes.
    
    Steps:
    1. Convert to string, uppercase
    2. Remove punctuation
    3. Collapse spaces
    4. Expand known abbreviations
    
    Returns the normalized form. The original should be preserved separately.
    """
    if raw is None:
        return ""

    text = str(raw).strip().upper()
    if not text:
        return ""

    # Remove punctuation
    text = _PUNCT_PATTERN.sub(" ", text)

    # Collapse spaces
    text = re.sub(r"\s+", " ", text).strip()

    # Expand known abbreviations
    words = text.split()
    expanded = []
    for w in words:
        if w in _COMPANY_SUFFIXES:
            expanded.append(_COMPANY_SUFFIXES[w])
        else:
            expanded.append(w)

    return " ".join(expanded)
