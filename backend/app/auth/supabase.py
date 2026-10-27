"""Verify Supabase Auth access tokens without coupling auth to the app database."""

import os
from urllib.parse import urlsplit

import requests
from fastapi import HTTPException


def _configuration() -> tuple[str, str]:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or not key:
        raise HTTPException(503, "Supabase authentication is not configured")
    return url, key


def _admin_configuration() -> tuple[str, str]:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SECRET_KEY", "").strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or not key:
        raise HTTPException(503, "Supabase account administration is not configured")
    return url, key


def verify_supabase_token(raw_token: str) -> dict:
    """Ask Supabase Auth for the token's current user.

    This call happens only when exchanging a Supabase session for ALIGNED's
    server-side session cookie. Business requests do not depend on Supabase.
    """
    url, key = _configuration()
    try:
        response = requests.get(
            f"{url}/auth/v1/user",
            headers={"apikey": key, "Authorization": f"Bearer {raw_token}"},
            timeout=8,
        )
    except requests.RequestException as exc:
        raise HTTPException(503, "Supabase authentication is temporarily unavailable") from exc
    if response.status_code in {401, 403}:
        raise HTTPException(401, "Supabase sign-in has expired. Sign in again.")
    if response.status_code != 200:
        raise HTTPException(502, "Supabase could not verify this sign-in")
    user = response.json()
    if not user.get("id"):
        raise HTTPException(401, "Supabase returned an invalid user")
    return user


def identity_issuer() -> str:
    url, _ = _configuration()
    return f"{url}/auth/v1"


def _admin_request(method: str, subject: str, payload: dict | None = None) -> None:
    url, key = _admin_configuration()
    try:
        response = requests.request(
            method,
            f"{url}/auth/v1/admin/users/{subject}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            json=payload,
            timeout=10,
        )
    except requests.RequestException as exc:
        raise HTTPException(503, "Supabase account administration is temporarily unavailable") from exc
    if response.status_code in {200, 204}:
        return
    if method == "DELETE" and response.status_code == 404:
        return
    if response.status_code in {401, 403}:
        raise HTTPException(503, "Supabase secret key is missing or invalid")
    raise HTTPException(502, "Supabase rejected the account administration request")


def delete_supabase_user(subject: str) -> None:
    _admin_request("DELETE", subject)
