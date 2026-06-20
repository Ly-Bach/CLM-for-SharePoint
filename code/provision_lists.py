"""
provision_lists.py (v2)

Creates the three SharePoint Lists that form the CLM query layer:

    * Contract Index      — one row per contract
    * Amendment Index     — one row per amendment
    * Clause Map Index    — one row per clause<->subject-matter mapping

Columns are typed (text / number / choice / dateTime / boolean) so Power BI and
Power Automate see real types, and the key + ReviewStatus columns are what the
orchestrator upserts against. Idempotent: existing lists/columns are skipped.

Run once after the Azure AD app has Sites.Selected on the Contracts site:

    python provision_lists.py
"""
from __future__ import annotations

from typing import Any, Dict, List

import requests

from auth import get_access_token
from config import settings

GRAPH = "https://graph.microsoft.com/v1.0"
SITE = f"{GRAPH}/sites/{settings.site_id}"

REVIEW_CHOICES = ["Pending", "Reviewed", "Approved"]


# --------------------------------------------------------------------------- #
# Column-definition helpers (Graph columnDefinition shapes)
# --------------------------------------------------------------------------- #
def text(name: str) -> Dict[str, Any]:
    return {"name": name, "text": {}}

def number(name: str, decimals: int = 2) -> Dict[str, Any]:
    return {"name": name, "number": {"decimalPlaces": str(decimals)}}

def boolean(name: str) -> Dict[str, Any]:
    return {"name": name, "boolean": {}}

def datetime_col(name: str) -> Dict[str, Any]:
    return {"name": name, "dateTime": {"format": "dateOnly"}}

def choice(name: str, choices: List[str]) -> Dict[str, Any]:
    return {"name": name, "choice": {"choices": choices, "displayAs": "dropDownMenu"}}


# --------------------------------------------------------------------------- #
# List definitions
# --------------------------------------------------------------------------- #
LISTS: Dict[str, List[Dict[str, Any]]] = {
    "Contract Index": [
        text("ContractID"),          # key
        text("Title"),
        text("Counterparty"),
        choice("ContractType", ["Grant", "Vendor", "Subrecipient", "MOU", "Lease", "Other"]),
        choice("Status", ["Draft", "Active", "Expired", "Terminated"]),
        datetime_col("EffectiveDate"),
        datetime_col("ExpirationDate"),
        number("CurrentValue"),
        choice("FundingSource", ["Federal", "State", "Local", "Private"]),
        choice("ReviewStatus", REVIEW_CHOICES),
    ],
    "Amendment Index": [
        text("AmendmentID"),         # key
        text("ContractID"),          # foreign key
        number("AmendmentNumber", decimals=0),
        choice("AmendmentType",
               ["Extension", "Budget Revision", "Scope Change", "Legal Correction", "Other"]),
        datetime_col("EffectiveDate"),
        number("ValueChange"),
        choice("ReviewStatus", REVIEW_CHOICES),
    ],
    "Clause Map Index": [
        text("MapID"),               # key
        text("ContractID"),          # foreign key
        text("ClauseID"),
        text("TermID"),
        number("RelevanceScore"),
        number("ExtractionConfidence"),
        choice("ReviewStatus", REVIEW_CHOICES),
    ],
}


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {get_access_token()}", "Content-Type": "application/json"}

def _existing_lists() -> Dict[str, str]:
    r = requests.get(f"{SITE}/lists?$select=id,displayName", headers=_headers(), timeout=60)
    r.raise_for_status()
    return {x["displayName"]: x["id"] for x in r.json().get("value", [])}

def _existing_columns(list_id: str) -> set[str]:
    r = requests.get(f"{SITE}/lists/{list_id}/columns?$select=name", headers=_headers(), timeout=60)
    r.raise_for_status()
    return {c["name"] for c in r.json().get("value", [])}

def ensure_list(display_name: str, columns: List[Dict[str, Any]]) -> None:
    existing = _existing_lists()
    if display_name in existing:
        list_id = existing[display_name]
        print(f"= list exists: {display_name}")
    else:
        body = {"displayName": display_name, "list": {"template": "genericList"}}
        r = requests.post(f"{SITE}/lists", headers=_headers(), json=body, timeout=60)
        r.raise_for_status()
        list_id = r.json()["id"]
        print(f"+ created list: {display_name}")

    have = _existing_columns(list_id)
    for col in columns:
        if col["name"] in have:
            continue
        r = requests.post(f"{SITE}/lists/{list_id}/columns",
                          headers=_headers(), json=col, timeout=60)
        r.raise_for_status()
        print(f"    + column: {col['name']}")


def main() -> None:
    for name, cols in LISTS.items():
        ensure_list(name, cols)
    print("Done. Lists are ready for the orchestrator to upsert into.")


if __name__ == "__main__":
    main()
