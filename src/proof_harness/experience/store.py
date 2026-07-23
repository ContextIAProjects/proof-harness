"""Append-only, deterministic experience store under ``.proof-harness/``.

Layout:
    runs/trajectories.jsonl      canonical envelopes, one per line
    runs/artifacts/<sha256>.json content-addressed blobs
    experience/experiences.jsonl canonical experiences, one per line
    quarantine/quarantine.jsonl  {schema_version, run_id, ref, reason} lines

No wall-clock timestamps in stored lines: the same inputs produce a
byte-identical store. Logs are the source of truth; anything else is derived.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from proof_harness.canonical import canonical_json, sha256_hex
from proof_harness.errors import ValidationError
from proof_harness.persistence import append_line, atomic_write_text, read_jsonl_lines

STORE_DIRNAME = ".proof-harness"


class ExperienceStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.base = self.root / STORE_DIRNAME
        self.trajectories_path = self.base / "runs" / "trajectories.jsonl"
        self.artifacts_dir = self.base / "runs" / "artifacts"
        self.experiences_path = self.base / "experience" / "experiences.jsonl"
        self.quarantine_path = self.base / "quarantine" / "quarantine.jsonl"

    # -- reads ---------------------------------------------------------------
    def _find_by_run_id(self, path: Path, run_id: str) -> dict[str, Any] | None:
        for number, line in enumerate(read_jsonl_lines(path), start=1):
            try:
                document = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    f"corrupt line {number} in {path.name}: {exc}"
                ) from exc
            if isinstance(document, dict) and document.get("run_id") == run_id:
                return document
        return None

    def find_trajectory(self, run_id: str) -> dict[str, Any] | None:
        return self._find_by_run_id(self.trajectories_path, run_id)

    def find_experience(self, run_id: str) -> dict[str, Any] | None:
        return self._find_by_run_id(self.experiences_path, run_id)

    def artifact_path(self, content_sha256_hex: str) -> Path:
        return self.artifacts_dir / f"{content_sha256_hex}.json"

    # -- writes (append-only) ------------------------------------------------
    def add_artifact(self, canonical_content: str) -> str:
        """Store a blob content-addressed; returns its ``artifact://`` ref.

        Idempotent: an existing blob with the same hash is left untouched.
        """
        digest = sha256_hex(canonical_content)
        path = self.artifact_path(digest)
        if not path.is_file():
            atomic_write_text(path, canonical_content)
        return f"artifact://runs/artifacts/{digest}.json"

    def append_trajectory(self, canonical_envelope: dict[str, Any]) -> None:
        append_line(self.trajectories_path, canonical_json(canonical_envelope))

    def append_quarantine(self, run_id: str, ref: str, reason: str) -> None:
        append_line(
            self.quarantine_path,
            canonical_json(
                {"schema_version": 1, "run_id": run_id, "ref": ref, "reason": reason}
            ),
        )

    def append_experience(self, canonical_experience: dict[str, Any]) -> None:
        append_line(self.experiences_path, canonical_json(canonical_experience))
