"""
Authentication (v2).

Acquires an app-only Microsoft Graph token. Prefers certificate-based auth and
falls back to a client secret only for local development. The app should be
granted Sites.Selected on the single Contracts site rather than the
tenant-wide Sites.ReadWrite.All.

Tokens are cached and reused until shortly before expiry.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import msal

from config import settings

_SCOPE = ["https://graph.microsoft.com/.default"]
_AUTHORITY = f"https://login.microsoftonline.com/{settings.tenant_id}"

_token: Optional[str] = None
_expires_at: float = 0.0


def _build_app() -> msal.ConfidentialClientApplication:
    settings.validate_auth()
    if settings.cert_path and settings.cert_thumbprint:
        credential = {
            "private_key": Path(settings.cert_path).read_text(),
            "thumbprint": settings.cert_thumbprint,
        }
    else:
        credential = settings.client_secret  # local-dev fallback
    return msal.ConfidentialClientApplication(
        client_id=settings.client_id,
        client_credential=credential,
        authority=_AUTHORITY,
    )


def get_access_token() -> str:
    """Return a cached token, refreshing ~2 minutes before expiry."""
    global _token, _expires_at
    if _token and time.time() < _expires_at - 120:
        return _token

    result = _build_app().acquire_token_for_client(scopes=_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(
            f"Auth failed: {result.get('error')}: {result.get('error_description')}"
        )
    _token = result["access_token"]
    _expires_at = time.time() + int(result.get("expires_in", 3600))
    return _token
