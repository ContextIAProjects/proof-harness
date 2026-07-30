"""RetrievalResult: the deterministic output of ``experience search``.

Unlike the Outcome (evidence with timings), retrieval is a pure function of
(bank bytes, grafos index, query): the document carries no timestamps and pins
both inputs, so a frozen bank+index must reproduce byte-identical results.
The type itself enforces the Phase 2 acceptance: a stale experience cannot
appear under ``results``, and any non-current status must explain itself.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from proof_harness.schemas.common import (
    ARTIFACT_URI_PATTERN,
    HARNESS_ID_PATTERN,
    RUN_ID_PATTERN,
    ArtifactModel,
    StrictModel,
)

EXPERIENCE_ID_PATTERN = r"^EXP-[0-9]{3,}$"
BANK_CONTENT_HASH_PATTERN = r"^[a-f0-9]{64}$"
GRAFOS_INDEX_ID_PATTERN = r"^sha256:[a-f0-9]{16}$"

Reason = Annotated[str, Field(min_length=1)]


class SearchQuery(StrictModel):
    task_id: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    any_task_type: bool
    strict_validity: bool


class Pinned(StrictModel):
    bank_content_hash: str = Field(pattern=BANK_CONTENT_HASH_PATTERN)
    grafos_index_id: str = Field(pattern=GRAFOS_INDEX_ID_PATTERN)


class RetrievedValidity(StrictModel):
    status: Literal["current", "suspect"]
    reasons: list[Reason]

    @model_validator(mode="after")
    def _non_current_needs_reasons(self) -> RetrievedValidity:
        if self.status != "current" and not self.reasons:
            raise ValueError(f"status {self.status!r} requires at least one reason")
        return self


class DiscardedValidity(StrictModel):
    status: Literal["current", "suspect", "stale"]
    reasons: list[Reason]

    @model_validator(mode="after")
    def _non_current_needs_reasons(self) -> DiscardedValidity:
        if self.status != "current" and not self.reasons:
            raise ValueError(f"status {self.status!r} requires at least one reason")
        return self


class OutcomeSummary(StrictModel):
    success: bool
    primary_reward: float = Field(ge=0, le=1)


class RetrievedExperience(StrictModel):
    experience_id: str = Field(pattern=EXPERIENCE_ID_PATTERN)
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    harness_id: str = Field(pattern=HARNESS_ID_PATTERN)
    task_features_ref: str = Field(pattern=ARTIFACT_URI_PATTERN)
    outcome: OutcomeSummary
    context_present: bool
    effective_validity: RetrievedValidity
    filters_passed: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)


class DiscardedExperience(StrictModel):
    experience_id: str = Field(pattern=EXPERIENCE_ID_PATTERN)
    reason: str = Field(min_length=1)
    effective_validity: DiscardedValidity


class SearchResults(StrictModel):
    successes: list[RetrievedExperience]
    failures: list[RetrievedExperience]


class RetrievalResult(ArtifactModel):
    """Everything a later ``SelectionDecision`` needs to cite: what was
    retrieved, what was discarded and why, against exactly which bank and
    index."""

    query: SearchQuery
    pinned: Pinned
    results: SearchResults
    discarded: list[DiscardedExperience]
