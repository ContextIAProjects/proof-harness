"""Deterministic experience retrieval: hard filters + query-time revalidation.

A pure function of (bank bytes, grafos index, query): the result pins both
inputs and carries no volatile fields, so a frozen bank+index reproduces
byte-identical documents. Validity is recomputed on EVERY search and the
store is never mutated: a referenced symbol that no longer resolves makes the
experience stale (discarded, with reasons); an index that moved while every
symbol still resolves makes it suspect (kept, flagged) — hiding the whole
bank after any commit would make retrieval useless. One resolver batch per
search covers every referenced symbol in the bank, so all verdicts share one
index snapshot. Total order: run_id descending with experience_id tie-break
for results; experience_id descending for discards (they carry no run_id).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from proof_harness.errors import ValidationError
from proof_harness.experience.store import ExperienceStore
from proof_harness.ingest.service import ReferenceResolver
from proof_harness.schemas import (
    DiscardedExperience,
    DiscardedValidity,
    ExecutionExperience,
    OutcomeSummary,
    Pinned,
    RetrievalResult,
    RetrievedExperience,
    RetrievedValidity,
    SearchQuery,
    SearchResults,
    TaskFeatures,
)

VALIDITY_FILTER = "validity"


def _parse[ModelT: BaseModel](document: Any, model: type[ModelT], label: str) -> ModelT:
    try:
        return model.model_validate(document)
    except PydanticValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        raise ValidationError(
            f"invalid {label}: {location or '<root>'}: "
            f"{first.get('msg', 'validation failed')}"
        ) from exc


def _features_of(store: ExperienceStore, experience: ExecutionExperience) -> TaskFeatures:
    blob_name = experience.task_features_ref.rsplit("/", 1)[-1]
    path = store.artifact_path(blob_name.removesuffix(".json"))
    if not path.is_file():
        raise ValidationError(
            f"experience {experience.experience_id} references a missing "
            f"task features blob: {experience.task_features_ref}"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"experience {experience.experience_id} has an unreadable "
            f"task features blob: {exc}"
        ) from exc
    return _parse(document, TaskFeatures, "stored task features")


def _context_present(store: ExperienceStore, experience: ExecutionExperience) -> bool:
    trajectory = store.find_trajectory(experience.run_id)
    if trajectory is None:
        raise ValidationError(
            f"experience {experience.experience_id} has no trajectory line "
            f"for {experience.run_id} (broken chain)"
        )
    return "context" in trajectory


def _discarded(
    experience: ExecutionExperience, reason: str, validity: DiscardedValidity
) -> DiscardedExperience:
    return DiscardedExperience(
        experience_id=experience.experience_id,
        reason=reason,
        effective_validity=validity,
    )


def search_experiences(
    store: ExperienceStore,
    resolver: ReferenceResolver,
    *,
    task_id: str,
    task_type: str,
    any_task_type: bool = False,
    strict_validity: bool = False,
) -> RetrievalResult:
    bank_bytes = store.experiences_bytes()
    experiences = [
        _parse(document, ExecutionExperience, "stored experience")
        for document in store.load_experiences()
    ]

    # ONE batch over every referenced symbol in the bank: every validity
    # verdict below shares the same index snapshot (empty bank still pins it).
    symbols = sorted({s for e in experiences for s in e.validity.referenced_symbols})
    resolved = resolver.resolve(symbols)
    unresolved = dict(resolved.quarantined)

    retrieved: list[RetrievedExperience] = []
    discarded: list[DiscardedExperience] = []
    for experience in experiences:
        missing = [s for s in experience.validity.referenced_symbols if s in unresolved]
        if missing:
            reasons = [f"{symbol}: {unresolved[symbol]}" for symbol in missing]
            discarded.append(
                _discarded(
                    experience,
                    "stale: " + "; ".join(reasons),
                    DiscardedValidity(status="stale", reasons=reasons),
                )
            )
            continue

        if resolved.index_id != experience.validity.grafos_index_id:
            effective = RetrievedValidity(
                status="suspect",
                reasons=[
                    f"grafos index moved since ingest "
                    f"({experience.validity.grafos_index_id} -> {resolved.index_id}) "
                    "while every referenced symbol still resolves"
                ],
            )
        else:
            effective = RetrievedValidity(status="current", reasons=[])

        if strict_validity and effective.status == "suspect":
            discarded.append(
                _discarded(
                    experience,
                    "suspect under strict validity: " + "; ".join(effective.reasons),
                    DiscardedValidity(status="suspect", reasons=effective.reasons),
                )
            )
            continue

        stored_features = _features_of(store, experience)
        if not any_task_type and stored_features.task_type != task_type:
            discarded.append(
                _discarded(
                    experience,
                    f"task_type mismatch ({stored_features.task_type} != {task_type})",
                    DiscardedValidity(
                        status=effective.status, reasons=effective.reasons
                    ),
                )
            )
            continue

        retrieved.append(
            RetrievedExperience(
                experience_id=experience.experience_id,
                run_id=experience.run_id,
                harness_id=experience.harness_id,
                task_features_ref=experience.task_features_ref,
                outcome=OutcomeSummary(
                    success=experience.outcome.success,
                    primary_reward=experience.outcome.primary_reward,
                ),
                context_present=_context_present(store, experience),
                effective_validity=effective,
                filters_passed=[
                    VALIDITY_FILTER,
                    "any_task_type" if any_task_type else f"task_type={task_type}",
                ],
            )
        )

    retrieved.sort(key=lambda r: (r.run_id, r.experience_id), reverse=True)
    discarded.sort(key=lambda d: d.experience_id, reverse=True)
    return RetrievalResult(
        query=SearchQuery(
            task_id=task_id,
            task_type=task_type,
            any_task_type=any_task_type,
            strict_validity=strict_validity,
        ),
        pinned=Pinned(
            bank_content_hash=hashlib.sha256(bank_bytes).hexdigest(),
            grafos_index_id=resolved.index_id,
        ),
        results=SearchResults(
            successes=[r for r in retrieved if r.outcome.success],
            failures=[r for r in retrieved if not r.outcome.success],
        ),
        discarded=discarded,
    )


def render_markdown(result: RetrievalResult) -> str:
    """Deterministic short render for a context manifest to embed as a plain
    ``path`` entry: provenance first, no timestamps, no machine paths."""
    query = result.query
    lines = [
        f"# Retrieved experience for {query.task_id}",
        "",
        f"- query: task_type={query.task_type} "
        f"(any_task_type={str(query.any_task_type).lower()}, "
        f"strict_validity={str(query.strict_validity).lower()})",
        f"- pinned: bank sha256:{result.pinned.bank_content_hash} "
        f"| grafos {result.pinned.grafos_index_id}",
    ]
    for title, items in (
        ("Successes", result.results.successes),
        ("Failures", result.results.failures),
    ):
        lines += ["", f"## {title}", ""]
        if not items:
            lines.append("(none)")
        for item in items:
            context = "compiled package" if item.context_present else "no compiled package"
            lines.append(
                f"- proof-harness://{item.experience_id} ({item.run_id}, "
                f"{item.harness_id}, reward {item.outcome.primary_reward}, "
                f"{context}, validity {item.effective_validity.status})"
            )
            lines.extend(f"  - {reason}" for reason in item.effective_validity.reasons)
            lines.append(f"  - filters: {', '.join(item.filters_passed)}")
    lines += ["", "## Discarded", ""]
    if not result.discarded:
        lines.append("(none)")
    lines.extend(f"- {d.experience_id}: {d.reason}" for d in result.discarded)
    return "\n".join(lines) + "\n"
