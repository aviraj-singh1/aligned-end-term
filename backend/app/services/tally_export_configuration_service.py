"""Tenant-scoped Tally profile and exact ledger-mapping services.

This phase persists configuration only. It deliberately performs no XML
generation, fuzzy ledger confirmation, or export-candidate validation.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.app.auth.security import utcnow
from backend.app.db.models import (
    AuditEvent,
    ClientGSTIN,
    TallyExportProfile,
    TallyPartyLedgerMapping,
    TallyPurchaseLedgerMapping,
)
from backend.app.db.session import get_database_engine
from backend.app.models.tally_export import TallyExportTransactionType
from backend.app.services.workspace_service import client_scope, missing, scope


GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$")
SUPPORTED_TALLY_VERSIONS = {"7.1"}
NUMBERING_STRATEGIES = {"TALLY_AUTO"}
STATUSES = {"ACTIVE", "INACTIVE"}


def _authorize_target(db: Session, user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
                      *, admin: bool = False):
    _, member = scope(db, user, workspace_id, admin=admin)
    client = client_scope(db, member, client_id)
    if client.status != "ACTIVE":
        missing()
    gstin = db.exec(select(ClientGSTIN).where(
        ClientGSTIN.workspace_id == workspace_id,
        ClientGSTIN.client_id == client_id,
        ClientGSTIN.id == client_gstin_id,
        ClientGSTIN.status == "ACTIVE",
    )).first()
    if not gstin:
        missing()
    return client, gstin


def _name(value: str | None, label: str, *, optional: bool = False,
          max_length: int = 255) -> str | None:
    if value is None and optional:
        return None
    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        raise HTTPException(422, f"{label} is required")
    if len(normalized) > max_length:
        raise HTTPException(422, f"{label} exceeds {max_length} characters")
    return normalized


def _tally_version(value: str) -> str:
    normalized = str(value).strip()
    if normalized not in SUPPORTED_TALLY_VERSIONS:
        raise HTTPException(422, "Tally Export V1 supports TallyPrime 7.1 only")
    return normalized


def _numbering_strategy(value: str) -> str:
    normalized = str(value).strip().upper()
    if normalized not in NUMBERING_STRATEGIES:
        raise HTTPException(422, "Voucher numbering strategy is not supported")
    return normalized


def _status(value: str) -> str:
    normalized = str(value).strip().upper()
    if normalized not in STATUSES:
        raise HTTPException(422, "Status must be ACTIVE or INACTIVE")
    return normalized


def _supplier_gstin(value: str) -> str:
    normalized = str(value).strip().upper()
    if not GSTIN_RE.fullmatch(normalized):
        raise HTTPException(422, "Supplier GSTIN is invalid")
    return normalized


def _transaction_kind(value: str) -> str:
    normalized = str(value).strip().lower()
    try:
        return TallyExportTransactionType(normalized).value
    except ValueError:
        raise HTTPException(422, "Transaction kind is not supported") from None


def _gst_rate(value: Decimal | str | int) -> Decimal:
    try:
        normalized = Decimal(str(value)).quantize(Decimal("0.001"))
    except (InvalidOperation, ValueError):
        raise HTTPException(422, "GST rate must be a valid number") from None
    if not normalized.is_finite() or normalized < 0 or normalized > 100:
        raise HTTPException(422, "GST rate must be between 0 and 100")
    return normalized


def _audit(db: Session, user, row, action: str, metadata: dict[str, Any] | None = None):
    db.add(AuditEvent(
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        actor_user_id=user.id,
        action=action,
        entity_type=row.__tablename__,
        entity_id=row.id,
        event_metadata=metadata,
    ))


def _profile_data(row: TallyExportProfile) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _party_data(row: TallyPartyLedgerMapping) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _purchase_data(row: TallyPurchaseLedgerMapping) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _profile(db: Session, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
             *, lock: bool = False) -> TallyExportProfile:
    query = select(TallyExportProfile).where(
        TallyExportProfile.workspace_id == workspace_id,
        TallyExportProfile.client_id == client_id,
        TallyExportProfile.client_gstin_id == client_gstin_id,
    )
    if lock:
        query = query.with_for_update()
    row = db.exec(query).first()
    if not row:
        missing()
    return row


def create_profile(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, *,
                   company_name: str, tally_version: str, purchase_voucher_type: str,
                   debit_note_voucher_type: str | None = None, igst_ledger_name: str,
                   cgst_ledger_name: str, sgst_ledger_name: str,
                   numbering_strategy: str = "TALLY_AUTO") -> dict[str, Any]:
    with Session(get_database_engine(), expire_on_commit=False) as db:
        _authorize_target(db, user, workspace_id, client_id, client_gstin_id, admin=True)
        row = TallyExportProfile(
            workspace_id=workspace_id,
            client_id=client_id,
            client_gstin_id=client_gstin_id,
            company_name=_name(company_name, "Tally company name"),
            tally_version=_tally_version(tally_version),
            purchase_voucher_type=_name(purchase_voucher_type, "Purchase voucher type", max_length=200),
            debit_note_voucher_type=_name(
                debit_note_voucher_type, "Debit Note voucher type", optional=True, max_length=200
            ),
            igst_ledger_name=_name(igst_ledger_name, "IGST ledger name"),
            cgst_ledger_name=_name(cgst_ledger_name, "CGST ledger name"),
            sgst_ledger_name=_name(sgst_ledger_name, "SGST ledger name"),
            numbering_strategy=_numbering_strategy(numbering_strategy),
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            raise HTTPException(409, "A Tally export profile already exists for this client GSTIN") from None
        _audit(db, user, row, "TALLY_EXPORT_PROFILE_CREATED", {"version": 1, "status": row.status})
        db.commit()
        return _profile_data(row)


def get_profile(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID) -> dict[str, Any]:
    with Session(get_database_engine()) as db:
        _authorize_target(db, user, workspace_id, client_id, client_gstin_id)
        return _profile_data(_profile(db, workspace_id, client_id, client_gstin_id))


def update_profile(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, *,
                   expected_version: int, company_name: str, tally_version: str,
                   purchase_voucher_type: str, debit_note_voucher_type: str | None,
                   igst_ledger_name: str, cgst_ledger_name: str, sgst_ledger_name: str,
                   numbering_strategy: str, status: str) -> dict[str, Any]:
    values = {
        "company_name": _name(company_name, "Tally company name"),
        "tally_version": _tally_version(tally_version),
        "purchase_voucher_type": _name(purchase_voucher_type, "Purchase voucher type", max_length=200),
        "debit_note_voucher_type": _name(
            debit_note_voucher_type, "Debit Note voucher type", optional=True, max_length=200
        ),
        "igst_ledger_name": _name(igst_ledger_name, "IGST ledger name"),
        "cgst_ledger_name": _name(cgst_ledger_name, "CGST ledger name"),
        "sgst_ledger_name": _name(sgst_ledger_name, "SGST ledger name"),
        "numbering_strategy": _numbering_strategy(numbering_strategy),
        "status": _status(status),
    }
    with Session(get_database_engine(), expire_on_commit=False) as db:
        _authorize_target(db, user, workspace_id, client_id, client_gstin_id, admin=True)
        row = _profile(db, workspace_id, client_id, client_gstin_id, lock=True)
        if row.version != expected_version:
            raise HTTPException(409, "The Tally export profile changed; refresh and retry")
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_by = user.id
        row.version += 1
        row.updated_at = utcnow()
        db.add(row)
        _audit(db, user, row, "TALLY_EXPORT_PROFILE_UPDATED", {"version": row.version, "status": row.status})
        db.commit()
        return _profile_data(row)


def _party_mapping(db: Session, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
                   supplier_gstin: str, *, lock: bool = False) -> TallyPartyLedgerMapping:
    query = select(TallyPartyLedgerMapping).where(
        TallyPartyLedgerMapping.workspace_id == workspace_id,
        TallyPartyLedgerMapping.client_id == client_id,
        TallyPartyLedgerMapping.client_gstin_id == client_gstin_id,
        TallyPartyLedgerMapping.supplier_gstin == supplier_gstin,
    )
    if lock:
        query = query.with_for_update()
    row = db.exec(query).first()
    if not row:
        missing()
    return row


def create_party_mapping(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, *,
                         supplier_gstin: str, source_supplier_name: str,
                         tally_party_ledger_name: str) -> dict[str, Any]:
    exact_gstin = _supplier_gstin(supplier_gstin)
    with Session(get_database_engine(), expire_on_commit=False) as db:
        _authorize_target(db, user, workspace_id, client_id, client_gstin_id)
        row = TallyPartyLedgerMapping(
            workspace_id=workspace_id,
            client_id=client_id,
            client_gstin_id=client_gstin_id,
            supplier_gstin=exact_gstin,
            last_source_supplier_name=_name(source_supplier_name, "Source supplier name"),
            tally_party_ledger_name=_name(tally_party_ledger_name, "Tally party ledger name"),
            confirmed_by=user.id,
            confirmed_at=utcnow(),
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            raise HTTPException(409, "A party ledger mapping already exists for this supplier GSTIN") from None
        _audit(db, user, row, "TALLY_PARTY_MAPPING_CREATED", {"version": 1, "status": row.status})
        db.commit()
        return _party_data(row)


def update_party_mapping(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, *,
                         supplier_gstin: str, expected_version: int,
                         source_supplier_name: str,
                         tally_party_ledger_name: str | None = None) -> dict[str, Any]:
    exact_gstin = _supplier_gstin(supplier_gstin)
    with Session(get_database_engine(), expire_on_commit=False) as db:
        _authorize_target(db, user, workspace_id, client_id, client_gstin_id)
        row = _party_mapping(db, workspace_id, client_id, client_gstin_id, exact_gstin, lock=True)
        if row.version != expected_version:
            raise HTTPException(409, "The party ledger mapping changed; refresh and retry")
        row.last_source_supplier_name = _name(source_supplier_name, "Source supplier name")
        if tally_party_ledger_name is not None:
            row.tally_party_ledger_name = _name(tally_party_ledger_name, "Tally party ledger name")
        row.status = "ACTIVE"
        row.confirmed_by = user.id
        row.confirmed_at = utcnow()
        row.version += 1
        row.updated_at = utcnow()
        db.add(row)
        _audit(db, user, row, "TALLY_PARTY_MAPPING_UPDATED", {"version": row.version, "status": row.status})
        db.commit()
        return _party_data(row)


def deactivate_party_mapping(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, *,
                             supplier_gstin: str, expected_version: int) -> dict[str, Any]:
    exact_gstin = _supplier_gstin(supplier_gstin)
    with Session(get_database_engine(), expire_on_commit=False) as db:
        _authorize_target(db, user, workspace_id, client_id, client_gstin_id)
        row = _party_mapping(db, workspace_id, client_id, client_gstin_id, exact_gstin, lock=True)
        if row.version != expected_version:
            raise HTTPException(409, "The party ledger mapping changed; refresh and retry")
        row.status = "INACTIVE"
        row.confirmed_by = user.id
        row.confirmed_at = utcnow()
        row.version += 1
        row.updated_at = utcnow()
        db.add(row)
        _audit(db, user, row, "TALLY_PARTY_MAPPING_DEACTIVATED", {"version": row.version, "status": row.status})
        db.commit()
        return _party_data(row)


def resolve_party_mapping(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
                          supplier_gstin: str) -> dict[str, Any] | None:
    exact_gstin = _supplier_gstin(supplier_gstin)
    with Session(get_database_engine()) as db:
        _authorize_target(db, user, workspace_id, client_id, client_gstin_id)
        row = db.exec(select(TallyPartyLedgerMapping).where(
            TallyPartyLedgerMapping.workspace_id == workspace_id,
            TallyPartyLedgerMapping.client_id == client_id,
            TallyPartyLedgerMapping.client_gstin_id == client_gstin_id,
            TallyPartyLedgerMapping.supplier_gstin == exact_gstin,
            TallyPartyLedgerMapping.status == "ACTIVE",
        )).first()
        return _party_data(row) if row else None


def list_party_mappings(user, workspace_id: UUID, client_id: UUID,
                        client_gstin_id: UUID) -> list[dict[str, Any]]:
    with Session(get_database_engine()) as db:
        _authorize_target(db, user, workspace_id, client_id, client_gstin_id)
        rows = db.exec(select(TallyPartyLedgerMapping).where(
            TallyPartyLedgerMapping.workspace_id == workspace_id,
            TallyPartyLedgerMapping.client_id == client_id,
            TallyPartyLedgerMapping.client_gstin_id == client_gstin_id,
        ).order_by(TallyPartyLedgerMapping.supplier_gstin)).all()
        return [_party_data(row) for row in rows]


def _purchase_mapping(db: Session, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID,
                      transaction_kind: str, gst_rate: Decimal, *, lock: bool = False):
    query = select(TallyPurchaseLedgerMapping).where(
        TallyPurchaseLedgerMapping.workspace_id == workspace_id,
        TallyPurchaseLedgerMapping.client_id == client_id,
        TallyPurchaseLedgerMapping.client_gstin_id == client_gstin_id,
        TallyPurchaseLedgerMapping.transaction_kind == transaction_kind,
        TallyPurchaseLedgerMapping.gst_rate == gst_rate,
    )
    if lock:
        query = query.with_for_update()
    row = db.exec(query).first()
    if not row:
        missing()
    return row


def create_purchase_ledger_mapping(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, *,
                                   transaction_kind: str, gst_rate: Decimal | str | int,
                                   tally_purchase_ledger_name: str) -> dict[str, Any]:
    kind = _transaction_kind(transaction_kind)
    rate = _gst_rate(gst_rate)
    with Session(get_database_engine(), expire_on_commit=False) as db:
        _authorize_target(db, user, workspace_id, client_id, client_gstin_id, admin=True)
        profile = _profile(db, workspace_id, client_id, client_gstin_id)
        row = TallyPurchaseLedgerMapping(
            workspace_id=workspace_id,
            client_id=client_id,
            client_gstin_id=client_gstin_id,
            profile_id=profile.id,
            transaction_kind=kind,
            gst_rate=rate,
            tally_purchase_ledger_name=_name(tally_purchase_ledger_name, "Tally purchase ledger name"),
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            raise HTTPException(409, "A purchase ledger mapping already exists for this transaction kind and GST rate") from None
        _audit(db, user, row, "TALLY_PURCHASE_MAPPING_CREATED", {
            "version": 1, "status": row.status, "transaction_kind": kind, "gst_rate": str(rate),
        })
        db.commit()
        return _purchase_data(row)


def update_purchase_ledger_mapping(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, *,
                                   transaction_kind: str, gst_rate: Decimal | str | int,
                                   expected_version: int, tally_purchase_ledger_name: str,
                                   status: str = "ACTIVE") -> dict[str, Any]:
    kind = _transaction_kind(transaction_kind)
    rate = _gst_rate(gst_rate)
    with Session(get_database_engine(), expire_on_commit=False) as db:
        _authorize_target(db, user, workspace_id, client_id, client_gstin_id, admin=True)
        row = _purchase_mapping(db, workspace_id, client_id, client_gstin_id, kind, rate, lock=True)
        if row.version != expected_version:
            raise HTTPException(409, "The purchase ledger mapping changed; refresh and retry")
        row.tally_purchase_ledger_name = _name(tally_purchase_ledger_name, "Tally purchase ledger name")
        row.status = _status(status)
        row.updated_by = user.id
        row.version += 1
        row.updated_at = utcnow()
        db.add(row)
        _audit(db, user, row, "TALLY_PURCHASE_MAPPING_UPDATED", {
            "version": row.version, "status": row.status,
            "transaction_kind": kind, "gst_rate": str(rate),
        })
        db.commit()
        return _purchase_data(row)


def resolve_purchase_ledger_mapping(user, workspace_id: UUID, client_id: UUID, client_gstin_id: UUID, *,
                                    transaction_kind: str, gst_rate: Decimal | str | int) -> dict[str, Any] | None:
    kind = _transaction_kind(transaction_kind)
    rate = _gst_rate(gst_rate)
    with Session(get_database_engine()) as db:
        _authorize_target(db, user, workspace_id, client_id, client_gstin_id)
        row = db.exec(select(TallyPurchaseLedgerMapping).where(
            TallyPurchaseLedgerMapping.workspace_id == workspace_id,
            TallyPurchaseLedgerMapping.client_id == client_id,
            TallyPurchaseLedgerMapping.client_gstin_id == client_gstin_id,
            TallyPurchaseLedgerMapping.transaction_kind == kind,
            TallyPurchaseLedgerMapping.gst_rate == rate,
            TallyPurchaseLedgerMapping.status == "ACTIVE",
        )).first()
        return _purchase_data(row) if row else None


def list_purchase_ledger_mappings(user, workspace_id: UUID, client_id: UUID,
                                  client_gstin_id: UUID) -> list[dict[str, Any]]:
    with Session(get_database_engine()) as db:
        _authorize_target(db, user, workspace_id, client_id, client_gstin_id)
        rows = db.exec(select(TallyPurchaseLedgerMapping).where(
            TallyPurchaseLedgerMapping.workspace_id == workspace_id,
            TallyPurchaseLedgerMapping.client_id == client_id,
            TallyPurchaseLedgerMapping.client_gstin_id == client_gstin_id,
        ).order_by(TallyPurchaseLedgerMapping.transaction_kind, TallyPurchaseLedgerMapping.gst_rate)).all()
        return [_purchase_data(row) for row in rows]
