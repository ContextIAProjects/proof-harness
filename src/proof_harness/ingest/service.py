"""Ingest pipeline: validate -> verify references -> normalize -> append.

Deterministic by construction: the experience is a pure function of
(envelope, task features, outcome, verified/quarantined refs, repository
identity). All validation and external verification happen BEFORE the first
write, so a rejected ingest leaves zero partial state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from proof_harness.canonical import canonical_dump, canonical_json, sha256_hex
from proof_harness.errors import ConflictError, ValidationError
from proof_harness.experience.store import ExperienceStore
from proof_harness.ingest.grafos import ResolvedRefs
from proof_harness.schemas import (
    Cost,
    ExecutionExperience,
    Outcome,
    QuarantinedRef,
    TaskFeatures,
    TrajectoryEnvelope,
    Validity,
    ValidityStatus,
)


class ReferenceResolver(Protocol):
    """Seam for verification: the real implementation talks to Grafos."""

    def revision(self) -> str: ...

    def resolve(self, refs: list[str]) -> ResolvedRefs: ...


@dataclass
class IngestResult:
    run_id: str
    experience_id: str
    experience_content_hash: str  # bare hex over the canonical experience line
    created: bool
    trajectory_ref: str
    task_features_ref: str
    verified_refs: list[str]
    quarantined: list[tuple[str, str]]
    index_id: str
    freshness: str
    warnings: list[str] = field(default_factory=list)


def _validate[ModelT: BaseModel](document: Any, model: type[ModelT], label: str) -> ModelT:
    try:
        return model.model_validate(document)
    except PydanticValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        raise ValidationError(
            f"invalid {label}: {location or '<root>'}: {first.get('msg', 'validation failed')}",
            details={"errors": len(exc.errors())},
        ) from exc


def _experience_id(canonical_envelope_json: str) -> str:
    """Deterministic id: decimal of the first 12 hex chars of the envelope hash."""
    return "EXP-" + str(int(sha256_hex(canonical_envelope_json)[:12], 16)).zfill(9)


def _derive_cost(envelope: TrajectoryEnvelope) -> Cost:
    usage = envelope.usage
    non_cached = usage.input_tokens + usage.output_tokens + usage.reasoning_tokens
    return Cost(
        total_tokens=non_cached + usage.cached_input_tokens,
        non_cached_tokens=non_cached,
        latency_ms=usage.latency_ms,
        monetary_cost=None,
    )


STRICT_SHORT_REF_REASON = (
    "short-name ref is ambiguous across repositories (strict refs): "
    "claim it fully qualified as package.module:symbol"
)


def _is_qualified(ref: str) -> bool:
    return ":" in ref


def ingest_trajectory(
    store: ExperienceStore,
    resolver: ReferenceResolver,
    *,
    envelope_doc: Any,
    features_doc: Any,
    outcome_doc: Any,
    claimed_refs: list[str],
    strict_refs: bool = False,
) -> IngestResult:
    envelope = _validate(envelope_doc, TrajectoryEnvelope, "trajectory envelope")
    features = _validate(features_doc, TaskFeatures, "task features")
    outcome = _validate(outcome_doc, Outcome, "outcome")
    if features.task_id != envelope.task_id:
        raise ValidationError(
            "task features and trajectory disagree on task_id",
            details={"features": features.task_id, "trajectory": envelope.task_id},
        )

    canonical_envelope = canonical_dump(envelope)
    envelope_json = canonical_json(canonical_envelope)

    existing = store.find_trajectory(envelope.run_id)
    if existing is not None:
        if canonical_json(existing) != envelope_json:
            raise ConflictError(
                f"run {envelope.run_id} is already ingested with DIFFERENT content; "
                "append-only stores do not overwrite",
                details={"run_id": envelope.run_id},
            )
        stored = store.find_experience(envelope.run_id)
        if stored is None:
            raise ConflictError(
                f"run {envelope.run_id} has a trajectory but no experience "
                "(interrupted ingest); manual review required",
                details={"run_id": envelope.run_id},
            )
        stored_json = canonical_json(stored)
        return IngestResult(
            run_id=envelope.run_id,
            experience_id=str(stored.get("experience_id", "")),
            experience_content_hash=sha256_hex(stored_json),
            created=False,
            trajectory_ref=str(stored.get("trajectory_ref", "")),
            task_features_ref=str(stored.get("task_features_ref", "")),
            verified_refs=list(stored.get("validity", {}).get("referenced_symbols", [])),
            quarantined=[],
            index_id=str(stored.get("validity", {}).get("grafos_index_id", "")),
            freshness="stored",
            warnings=["already ingested with identical content; nothing appended"],
        )

    # External verification before any write. Short (unqualified) refs are the
    # T-401 hazard: an anchor index rebinds them to a LOCAL homonym, producing
    # a plausible but semantically wrong verification. Strict mode quarantines
    # them without ever asking the resolver; default mode resolves but warns.
    claimed = sorted(set(claimed_refs))
    strict_quarantined: list[tuple[str, str]] = []
    if strict_refs:
        strict_quarantined = [
            (ref, STRICT_SHORT_REF_REASON) for ref in claimed if not _is_qualified(ref)
        ]
        claimed = [ref for ref in claimed if _is_qualified(ref)]

    revision = resolver.revision()
    resolved = resolver.resolve(claimed)
    warnings = [
        f"short-name ref {claimed_ref!r} was expanded to {canonical!r} by the "
        "anchor index; claim refs fully qualified in multi-repo sessions"
        for claimed_ref, canonical in resolved.expansions
        if not _is_qualified(claimed_ref)
    ]

    features_json = canonical_json(canonical_dump(features))
    verified = sorted(resolved.verified)
    quarantined = sorted(resolved.quarantined + strict_quarantined)
    experience = ExecutionExperience(
        experience_id=_experience_id(envelope_json),
        run_id=envelope.run_id,
        task_features_ref=f"artifact://runs/artifacts/{sha256_hex(features_json)}.json",
        harness_id=envelope.harness_id,
        trajectory_ref=f"artifact://trajectories/{envelope.run_id}.json",
        outcome=outcome,
        cost=_derive_cost(envelope),
        validity=Validity(
            repository_revision=revision,
            grafos_index_id=resolved.index_id,
            referenced_symbols=verified,
            status=ValidityStatus.CURRENT,
            quarantined_refs=(
                [QuarantinedRef(ref=ref, reason=reason) for ref, reason in quarantined]
                or None
            ),
        ),
    )
    canonical_experience = canonical_dump(experience)
    experience_json = canonical_json(canonical_experience)

    # Writes, experience last as the commit point.
    features_ref = store.add_artifact(features_json)
    store.append_trajectory(canonical_envelope)
    for ref, reason in quarantined:
        store.append_quarantine(envelope.run_id, ref, reason)
    store.append_experience(canonical_experience)

    return IngestResult(
        run_id=envelope.run_id,
        experience_id=experience.experience_id,
        experience_content_hash=sha256_hex(experience_json),
        created=True,
        trajectory_ref=experience.trajectory_ref,
        task_features_ref=features_ref,
        verified_refs=verified,
        quarantined=quarantined,
        index_id=resolved.index_id,
        freshness=resolved.freshness,
        warnings=warnings,
    )
