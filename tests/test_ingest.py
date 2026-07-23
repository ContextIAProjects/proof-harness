"""Ingest pipeline: determinism, idempotency, quarantine, zero partial state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from proof_harness.errors import ConflictError, ValidationError
from proof_harness.experience.store import ExperienceStore
from proof_harness.ingest.grafos import ResolvedRefs
from proof_harness.ingest.service import ingest_trajectory
from proof_harness.schemas import ExecutionExperience

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "examples"

GOOD_REF = "context_runtime.services.build_context:build_context"
BAD_REF = "context_runtime.services.legacy:old_compile"
INDEX_ID = "sha256:0123456789abcdef"


class FakeResolver:
    """Deterministic stand-in for GrafosResolver (the seam is the point)."""

    def revision(self) -> str:
        return "git:" + "a" * 40

    def resolve(self, refs: list[str]) -> ResolvedRefs:
        resolved = ResolvedRefs(index_id=INDEX_ID, freshness="fresh")
        for ref in refs:
            if ref == BAD_REF:
                resolved.quarantined.append((ref, "not verifiable: no candidates"))
            else:
                resolved.verified.append(ref)
        return resolved


def _load(name: str) -> Any:
    return json.loads((EXAMPLES_DIR / "valid" / name).read_text(encoding="utf-8"))


def _inputs() -> dict[str, Any]:
    return {
        "envelope_doc": _load("trajectory_envelope.success.json"),
        "features_doc": _load("task_features.bounded-implementation.json"),
        "outcome_doc": {"success": True, "primary_reward": 1.0, "verifier_results": []},
    }


def _store_bytes(store: ExperienceStore) -> dict[str, bytes]:
    return {
        path.relative_to(store.base).as_posix(): path.read_bytes()
        for path in sorted(store.base.rglob("*"))
        if path.is_file()
    }


def test_ingest_writes_the_full_chain(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path)
    result = ingest_trajectory(
        store, FakeResolver(), claimed_refs=[GOOD_REF, BAD_REF], **_inputs()
    )

    assert result.created is True
    assert result.verified_refs == [GOOD_REF]
    assert result.quarantined == [(BAD_REF, "not verifiable: no candidates")]

    stored_experience = store.find_experience("RUN-20260723-000001")
    assert stored_experience is not None
    experience = ExecutionExperience.model_validate(stored_experience)
    assert experience.experience_id == result.experience_id
    assert experience.validity.referenced_symbols == [GOOD_REF]
    assert experience.validity.grafos_index_id == INDEX_ID
    assert experience.validity.quarantined_refs is not None
    assert experience.cost.total_tokens == 1200 + 8400 + 950 + 0
    assert experience.cost.non_cached_tokens == 1200 + 950 + 0

    features_blob = store.artifact_path(result.task_features_ref.rsplit("/", 1)[1][:-5])
    assert features_blob.is_file()
    quarantine_lines = store.quarantine_path.read_text(encoding="utf-8").splitlines()
    assert len(quarantine_lines) == 1 and BAD_REF in quarantine_lines[0]


def test_same_inputs_produce_byte_identical_stores(tmp_path: Path) -> None:
    stores = [ExperienceStore(tmp_path / "a"), ExperienceStore(tmp_path / "b")]
    results = [
        ingest_trajectory(store, FakeResolver(), claimed_refs=[GOOD_REF, BAD_REF], **_inputs())
        for store in stores
    ]
    assert results[0].experience_id == results[1].experience_id
    assert results[0].experience_content_hash == results[1].experience_content_hash
    assert _store_bytes(stores[0]) == _store_bytes(stores[1])


def test_reingest_identical_is_idempotent(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path)
    first = ingest_trajectory(store, FakeResolver(), claimed_refs=[GOOD_REF], **_inputs())
    before = _store_bytes(store)

    second = ingest_trajectory(store, FakeResolver(), claimed_refs=[GOOD_REF], **_inputs())
    assert second.created is False
    assert second.experience_id == first.experience_id
    assert second.warnings and "already ingested" in second.warnings[0]
    assert _store_bytes(store) == before, "idempotent re-ingest appends nothing"


def test_same_run_id_different_content_is_a_conflict(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path)
    ingest_trajectory(store, FakeResolver(), claimed_refs=[], **_inputs())
    before = _store_bytes(store)

    tampered = _inputs()
    tampered["envelope_doc"]["usage"]["output_tokens"] += 1
    with pytest.raises(ConflictError):
        ingest_trajectory(store, FakeResolver(), claimed_refs=[], **tampered)
    assert _store_bytes(store) == before, "a conflict must not touch the store"


def test_invalid_envelope_leaves_zero_state(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path)
    broken = _inputs()
    del broken["envelope_doc"]["runner"]
    with pytest.raises(ValidationError):
        ingest_trajectory(store, FakeResolver(), claimed_refs=[], **broken)
    assert not store.base.exists(), "a rejected ingest writes nothing at all"


def test_task_id_mismatch_is_rejected(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path)
    mismatched = _inputs()
    mismatched["features_doc"] = _load("task_features.debugging-high.json")  # T-104
    with pytest.raises(ValidationError):
        ingest_trajectory(store, FakeResolver(), claimed_refs=[], **mismatched)
    assert not store.base.exists()
