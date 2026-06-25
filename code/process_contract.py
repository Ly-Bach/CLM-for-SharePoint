"""
process_contract.py (v2) - Phase-1 orchestrator.

End-to-end flow for a single contract:
- The orchestrator MINTS the ContractID (the AI never does) and creates the
  /Contracts/{ContractID}/ folder structure.
- Downloads the original document and extracts text (PDF / DOCX / image,
  with a local Tesseract OCR fallback for scanned files).
- Runs attribute-only AI extraction (dummy now, Azure OpenAI later).
- Parses every result into a validated Pydantic model. On validation failure
  the run is recorded as FAILED and nothing is written downstream.
- Writes validated JSON to 04_AI_Outputs and upserts the Contract Index and
  Clause Map Index rows with ReviewStatus = Pending.
- Records an AIExtractionRun for audit/traceability.
- Low-confidence extractions are flagged via the PriorityReview column so
  reviewers can triage them first.

Typical Phase-1 use (the contract folder + ID are created first, then a file
is dropped into 01_Original/):

    python process_contract.py --filename contract.pdf
    python process_contract.py --filename contract.pdf --contract-id <guid>
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

import ai_provider as ai
import sharepoint_io as sp
from ai_provider import MODEL_VERSION
from text_extract import extract_text
from schema import (
    AIExtractionRun,
    ClauseTermMap,
    Contract,
    ExtractionType,
    ReviewStatus,
)

log = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.85  # below this -> flagged for priority human review
# MODEL_VERSION is imported from ai_provider (above) so the dummy/real backend
# toggle drives it; it is intentionally not hardcoded here.

CONTRACT_INDEX_LIST = "Contract Index"
CLAUSE_MAP_LIST = "Clause Map Index"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def process_contract(filename: str, contract_id: Optional[str] = None) -> str:
    """
    Run one contract end-to-end. Returns the ContractID on success.
    Raises on unrecoverable failures after recording a FAILED AIExtractionRun.
    """
    # 1. Identity is owned here, not by the AI.
    contract_id = contract_id or str(uuid.uuid4())
    run_id = uuid.uuid4()
    root = sp.ensure_contract_folder_structure(contract_id)
    out = f"{root}/04_AI_Outputs"

    types: List[ExtractionType] = []
    confidences: Dict[str, float] = {}

    # 2. Pull source + extract text.
    try:
        data = sp.download_file(f"{root}/01_Original/{filename}")
        text = extract_text(filename, data)
        if not text or not text.strip():
            raise RuntimeError(
                f"Empty text after extraction for {filename}. "
                "Check that the file is not a blank scan and that OCR is installed."
            )
    except Exception as exc:
        _write_run(out, run_id, contract_id, types, confidences,
                   succeeded=False, error=f"text_extract: {exc}")
        log.exception("Text extraction failed for %s", filename)
        raise

    # 3. AI extraction, attributes only. Each step records its type + confidence
    #    so a partial failure still produces an honest audit record.
    try:
        meta = ai.extract_metadata(text)
        types.append(ExtractionType.METADATA)
        confidences[ExtractionType.METADATA.value] = float(meta.get("confidence", 0.0))

        clauses = ai.extract_clauses(text)
        types.append(ExtractionType.CLAUSES)
        confidences[ExtractionType.CLAUSES.value] = float(clauses.get("confidence", 0.0))

        # Phase 1: no SubjectMatterTerm seed list yet, so the mapper abstains on
        # every clause. The Clause Map Index will be empty until backlog item 9
        # (seed candidate terms) lands.
        mappings = ai.map_subject_terms(text, clauses)
        types.append(ExtractionType.TAXONOMY)
        confidences[ExtractionType.TAXONOMY.value] = float(mappings.get("confidence", 0.0))
    except Exception as exc:
        _write_run(out, run_id, contract_id, types, confidences,
                   succeeded=False, error=f"ai_extract: {exc}")
        log.exception("AI extraction failed for %s", filename)
        raise

    # 4. Priority-review flag: trip if ANY of the three confidences is below
    #    the threshold (per design decision E).
    priority_review = any(c < CONFIDENCE_THRESHOLD for c in confidences.values())

    # 5. Validate into Pydantic models. On failure, nothing downstream is written.
    try:
        contract = Contract(
            ContractID=contract_id,
            ReviewStatus=ReviewStatus.PENDING,
            **meta["attributes"],
        )
        clause_maps: List[ClauseTermMap] = []
        for m in mappings.get("mappings", []):
            clause_maps.append(ClauseTermMap(
                MapID=uuid.uuid4(),
                ContractID=contract_id,
                ClauseID=m["ClauseID"],
                TermID=m["TermID"],
                RelevanceScore=float(m.get("RelevanceScore", 0.0)),
                ExtractionConfidence=float(m.get("ExtractionConfidence", 0.0)),
                Notes=m.get("Notes", ""),
                ReviewStatus=ReviewStatus.PENDING,
            ))
    except ValidationError as exc:
        _write_run(out, run_id, contract_id, types, confidences,
                   succeeded=False, error=f"schema_validation: {exc.errors()}")
        log.error("Validation failed for %s: %s", contract_id, exc)
        raise

    # 6. Persist JSON detail (the audit record).
    sp.upload_json(
        f"{out}/Metadata/contract_{contract_id}.json",
        contract.model_dump(by_alias=True),
    )
    sp.upload_json(
        f"{out}/ClauseExtraction/clauses_{contract_id}.json",
        {
            "clauses": clauses.get("clauses", []),
            "mappings": [cm.model_dump(by_alias=True) for cm in clause_maps],
            "abstained": mappings.get("abstained", []),
        },
    )

    # 7. Upsert the query-layer List rows. ReviewStatus = Pending until a human
    #    approves. PriorityReview at the contract level mirrors the any-low rule;
    #    each clause-map row also carries its own per-row PriorityReview based
    #    on its individual ExtractionConfidence.
    sp.upsert_list_item(CONTRACT_INDEX_LIST, "ContractID", {
        "ContractID": str(contract_id),
        "Title": contract.title,
        "Counterparty": contract.counterparty,
        "ContractType": contract.contract_type,
        "Status": contract.status,
        "EffectiveDate": _iso(contract.effective_date),
        "ExpirationDate": _iso(contract.expiration_date),
        "CurrentValue": contract.current_value,
        "FundingSource": contract.funding_source,
        "ReviewStatus": ReviewStatus.PENDING.value,
        "PriorityReview": priority_review,
    })
    for cm in clause_maps:
        sp.upsert_list_item(CLAUSE_MAP_LIST, "MapID", {
            "MapID": str(cm.map_id),
            "ContractID": str(cm.contract_id),
            "ClauseID": cm.clause_id,
            "TermID": cm.term_id,
            "RelevanceScore": cm.relevance_score,
            "ExtractionConfidence": cm.extraction_confidence,
            "ReviewStatus": ReviewStatus.PENDING.value,
            "PriorityReview": cm.extraction_confidence < CONFIDENCE_THRESHOLD,
        })

    # 8. Successful audit log.
    _write_run(out, run_id, contract_id, types, confidences, succeeded=True)
    log.info("Processed contract %s (priority_review=%s)", contract_id, priority_review)
    return str(contract_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _iso(value: Any) -> Optional[str]:
    "ISO-8601 string for date/datetime, or None. Lists want strings, not date objects."
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _write_run(out_base: str, run_id: uuid.UUID, contract_id: str,
               types: List[ExtractionType], confidences: Dict[str, float],
               succeeded: bool, error: Optional[str] = None) -> None:
    "Write the AIExtractionRun audit record. Always called, on success or failure."
    run = AIExtractionRun(
        RunID=run_id,
        ContractID=contract_id,
        Timestamp=datetime.now(timezone.utc),
        ModelVersion=MODEL_VERSION,
        ExtractionTypes=types,
        ConfidenceScores=confidences,
        Succeeded=succeeded,
    )
    payload = run.model_dump(by_alias=True)
    if error:
        payload["Error"] = error
    sp.upload_json(f"{out_base}/Metadata/ai_run_{run_id}.json", payload)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Process one contract through the CLM pipeline.")
    ap.add_argument("--filename", required=True, help="File name inside 01_Original/")
    ap.add_argument("--contract-id", default=None,
                    help="Existing ContractID; omit to mint a new one.")
    args = ap.parse_args()
    try:
        process_contract(args.filename, args.contract_id)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
