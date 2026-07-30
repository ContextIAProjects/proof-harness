"""CLI: envelope shape and stable exit codes (0 ok, 2 domain, 3 dependency)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from proof_harness import cli
from proof_harness.ingest.grafos import ResolvedRefs

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "examples"


class FakeResolver:
    def revision(self) -> str:
        return "git:" + "b" * 40

    def resolve(self, refs: list[str]) -> ResolvedRefs:
        return ResolvedRefs(
            verified=list(refs), index_id="sha256:fedcba9876543210", freshness="fresh"
        )


def _write_inputs(tmp_path: Path) -> tuple[str, str, str]:
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text(
        (EXAMPLES_DIR / "valid" / "trajectory_envelope.success.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    features = tmp_path / "features.json"
    features.write_text(
        (EXAMPLES_DIR / "valid" / "task_features.bounded-implementation.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    outcome = tmp_path / "outcome.json"
    outcome.write_text(
        json.dumps({"success": True, "primary_reward": 1.0, "verifier_results": []}),
        encoding="utf-8",
    )
    return str(trajectory), str(features), str(outcome)


def _run(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    code = cli.main(args)
    return code, json.loads(capsys.readouterr().out)


def test_ingest_happy_path_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "build_resolver", lambda code_root: FakeResolver())
    trajectory, features, outcome = _write_inputs(tmp_path)
    code, envelope = _run(
        [
            "--json",
            "--root",
            str(tmp_path),
            "run",
            "ingest",
            trajectory,
            "--task-features",
            features,
            "--outcome",
            outcome,
            "--ref",
            "context_runtime.services.build_context:build_context",
        ],
        capsys,
    )
    assert code == 0
    assert envelope["schema_version"] == 1
    assert envelope["ok"] is True
    assert envelope["command"] == "run ingest"
    data = envelope["data"]
    assert isinstance(data, dict) and data["created"] is True
    assert (tmp_path / ".proof-harness" / "experience" / "experiences.jsonl").is_file()


def test_unreadable_trajectory_is_a_domain_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, features, outcome = _write_inputs(tmp_path)
    broken = tmp_path / "broken.json"
    broken.write_text("{{{", encoding="utf-8")
    code, envelope = _run(
        ["--json", "--root", str(tmp_path), "run", "ingest", str(broken),
         "--task-features", features, "--outcome", outcome],
        capsys,
    )
    assert code == 2
    assert envelope["ok"] is False
    errors = envelope["errors"]
    assert isinstance(errors, list) and errors[0]["type"] == "validation_error"
    assert not (tmp_path / ".proof-harness").exists()


def test_adapt_writes_artifacts_and_chains_into_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from test_adapter_claude_code import SESSION_ID, _transcript_lines

    monkeypatch.setattr(cli, "build_resolver", lambda code_root: FakeResolver())
    monkeypatch.setattr(cli, "grafos_repo_files", lambda code_root: (53, []))
    transcript = tmp_path / f"{SESSION_ID}.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(r) for r in _transcript_lines()) + "\n", encoding="utf-8"
    )
    declaration = tmp_path / "declaration.json"
    declaration.write_text(
        json.dumps(
            {
                "task_id": "T-201",
                "task_type": "repository_documentation",
                "difficulty": "medium",
                "ambiguity": "low",
                "risk": "low",
                "budget": {"input_tokens": 200000, "output_tokens": 30000},
                "harness_id": "harness-000000",
                "verifiers": ['python -c "print(1)"'],
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    code, envelope = _run(
        ["--json", "--root", str(tmp_path), "run", "adapt", "claude-code",
         str(transcript), "--declaration", str(declaration),
         "--out", str(out_dir), "--code-root", str(tmp_path), "--ingest"],
        capsys,
    )
    assert code == 0
    assert envelope["command"] == "run adapt"
    data = envelope["data"]
    assert isinstance(data, dict)
    assert data["run_id"] == "RUN-20260720-1394110003"
    assert (out_dir / "envelope.json").is_file()
    assert (out_dir / "features.json").is_file()
    assert (out_dir / "outcome.json").is_file()
    ingest = data["ingest"]
    assert isinstance(ingest, dict) and ingest["created"] is True
    assert (tmp_path / ".proof-harness" / "experience" / "experiences.jsonl").is_file()


def test_experience_search_end_to_end_and_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "build_resolver", lambda code_root: FakeResolver())
    trajectory, features, outcome = _write_inputs(tmp_path)
    code, _ = _run(
        ["--json", "--root", str(tmp_path), "run", "ingest", trajectory,
         "--task-features", features, "--outcome", outcome,
         "--ref", "context_runtime.services.build_context:build_context"],
        capsys,
    )
    assert code == 0

    emit_a = tmp_path / "emit-a"
    code, envelope = _run(
        ["--json", "--root", str(tmp_path), "experience", "search",
         "--features", features, "--emit", str(emit_a)],
        capsys,
    )
    assert code == 0
    assert envelope["command"] == "experience search"
    data = envelope["data"]
    assert isinstance(data, dict)
    assert data["successes"] == 1 and data["failures"] == 0 and data["discarded"] == 0
    result = data["result"]
    assert isinstance(result, dict)
    assert result["pinned"]["grafos_index_id"] == "sha256:fedcba9876543210"
    assert (emit_a / "retrieval_result.json").is_file()
    assert (emit_a / "retrieval_result.md").is_file()

    emit_b = tmp_path / "emit-b"
    code, _ = _run(
        ["--json", "--root", str(tmp_path), "experience", "search",
         "--features", features, "--emit", str(emit_b)],
        capsys,
    )
    assert code == 0
    for name in ("retrieval_result.json", "retrieval_result.md"):
        assert (emit_a / name).read_bytes() == (emit_b / name).read_bytes()


def test_experience_search_rejects_a_query_without_task_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "build_resolver", lambda code_root: FakeResolver())
    broken = tmp_path / "query.json"
    broken.write_text(json.dumps({"task_id": "T-900"}), encoding="utf-8")
    code, envelope = _run(
        ["--json", "--root", str(tmp_path), "experience", "search",
         "--features", str(broken)],
        capsys,
    )
    assert code == 2
    assert envelope["ok"] is False
    errors = envelope["errors"]
    assert isinstance(errors, list) and "task_type" in errors[0]["message"]


def test_code_root_without_git_is_a_dependency_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trajectory, features, outcome = _write_inputs(tmp_path)
    bare = tmp_path / "not-a-repo"
    bare.mkdir()
    code, envelope = _run(
        ["--json", "--root", str(tmp_path), "run", "ingest", trajectory,
         "--task-features", features, "--outcome", outcome,
         "--code-root", str(bare)],
        capsys,
    )
    assert code == 3
    assert envelope["ok"] is False
    errors = envelope["errors"]
    assert isinstance(errors, list) and errors[0]["type"] == "dependency_error"
    assert not (tmp_path / ".proof-harness").exists()
