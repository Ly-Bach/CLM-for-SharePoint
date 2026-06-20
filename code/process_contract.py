"""
process_contract.py (v2) — Phase-1 orchestrator.

End-to-end flow for a single contract, corrected from v1:

  1. The orchestrator MINTS the ContractID (the AI never does) and creates the
     /Contracts/{ContractID}/ folder structure.
  2. Downloads the original document and extracts text (PDF / DOCX / image,
     with a local Tesseract OCR fallback for scanned files).
  3. Runs attribute-only AI extraction (dummy now, Azure OpenAI later).
  4. Parses every result into a validated Pydantic model — on validation
     failure the run is recorded as FAILED and nothing is written downstream.
  5. Writes validated JSON to 04_AI_Outputs and upserts the Contract Index /
     Clause Map List rows with ReviewStatus = Pending.
  6. Records an AIExtractionRun for audit/traceability.
  7. Low-confidence extractions are flagged for priority review.

Typical Phase-1 use (the contract folder + ID are created first, then a file
is dropped into 01_Original/):

    python process_contract.py --filename contract.pdf
    python process_contract.py --contract-id <existing-guid> --filename amend.pdf
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import ValidationError

import ai_extract as ai
import sharepoint_io as sp
from text_extract import extract_text
from schema import (
    AIExtractionRun,
    ClauseTermMap,
    Contract,
    ExtractionType,
    ReviewStatus,
)

CONFIDENCE_THRESHOLD = 0.85  # below this -> flagged for priority human review
MODEL_VERSION = "dummy-v0"

CONTRACT_INDEX_LIST = "Contract Index"
CLAUSE_MAP_LIST = "Clause Map Index"


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def process_contract(filename: str, contract_id: Optional[str] = None) -> str:
    # 1. Identity is owned here, not by the AI.
    contract_id = contract_id or str(uuid.uuid4())
    run_id = uuid.uuid4()
    root = sp.ensure_contract_folder_structure(contract_id)
    out = f"{root}/04_AI_Outputs"

    extraction_types: list[ExtractionType] = []
    confidences: dict[str, float] = {}

    try:
        # 2. Download + text (OCR fallback handled in text_extract)
        raw = sp.download_file(f"{root}/01_Original/{filename}")
        text = extract_text(filename, raw)

        # 3. AI (attributes only)
        meta = ai.extract_metadata(text)
        clause_res = ai.extract_clauses(text)
        term_res = ai.map_subject_terms(text, clause_res)
        confidences = {
            "Metadata": meta["confidence"],
            "Clauses": clause_res["confidence"],
            "Taxonomy": term_res["confidence"],
        }
        extraction_types = [ExtractionType.METADATA, ExtractionType.CLAUSES, ExtractionType.TAXONOMY]

        # 4. Validate into models (orchestrator supplies all IDs)
        contract = Contract(
            ContractID=contract_id,
            PrimaryDocumentURL=f"{root}/01_Original/{filename}",
            **meta["attributes"],
        )
        clause_maps = [
            ClauseTermMap(
                MapID=uuid.uuid4(),
                ContractID=contract_id,
                ClauseID=m["ClauseID"],
                TermID=m["TermID"],
                RelevanceScore=m["RelevanceScore"],
                ExtractionConfidence=m["ExtractionConfidence"],
                Notes=m.get("Notes", ""),
            )
            for m in term_res["mappings"]
        ]

    except (ValidationError, RuntimeError, Exception) as exc:  # noqa: BLE001
        # 4b. Failure: record the run as failed, write nothing downstream.
        _write_run(out, run_id, contract_id, extraction_types, confidences,
                   succeeded=False, error=str(exc))
        print(f"[FAILED] {contract_id}: {exc}", file=sys.stderr)
        raise

    # 5. Persist validated JSON
    sp.upload_json(f"{out}/Metadata/contract_{contract_id}.json",
                   contract.model_dump(by_alias=True))
    sp.upload_json(f"{out}/ClauseExtraction/clauses_{contract_id}.json", clause_res)
    sp.upload_json(f"{out}/ClauseExtraction/maps_{contract_id}.json",
                   {"mappings": [m.model_dump(by_alias=True) for m in clause_maps]})
    summary = (f"# Contract Summary\n\n"
               f"- **Title:** {contract.title}\n"
               f"- **Counterparty:** {contract.counterparty}\n"
               f"- **Type:** {contract.contract_type}\n"
               f"- **Current Value:** {contract.current_value}\n")
    sp.upload_markdown(f"{out}/Summaries/summary_{contract_id}.md", summary)

    # 6. Upsert the query-layer List rows (ReviewStatus = Pending by default)
    sp.upsert_list_item(CONTRACT_INDEX_LIST, "ContractID", contract.index_row())
    for m in clause_maps:
        sp.upsert_list_item(CLAUSE_MAP_LIST, "MapID", {
            "MapID": str(m.map_id),
            "ContractID": str(m.contract_id),
            "ClauseID": m.clause_id,
            "TermID": m.term_id,
            "RelevanceScore": m.relevance_score,
            "ExtractionConfidence": m.extraction_confidence,
            "ReviewStatus": m.review_status,
        })

    # 7. Record the run + flag low confidence
    _write_run(out, run_id, contract_id, extraction_types, confidences, succeeded=True)
    low = {k: v for k, v in confidences.items() if v < CONFIDENCE_THRESHOLD}
    flag = f"  ⚠ low-confidence (priority review): {low}" if low else ""
    print(f"[OK] processed {contract_id} (review={ReviewStatus.PENDING.value}).{flag}")
    return contract_id


def _write_run(out_base: str, run_id: uuid.UUID, contract_id: str,
               types: list, confidences: dict, succeeded: bool,
               error: Optional[str] = None) -> None:
    run = AIExtractionRun(
        RunID=run_id,
        ContractID=contract_id,
        Timestamp=datetime.now(timezone.utc),
        ModelVersion=MODEL_VERSION,
        ExtractionType=types,
        ConfidenceScores=confidences,
        Succeeded=succeeded,
    )
    payload = run.model_dump(by_alias=True)
    if error:
        payload["Error"] = error
    sp.upload_json(f"{out_base}/Metadata/ai_run_{run_id}.json", payload)


def main() -> None:
    ap = argparse.ArgumentParser(description="Process one contract through the CLM pipeline.")
    ap.add_argument("--filename", required=True, help="File name inside 01_Original/")
    ap.add_argument("--contract-id", default=None,
                    help="Existing ContractID; omit to mint a new one.")
    args = ap.parse_args()
    process_contract(args.filename, args.contract_id)


if __name__ == "__main__":
    main()
