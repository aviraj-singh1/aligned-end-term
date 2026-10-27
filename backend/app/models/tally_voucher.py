"""Resolved accounting DTOs consumed by the Tally XML renderer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Tuple
from uuid import UUID


@dataclass(frozen=True)
class TallyPurchaseAllocation:
    gst_rate: Decimal
    ledger_name: str
    taxable_value: Decimal


@dataclass(frozen=True)
class TallyTaxLedgerEntry:
    tax_head: str
    ledger_name: str
    amount: Decimal


@dataclass(frozen=True)
class CommonTallyPurchaseVoucher:
    candidate_id: str
    remote_id: UUID
    fingerprint: str
    client_gstin: str
    company_name: str
    tally_version: str
    voucher_type: str
    voucher_date: date
    supplier_invoice_number: str
    supplier_invoice_date: date
    party_ledger_name: str
    supplier_gstin: str
    place_of_supply: str
    purchase_allocations: Tuple[TallyPurchaseAllocation, ...]
    tax_entries: Tuple[TallyTaxLedgerEntry, ...]
    total_party_amount: Decimal
    narration: str
    reconciliation_run_id: UUID


@dataclass(frozen=True)
class TallyExportManifest:
    batch_id: UUID
    tally_version: str
    profile_id: UUID
    profile_version: int
    company_name: str
    reconciliation_run_id: UUID
    voucher_count: int
    candidate_ids: Tuple[str, ...]
    remote_ids: Tuple[UUID, ...]
    fingerprints: Tuple[str, ...]
    content_sha256: str


@dataclass(frozen=True)
class TallyXmlArtifact:
    xml_bytes: bytes
    content_sha256: str
    manifest: TallyExportManifest


@dataclass(frozen=True)
class PersistedTallyXmlArtifact:
    batch_id: UUID
    artifact_object_ref: str | None
    artifact: TallyXmlArtifact | None
    status: str
    reused: bool = False
    content_sha256: str | None = None
