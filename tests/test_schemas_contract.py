"""The JSON Schemas are the contract; the Pydantic models implement it.

Round-trip check on every example fixture: valid documents must parse with
the model AND their canonical dump must revalidate against the JSON Schema;
invalid documents must be rejected by BOTH layers. Any drift between
``schemas/`` and ``src/proof_harness/schemas/`` fails here.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from proof_harness.canonical import canonical_dump
from proof_harness.errors import ValidationError
from proof_harness.persistence import load_yaml_model
from proof_harness.schemas import (
    ExecutionExperience,
    HarnessBundle,
    RetrievalResult,
    TaskFeatures,
    TrajectoryEnvelope,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = REPO_ROOT / "schemas"
EXAMPLES_DIR = REPO_ROOT / "tests" / "fixtures" / "examples"

MODELS: dict[str, type[BaseModel]] = {
    "harness_bundle": HarnessBundle,
    "task_features": TaskFeatures,
    "trajectory_envelope": TrajectoryEnvelope,
    "execution_experience": ExecutionExperience,
    "retrieval_result": RetrievalResult,
}


def _artifact_of(path: Path) -> str:
    return path.name.split(".")[0]


def _validator_for(artifact: str) -> jsonschema.Draft202012Validator:
    schema = json.loads((SCHEMAS_DIR / f"{artifact}.schema.json").read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def _examples(kind: str) -> list[Path]:
    files = sorted((EXAMPLES_DIR / kind).glob("*.json"))
    assert files, f"no {kind} example fixtures found"
    return files


@pytest.mark.parametrize("path", _examples("valid"), ids=lambda p: p.name)
def test_valid_examples_pass_both_layers(path: Path) -> None:
    artifact = _artifact_of(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    validator = _validator_for(artifact)

    assert not list(validator.iter_errors(document)), "source example must satisfy the schema"
    model = MODELS[artifact].model_validate(document)
    dumped = canonical_dump(model)
    assert not list(validator.iter_errors(dumped)), "canonical dump must satisfy the schema"


@pytest.mark.parametrize("path", _examples("invalid"), ids=lambda p: p.name)
def test_invalid_examples_fail_both_layers(path: Path) -> None:
    artifact = _artifact_of(path)
    document = json.loads(path.read_text(encoding="utf-8"))

    assert list(_validator_for(artifact).iter_errors(document)), "schema must reject it"
    with pytest.raises(PydanticValidationError):
        MODELS[artifact].model_validate(document)


def test_every_schema_has_examples_both_ways() -> None:
    artifacts = {_artifact_of(p) for p in SCHEMAS_DIR.glob("*.schema.json")}
    assert artifacts == set(MODELS)
    for kind in ("valid", "invalid"):
        covered = {_artifact_of(p) for p in _examples(kind)}
        assert covered == artifacts, f"every artifact needs {kind} examples"


def test_bundle_loads_from_yaml(tmp_path: Path) -> None:
    document = json.loads(
        (EXAMPLES_DIR / "valid" / "harness_bundle.baseline.json").read_text(encoding="utf-8")
    )
    yaml_path = tmp_path / "bundle.yaml"
    yaml_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    bundle = load_yaml_model(yaml_path, HarnessBundle)
    assert bundle.harness_id == document["harness_id"]

    yaml_path.write_text("status: [unclosed", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_yaml_model(yaml_path, HarnessBundle)
