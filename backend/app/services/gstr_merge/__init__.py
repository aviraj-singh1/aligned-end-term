"""Strict, lossless GSTR workbook consolidation core.

This package is deliberately independent from Aligned's tolerant
reconciliation parser and is not wired into the running application in Phase 1.
"""

from backend.app.services.gstr_merge.package_safety import (
    PackageSafetyError,
    validate_xlsx_package,
)
from backend.app.services.gstr_merge.processor import (
    MergeContext,
    ProcessingValidationError,
    process_workbooks,
)

__all__ = [
    "MergeContext",
    "PackageSafetyError",
    "ProcessingValidationError",
    "process_workbooks",
    "validate_xlsx_package",
]
