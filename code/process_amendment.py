"""
process_amendment.py (v2) — Phase-1 amendment orchestrator.

Clones the contract pipeline pattern and adds the three things an amendment
needs: amendment-type/value extraction, a clause diff against the approved
original, and a roll-up of the amendment's value change into the contract's
computed CurrentValue.

Flow:
  1. Orchestrator MINTS the AmendmentID (never the AI) for an existing ContractID.
  2. Loads the approved original contract metadata + clause set from SharePoint.
  3. Downloads the amendment doc and extracts text (OCR fallback included).
  4. AI extracts amendment attributes and detects modified clauses (attributes only).
  5. Validates into an Amendment model; on failure records a FAILED run, writes nothing.
  6. Rolls the ValueChange into the contract (computed CurrentValue), writes a
     MetadataDiff, updated contract JSON, amendment JSON, modified-clauses JSON.
  7. Upserts the Amendment Index row and updates the Contract Index CurrentValue.
     Everything lands at ReviewStatus = Pending.

Usage:
    python process_amendment.py --contract-id <guid> \
        --amendment-number 2 --filename amendment_02.pdf
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import ValidationError

import ai_provider as ai
import sharepoint_io as sp
from ai_provider import MODEL_VERSION
from text_extract import extract_text
from schema import (
    AIExtractionRun,
    Amendment,
    Contract,
    ExtractionType,
    ReviewStatus,
)

log = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.85
CONTRACT_INDEX_LIST = "Contract Index"
AMENDMENT_INDEX_LIST = "Amendment Index"

# Computed/derived keys that the Contract model does not accept as input
# (extra="forbid"), so they must be stripped before re-validating a saved dump.
_DERIVED_CONTRACT_KEYS = ("CurrentValue",)


# --------------------------------------------------------------------------- #
# Loading the approved original
# --------------------------------------------------------------------------- #
def _load_contract(contract_id: str) -> Contract:
    """Reconstruct the Contract model from its saved metadata JSON."""
    root = sp.contract_root(contract_id)
    raw = sp.download_file(f"{root}/04_AI_Outputs/Metadata/contract_{contract_id}.json")
    data = json.loads(raw)
    for k in _DERIVED_CONTRACT_KEYS:
        data.pop(k, None)
    return Contract.model_validate(data)


def _load_clause_set(contract_id: str) -> Dict[str, Any]:
    root = sp.contract_root(contract_id)
    try:
        raw = sp.download_file(f"{root}/04_AI_Outputs/ClauseExtraction/clauses_{contract_id}.json")
        return json.loads(raw)
    except Exception:  # noqa: BLE001 — original clause set is optional for the diff
        return {"clauses": []}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def process_amendment(contract_id: str, amendment_number: int, filename: str) -> str:
    amendment_id = uuid.uuid4()
    run_id = uuid.uuid4()
    root = sp.contract_root(contract_id)
    amd_folder = f"{root}/02_Amendments/Amendment_{amendment_number:02d}"
    sp.ensure_folder(amd_folder)
    out = f"{root}/04_AI_Outputs"

    extraction_types: list[ExtractionType] = []
    confidences: Dict[str, float] = {}

    # 1. Load the approved original + extract amendment text.
    try:
        contract = _load_contract(contract_id)
        original_clauses = _load_clause_set(contract_id)

        raw = sp.download_file(f"{amd_folder}/{filename}")
        text = extract_text(filename, raw)
        if not text or not text.strip():
            raise RuntimeError(
                f"Empty text after extraction for {filename}. "
                "Check that the file is not a blank scan and that OCR is installed."
            )
    except Exception as exc:
        _write_run(out, run_id, contract_id, extraction_types, confidences,
                   succeeded=False, error=f"load_or_text_extract: {exc}")
        log.exception("Load/text extraction failed for amendment on %s", contract_id)
        raise

    # 2. AI extraction, attributes only.
    try:
        meta = ai.extract_amendment_metadata(text)
        diff = ai.detect_modified_clauses(text, original_clauses)
        confidences = {"AmendmentMetadata": meta["confidence"], "ClauseDiff": diff["confidence"]}
        extraction_types = [ExtractionType.METADATA, ExtractionType.CLAUSES]
    except Exception as exc:
        _write_run(out, run_id, contract_id, extraction_types, confidences,
                   succeeded=False, error=f"ai_extract: {exc}")
        log.exception("AI extraction failed for amendment on %s", contract_id)
        raise

    # 3. Validate + roll the value up. On failure, nothing downstream is written.
    try:
        amendment = Amendment(
            AmendmentID=amendment_id,
            ContractID=contract_id,
            AmendmentNumber=amendment_number,
            AmendmentDocumentURL=f"{amd_folder}/{filename}",
            ParentFolderURL=amd_folder,
            **meta["attributes"],
        )

        # Value roll-up: append the amendment so Contract.current_value recomputes.
        before_value = contract.current_value
        contract.amendments.append(amendment)
        contract.metadata_version += 1
        # Apply any contract-level field changes the amendment dictates.
        contract_changes = meta.get("contract_changes", {})
        metadata_diff = _build_metadata_diff(contract, contract_changes, before_value)
        for field_alias, new_val in contract_changes.items():
            _apply_contract_change(contract, field_alias, new_val)
    except ValidationError as exc:
        _write_run(out, run_id, contract_id, extraction_types, confidences,
                   succeeded=False, error=f"schema_validation: {exc.errors()}")
        log.error("Validation failed for amendment on %s: %s", contract_id, exc)
        raise

    # Persist amendment + diff + updated contract
    sp.upload_json(f"{amd_folder}/metadata_amendment_{amendment_id}.json",
                   amendment.model_dump(by_alias=True))
    sp.upload_json(f"{out}/Metadata/metadata_diff_{amendment_id}.json", metadata_diff)
    sp.upload_json(f"{out}/ClauseExtraction/modified_clauses_{amendment_id}.json",
                   {"AmendmentID": str(amendment_id), "modified": diff["modified"]})
    sp.upload_json(f"{out}/Metadata/contract_{contract_id}.json",
                   contract.model_dump(by_alias=True))

    # Update the query layer
    sp.upsert_list_item(AMENDMENT_INDEX_LIST, "AmendmentID", {
        "AmendmentID": str(amendment.amendment_id),
        "ContractID": str(amendment.contract_id),
        "AmendmentNumber": amendment.amendment_number,
        "AmendmentType": amendment.amendment_type,
        "EffectiveDate": amendment.effective_date.isoformat() if amendment.effective_date else None,
        "ValueChange": amendment.value_change,
        "ReviewStatus": amendment.review_status,
    })
    sp.upsert_list_item(CONTRACT_INDEX_LIST, "ContractID", contract.index_row())

    _write_run(out, run_id, contract_id, extraction_types, confidences, succeeded=True)
    priority_review = any(c < CONFIDENCE_THRESHOLD for c in confidences.values())
    log.info(
        "Processed amendment %s on %s: value %s -> %s, %d clause(s) modified "
        "(review=%s, priority_review=%s)",
        amendment_number, contract_id,
        metadata_diff["CurrentValue"]["old"], metadata_diff["CurrentValue"]["new"],
        len(diff["modified"]), ReviewStatus.PENDING.value, priority_review,
    )
    return str(amendment_id)


# --------------------------------------------------------------------------- #
# Diff + change helpers
# --------------------------------------------------------------------------- #
# Map Contract field aliases -> model attribute names for safe assignment.
_ALIAS_TO_ATTR = {
    "ExpirationDate": "expiration_date",
    "Status": "status",
    "EffectiveDate": "effective_date",
    "AutoRenewal": "auto_renewal",
}

def _apply_contract_change(contract: Contract, field_alias: str, new_val: Any) -> None:
    attr = _ALIAS_TO_ATTR.get(field_alias)
    if attr:
        setattr(contract, attr, new_val)

def _build_metadata_diff(contract: Contract, contract_changes: Dict[str, Any],
                         before_value: float) -> Dict[str, Any]:
    """old/new snapshot of every field the amendment touches, including value roll-up."""
    diff: Dict[str, Any] = {
        "CurrentValue": {"old": before_value, "new": contract.current_value},
    }
    for alias, new_val in contract_changes.items():
        attr = _ALIAS_TO_ATTR.get(alias)
        old_val = getattr(contract, attr) if attr else None
        old_str = old_val.isoformat() if hasattr(old_val, "isoformat") else old_val
        diff[alias] = {"old": old_str, "new": new_val}
    return diff

def _write_run(out_base: str, run_id: uuid.UUID, contract_id: str, types: list,
               confidences: dict, succeeded: bool, error: Optional[str] = None) -> None:
    "Write the AIExtractionRun audit record for an amendment run."
    run = AIExtractionRun(
        RunID=run_id,
        ContractID=contract_id,
        Timestamp=datetime.now(timezone.utc),
        ModelVersion=MODEL_VERSION,
        ExtractionTypes=types,            # was: ExtractionType=types
        ConfidenceScores=confidences,
        Succeeded=succeeded,
    )
    payload = run.model_dump(by_alias=True)
    if error:
        payload["Error"] = error
    sp.upload_json(f"{out_base}/Metadata/ai_run_{run_id}.json", payload)

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Process one amendment through the CLM pipeline.")
    ap.add_argument("--contract-id", required=True, help="Existing ContractID.")
    ap.add_argument("--amendment-number", required=True, type=int)
    ap.add_argument("--filename", required=True,
                    help="File name inside 02_Amendments/Amendment_NN/.")
    args = ap.parse_args()
    try:
        process_amendment(args.contract_id, args.amendment_number, args.filename)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
