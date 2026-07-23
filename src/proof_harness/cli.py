"""proof-harness CLI: versioned JSON envelope, stable exit codes (0/2/3/4)."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any

from proof_harness import __version__
from proof_harness.errors import ProofHarnessError, ValidationError
from proof_harness.experience.store import ExperienceStore
from proof_harness.ingest.grafos import GrafosResolver
from proof_harness.ingest.service import IngestResult, ReferenceResolver, ingest_trajectory


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    command = f"{args.command} {args.run_command}"
    envelope: dict[str, Any] = {
        "schema_version": 1,
        "ok": True,
        "command": command,
        "data": {},
        "warnings": [],
        "errors": [],
    }
    try:
        data, warnings = _run_ingest(args)
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
