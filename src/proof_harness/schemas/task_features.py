from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from proof_harness.schemas.common import TASK_ID_PATTERN, ArtifactModel, StrictModel


class Level(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RepositorySizeBucket(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    UNKNOWN = "unknown"


class ChangedFilesBucket(StrEnum):
    NONE = "none"
    FEW = "few"
    MANY = "many"
    UNKNOWN = "unknown"


class Budget(StrictModel):
    input_tokens: int = Field(ge=1)
    output_tokens: int = Field(ge=1)


class TaskFeatures(ArtifactModel):
    """Information visible BEFORE executing a task; declarative in v1."""

    task_id: str = Field(pattern=TASK_ID_PATTERN)
    task_type: str = Field(min_length=1)
    difficulty: Level
    language: str = Field(min_length=1)
    requires_external_knowledge: bool
    requires_tools: bool
    requires_code_change: bool
    requires_structured_output: bool
    repository_size_bucket: RepositorySizeBucket
    changed_files_bucket: ChangedFilesBucket
    ambiguity: Level
    risk: Level
    budget: Budget
