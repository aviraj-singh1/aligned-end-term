"""Verify MSG91 OTP Widget tokens and bind proof to a Supabase identity."""

from __future__ import annotations

import base64
from datetime import timedelta
import hmac
import json
import os
import re
import secrets

from fastapi import HTTPException
import requests

from backend.app.auth.security import signature, utcnow

MSG91_VERIFY_ACCESS_TOKEN_URL = "https://control.msg91.com/api/v5/widget/verifyAccessToken"
RECEIPT_VERSION = 1
RECEIPT_TTL = timedelta(minutes=10)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _normalize_indian_phone(value: object) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    national = digits[2:] if len(digits) == 12 and digits.startswith("91") else digits
    return f"+91{national}" if re.fullmatch(r"[6-9]\d{9}", national) else None


def _find_phone(value: object) -> str | None:
    if isinstance(value, dict):
        for key in ("mobile", "mobile_number", "phone", "phone_number", "identifier"):
            if key in value and (phone := _normalize_indian_phone(value[key])):
                return phone
        for nested in value.values():
            if (phone := _find_phone(nested)):
                return phone
    elif isinstance(value, list):
        for nested in value:
            if (phone := _find_phone(nested)):
                return phone
    return None


def _successful(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    values = {str(payload.get(key) or "").strip().lower() for key in ("type", "status", "message")}
    return payload.get("success") is True or bool(values & {
        "success", "verified", "verification successful", "token verified",
    })


def verify_msg91_access_token(access_token: str) -> str:
    authkey = os.getenv("MSG91_AUTH_KEY", "").strip()
    if not authkey:
        raise HTTPException(503, "Phone verification is not configured")
    try:
        response = requests.post(
            MSG91_VERIFY_ACCESS_TOKEN_URL,
            json={"authkey": authkey, "access-token": access_token},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=8,
        )
    except requests.RequestException as exc:
        raise HTTPException(503, "Phone verification service is temporarily unavailable") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(502, "Phone verification service returned an invalid response") from exc
    if response.status_code != 200 or not _successful(payload):
        raise HTTPException(401, "Phone verification token is invalid or expired")
    phone = _find_phone(payload)
    if not phone:
        raise HTTPException(502, "Phone verification response did not contain a valid Indian mobile number")
    return phone


def issue_phone_receipt(subject: str, phone: str) -> str:
    now = utcnow()
    payload = _b64encode(json.dumps({
        "v": RECEIPT_VERSION,
        "sub": subject,
        "phone": phone,
        "iat": int(now.timestamp()),
        "exp": int((now + RECEIPT_TTL).timestamp()),
        "nonce": secrets.token_urlsafe(12),
    }, separators=(",", ":"), sort_keys=True).encode())
    return f"{payload}.{signature('phone-receipt:' + payload)}"


def verify_phone_receipt(receipt: str, expected_subject: str) -> str:
    try:
        payload, supplied_mac = receipt.split(".", 1)
        if not hmac.compare_digest(supplied_mac, signature("phone-receipt:" + payload)):
            raise ValueError
        data = json.loads(_b64decode(payload))
        now = int(utcnow().timestamp())
        phone = _normalize_indian_phone(data.get("phone"))
        if not (
            data.get("v") == RECEIPT_VERSION
            and data.get("sub") == expected_subject
            and isinstance(data.get("iat"), int)
            and isinstance(data.get("exp"), int)
            and data["iat"] <= now <= data["exp"]
            and data["exp"] - data["iat"] <= int(RECEIPT_TTL.total_seconds())
            and phone is not None
        ):
            raise ValueError
        return phone
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(401, "Mobile verification expired. Verify your number again.") from exc
