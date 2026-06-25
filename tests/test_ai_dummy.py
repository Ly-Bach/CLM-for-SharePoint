"""The dummy backend must match the shapes ai_extract produces and feed validation."""
from __future__ import annotations

import uuid

import ai_dummy
from schema import ClauseTermMap


def test_metadata_attributes_only_no_identity_fields():
    res = ai_dummy.extract_metadata("text")
    attrs = res["attributes"]
    assert "ContractID" not in attrs and "RunID" not in attrs
    assert 0.0 <= res["confidence"] <= 1.0


def test_clause_shape_matches_real_backend():
    clauses = ai_dummy.extract_clauses("text")["clauses"]
    for c in clauses:
        assert set(c) == {"clause_id", "clause_name", "text_span"}


def test_map_subject_terms_accepts_candidate_terms_arg():
    # Signature parity with ai_extract: must accept the third argument.
    res = ai_dummy.map_subject_terms("text", {"clauses": []}, [{"TermID": "X"}])
    assert "mappings" in res


def test_dummy_mappings_validate_into_clause_term_map():
    res = ai_dummy.map_subject_terms("text", {"clauses": []})
    for m in res["mappings"]:
        ClauseTermMap(
            MapID=uuid.uuid4(), ContractID=uuid.uuid4(),
            ClauseID=m["ClauseID"], TermID=m["TermID"],
            RelevanceScore=m["RelevanceScore"],
            ExtractionConfidence=m["ExtractionConfidence"],
            Notes=m.get("Notes", ""),
        )


def test_amendment_metadata_has_contract_changes():
    res = ai_dummy.extract_amendment_metadata("text")
    assert "ValueChange" in res["attributes"]
    assert isinstance(res["contract_changes"], dict)
