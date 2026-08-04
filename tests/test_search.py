"""Search: revalidation verdicts, hard filters, total order, determinism."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from proof_harness.canonical import canonical_model_json
from proof_harness.errors import ValidationError
from proof_harness.experience.search import (
    BankSpec,
    render_markdown,
    render_markdown_multi,
    search_banks,
    search_experiences,
)
from proof_harness.experience.store import ExperienceStore
from proof_harness.ingest.grafos import ResolvedRefs
from proof_harness.ingest.service import ingest_trajectory

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "examples"

REF = "context_runtime.services.build_context:build_context"
INGEST_INDEX = "sha256:0123456789abcdef"
EMPTY_BANK_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class StubResolver:
    """Configurable stand-in: which refs fail to resolve, which index is live."""

    def __init__(self, *, index_id: str = INGEST_INDEX, missing: set[str] | None = None):
        self.index_id = index_id
        self.missing = missing or set()

    def revision(self) -> str:
        return "git:" + "a" * 40

    def resolve(self, refs: list[str]) -> ResolvedRefs:
        resolved = ResolvedRefs(index_id=self.index_id, freshness="fresh")
        for ref in refs:
            if ref in self.missing:
                resolved.quarantined.append((ref, "not verifiable: no candidates"))
            else:
                resolved.verified.append(ref)
        return resolved


def _load(name: str) -> Any:
    return json.loads((EXAMPLES_DIR / "valid" / name).read_text(encoding="utf-8"))


def _ingest(
    store: ExperienceStore,
    *,
    run_id: str,
    task_id: str,
    task_type: str = "bounded_implementation",
    success: bool = True,
    context: bool = True,
    refs: tuple[str, ...] = (REF,),
) -> None:
    envelope = _load("trajectory_envelope.success.json")
    envelope["run_id"] = run_id
    envelope["task_id"] = task_id
    if not context:
        del envelope["context"]
    features = _load("task_features.bounded-implementation.json")
    features["task_id"] = task_id
    features["task_type"] = task_type
    outcome = {
        "success": success,
        "primary_reward": 1.0 if success else 0.0,
        "verifier_results": [],
    }
    ingest_trajectory(
        store,
        StubResolver(),
        envelope_doc=envelope,
        features_doc=features,
        outcome_doc=outcome,
        claimed_refs=list(refs),
    )


def _search(store: ExperienceStore, resolver: StubResolver, **kwargs: Any):
    defaults: dict[str, Any] = {
        "task_id": "T-900",
        "task_type": "bounded_implementation",
    }
    defaults.update(kwargs)
    return search_experiences(store, resolver, **defaults)


def test_current_experience_is_retrieved_with_explanation(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path)
    _ingest(store, run_id="RUN-20260723-000001", task_id="T-001")

    result = _search(store, StubResolver())

    assert [r.effective_validity.status for r in result.results.successes] == ["current"]
    retrieved = result.results.successes[0]
    assert retrieved.filters_passed == ["validity", "task_type=bounded_implementation"]
    assert retrieved.context_present is True
    assert result.results.failures == []
    assert result.discarded == []
    assert result.pinned.grafos_index_id == INGEST_INDEX
    import hashlib

    assert result.pinned.bank_content_hash == hashlib.sha256(
        store.experiences_bytes()
    ).hexdigest()


def test_missing_symbol_is_stale_and_leaves_results(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path)
    _ingest(store, run_id="RUN-20260723-000001", task_id="T-001")

    result = _search(store, StubResolver(missing={REF}))

    assert result.results.successes == [] and result.results.failures == []
    assert len(result.discarded) == 1
    gone = result.discarded[0]
    assert gone.effective_validity.status == "stale"
    assert gone.reason.startswith("stale: ")
    assert REF in gone.effective_validity.reasons[0]


def test_moved_index_is_suspect_but_kept_unless_strict(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path)
    _ingest(store, run_id="RUN-20260723-000001", task_id="T-001")
    moved = StubResolver(index_id="sha256:fedcba9876543210")

    kept = _search(store, moved)
    assert [r.effective_validity.status for r in kept.results.successes] == ["suspect"]
    assert kept.results.successes[0].effective_validity.reasons

    strict = _search(store, moved, strict_validity=True)
    assert strict.results.successes == []
    assert [d.effective_validity.status for d in strict.discarded] == ["suspect"]


def test_task_type_filter_and_any_task_type(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path)
    _ingest(store, run_id="RUN-20260723-000001", task_id="T-001")

    mismatch = _search(store, StubResolver(), task_type="bugfix")
    assert mismatch.results.successes == []
    assert mismatch.discarded[0].reason == (
        "task_type mismatch (bounded_implementation != bugfix)"
    )
    assert mismatch.discarded[0].effective_validity.status == "current"

    relaxed = _search(store, StubResolver(), task_type="bugfix", any_task_type=True)
    assert len(relaxed.results.successes) == 1
    assert relaxed.results.successes[0].filters_passed == ["validity", "any_task_type"]


def test_successes_and_failures_split_with_context_flag(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path)
    _ingest(store, run_id="RUN-20260723-000001", task_id="T-001", success=True)
    _ingest(
        store,
        run_id="RUN-20260724-000002",
        task_id="T-002",
        success=False,
        context=False,
    )

    result = _search(store, StubResolver())

    assert [r.run_id for r in result.results.successes] == ["RUN-20260723-000001"]
    assert [r.run_id for r in result.results.failures] == ["RUN-20260724-000002"]
    assert result.results.successes[0].context_present is True
    assert result.results.failures[0].context_present is False


def test_total_order_is_run_id_descending(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path)
    for day, task in (("23", "T-001"), ("25", "T-003"), ("24", "T-002")):
        _ingest(store, run_id=f"RUN-202607{day}-000001", task_id=task)

    result = _search(store, StubResolver())

    assert [r.run_id for r in result.results.successes] == [
        "RUN-20260725-000001",
        "RUN-20260724-000001",
        "RUN-20260723-000001",
    ]


def test_search_is_deterministic_byte_for_byte(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path)
    _ingest(store, run_id="RUN-20260723-000001", task_id="T-001")
    _ingest(store, run_id="RUN-20260724-000002", task_id="T-002", success=False)
    resolver = StubResolver(index_id="sha256:fedcba9876543210")

    first = canonical_model_json(_search(store, resolver))
    second = canonical_model_json(_search(store, resolver))

    assert first == second


def test_empty_bank_pins_snapshot(tmp_path: Path) -> None:
    result = _search(ExperienceStore(tmp_path), StubResolver())

    assert result.results.successes == [] and result.results.failures == []
    assert result.discarded == []
    assert result.pinned.bank_content_hash == EMPTY_BANK_SHA256
    assert result.pinned.grafos_index_id == INGEST_INDEX


def test_search_never_mutates_the_store(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path)
    _ingest(store, run_id="RUN-20260723-000001", task_id="T-001")
    before = {
        path.relative_to(store.base).as_posix(): path.read_bytes()
        for path in sorted(store.base.rglob("*"))
        if path.is_file()
    }

    _search(store, StubResolver(missing={REF}))
    _search(store, StubResolver(index_id="sha256:fedcba9876543210"))

    after = {
        path.relative_to(store.base).as_posix(): path.read_bytes()
        for path in sorted(store.base.rglob("*"))
        if path.is_file()
    }
    assert after == before


def _two_banks(tmp_path: Path) -> tuple[BankSpec, BankSpec]:
    eco_store = ExperienceStore(tmp_path / "eco")
    _ingest(eco_store, run_id="RUN-20260723-000001", task_id="T-001")
    local_store = ExperienceStore(tmp_path / "local")
    _ingest(local_store, run_id="RUN-20260730-000002", task_id="T-002",
            task_type="refactoring", context=False)
    return (
        BankSpec(label="ecosistema", store=eco_store, resolver=StubResolver()),
        BankSpec(label="local", store=local_store,
                 resolver=StubResolver(index_id="sha256:fedcba9876543210")),
    )


def test_search_banks_composes_the_singles_in_order(tmp_path: Path) -> None:
    eco, local = _two_banks(tmp_path)

    multi = search_banks([eco, local], task_id="T-900",
                         task_type="refactoring", any_task_type=True)

    assert [bank.label for bank in multi.banks] == ["ecosistema", "local"]
    for spec, bank in zip((eco, local), multi.banks, strict=True):
        single = search_experiences(
            spec.store, spec.resolver,
            task_id="T-900", task_type="refactoring", any_task_type=True,
        )
        assert canonical_model_json(bank.result) == canonical_model_json(single), (
            "native composition must equal the manual single, byte for byte"
        )
    assert multi.banks[0].result.pinned.grafos_index_id == INGEST_INDEX
    assert multi.banks[1].result.pinned.grafos_index_id == "sha256:fedcba9876543210"


def test_search_banks_is_deterministic_and_labels_unique(tmp_path: Path) -> None:
    eco, local = _two_banks(tmp_path)

    first = canonical_model_json(
        search_banks([eco, local], task_id="T-900", task_type="refactoring",
                     any_task_type=True)
    )
    second = canonical_model_json(
        search_banks([eco, local], task_id="T-900", task_type="refactoring",
                     any_task_type=True)
    )
    assert first == second

    with pytest.raises(ValidationError):
        search_banks([eco, eco], task_id="T-900", task_type="refactoring")
    with pytest.raises(ValidationError):
        search_banks([], task_id="T-900", task_type="refactoring")


def test_multi_render_has_one_section_per_bank(tmp_path: Path) -> None:
    eco, local = _two_banks(tmp_path)
    multi = search_banks([eco, local], task_id="T-900",
                         task_type="refactoring", any_task_type=True)

    rendered = render_markdown_multi(multi)

    assert rendered.startswith("# Retrieved experience for T-900\n")
    assert "- banks: ecosistema, local" in rendered
    assert "## Bank: ecosistema" in rendered and "## Bank: local" in rendered
    assert rendered.count("- pinned: bank sha256:") == 2
    assert render_markdown_multi(multi) == rendered


def test_markdown_render_is_provenance_first(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path)
    _ingest(store, run_id="RUN-20260723-000001", task_id="T-001")

    result = _search(store, StubResolver())
    rendered = render_markdown(result)

    assert rendered.startswith("# Retrieved experience for T-900\n")
    assert "proof-harness://" + result.results.successes[0].experience_id in rendered
    assert INGEST_INDEX in rendered
    assert result.pinned.bank_content_hash in rendered
    assert render_markdown(_search(store, StubResolver())) == rendered
