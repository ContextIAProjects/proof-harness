from __future__ import annotations

import re

from pydantic import Field, field_validator

from proof_harness.schemas.common import (
    ARTIFACT_URI_PATTERN,
    HARNESS_ID_PATTERN,
    OWN_SHA256_PATTERN,
    RUN_ID_PATTERN,
    TASK_ID_PATTERN,
    ArtifactModel,
    StrictModel,
)

_SNAPSHOT_KEY = re.compile(r"^[a-z][a-z0-9_-]*$")


class Runner(StrictModel):
    """The execution surface that produced this trajectory.

    Free identifier by contract (claude-code, codex, opencode,
    synthetic-fixture, ...): nothing in the envelope may assume one
    provider's event format.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9._-]*$")
    version: str | None = Field(default=None, min_length=1)


class ModelInfo(StrictModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    snapshot: str = Field(min_length=1)


class ContextRef(StrictModel):
    """Identity of the compiled context package, external ids verbatim."""

    context_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_snapshots: dict[str, str] = Field(default_factory=dict)

    @field_validator("provider_snapshots")
    @classmethod
    def validate_snapshots(cls, value: dict[str, str]) -> dict[str, str]:
        for key, snapshot_id in value.items():
            if not _SNAPSHOT_KEY.match(key):
                raise ValueError(f"invalid provider snapshot key: {key!r}")
            if not snapshot_id:
                raise ValueError(f"empty snapshot id for provider {key!r}")
        return value


class TrajectoryEvent(StrictModel):
    event_id: str = Field(pattern=r"^EVT-[0-9]{3,}$")
    kind: str = Field(min_length=1)
    tool: str | None = Field(default=None, min_length=1)
    input_ref: str | None = Field(default=None, pattern=ARTIFACT_URI_PATTERN)
    output_ref: str | None = Field(default=None, pattern=ARTIFACT_URI_PATTERN)
    success: bool


class TrajectoryArtifact(StrictModel):
    ref: str = Field(pattern=ARTIFACT_URI_PATTERN)
    sha256: str = Field(pattern=OWN_SHA256_PATTERN)
    description: str | None = Field(default=None, min_length=1)


class Usage(StrictModel):
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    latency_ms: int = Field(ge=0)


class TrajectoryEnvelope(ArtifactModel):
    """Raw record of one execution, runner-agnostic by contract."""

    run_id: str = Field(pattern=RUN_ID_PATTERN)
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    harness_id: str = Field(pattern=HARNESS_ID_PATTERN)
    runner: Runner
    model: ModelInfo
    context: ContextRef
    events: list[TrajectoryEvent] = Field(default_factory=list)
    artifacts: list[TrajectoryArtifact] = Field(default_factory=list)
    usage: Usage
