"""Claude Code adapter: deterministic mapping, honest exclusions, real verifiers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from proof_harness.adapters.claude_code import (
    TaskDeclaration,
    adapt_session,
    build_envelope,
    build_features,
    derive_run_id,
    load_package_context,
    parse_transcript,
)
from proof_harness.canonical import canonical_dump, canonical_json
from proof_harness.errors import ValidationError
from proof_harness.experience.store import ExperienceStore

SESSION_ID = "53186e33-3e1e-4981-935b-425663201367"
GRAFOS_CMD = (
    'grafos --json --read-only query explain '
    '"context_runtime.services.build_context:build_context" --depth 2'
)


def _record(kind: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"type": kind, "sessionId": SESSION_ID, "version": "2.1.209"}
    base.update(extra)
    return base


def _assistant(ts: str, content: list[dict[str, Any]], **usage: int) -> dict[str, Any]:
    defaults = {"input_tokens": 0, "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0, "output_tokens": 0}
    defaults.update(usage)
    return _record(
        "assistant",
        timestamp=ts,
        message={"model": "claude-fable-5", "usage": defaults, "content": content},
    )


def _transcript_lines() -> list[dict[str, Any]]:
    return [
        _record("custom-title", customTitle="fixture"),
        _assistant(
            "2026-07-20T08:00:00.000Z",
            [{"type": "tool_use", "id": "toolu_01", "name": "Bash",
              "input": {"command": GRAFOS_CMD}}],
            input_tokens=100, cache_creation_input_tokens=50,
            cache_read_input_tokens=1000, output_tokens=20,
        ),
        _record("user", timestamp="2026-07-20T08:00:05.000Z", message={
            "content": [{"type": "tool_result", "tool_use_id": "toolu_01",
                         "is_error": False}]}),
        _assistant(
            "2026-07-20T08:00:10.000Z",
            [{"type": "tool_use", "id": "toolu_02", "name": "Edit", "input": {}},
             {"type": "tool_use", "id": "toolu_03", "name": "Bash",
              "input": {"command": "grafos memory for atomic_write_text"}}],
            input_tokens=40, output_tokens=60,
        ),
        _record("user", timestamp="2026-07-20T08:00:20.000Z", message={
            "content": [{"type": "tool_result", "tool_use_id": "toolu_02",
                         "is_error": True}]}),
        # sidechain (subagent) activity must be excluded, and counted
        _record("assistant", isSidechain=True, timestamp="2026-07-20T08:00:25.000Z",
                message={"model": "claude-fable-5",
                         "usage": {"input_tokens": 999, "output_tokens": 999},
                         "content": [{"type": "tool_use", "id": "toolu_99",
                                      "name": "Read", "input": {}}]}),
        _assistant("2026-07-20T08:01:40.000Z", [], output_tokens=30),
    ]


@pytest.fixture
def transcript(tmp_path: Path) -> Path:
    path = tmp_path / f"{SESSION_ID}.jsonl"
    path.write_text(
        "\n".join(json.dumps(record) for record in _transcript_lines()) + "\n",
        encoding="utf-8",
    )
    return path


def _declaration(**overrides: Any) -> TaskDeclaration:
    document: dict[str, Any] = {
        "task_id": "T-201",
        "task_type": "repository_documentation",
        "difficulty": "medium",
        "ambiguity": "low",
        "risk": "low",
        "budget": {"input_tokens": 200000, "output_tokens": 30000},
        "harness_id": "harness-000000",
        # POSIX-style commands resolved via PATH (backslash paths would be
        # mangled by the posix shlex split - same rule as context-runtime checks)
        "verifiers": ['python -c "print(1)"'],
    }
    document.update(overrides)
    return TaskDeclaration.model_validate(document)


def test_mapping_is_the_normative_one(transcript: Path) -> None:
    summary = parse_transcript(transcript)
    assert derive_run_id(summary) == "RUN-20260720-1394110003"  # int('53186e33', 16)

    envelope, warnings = build_envelope(summary, _declaration())
    assert envelope.runner.name == "claude-code"
    assert envelope.runner.version == "2.1.209"
    assert envelope.model.model == "claude-fable-5"
    assert envelope.context is None  # D7: no compiled package drove the session
    assert [e.tool for e in envelope.events] == ["Bash", "Edit", "Bash"]
    assert [e.success for e in envelope.events] == [True, False, False]
    assert envelope.usage.input_tokens == 100 + 50 + 40  # fresh + cache_creation
    assert envelope.usage.cached_input_tokens == 1000
    assert envelope.usage.output_tokens == 20 + 60 + 30
    assert envelope.usage.tool_calls == 3  # sidechain tool excluded
    assert envelope.usage.latency_ms == 100_000  # 08:00:00 -> 08:01:40
    assert any("sidechain" in w for w in warnings)
    assert any("without a recorded result" in w for w in warnings)

    assert summary.grafos_refs == {
        "context_runtime.services.build_context:build_context",
        "atomic_write_text",
    }


def test_powershell_tool_grafos_queries_are_claimed_refs(tmp_path: Path) -> None:
    # The first Windows session (T-502) issued its consultations through the
    # PowerShell tool, not Bash; capture must not depend on which shell tool
    # carried the command.
    command = (
        "grafos query impact dev_scripts.build_zamudio 2>&1 | Out-String; "
        "grafos memory for dev_scripts.build_parke 2>&1 | Out-String"
    )
    lines = [
        _assistant(
            "2026-07-30T12:00:00.000Z",
            [{"type": "tool_use", "id": "toolu_01", "name": "PowerShell",
              "input": {"command": command}}],
        ),
        _record("user", timestamp="2026-07-30T12:00:05.000Z", message={
            "content": [{"type": "tool_result", "tool_use_id": "toolu_01",
                         "is_error": False}]}),
    ]
    path = tmp_path / f"{SESSION_ID}.jsonl"
    path.write_text(
        "\n".join(json.dumps(record) for record in lines) + "\n", encoding="utf-8"
    )

    summary = parse_transcript(path)
    assert summary.grafos_refs == {
        "dev_scripts.build_zamudio",
        "dev_scripts.build_parke",
    }


def _single_command_transcript(tmp_path: Path, command: str) -> Path:
    lines = [
        _assistant(
            "2026-07-30T14:00:00.000Z",
            [{"type": "tool_use", "id": "toolu_01", "name": "PowerShell",
              "input": {"command": command}}],
        ),
        _record("user", timestamp="2026-07-30T14:00:05.000Z", message={
            "content": [{"type": "tool_result", "tool_use_id": "toolu_01",
                         "is_error": False}]}),
    ]
    path = tmp_path / f"{SESSION_ID}.jsonl"
    path.write_text(
        "\n".join(json.dumps(record) for record in lines) + "\n", encoding="utf-8"
    )
    return path


def test_query_path_claims_both_refs(tmp_path: Path) -> None:
    # D6 audit closure #2: `query path A B` carries TWO refs, both claims.
    summary = parse_transcript(_single_command_transcript(
        tmp_path,
        "grafos --json --read-only query path pkg.mod:origin pkg.mod:target --directed",
    ))
    assert summary.grafos_refs == {"pkg.mod:origin", "pkg.mod:target"}


def test_memory_add_refs_are_anchors_not_reads(tmp_path: Path) -> None:
    # D6 audit closure #4, the T-502 real case: a lesson pinned via --refs
    # must get its anchor verified, but never counted as a read.
    summary = parse_transcript(_single_command_transcript(
        tmp_path,
        'grafos memory add --type lesson --text "pal CRLF" '
        "--refs dev_scripts.build_fase_b:pack_tileset 2>&1 | Out-String",
    ))
    assert summary.grafos_anchor_refs == {"dev_scripts.build_fase_b:pack_tileset"}
    assert summary.grafos_refs == set()


def test_memory_add_comma_separated_refs_split(tmp_path: Path) -> None:
    summary = parse_transcript(_single_command_transcript(
        tmp_path,
        "grafos memory add --type decision --text x --refs pkg.a:one,pkg.b:two",
    ))
    assert summary.grafos_anchor_refs == {"pkg.a:one", "pkg.b:two"}


def test_query_runtime_is_a_claimed_ref(tmp_path: Path) -> None:
    # The first package-driven session (T-301) checked its new helper with
    # `grafos query runtime` inside a piped compound; capture must not depend
    # on which ref-bearing query kind the agent picked.
    command = (
        "grafos status 2>&1 | grep edges; "
        "grafos query runtime _replace_with_win32_retry 2>&1 | head -12"
    )
    lines = [
        _assistant(
            "2026-07-23T11:33:23.000Z",
            [{"type": "tool_use", "id": "toolu_01", "name": "Bash",
              "input": {"command": command}}],
        ),
        _record("user", timestamp="2026-07-23T11:33:30.000Z", message={
            "content": [{"type": "tool_result", "tool_use_id": "toolu_01",
                         "is_error": False}]}),
    ]
    path = tmp_path / f"{SESSION_ID}.jsonl"
    path.write_text(
        "\n".join(json.dumps(record) for record in lines) + "\n", encoding="utf-8"
    )

    summary = parse_transcript(path)
    assert summary.grafos_refs == {"_replace_with_win32_retry"}


def test_features_split_derived_vs_declared(transcript: Path) -> None:
    summary = parse_transcript(transcript)
    features = build_features(summary, _declaration(), repo_files=53)
    assert features.requires_tools is True
    assert features.requires_code_change is True  # Edit happened
    assert features.repository_size_bucket.value == "small"
    assert features.changed_files_bucket.value == "unknown"
    assert features.difficulty.value == "medium"  # declared, untouched

    no_index = build_features(summary, _declaration(), repo_files=None)
    assert no_index.repository_size_bucket.value == "unknown"


def test_adapt_session_end_to_end_with_real_verifiers(
    transcript: Path, tmp_path: Path
) -> None:
    store = ExperienceStore(tmp_path / "store")
    result = adapt_session(
        transcript,
        _declaration(),
        store,
        code_root=tmp_path,
        repo_files=53,
    )
    assert result.outcome.success is True
    assert result.outcome.primary_reward == 1.0
    ref = result.outcome.verifier_results[0]
    blob = store.artifact_path(ref.rsplit("/", 1)[1][:-5])
    report = json.loads(blob.read_text(encoding="utf-8"))
    assert report["kind"] == "verifier_report"
    assert report["reports"][0]["passed"] is True
    assert result.claimed_refs == sorted(result.claimed_refs)


def test_failing_verifier_grounds_a_failed_outcome(
    transcript: Path, tmp_path: Path
) -> None:
    store = ExperienceStore(tmp_path / "store")
    declaration = _declaration(verifiers=['python -c "raise SystemExit(3)"'])
    result = adapt_session(
        transcript, declaration, store, code_root=tmp_path, repo_files=None
    )
    assert result.outcome.success is False
    assert result.outcome.primary_reward == 0.0


def test_envelope_and_features_are_deterministic(
    transcript: Path, tmp_path: Path
) -> None:
    stores = [ExperienceStore(tmp_path / "a"), ExperienceStore(tmp_path / "b")]
    dumps = []
    for store in stores:
        result = adapt_session(
            transcript, _declaration(), store, code_root=tmp_path, repo_files=53
        )
        dumps.append(
            (
                canonical_json(canonical_dump(result.envelope)),
                canonical_json(canonical_dump(result.features)),
            )
        )
    assert dumps[0] == dumps[1]


def test_package_identity_travels_verbatim(transcript: Path, tmp_path: Path) -> None:
    package = tmp_path / "context_package.json"
    package.write_text(
        json.dumps(
            {
                "content_hash": "ab" * 32,
                "provider_snapshots": {"grafos": "sha256:0123456789abcdef"},
                "estimated_tokens": 6949,
            }
        ),
        encoding="utf-8",
    )
    context = load_package_context(package)
    result = adapt_session(
        transcript,
        _declaration(),
        ExperienceStore(tmp_path / "store"),
        code_root=tmp_path,
        repo_files=None,
        package_context=context,
    )
    assert result.envelope.context is not None
    assert result.envelope.context.context_hash == "ab" * 32
    assert result.envelope.context.provider_snapshots == {
        "grafos": "sha256:0123456789abcdef"
    }


def test_package_with_broken_identity_is_rejected(tmp_path: Path) -> None:
    package = tmp_path / "broken_package.json"
    package.write_text('{"content_hash": "not-a-hash"}', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_package_context(package)
    with pytest.raises(ValidationError):
        load_package_context(tmp_path / "missing.json")


def test_declaration_requires_at_least_one_verifier() -> None:
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        _declaration(verifiers=[])


def test_transcript_without_activity_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text(json.dumps(_record("custom-title")) + "\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        parse_transcript(path)
