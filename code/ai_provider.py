"""
ai_provider.py

Single selection point for the AI backend, so the orchestrators import one
module and never change when the implementation is swapped:

    USE_DUMMY_AI=true   -> ai_dummy   (offline placeholder; no API calls)
    USE_DUMMY_AI=false  -> ai_extract (real Azure OpenAI structured outputs)

Both backends expose the same function names and return shapes, so the
orchestrators are agnostic to which one is active. MODEL_VERSION is derived from
the active backend so it lands correctly in each AIExtractionRun record.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import settings

if settings.use_dummy_ai:
    import ai_dummy as _backend

    MODEL_VERSION = "dummy-v0"
else:
    import ai_extract as _backend
    from azure_client import which_models

    _m = which_models()
    MODEL_VERSION = f"extract={_m['extract']};judge={_m['judge']}"


def extract_metadata(text: str) -> Dict[str, Any]:
    return _backend.extract_metadata(text)


def extract_clauses(text: str) -> Dict[str, Any]:
    return _backend.extract_clauses(text)


def map_subject_terms(text: str, clauses: Dict[str, Any],
                      candidate_terms: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    # Both backends accept candidate_terms (ai_dummy ignores it, ai_extract
    # grounds against it), so forward it directly.
    return _backend.map_subject_terms(text, clauses, candidate_terms)


def extract_amendment_metadata(text: str) -> Dict[str, Any]:
    return _backend.extract_amendment_metadata(text)


def detect_modified_clauses(text: str, original_clauses: Dict[str, Any]) -> Dict[str, Any]:
    return _backend.detect_modified_clauses(text, original_clauses)
