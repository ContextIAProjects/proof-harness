from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from proof_harness.schemas.common import (
    ARTIFACT_URI_PATTERN,
    HARNESS_ID_PATTERN,
    RUN_ID_PATTERN,
    ArtifactModel,
    StrictModel,
)


class Dimension(StrEnum):
    """Six MemoHarness dimensions plus three external categories, so not every
    failure is forced inside the harness."""

    CONTEXT = "context"
    TOOLS = "tools"
    GENERATION = "generation"
    ORCHESTRATION = "orchestration"
    MEMORY = "memory"
    OUTPUT = "output"
    MODEL_CAPABILITY = "model_capability"
    ENVIRONMENT = "environment"
    CONTRACT_INPUT = "contract_input"


class ValidityStatus(StrEnum):
    CURRENT = "current"
    SUSPECT = "suspect"
    STALE = "stale"
    INVALIDATED = "invalidated"
    REVALIDATED = "revalidated"


class Outcome(StrictModel):
    success: bool
    primary_reward: float = Field(ge=0, le=1)
    verifier_results: list[str] = Field(default_factory=list)


class Cost(StrictModel):
    total_tokens: int = Field(ge=0)
    non_cached_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    monetary_cost: float | None = Field(default=None, ge=0)


class Diagnosis(StrictModel):
    """Absent in increment 1: ingest does not diagnose (Fase 2)."""

    primary_dimension: Dimension
    secondary_dimensions: list[Dimension] | None = None
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(min_length=1)
    analysis: str | None = Field(default=None, min_length=1)


class QuarantinedRef(StrictModel):
    ref: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Validity(StrictModel):
    repository_revision: str = Field(pattern=r"^git:[a-f0-9]{7,40}$")
    grafos_index_id: str = Field(pattern=r"^sha256:[a-f0-9]{16}$")
    referenced_symbols: list[str] = Field(default_factory=list)
    status: ValidityStatus
    quarantined_refs: list[QuarantinedRef] | None = None


class ExecutionExperience(ArtifactModel):
    """Normalized unit for learning, derived deterministically from an envelope."""

    experience_id: str = Field(pattern=r"^EXP-[0-9]{3,}$")
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    task_features_ref: str = Field(pattern=ARTIFACT_URI_PATTERN)
    harness_id: str = Field(pattern=HARNESS_ID_PATTERN)
    harness_delta_ref: str | None = Field(default=None, pattern=ARTIFACT_URI_PATTERN)
    trajectory_ref: str = Field(pattern=ARTIFACT_URI_PATTERN)
    outcome: Outcome
    cost: Cost
    diagnosis: Diagnosis | None = None
    validity: Validity
