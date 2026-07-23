"""Model bases mirroring the JSON Schema contracts in ``schemas/``.

``StrictModel`` is for nested objects (closed, no schema_version);
``ArtifactModel`` is for the four canonical roots (adds ``schema_version``).
The JSON Schemas are the contract; these models implement it, and
``tests/test_schemas_contract.py`` keeps both layers honest.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

ARTIFACT_URI_PATTERN = r"^artifact://[A-Za-z0-9][A-Za-z0-9/._-]*$"
HARNESS_ID_PATTERN = r"^harness-[0-9]{3,}$"
RUN_ID_PATTERN = r"^RUN-[0-9]{8}-[0-9]{3,}$"
TASK_ID_PATTERN = r"^[A-Z][A-Z0-9]*-[0-9]{3,}$"
OWN_SHA256_PATTERN = r"^sha256:[a-f0-9]{64}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ArtifactModel(StrictModel):
    schema_version: Literal[1] = 1
