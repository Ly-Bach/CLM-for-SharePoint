"""
ai_schemas.py (v2)

Pydantic models describing exactly what the LLM is asked to return. These are
DIFFERENT from the storage models in schema.py on purpose:

  * They never contain identity fields (ContractID, AmendmentID, ...). The
    orchestrator owns identity; the model only produces attributes.
  * Every extraction carries an evidence span so a reviewer can verify it.
  * Mapping and judgment models can ABSTAIN ("ambiguous") rather than guess.

azure_client.to_strict_schema() turns these into the strict JSON Schema sent to
Azure OpenAI Structured Outputs, so the model literally cannot return a shape
that fails validation.

Dates are plain strings here (ISO 8601). The orchestrator validates them into
real date types when it builds the storage models.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from schema import AmendmentType, ContractStatus, ContractType, FundingSource


class _Extraction(BaseModel):
    # No defaults: strict Structured Outputs requires every property present.
    model_config = {"extra": "forbid"}


class FieldEvidence(_Extraction):
    field: str
    quote: str = Field(description="Verbatim span from the document supporting this field.")


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #
class MetadataExtraction(_Extraction):
    title: str
    counterparty: str
    contract_type: ContractType
    effective_date: Optional[str]
    expiration_date: Optional[str]
    auto_renewal: bool
    total_value: float
    funding_source: Optional[FundingSource]
    status: ContractStatus
    evidence: List[FieldEvidence]


# --------------------------------------------------------------------------- #
# Clauses
# --------------------------------------------------------------------------- #
class ClauseItem(_Extraction):
    clause_id: str = Field(description="Standard-taxonomy clause id, e.g. TERM, SOW.")
    clause_name: str
    text_span: str


class ClauseExtraction(_Extraction):
    clauses: List[ClauseItem]


# --------------------------------------------------------------------------- #
# Subject-matter mapping (grounded + abstaining)
# --------------------------------------------------------------------------- #
class TermMapping(_Extraction):
    clause_id: str
    # term_id is null when the model abstains; ambiguous flags genuine uncertainty.
    term_id: Optional[str] = Field(description="A TermID from the supplied candidate list, or null.")
    sense_label: str = Field(description="Which sense was chosen, in plain words.")
    discriminator: str = Field(description="Why this sense and not a competing one.")
    evidence_span: str
    relevance: float = Field(ge=0.0, le=1.0)
    ambiguous: bool = Field(description="True if context is insufficient to map confidently.")


class TaxonomyExtraction(_Extraction):
    mappings: List[TermMapping]


# --------------------------------------------------------------------------- #
# Amendment
# --------------------------------------------------------------------------- #
class FieldChange(_Extraction):
    field: str = Field(description="Contract field alias changed, e.g. ExpirationDate.")
    new_value: str


class AmendmentExtraction(_Extraction):
    amendment_type: AmendmentType
    effective_date: Optional[str]
    expiration_date: Optional[str]
    value_change: float
    summary_of_changes: str
    contract_changes: List[FieldChange]
    evidence: List[FieldEvidence]


# --------------------------------------------------------------------------- #
# Clause-pair judgment (the diff "judge", ideally a reasoning model)
# --------------------------------------------------------------------------- #
class ClauseDiffJudgment(_Extraction):
    canonical_requirement: str
    relationship: str = Field(
        description="identical | stricter | looser | superset | subset | related | conflicting"
    )
    intensity_a: Optional[str]
    intensity_b: Optional[str]
    rationale: str
    evidence_a: str
    evidence_b: str


# --------------------------------------------------------------------------- #
# Modified-clause detection (amendment diff vs original)
# --------------------------------------------------------------------------- #
class ModifiedClause(_Extraction):
    clause_id: str
    change_type: str = Field(description="Added | Removed | Modified")
    note: str
    evidence_span: str


class ModifiedClausesExtraction(_Extraction):
    modified: List[ModifiedClause]
