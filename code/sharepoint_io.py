"""
sharepoint_io.py (v2)

SharePoint/Graph I/O for the CLM pipeline. Changes from v1:

  * Folders are keyed by ContractID only (stable, machine-safe paths) — the
    human-readable title lives in the Contract Index List, not the path.
  * Every request goes through _request(), which retries on HTTP 429/5xx with
    exponential backoff honoring the Retry-After header.
  * ensure_folder() creates parents recursively and is idempotent (no dead code).
  * Adds Contract Index List helpers — the query layer that backs analytics.

All Graph calls use the drive identified by settings.drive_id, on the single
site granted via Sites.Selected.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import requests

from auth import get_access_token
from config import settings

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_DRIVE = f"{GRAPH_BASE}/sites/{settings.site_id}/drives/{settings.drive_id}"
_SITE = f"{GRAPH_BASE}/sites/{settings.site_id}"

CONTRACTS_ROOT = "Contracts"
SUBFOLDERS = [
    "01_Original",
    "02_Amendments",
    "03_SupportingDocs",
    "04_AI_Outputs",
    "04_AI_Outputs/Metadata",
    "04_AI_Outputs/ClauseExtraction",
    "04_AI_Outputs/Summaries",
    "04_AI_Outputs/Visualizations",
]

_MAX_RETRIES = 5


# --------------------------------------------------------------------------- #
# Core request wrapper with retry/backoff
# --------------------------------------------------------------------------- #
def _auth_header(content_type: Optional[str] = "application/json") -> Dict[str, str]:
    h = {"Authorization": f"Bearer {get_access_token()}"}
    if content_type:
        h["Content-Type"] = content_type
    return h

def _request(method: str, url: str, **kwargs: Any) -> requests.Response:
    """Single point for all Graph calls. Retries 429 and 5xx with backoff."""
    for attempt in range(_MAX_RETRIES):
        resp = requests.request(method, url, timeout=60, **kwargs)
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            if attempt == _MAX_RETRIES - 1:
                resp.raise_for_status()
            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else min(2 ** attempt, 30)
            time.sleep(delay)
            continue
        return resp
    return resp  # unreachable, keeps type checkers happy


# --------------------------------------------------------------------------- #
# Pathing — keyed by ContractID only
# --------------------------------------------------------------------------- #
def contract_root(contract_id: str) -> str:
    """Stable folder path. No title, no sanitization needed."""
    return f"{CONTRACTS_ROOT}/{contract_id}"


# --------------------------------------------------------------------------- #
# Folder helpers
# --------------------------------------------------------------------------- #
def _item_url(path: str) -> str:
    return f"{_DRIVE}/root:/{path}"

def folder_exists(path: str) -> bool:
    return _request("GET", _item_url(path), headers=_auth_header()).status_code == 200

def ensure_folder(path: str) -> Dict[str, Any]:
    """Idempotently create `path`, creating any missing parents first."""
    r = _request("GET", _item_url(path), headers=_auth_header())
    if r.status_code == 200:
        return r.json()

    parent, _, name = path.rpartition("/")
    if parent:
        ensure_folder(parent)  # recurse so parents exist before the child
        children_url = f"{_item_url(parent)}:/children"
    else:
        children_url = f"{_DRIVE}/root/children"

    payload = {
        "name": name,
        "folder": {},
        "@microsoft.graph.conflictBehavior": "fail",  # don't clobber existing data
    }
    create = _request("POST", children_url, headers=_auth_header(), json=payload)
    if create.status_code == 409:  # created concurrently; re-fetch
        return _request("GET", _item_url(path), headers=_auth_header()).json()
    create.raise_for_status()
    return create.json()

def ensure_contract_folder_structure(contract_id: str) -> str:
    """Create /Contracts/{ContractID}/<standard subfolders>. Returns the root path."""
    root = contract_root(contract_id)
    ensure_folder(root)
    for sf in SUBFOLDERS:
        ensure_folder(f"{root}/{sf}")
    return root


# --------------------------------------------------------------------------- #
# File I/O
# --------------------------------------------------------------------------- #
def download_file(sp_path: str) -> bytes:
    r = _request("GET", f"{_item_url(sp_path)}:/content", headers=_auth_header(None))
    r.raise_for_status()
    return r.content

def upload_file(sp_path: str, content: bytes, content_type: str = "application/octet-stream") -> Dict[str, Any]:
    # Simple upload (<4 MB). For large files, switch to an upload session.
    headers = _auth_header(None)
    headers["Content-Type"] = content_type
    r = _request("PUT", f"{_item_url(sp_path)}:/content", headers=headers, data=content)
    r.raise_for_status()
    return r.json()

def upload_json(sp_path: str, data: Dict[str, Any]) -> Dict[str, Any]:
    body = json.dumps(data, indent=2, default=str).encode("utf-8")
    return upload_file(sp_path, body, "application/json")

def upload_markdown(sp_path: str, text: str) -> Dict[str, Any]:
    return upload_file(sp_path, text.encode("utf-8"), "text/markdown")


# --------------------------------------------------------------------------- #
# Contract Index List — the query layer
# --------------------------------------------------------------------------- #
def _list_items_url(list_name: str) -> str:
    return f"{_SITE}/lists/{list_name}/items"

def upsert_list_item(list_name: str, key_field: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """
    Insert or update a row keyed by `key_field` (e.g. 'ContractID').
    Keeps the List in sync with the JSON detail files.
    """
    key_value = fields[key_field]
    query = (
        f"{_list_items_url(list_name)}"
        f"?$expand=fields&$filter=fields/{key_field} eq '{key_value}'"
    )
    headers = _auth_header()
    headers["Prefer"] = "HonorNonIndexedQueriesWarningMayFailRandomly"
    existing = _request("GET", query, headers=headers)
    existing.raise_for_status()
    matches = existing.json().get("value", [])

    if matches:
        item_id = matches[0]["id"]
        url = f"{_list_items_url(list_name)}/{item_id}/fields"
        r = _request("PATCH", url, headers=_auth_header(), json=fields)
    else:
        r = _request("POST", _list_items_url(list_name), headers=_auth_header(),
                     json={"fields": fields})
    r.raise_for_status()
    return r.json()
