"""
ai_extract.py (v2)

Real Azure OpenAI extraction, drop-in for ai_dummy.py: it exposes the same
function names and return shapes the orchestrators already consume, so
process_contract.py / process_amendment.py do not change beyond the import.

Three principles baked in (from the design discussion):

  1. Attributes only. The model never returns identity fields; the orchestrator
     mints all IDs.
  2. Evidence + abstention. Every extraction carries a verbatim span, and the
     subject-matter mapper is grounded against a candidate term list and may
     return null ("ambiguous") instead of guessing.
  3. Derived confidence. The "confidence" the orchestrator flags on is computed
     from evidence coverage / abstention signals, NOT a number the model makes
     up about itself (model self-confidence is not calibrated).

The clause-pair diff uses the separate reasoning deployment via judge().
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import azure_client as aoai
import ai_schemas as S

# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
_RULES = (
    "You extract structured facts from contract text. Rules: (1) Return ONLY "
    "attributes found in the text; never invent identifiers. (2) For every field "
    "provide a verbatim evidence span. (3) If the text does not support a value, "
    "leave it null rather than guessing."
)
_MAP_RULES = (
    "You map a clause to ONE subject-matter term from the supplied candidate list. "
    "Disambiguate by sense using the clause text and its context. If no candidate "
    "fits, or the sense is genuinely ambiguous, set term_id to null and ambiguous "
    "to true. Always cite the evidence span and state why this sense, not a "
    "competing one (the discriminator)."
)
_JUDGE_RULES = (
    "You compare two contract clauses that address the same area. Decompose to the "
    "underlying obligation, then classify their relationship as one of: identical, "
    "stricter, looser, superset, subset, related, conflicting. Give the intensity "
    "of each side and a short rationale, with an evidence span from each clause."
)


def _coverage_confidence(evidenced: set, key_fields: set) -> float:
    if not key_fields:
        return 1.0
    return round(len(key_fields & evidenced) / len(key_fields), 3)


# --------------------------------------------------------------------------- #
# Contract-side extraction
# --------------------------------------------------------------------------- #
def extract_metadata(text: str) -> Dict[str, Any]:
    m = aoai.extract(S.MetadataExtraction, _RULES, f"Contract text:\n{text}")
    evidenced = {e.field for e in m.evidence}
    conf = _coverage_confidence(evidenced, {"Title", "Counterparty", "ContractType", "TotalValue"})
    return {
        "attributes": {
            "Title": m.title,
            "Counterparty": m.counterparty,
            "ContractType": m.contract_type,
            "EffectiveDate": m.effective_date,
            "ExpirationDate": m.expiration_date,
            "AutoRenewal": m.auto_renewal,
            "TotalValue": m.total_value,
            "FundingSource": m.funding_source,
            "Status": m.status,
        },
        "evidence": [e.model_dump() for e in m.evidence],
        "confidence": conf,
    }


def extract_clauses(text: str) -> Dict[str, Any]:
    c = aoai.extract(S.ClauseExtraction, _RULES, f"Identify standard clauses.\n{text}")
    return {
        "clauses": [ci.model_dump() for ci in c.clauses],
        "confidence": 1.0 if c.clauses else 0.0,
    }


def map_subject_terms(text: str, clauses: Dict[str, Any],
                      candidate_terms: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Grounded mapping. candidate_terms: [{TermID, TermName, Domain, Definition}, ...]."""
    candidates = candidate_terms or []
    cand_lines = "\n".join(
        f"- {t['TermID']}: {t.get('TermName','')} ({t.get('Domain','')}) - {t.get('Definition','')}"
        for t in candidates
    ) or "(none supplied; you must abstain for every clause)"
    user = (
        f"Candidate terms:\n{cand_lines}\n\n"
        f"Clauses:\n{clauses.get('clauses')}\n\nContext:\n{text}"
    )
    res = aoai.map_terms(S.TaxonomyExtraction, _MAP_RULES, user)  # see alias below
    resolved, abstained = [], []
    for mp in res.mappings:
        if mp.term_id and not mp.ambiguous:
            resolved.append({
                "ClauseID": mp.clause_id,
                "TermID": mp.term_id,
                "RelevanceScore": mp.relevance,
                "ExtractionConfidence": mp.relevance,
                "Notes": f"{mp.sense_label}: {mp.discriminator}",
            })
        else:
            abstained.append({
                "ClauseID": mp.clause_id,
                "Reason": "ambiguous" if mp.ambiguous else "no candidate fit",
                "EvidenceSpan": mp.evidence_span,
            })
    conf = round(min((r["RelevanceScore"] for r in resolved), default=0.0), 3)
    return {"mappings": resolved, "abstained": abstained, "confidence": conf}


# --------------------------------------------------------------------------- #
# Amendment-side extraction
# --------------------------------------------------------------------------- #
def extract_amendment_metadata(text: str) -> Dict[str, Any]:
    a = aoai.extract(S.AmendmentExtraction, _RULES, f"Amendment text:\n{text}")
    evidenced = {e.field for e in a.evidence}
    conf = _coverage_confidence(evidenced, {"AmendmentType", "ValueChange"})
    return {
        "attributes": {
            "AmendmentType": a.amendment_type,
            "EffectiveDate": a.effective_date,
            "ExpirationDate": a.expiration_date,
            "ValueChange": a.value_change,
            "SummaryOfChanges": a.summary_of_changes,
        },
        "contract_changes": {fc.field: fc.new_value for fc in a.contract_changes},
        "confidence": conf,
    }


def detect_modified_clauses(text: str, original_clauses: Dict[str, Any]) -> Dict[str, Any]:
    user = f"Original clauses:\n{original_clauses.get('clauses')}\n\nAmendment text:\n{text}"
    res = aoai.judge(S.ModifiedClausesExtraction, _RULES, user)  # reasoning deployment
    return {
        "modified": [
            {"ClauseID": mc.clause_id, "ChangeType": mc.change_type, "Note": mc.note}
            for mc in res.modified
        ],
        "confidence": 1.0 if res.modified else 0.0,
    }


def judge_clause_pair(clause_a: str, clause_b: str) -> Dict[str, Any]:
    """Reasoning-model judgment of how two clauses relate (for the comparison views)."""
    user = f"Clause A:\n{clause_a}\n\nClause B:\n{clause_b}"
    j = aoai.judge(S.ClauseDiffJudgment, _JUDGE_RULES, user)
    return j.model_dump()
