"""
CLM schema (v2) — validated Pydantic models.

Every object the pipeline produces is parsed into one of these models. AI output
that does not conform is rejected before anything is written downstream, so a
malformed extraction is visible rather than silently persisted.

Requires Pydantic v2:  pip install "pydantic>=2"
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

SCHEMA_VERSION = "2.0"


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class ContractType(str, Enum):
    GRANT = "Grant"
    VENDOR = "Vendor"
    SUBRECIPIENT = "Subrecipient"
    MOU = "MOU"
    LEASE = "Lease"
    OTHER = "Other"


class FundingSource(str, Enum):
    FEDERAL = "Federal"
    STATE = "State"
    LOCAL = "Local"
    PRIVATE = "Private"


class ContractStatus(str, Enum):
    DRAFT = "Draft"
    ACTIVE = "Active"
    EXPIRED = "Expired"
    TERMINATED = "Terminated"


class RiskLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class ReviewStatus(str, Enum):
    """The gate between AI output and trusted data. Only APPROVED feeds analytics."""
    PENDING = "Pending"
    REVIEWED = "Reviewed"
    APPROVED = "Approved"


class AmendmentType(str, Enum):
    EXTENSION = "Extension"
    BUDGET_REVISION = "Budget Revision"
    SCOPE_CHANGE = "Scope Change"
    LEGAL_CORRECTION = "Legal Correction"
    OTHER = "Other"


# --------------------------------------------------------------------------- #
# Base
# --------------------------------------------------------------------------- #
class CLMBase(BaseModel):
    """Common fields + strict validation for every persisted object."""
    model_config = {"extra": "forbid", "use_enum_values": True,
                    "validate_default": True, "validate_assignment": True}

    schema_version: str = Field(default=SCHEMA_VERSION, alias="SchemaVersion")
    review_status: ReviewStatus = Field(default=ReviewStatus.PENDING, alias="ReviewStatus")

    model_config["populate_by_name"] = True  # accept both snake_case and alias


# --------------------------------------------------------------------------- #
# Amendment
# --------------------------------------------------------------------------- #
class Amendment(CLMBase):
    amendment_id: UUID = Field(alias="AmendmentID")
    contract_id: UUID = Field(alias="ContractID")
    amendment_number: int = Field(alias="AmendmentNumber", ge=1)
    amendment_type: AmendmentType = Field(alias="AmendmentType")
    effective_date: Optional[date] = Field(default=None, alias="EffectiveDate")
    expiration_date: Optional[date] = Field(default=None, alias="ExpirationDate")
    value_change: float = Field(default=0.0, alias="ValueChange")
    summary_of_changes: str = Field(default="", alias="SummaryOfChanges")
    amendment_document_url: Optional[str] = Field(default=None, alias="AmendmentDocumentURL")
    parent_folder_url: Optional[str] = Field(default=None, alias="ParentFolderURL")
    ai_summary_url: Optional[str] = Field(default=None, alias="AIGeneratedSummaryURL")


# --------------------------------------------------------------------------- #
# Contract
# --------------------------------------------------------------------------- #
class Contract(CLMBase):
    # Identity is minted by the orchestrator, never by the AI.
    contract_id: UUID = Field(alias="ContractID")

    title: str = Field(alias="Title", min_length=1)
    counterparty: str = Field(alias="Counterparty", min_length=1)
    contract_type: ContractType = Field(alias="ContractType")

    effective_date: Optional[date] = Field(default=None, alias="EffectiveDate")
    expiration_date: Optional[date] = Field(default=None, alias="ExpirationDate")
    auto_renewal: bool = Field(default=False, alias="AutoRenewal")

    total_value: float = Field(default=0.0, alias="TotalValue", ge=0)
    funding_source: Optional[FundingSource] = Field(default=None, alias="FundingSource")
    program_id: Optional[str] = Field(default=None, alias="ProgramID")
    status: ContractStatus = Field(default=ContractStatus.DRAFT, alias="Status")

    primary_document_url: Optional[str] = Field(default=None, alias="PrimaryDocumentURL")
    metadata_version: int = Field(default=1, alias="MetadataVersion", ge=1)

    # Amendments roll up into the computed current value.
    amendments: list[Amendment] = Field(default_factory=list, alias="Amendments")

    @computed_field(alias="CurrentValue")  # type: ignore[misc]
    @property
    def current_value(self) -> float:
        """TotalValue + sum of all amendment ValueChanges."""
        return round(self.total_value + sum(a.value_change for a in self.amendments), 2)

    def index_row(self) -> dict:
        """Flat dict for the Contract Index SharePoint List."""
        return {
            "ContractID": str(self.contract_id),
            "Title": self.title,
            "Counterparty": self.counterparty,
            "ContractType": self.contract_type,
            "Status": self.status,
            "EffectiveDate": self.effective_date.isoformat() if self.effective_date else None,
            "ExpirationDate": self.expiration_date.isoformat() if self.expiration_date else None,
            "CurrentValue": self.current_value,
            "FundingSource": self.funding_source,
            "ReviewStatus": self.review_status,
        }


# --------------------------------------------------------------------------- #
# Clause & taxonomy (dual taxonomy)
# --------------------------------------------------------------------------- #
class StandardClause(CLMBase):
    clause_id: str = Field(alias="ClauseID")
    clause_name: str = Field(alias="ClauseName")
    definition: str = Field(default="", alias="Definition")
    required_for: list[ContractType] = Field(default_factory=list, alias="RequiredFor")
    risk_level: RiskLevel = Field(default=RiskLevel.LOW, alias="RiskLevel")
    keywords: list[str] = Field(default_factory=list, alias="Keywords")


class SubjectMatterTerm(CLMBase):
    term_id: str = Field(alias="TermID")
    domain: str = Field(alias="Domain")
    term_name: str = Field(alias="TermName")
    definition: str = Field(default="", alias="Definition")
    synonyms: list[str] = Field(default_factory=list, alias="Synonyms")
    regulatory_source: Optional[str] = Field(default=None, alias="RegulatorySource")


class ClauseTermMap(CLMBase):
    map_id: UUID = Field(alias="MapID")
    contract_id: UUID = Field(alias="ContractID")
    clause_id: str = Field(alias="ClauseID")
    term_id: str = Field(alias="TermID")
    relevance_score: float = Field(alias="RelevanceScore", ge=0.0, le=1.0)
    extraction_confidence: float = Field(alias="ExtractionConfidence", ge=0.0, le=1.0)
    notes: str = Field(default="", alias="Notes")


# --------------------------------------------------------------------------- #
# AI processing
# --------------------------------------------------------------------------- #
class ExtractionType(str, Enum):
    METADATA = "Metadata"
    CLAUSES = "Clauses"
    TAXONOMY = "Taxonomy"
    SUMMARIES = "Summaries"


class AIExtractionRun(CLMBase):
    run_id: UUID = Field(alias="RunID")
    contract_id: UUID = Field(alias="ContractID")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="Timestamp")
    model_version: str = Field(alias="ModelVersion")
    extraction_types: list[ExtractionType] = Field(default_factory=list, alias="ExtractionType")
    output_url: Optional[str] = Field(default=None, alias="OutputURL")
    confidence_scores: dict[str, float] = Field(default_factory=dict, alias="ConfidenceScores")
    error_log_url: Optional[str] = Field(default=None, alias="ErrorLogURL")
    succeeded: bool = Field(default=True, alias="Succeeded")


# --------------------------------------------------------------------------- #
# Requirement atoms (for clause comparison views)
# --------------------------------------------------------------------------- #
# A clause is decomposed into atomic obligations so comparison happens at the
# right grain: "same requirement, different intensity" and "additional/related
# requirement" both become representable, and intensity becomes orderable.
class Modality(str, Enum):
    MUST = "must"
    MAY = "may"
    MUST_NOT = "must_not"


class RelationshipType(str, Enum):
    IDENTICAL = "identical"
    STRICTER = "stricter"
    LOOSER = "looser"
    SUPERSET = "superset"        # contains the other plus more
    SUBSET = "subset"            # part of a larger obligation
    RELATED = "related"          # intertwined but distinct
    CONFLICTING = "conflicting"


class RequirementAtom(CLMBase):
    atom_id: UUID = Field(alias="AtomID")
    contract_id: UUID = Field(alias="ContractID")
    source_clause_id: str = Field(alias="SourceClauseID")
    canonical_requirement: str = Field(alias="CanonicalRequirement")
    party: Optional[str] = Field(default=None, alias="Party")
    modality: Optional[Modality] = Field(default=None, alias="Modality")
    trigger: Optional[str] = Field(default=None, alias="Trigger")
    # Intensity: a scalar (e.g. 60 days) when quantifiable, plus a rank used to
    # order contracts by strictness for the comparison ladder/marker.
    intensity_value: Optional[float] = Field(default=None, alias="IntensityValue")
    intensity_unit: Optional[str] = Field(default=None, alias="IntensityUnit")
    intensity_rank: Optional[int] = Field(default=None, alias="IntensityRank")
    # Subset/superset hierarchy: this atom is part of a larger obligation.
    scope_parent_atom_id: Optional[UUID] = Field(default=None, alias="ScopeParentAtomID")
    evidence_span: str = Field(default="", alias="EvidenceSpan")


class RequirementRelationship(CLMBase):
    relationship_id: UUID = Field(alias="RelationshipID")
    source_atom_id: UUID = Field(alias="SourceAtomID")
    target_atom_id: UUID = Field(alias="TargetAtomID")
    relationship: RelationshipType = Field(alias="Relationship")
    rationale: str = Field(default="", alias="Rationale")
    # Every model-proposed relationship is a suggestion until a human ratifies it.
    confirmed_by_human: bool = Field(default=False, alias="ConfirmedByHuman")
