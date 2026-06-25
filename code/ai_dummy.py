"""
ai_dummy.py (v2)

Placeholder AI so the SharePoint + schema wiring can be tested before the real
Azure OpenAI calls exist. The key correction vs v1: the AI returns ATTRIBUTES
ONLY. It never mints ContractID / AmendmentID / RunID — those are owned by the
orchestrator and passed in. Each function also returns a confidence score so the
review gate and confidence-threshold flagging have something to act on.

Swap these for real LLM calls later; keep the return shapes identical so
process_contract.py does not change.
"""
from __future__ import annotations

from typing import Any, Dict


def extract_metadata(text: str) -> Dict[str, Any]:
    """Return contract attributes only — NOT identity fields."""
    return {
        "attributes": {
            "Title": "Sample Vendor Agreement",
            "Counterparty": "Sample Vendor Inc.",
            "ContractType": "Vendor",
            "EffectiveDate": "2025-01-01",
            "ExpirationDate": "2026-01-01",
            "AutoRenewal": False,
            "TotalValue": 100000,
            "FundingSource": "State",
            "Status": "Active",
        },
        "confidence": 0.91,
    }


def extract_clauses(text: str) -> Dict[str, Any]:
    # Shapes match ai_extract.extract_clauses (clause_id / clause_name / text_span)
    # so the two backends are truly interchangeable.
    return {
        "clauses": [
            {"clause_id": "TERM", "clause_name": "Termination", "text_span": "…dummy termination text…"},
            {"clause_id": "SOW", "clause_name": "Scope of Work", "text_span": "…dummy scope text…"},
        ],
        "confidence": 0.84,
    }


def map_subject_terms(text: str, clauses: Dict[str, Any],
                      candidate_terms: Any = None) -> Dict[str, Any]:
    """Dual-taxonomy mappings — clause -> subject-matter term.

    Accepts candidate_terms for signature parity with ai_extract; the dummy
    backend ignores it.
    """
    return {
        "mappings": [
            {"ClauseID": "TERM", "TermID": "PROGRAM_EXIT",
             "RelevanceScore": 0.78, "ExtractionConfidence": 0.80,
             "Notes": "Termination ↔ Program Exit Procedures (Housing)"},
        ],
        "confidence": 0.80,
    }


# --------------------------------------------------------------------------- #
# Amendment-side extraction (attributes only — IDs owned by the orchestrator)
# --------------------------------------------------------------------------- #
def extract_amendment_metadata(text: str) -> Dict[str, Any]:
    """Amendment attributes, plus any contract-level fields the amendment changes."""
    return {
        "attributes": {
            "AmendmentType": "Extension",
            "EffectiveDate": "2026-01-01",
            "ExpirationDate": "2027-01-01",
            "ValueChange": 25000,
            "SummaryOfChanges": "Extends the term by one year and adds $25,000 in funding.",
        },
        # Contract-level fields this amendment modifies (drives the MetadataDiff).
        "contract_changes": {
            "ExpirationDate": "2027-01-01",
            "Status": "Active",
        },
        "confidence": 0.88,
    }


def detect_modified_clauses(text: str, original_clauses: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compare the amendment against the approved original clause set and report
    which clauses changed. Real version: clause-by-clause semantic diff.
    """
    return {
        "modified": [
            {"ClauseID": "TERM", "ChangeType": "Modified",
             "Note": "Termination notice period changed from 30 to 60 days."},
        ],
        "confidence": 0.82,
    }
