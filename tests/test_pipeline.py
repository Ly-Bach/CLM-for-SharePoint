"""
End-to-end pipeline tests with SharePoint and text extraction stubbed.

Confirms the USE_DUMMY_AI toggle selects the offline backend and that both
orchestrators validate, persist, and roll values through correctly — without any
network call.
"""
from __future__ import annotations

import json
import sys
import types

import pytest


@pytest.fixture
def store(monkeypatch):
    """In-memory stand-in for SharePoint; returns the dict it writes into."""
    data: dict = {}

    sp = types.ModuleType("sharepoint_io")
    sp.ensure_contract_folder_structure = lambda cid: f"Contracts/{cid}"
    sp.contract_root = lambda cid: f"Contracts/{cid}"
    sp.download_file = lambda p: data.get(p, b"dummy")
    sp.upload_json = lambda p, d: data.__setitem__(p, json.dumps(d, default=str).encode()) or {}
    sp.upload_markdown = lambda p, t: data.__setitem__(p, t.encode()) or {}
    sp.upsert_list_item = lambda ln, key, fields: data.setdefault("LIST:" + ln, []).append(fields) or {}
    sp.ensure_folder = lambda p: {}
    monkeypatch.setitem(sys.modules, "sharepoint_io", sp)

    te = types.ModuleType("text_extract")
    te.extract_text = lambda fn, raw, **k: "CONTRACT TEXT"
    monkeypatch.setitem(sys.modules, "text_extract", te)

    # Import after stubs are in place; drop cached copies so stubs take effect.
    for m in ("ai_provider", "process_contract", "process_amendment"):
        sys.modules.pop(m, None)
    return data


def test_toggle_selects_dummy_backend(store):
    import ai_provider
    assert ai_provider.MODEL_VERSION == "dummy-v0"


def test_process_contract_writes_index_and_json(store):
    import process_contract as pc
    cid = pc.process_contract("contract.pdf")

    rows = store["LIST:Contract Index"]
    assert len(rows) == 1
    assert rows[0]["ContractID"] == cid
    assert rows[0]["CurrentValue"] == 100000.0
    assert rows[0]["ReviewStatus"] == "Pending"
    assert any("contract_" in k for k in store)        # metadata JSON
    assert any("ai_run_" in k for k in store)          # run record


def test_process_amendment_rolls_value(store):
    import process_contract as pc
    import process_amendment as pa
    cid = pc.process_contract("contract.pdf")
    pa.process_amendment(cid, 1, "amend.pdf")

    assert store["LIST:Contract Index"][-1]["CurrentValue"] == 125000.0
    assert len(store["LIST:Amendment Index"]) == 1
    assert store["LIST:Amendment Index"][0]["ValueChange"] == 25000.0


def test_failed_run_writes_record_and_no_index_row(store, monkeypatch):
    """A validation failure must record a failed run and write nothing downstream."""
    import ai_provider
    monkeypatch.setattr(
        ai_provider, "extract_metadata",
        lambda text: {"attributes": {"Title": ""}, "confidence": 0.9},  # invalid: empty title etc.
    )
    import process_contract as pc
    with pytest.raises(Exception):
        pc.process_contract("contract.pdf")
    assert "LIST:Contract Index" not in store     # nothing persisted downstream
    assert any("ai_run_" in k for k in store)      # but the failed run is recorded
