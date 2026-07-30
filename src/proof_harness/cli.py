"""proof-harness CLI: versioned JSON envelope, stable exit codes (0/2/3/4)."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any

from proof_harness import __version__
from proof_harness.adapters.claude_code import (
    adapt_session,
    load_declaration,
    load_package_context,
)
from proof_harness.canonical import canonical_dump
from proof_harness.errors import ProofHarnessError, ValidationError
from proof_harness.experience.search import render_markdown, search_experiences
from proof_harness.experience.store import ExperienceStore
from proof_harness.ingest.grafos import GrafosResolver
from proof_harness.ingest.service import IngestResult, ReferenceResolver, ingest_trajectory
from proof_harness.persistence import atomic_write_text


def build_resolver(code_root: Path) -> ReferenceResolver:
    """Factory seam so tests can substitute the resolver."""
    return GrafosResolver(code_root)


def _load_json_document(path_text: str, label: str) -> Any:
    path = Path(path_text)
    if not path.is_file():
        raise ValidationError(f"{label} file does not exist: {path_text}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{label} is not readable JSON: {exc}") from exc


def _emit(envelope: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True))
        return
    status = "ok" if envelope["ok"] else "error"
    print(f"{status}: {envelope['command']}")
    for key, value in envelope["data"].items():
        print(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    for warning in envelope["warnings"]:
        print(f"warning: {warning}")
    for error in envelope["errors"]:
        print(f"error: {error['message']}")


def _ingest_data(result: IngestResult) -> tuple[dict[str, Any], list[str]]:
    data = {
        "run_id": result.run_id,
        "experience_id": result.experience_id,
        "experience_content_hash": result.experience_content_hash,
        "created": result.created,
        "trajectory_ref": result.trajectory_ref,
        "task_features_ref": result.task_features_ref,
        "verified_refs": result.verified_refs,
        "quarantined": [
            {"ref": ref, "reason": reason} for ref, reason in result.quarantined
        ],
        "grafos_index_id": result.index_id,
        "grafos_freshness": result.freshness,
    }
    return data, list(result.warnings)


def _run_ingest(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    store = ExperienceStore(Path(args.root))
    resolver = build_resolver(Path(args.code_root))
    result = ingest_trajectory(
        store,
        resolver,
        envelope_doc=_load_json_document(args.trajectory, "trajectory"),
        features_doc=_load_json_document(args.task_features, "task features"),
        outcome_doc=_load_json_document(args.outcome, "outcome"),
        claimed_refs=list(args.ref or []),
    )
    return _ingest_data(result)


def _query_fields(document: Any) -> tuple[str, str]:
    """task_id/task_type of the query document (D17): a full TaskFeatures or a
    hand-written declaration both carry them; nothing else is needed."""
    if not isinstance(document, dict):
        raise ValidationError("task features must be a JSON object")
    task_id = document.get("task_id")
    task_type = document.get("task_type")
    if not isinstance(task_id, str) or not task_id:
        raise ValidationError("task features must carry a non-empty task_id")
    if not isinstance(task_type, str) or not task_type:
        raise ValidationError("task features must carry a non-empty task_type")
    return task_id, task_type


def _experience_search(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    store = ExperienceStore(Path(args.root))
    resolver = build_resolver(Path(args.code_root))
    task_id, task_type = _query_fields(
        _load_json_document(args.features, "task features")
    )
    result = search_experiences(
        store,
        resolver,
        task_id=task_id,
        task_type=task_type,
        any_task_type=args.any_task_type,
        strict_validity=args.strict_validity,
    )
    data: dict[str, Any] = {
        "result": canonical_dump(result),
        "successes": len(result.results.successes),
        "failures": len(result.results.failures),
        "discarded": len(result.discarded),
    }
    if args.emit:
        out_dir = Path(args.emit)
        json_path = out_dir / "retrieval_result.json"
        md_path = out_dir / "retrieval_result.md"
        atomic_write_text(
            json_path,
            json.dumps(canonical_dump(result), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        atomic_write_text(md_path, render_markdown(result))
        data["written"] = {"json": str(json_path), "markdown": str(md_path)}
    return data, []


def grafos_repo_files(code_root: Path) -> tuple[int | None, list[str]]:
    """Best-effort file count of the Grafos index (feeds the size bucket)."""
    try:
        files = GrafosResolver(code_root).status_data().get("files")
        return (int(files), []) if isinstance(files, int) else (None, [])
    except ProofHarnessError as exc:
        return None, [f"repository size unknown (grafos status failed: {exc.message})"]


def _run_adapt(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    store = ExperienceStore(Path(args.root))
    declaration = load_declaration(_load_json_document(args.declaration, "task declaration"))
    code_root = Path(args.code_root)
    repo_files, warnings = grafos_repo_files(code_root)
    package_context = (
        load_package_context(Path(args.package)) if args.package else None
    )
    result = adapt_session(
        Path(args.transcript),
        declaration,
        store,
        code_root=code_root,
        repo_files=repo_files,
        package_context=package_context,
    )
    warnings.extend(result.warnings)

    out_dir = Path(args.out)
    written: dict[str, str] = {}
    for name, model in (
        ("envelope", result.envelope),
        ("features", result.features),
        ("outcome", result.outcome),
    ):
        path = out_dir / f"{name}.json"
        atomic_write_text(
            path,
            json.dumps(canonical_dump(model), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        written[name] = str(path)

    data: dict[str, Any] = {
        "run_id": result.envelope.run_id,
        "runner": result.envelope.runner.name,
        "events": len(result.envelope.events),
        "claimed_refs": result.claimed_refs,
        "outcome_success": result.outcome.success,
        "written": written,
    }
    if args.ingest:
        ingest_result = ingest_trajectory(
            store,
            build_resolver(code_root),
            envelope_doc=canonical_dump(result.envelope),
            features_doc=canonical_dump(result.features),
            outcome_doc=canonical_dump(result.outcome),
            claimed_refs=result.claimed_refs,
        )
        ingest_data, ingest_warnings = _ingest_data(ingest_result)
        data["ingest"] = ingest_data
        warnings.extend(ingest_warnings)
    return data, warnings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proof-harness",
        description="Experimental substrate for proof-carrying harness adaptation.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--json", action="store_true", help="emit the JSON envelope")
    parser.add_argument("--debug", action="store_true", help="show tracebacks")
    parser.add_argument(
        "--root", default=".", help="directory holding the .proof-harness store"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run-related commands")
    run_sub = run_parser.add_subparsers(dest="run_command", required=True)
    ingest = run_sub.add_parser(
        "ingest", help="validate, verify and append one trajectory"
    )
    ingest.set_defaults(handler=_run_ingest, command_label="run ingest")
    ingest.add_argument("trajectory", help="TrajectoryEnvelope JSON file")
    ingest.add_argument("--task-features", required=True, help="TaskFeatures JSON file")
    ingest.add_argument("--outcome", required=True, help="Outcome JSON file")
    ingest.add_argument(
        "--ref",
        action="append",
        default=[],
        help="claimed code reference to verify against Grafos (repeatable)",
    )
    ingest.add_argument(
        "--code-root",
        default=".",
        help="Git checkout with a fresh Grafos index the references resolve against",
    )

    adapt = run_sub.add_parser(
        "adapt", help="turn runner telemetry into canonical artifacts"
    )
    adapt.set_defaults(handler=_run_adapt, command_label="run adapt")
    adapt.add_argument("runner_name", choices=["claude-code"], help="source runner")
    adapt.add_argument("transcript", help="session transcript (JSONL)")
    adapt.add_argument(
        "--declaration", required=True, help="task declaration JSON (judgment fields)"
    )
    adapt.add_argument(
        "--out", required=True, help="directory for envelope/features/outcome JSON"
    )
    adapt.add_argument(
        "--code-root",
        default=".",
        help="Git checkout the verifiers run in (and refs resolve against)",
    )
    adapt.add_argument(
        "--package",
        help="compiled context_package.json that drove the session; its "
        "content_hash and provider_snapshots travel verbatim into the envelope",
    )
    adapt.add_argument(
        "--ingest", action="store_true", help="chain straight into run ingest"
    )

    experience_parser = subparsers.add_parser(
        "experience", help="experience bank commands"
    )
    experience_sub = experience_parser.add_subparsers(
        dest="experience_command", required=True
    )
    search = experience_sub.add_parser(
        "search",
        help="retrieve bank experiences with query-time revalidation "
        "(deterministic; never mutates the store)",
    )
    search.set_defaults(handler=_experience_search, command_label="experience search")
    search.add_argument(
        "--features",
        required=True,
        help="TaskFeatures (or declaration) JSON carrying task_id and task_type",
    )
    search.add_argument(
        "--code-root",
        default=".",
        help="Git checkout with a fresh Grafos index validity revalidates against",
    )
    search.add_argument(
        "--any-task-type",
        action="store_true",
        help="relax the task_type hard filter",
    )
    search.add_argument(
        "--strict-validity",
        action="store_true",
        help="discard suspect experiences instead of flagging them",
    )
    search.add_argument(
        "--emit",
        help="directory for retrieval_result.json + retrieval_result.md "
        "(manifest-embeddable render)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    envelope: dict[str, Any] = {
        "schema_version": 1,
        "ok": True,
        "command": args.command_label,
        "data": {},
        "warnings": [],
        "errors": [],
    }
    try:
        data, warnings = args.handler(args)
        envelope["data"] = data
        envelope["warnings"] = warnings
    except ProofHarnessError as exc:
        envelope["ok"] = False
        envelope["errors"] = [
            {"type": exc.error_type, "message": exc.message, "details": exc.details}
        ]
        _emit(envelope, as_json=args.json)
        return exc.exit_code
    except Exception as exc:  # the CLI boundary maps everything unexpected to 4
        envelope["ok"] = False
        envelope["errors"] = [{"type": "internal_error", "message": str(exc)}]
        if args.debug:
            traceback.print_exc(file=sys.stderr)
        _emit(envelope, as_json=args.json)
        return 4
    _emit(envelope, as_json=args.json)
    return 0
