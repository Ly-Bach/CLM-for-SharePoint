"""Validation, value roll-up, and round-trip tests for the storage models."""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from schema import Amendment, ClauseTermMap, Contract


METADATA = {
    "Title": "Sample Vendor Agreement",
    "Counterparty": "Sample Vendor Inc.",
    "ContractType": "Vendor",
    "EffectiveDate": "2025-01-01",
    "ExpirationDate": "2026-01-01",
    "AutoRenewal": False,
    "TotalValue": 100000,
    "FundingSource": "State",
    "Status": "Active",
}


def _contract(**overrides):
    data = {"ContractID": str(uuid.uuid4()), "PrimaryDocumentURL": "x", **METADATA}
    data.update(overrides)
    return Contract(**data)


def test_contract_validates_and_defaults_review_pending():
    c = _contract()
    assert c.review_status == "Pending"
    assert c.current_value == 100000.0


def test_index_row_has_expected_keys():
    row = _contract().index_row()
    assert set(row) == {
        "ContractID", "Title", "Counterparty", "ContractType", "Status",
        "EffectiveDate", "ExpirationDate", "CurrentValue", "FundingSource", "ReviewStatus",
    }


def test_amendment_value_rollup():
    c = _contract()
    c.amendments.append(Amendment(
        AmendmentID=uuid.uuid4(), ContractID=c.contract_id,
        AmendmentNumber=1, AmendmentType="Extension", ValueChange=25000,
    ))
    assert c.current_value == 125000.0


def test_dump_strip_currentvalue_roundtrips():
    """Mirrors process_amendment._load_contract: dump -> strip computed -> revalidate."""
    c = _contract()
    c.amendments.append(Amendment(
        AmendmentID=uuid.uuid4(), ContractID=c.contract_id,
        AmendmentNumber=1, AmendmentType="Extension", ValueChange=25000,
    ))
    dumped = c.model_dump(by_alias=True)
    dumped.pop("CurrentValue", None)
    again = Contract.model_validate(dumped)
    assert again.current_value == 125000.0


def test_negative_total_value_rejected():
    with pytest.raises(ValidationError):
        _contract(TotalValue=-1)


def test_invalid_contract_type_rejected():
    with pytest.raises(ValidationError):
        _contract(ContractType="NotAType")


def test_clause_term_map_relevance_bounds():
    base = dict(MapID=uuid.uuid4(), ContractID=uuid.uuid4(),
                ClauseID="TERM", TermID="PROGRAM_EXIT", ExtractionConfidence=0.5)
    ClauseTermMap(RelevanceScore=0.8, **base)  # valid
    with pytest.raises(ValidationError):
        ClauseTermMap(RelevanceScore=1.5, **base)  # out of [0,1]
