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
