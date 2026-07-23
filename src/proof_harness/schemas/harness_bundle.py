from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from proof_harness.canonical import canonical_json, prefixed_sha256
from proof_harness.schemas.common import (
    HARNESS_ID_PATTERN,
    OWN_SHA256_PATTERN,
    ArtifactModel,
    StrictModel,
)


class BundleStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


class Scope(StrictModel):
    """Empty array means unrestricted on that axis."""

    task_types: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    repositories: list[str] = Field(default_factory=list)


class ContextDimension(StrictModel):
    manifest_policy: str = Field(min_length=1)
    max_context_tokens: int = Field(ge=1)
    compression: bool


class ToolsDimension(StrictModel):
    allowed: list[str] = Field(default_factory=list)
    retrieval_top_k: int = Field(ge=0)
    retry_policy: str = Field(min_length=1)


class GenerationDimension(StrictModel):
    temperature: float = Field(ge=0, le=2)
    max_output_tokens: int = Field(ge=1)


class OrchestrationDimension(StrictModel):
    workflow: str = Field(min_length=1)
    max_steps: int = Field(ge=1)


class MemoryDimension(StrictModel):
    retrieve_successes: int = Field(ge=0)
    retrieve_failures: int = Field(ge=0)
    include_global_patterns: bool


class OutputDimension(StrictModel):
    require_schema: bool
    require_declared_checks: bool
    fallback: str = Field(min_length=1)


class Dimensions(StrictModel):
    context: ContextDimension
    tools: ToolsDimension
    generation: GenerationDimension
    orchestration: OrchestrationDimension
    memory: MemoryDimension
    output: OutputDimension


class Evidence(StrictModel):
    supporting_patterns: list[str] = Field(default_factory=list)
    evaluation_id: str | None = Field(default=None, pattern=r"^EVAL-[0-9]{3,}$")


class HarnessBundle(ArtifactModel):
    """One complete, immutable harness policy."""

    harness_id: str = Field(pattern=HARNESS_ID_PATTERN)
    content_hash: str = Field(pattern=OWN_SHA256_PATTERN)
    parent_harness_id: str | None = Field(default=None, pattern=HARNESS_ID_PATTERN)
    created_at: str = Field(min_length=1)
    status: BundleStatus
    scope: Scope
    dimensions: Dimensions
    evidence: Evidence | None = None


def bundle_content_hash(bundle: HarnessBundle) -> str:
    """Hash of the policy alone: lifecycle and provenance metadata stay out."""
    payload = {
        "schema_version": bundle.schema_version,
        "harness_id": bundle.harness_id,
        "parent_harness_id": bundle.parent_harness_id,
        "scope": bundle.scope.model_dump(mode="json"),
        "dimensions": bundle.dimensions.model_dump(mode="json"),
    }
    return prefixed_sha256(canonical_json(payload))
