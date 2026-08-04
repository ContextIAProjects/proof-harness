"""Claude Code transcript adapter: session JSONL -> canonical artifacts.

Read-only over the transcript (D5). The envelope and the features are pure,
deterministic functions of (transcript, declaration, repo stats) per the
normative mapping (D6/D8); the outcome is EVIDENCE of really running the
declared verifiers at one moment in time, so it carries timings and is not a
pure function. No prompt or tool payload content is ever copied: events keep
only skeleton and counters, and claimed references are the ``grafos`` queries
the agent actually issued during the session.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError

from proof_harness.canonical import canonical_json
from proof_harness.errors import ValidationError
from proof_harness.experience.store import ExperienceStore
from proof_harness.schemas import (
    Budget,
    ChangedFilesBucket,
    ContextRef,
    Level,
    ModelInfo,
    Outcome,
    RepositorySizeBucket,
    Runner,
    TaskFeatures,
    TrajectoryEnvelope,
    TrajectoryEvent,
    Usage,
)
from proof_harness.schemas.common import HARNESS_ID_PATTERN, TASK_ID_PATTERN, StrictModel

RUNNER_NAME = "claude-code"

# Session-level TLS mitigation for this machine: a broken Machine-scoped
# CURL_CA_BUNDLE cannot be removed without admin, so child processes get the
# four TLS variables purged.
TLS_ENV_VARS = ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "PIP_CERT")

CODE_CHANGE_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})

_GRAFOS_QUERY = re.compile(
    r"\bgrafos\b[^\n;&|]*?\bquery\s+"
    r"(?:explain|symbol|impact|callers|callees|runtime)\s+([\"']?)([^\s\"']+)\1"
)
_GRAFOS_PATH = re.compile(
    r"\bgrafos\b[^\n;&|]*?\bquery\s+path\s+"
    r"([\"']?)([^\s\"']+)\1\s+([\"']?)([^\s\"']+)\3"
)
_GRAFOS_MEMORY = re.compile(r"\bgrafos\b[^\n;&|]*?\bmemory\s+for\s+([\"']?)([^\s\"']+)\1")
# Anchor refs are WRITES (the symbols a lesson/decision gets pinned to): they
# deserve verification at ingest like any claim, but travel separately so the
# read-adherence KPI cannot be inflated by them (D6 audit, inc-7).
_GRAFOS_MEMORY_ADD = re.compile(
    r"\bgrafos\b[^\n;&|]*?\bmemory\s+add\b[^\n;&|]*?--refs[\s=]+([\"']?)([^\s\"']+)\1"
)

VERIFIER_TIMEOUT_SECONDS = 600
_OUTPUT_TAIL_CHARS = 2_000


class TaskDeclaration(StrictModel):
    """Human-declared judgment fields the transcript cannot honestly provide."""

    task_id: str = Field(pattern=TASK_ID_PATTERN)
    task_type: str = Field(min_length=1)
    difficulty: Level
    ambiguity: Level
    risk: Level
    requires_external_knowledge: bool = False
    requires_structured_output: bool = False
    language: str = Field(default="python", min_length=1)
    budget: Budget
    harness_id: str = Field(pattern=HARNESS_ID_PATTERN)
    verifiers: list[str] = Field(min_length=1)


@dataclass
class SessionSummary:
    """Deterministic extraction from one transcript (main chain only)."""

    session_id: str = ""
    runner_version: str | None = None
    model: str | None = None
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    tool_uses: list[tuple[str, str]] = field(default_factory=list)  # (tool_use_id, name)
    tool_results: dict[str, bool] = field(default_factory=dict)  # id -> is_error
    grafos_refs: set[str] = field(default_factory=set)  # reads (queries)
    grafos_anchor_refs: set[str] = field(default_factory=set)  # writes (memory add --refs)
    sidechain_records: int = 0


@dataclass
class AdaptResult:
    envelope: TrajectoryEnvelope
    features: TaskFeatures
    outcome: Outcome
    claimed_refs: list[str]  # reads only - the read-adherence KPI numerator
    anchor_refs: list[str]  # memory-add anchors - verified, never counted as reads
    warnings: list[str]


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_transcript(path: Path) -> SessionSummary:
    if not path.is_file():
        raise ValidationError(f"transcript does not exist: {path}")
    summary = SessionSummary()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"transcript line {number} is not JSON: {exc}") from exc
        if not isinstance(record, dict):
            continue
        if not summary.session_id and isinstance(record.get("sessionId"), str):
            summary.session_id = record["sessionId"]
        if summary.runner_version is None and isinstance(record.get("version"), str):
            summary.runner_version = record["version"]
        if record.get("isSidechain"):
            summary.sidechain_records += 1
            continue

        stamp = _parse_timestamp(record.get("timestamp"))
        if stamp is not None:
            if summary.first_timestamp is None or stamp < summary.first_timestamp:
                summary.first_timestamp = stamp
            if summary.last_timestamp is None or stamp > summary.last_timestamp:
                summary.last_timestamp = stamp

        message = record.get("message")
        if not isinstance(message, dict):
            continue
        record_type = record.get("type")
        if record_type == "assistant":
            usage = message.get("usage")
            if isinstance(usage, dict):
                summary.input_tokens += int(usage.get("input_tokens") or 0)
                summary.input_tokens += int(usage.get("cache_creation_input_tokens") or 0)
                summary.cached_input_tokens += int(usage.get("cache_read_input_tokens") or 0)
                summary.output_tokens += int(usage.get("output_tokens") or 0)
            if summary.model is None and isinstance(message.get("model"), str):
                summary.model = message["model"]
            for block in message.get("content") or []:
                if not (isinstance(block, dict) and block.get("type") == "tool_use"):
                    continue
                tool_use_id = str(block.get("id") or f"missing-{number}")
                name = str(block.get("name") or "unknown")
                summary.tool_uses.append((tool_use_id, name))
                # Any command-bearing tool counts (Bash on POSIX, PowerShell
                # on Windows): the claim is what the agent consulted, not
                # which shell carried it.
                command = block.get("input", {}).get("command")
                if isinstance(command, str):
                    for pattern in (_GRAFOS_QUERY, _GRAFOS_MEMORY):
                        for match in pattern.finditer(command):
                            summary.grafos_refs.add(match.group(2))
                    for match in _GRAFOS_PATH.finditer(command):
                        summary.grafos_refs.add(match.group(2))
                        summary.grafos_refs.add(match.group(4))
                    for match in _GRAFOS_MEMORY_ADD.finditer(command):
                        for ref in match.group(2).split(","):
                            if ref:
                                summary.grafos_anchor_refs.add(ref)
        elif record_type == "user":
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    result_id = str(block.get("tool_use_id") or "")
                    if result_id:
                        summary.tool_results[result_id] = bool(block.get("is_error"))
    if not summary.session_id:
        raise ValidationError("transcript carries no sessionId")
    if summary.first_timestamp is None or summary.model is None:
        raise ValidationError("transcript has no main-chain assistant activity")
    return summary


def derive_run_id(summary: SessionSummary) -> str:
    """``RUN-YYYYMMDD-<decimal of the first 8 hex of sessionId>`` (no counters)."""
    assert summary.first_timestamp is not None
    hex_prefix = summary.session_id.replace("-", "")[:8]
    try:
        decimal = int(hex_prefix, 16)
    except ValueError as exc:
        raise ValidationError(f"sessionId is not hex-prefixed: {summary.session_id}") from exc
    return f"RUN-{summary.first_timestamp:%Y%m%d}-{str(decimal).zfill(3)}"


def load_package_context(path: Path) -> ContextRef:
    """Verbatim identity of a compiled context-runtime package.

    Reads ``content_hash`` and ``provider_snapshots`` exactly as the package
    states them; anything malformed is an error, never a sentinel.
    """
    if not path.is_file():
        raise ValidationError(f"context package does not exist: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"context package is not readable JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValidationError("context package is not a JSON object")
    content_hash = document.get("content_hash")
    snapshots = document.get("provider_snapshots")
    try:
        return ContextRef(
            context_hash=content_hash if isinstance(content_hash, str) else "",
            provider_snapshots=snapshots if isinstance(snapshots, dict) else {},
        )
    except PydanticValidationError as exc:
        first = exc.errors()[0]
        raise ValidationError(
            f"context package carries an invalid identity: {first.get('msg')}"
        ) from exc


def build_envelope(
    summary: SessionSummary,
    declaration: TaskDeclaration,
    context: ContextRef | None = None,
) -> tuple[TrajectoryEnvelope, list[str]]:
    warnings: list[str] = []
    if summary.sidechain_records:
        warnings.append(
            f"{summary.sidechain_records} sidechain (subagent) records excluded (v1)"
        )
    events: list[TrajectoryEvent] = []
    unpaired = 0
    for index, (tool_use_id, name) in enumerate(summary.tool_uses, start=1):
        if tool_use_id in summary.tool_results:
            success = not summary.tool_results[tool_use_id]
        else:
            success = False
            unpaired += 1
        events.append(
            TrajectoryEvent(
                event_id=f"EVT-{index:03d}", kind="tool_call", tool=name, success=success
            )
        )
    if unpaired:
        warnings.append(f"{unpaired} tool calls without a recorded result (marked failed)")
    assert summary.first_timestamp is not None and summary.last_timestamp is not None
    latency_ms = int(
        (summary.last_timestamp - summary.first_timestamp).total_seconds() * 1_000
    )
    model = summary.model or "unknown"
    envelope = TrajectoryEnvelope(
        run_id=derive_run_id(summary),
        task_id=declaration.task_id,
        harness_id=declaration.harness_id,
        runner=Runner(name=RUNNER_NAME, version=summary.runner_version),
        model=ModelInfo(provider="anthropic", model=model, snapshot=model),
        # None = no compiled package drove this session (D7); populated
        # verbatim from the package when the caller provides one.
        context=context,
        events=events,
        artifacts=[],
        usage=Usage(
            input_tokens=summary.input_tokens,
            cached_input_tokens=summary.cached_input_tokens,
            output_tokens=summary.output_tokens,
            reasoning_tokens=0,  # the transcript does not separate them; not invented
            tool_calls=len(summary.tool_uses),
            latency_ms=max(latency_ms, 0),
        ),
    )
    return envelope, warnings


def _size_bucket(repo_files: int | None) -> RepositorySizeBucket:
    if repo_files is None:
        return RepositorySizeBucket.UNKNOWN
    if repo_files < 100:
        return RepositorySizeBucket.SMALL
    if repo_files <= 1_000:
        return RepositorySizeBucket.MEDIUM
    return RepositorySizeBucket.LARGE


def build_features(
    summary: SessionSummary, declaration: TaskDeclaration, repo_files: int | None
) -> TaskFeatures:
    tool_names = {name for _, name in summary.tool_uses}
    return TaskFeatures(
        task_id=declaration.task_id,
        task_type=declaration.task_type,
        difficulty=declaration.difficulty,
        language="python" if repo_files else declaration.language,
        requires_external_knowledge=declaration.requires_external_knowledge,
        requires_tools=bool(summary.tool_uses),
        requires_code_change=bool(tool_names & CODE_CHANGE_TOOLS),
        requires_structured_output=declaration.requires_structured_output,
        repository_size_bucket=_size_bucket(repo_files),
        changed_files_bucket=ChangedFilesBucket.UNKNOWN,  # transcript records no base commit
        ambiguity=declaration.ambiguity,
        risk=declaration.risk,
        budget=declaration.budget,
    )


def _verifier_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in TLS_ENV_VARS:
        env.pop(name, None)
    return env


def run_verifiers(
    code_root: Path,
    commands: list[str],
    store: ExperienceStore,
    *,
    timeout: int = VERIFIER_TIMEOUT_SECONDS,
) -> Outcome:
    """Execute the declared verifiers for real; the report becomes an artifact.

    Evidence, not a pure function: reports carry durations and tool output.
    """
    reports: list[dict[str, object]] = []
    all_passed = True
    for command in commands:
        argv = shlex.split(command, posix=True)
        if not argv:
            raise ValidationError("empty verifier command in the declaration")
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                argv,
                cwd=code_root,
                env=_verifier_env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout,
            )
            returncode: int | None = completed.returncode
            stdout_tail = completed.stdout[-_OUTPUT_TAIL_CHARS:]
            stderr_tail = completed.stderr[-_OUTPUT_TAIL_CHARS:]
        except (OSError, subprocess.TimeoutExpired) as exc:
            returncode = None
            stdout_tail = ""
            stderr_tail = str(exc)[:_OUTPUT_TAIL_CHARS]
        passed = returncode == 0
        all_passed = all_passed and passed
        reports.append(
            {
                "command": command,
                "returncode": returncode,
                "passed": passed,
                "duration_ms": round((time.perf_counter() - started) * 1_000),
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            }
        )
    report_ref = store.add_artifact(
        canonical_json({"schema_version": 1, "kind": "verifier_report", "reports": reports})
    )
    return Outcome(
        success=all_passed,
        primary_reward=1.0 if all_passed else 0.0,
        verifier_results=[report_ref],
    )


def load_declaration(document: object) -> TaskDeclaration:
    try:
        return TaskDeclaration.model_validate(document)
    except PydanticValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        raise ValidationError(
            f"invalid task declaration: {location or '<root>'}: "
            f"{first.get('msg', 'validation failed')}"
        ) from exc


def adapt_session(
    transcript: Path,
    declaration: TaskDeclaration,
    store: ExperienceStore,
    *,
    code_root: Path,
    repo_files: int | None,
    package_context: ContextRef | None = None,
    verifier_timeout: int = VERIFIER_TIMEOUT_SECONDS,
) -> AdaptResult:
    summary = parse_transcript(transcript)
    envelope, warnings = build_envelope(summary, declaration, package_context)
    features = build_features(summary, declaration, repo_files)
    outcome = run_verifiers(
        code_root, declaration.verifiers, store, timeout=verifier_timeout
    )
    return AdaptResult(
        envelope=envelope,
        features=features,
        outcome=outcome,
        claimed_refs=sorted(summary.grafos_refs),
        anchor_refs=sorted(summary.grafos_anchor_refs),
        warnings=warnings,
    )
