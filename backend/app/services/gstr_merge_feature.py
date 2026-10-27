"""Authoritative backend feature switch for GSTR Consolidation."""

from __future__ import annotations

import os

from fastapi import HTTPException, status

GSTR_CONSOLIDATION_ENABLED_ENV = "GSTR_CONSOLIDATION_ENABLED"
GSTR_CONSOLIDATION_UNAVAILABLE_DETAIL = (
    "GSTR Consolidation is temporarily unavailable."
)


def gstr_consolidation_enabled() -> bool:
    """Read the strict, disabled-by-default backend feature switch."""

    raw_value = os.getenv(GSTR_CONSOLIDATION_ENABLED_ENV, "false").strip().lower()
    if raw_value not in {"true", "false"}:
        raise RuntimeError(
            f"{GSTR_CONSOLIDATION_ENABLED_ENV} must be either 'true' or 'false'."
        )
    return raw_value == "true"


def require_gstr_consolidation_enabled() -> None:
    """Fail closed before creating or queueing new GSTR work."""

    if not gstr_consolidation_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=GSTR_CONSOLIDATION_UNAVAILABLE_DETAIL,
        )
