"""Code-reference verification against a Grafos index (public JSON CLI only).

Mirrors the ecosystem's consumer rules: capability negotiation instead of
version pinning, a SINGLE ``query batch`` per ingest so every verification
shares one ``index_id``, ``--read-only --require-fresh`` so ingest can neither
mutate the index nor silently accept a stale one, and never touching
``.grafos/grafos.db``. This module never writes anything into Grafos.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from proof_harness.errors import DependencyError

PROTOCOL_VERSION = 1
BATCH_OP = "memory"  # cheapest op that resolves a reference (or fails per item)

_EXIT_HINTS = {
    2: "Grafos rejected the input as invalid.",
    3: "The Grafos index is missing or stale; run `grafos index` at the code root and retry.",
    4: "Grafos internal failure.",
}


@dataclass
class ResolvedRefs:
    verified: list[str] = field(default_factory=list)
    quarantined: list[tuple[str, str]] = field(default_factory=list)  # (ref, reason)
    expansions: list[tuple[str, str]] = field(default_factory=list)  # (claimed, canonical)
    index_id: str = ""
    freshness: str = "unknown"


class GrafosResolver:
    """Verifies claimed symbol references and pins repository identity."""

    def __init__(self, code_root: Path, *, executable: str = "grafos", timeout: int = 30) -> None:
        self.code_root = code_root.resolve()
        self.executable = executable
        self.timeout = timeout

    # -- repository identity -------------------------------------------------
    def revision(self) -> str:
        """``git:<sha>`` of the code root; a non-repo is a dependency error."""
        try:
            result = subprocess.run(
                ["git", "-C", str(self.code_root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DependencyError(f"git is not available: {exc}") from exc
        if result.returncode != 0:
            raise DependencyError(
                "the code root is not a Git repository; validity needs a revision to pin",
                details={"code_root": str(self.code_root)},
            )
        return f"git:{result.stdout.strip()}"

    # -- grafos plumbing -----------------------------------------------------
    def _run(self, arguments: list[str], *, read_only: bool = True,
             require_fresh: bool = False) -> str:
        executable = shutil.which(self.executable)
        if executable is None:
            raise DependencyError(
                "grafos is not installed or not on PATH.",
                details={"executable": self.executable},
            )
        command = [executable, "--root", str(self.code_root), "--json"]
        if read_only:
            command.append("--read-only")
        if require_fresh:
            command.append("--require-fresh")
        command.extend(arguments)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise DependencyError("Grafos timed out.") from exc
        if result.returncode != 0:
            raise DependencyError(
                _EXIT_HINTS.get(result.returncode, "Grafos returned an error."),
                details={"returncode": result.returncode,
                         "detail": result.stderr.strip() or result.stdout.strip()[:500]},
            )
        return result.stdout

    @staticmethod
    def _parse_envelope(raw: str, *, context: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DependencyError(f"Grafos returned invalid JSON in {context}.") from exc
        if not isinstance(payload, dict) or "data" not in payload or "meta" not in payload:
            raise DependencyError(f"Grafos did not return the protocol envelope in {context}.")
        if not payload.get("ok", False):
            errors = payload.get("errors") or ["unknown error"]
            raise DependencyError(
                f"Grafos reported errors in {context}.", details={"errors": errors}
            )
        return payload

    def _capabilities(self) -> None:
        raw = self._run(["capabilities"], read_only=False)
        envelope = self._parse_envelope(raw, context="capabilities")
        protocol = envelope["meta"].get("protocol_version")
        if protocol != PROTOCOL_VERSION:
            raise DependencyError(
                f"incompatible Grafos protocol: {protocol!r}",
                details={"supported": PROTOCOL_VERSION},
            )
        data = envelope["data"]
        batch = data.get("batch") if isinstance(data, dict) else None
        offered = set((batch or {}).get("ops") or [])
        if BATCH_OP not in offered:
            raise DependencyError(
                f"Grafos does not offer the required batch op {BATCH_OP!r}.",
                details={"offered": sorted(offered)},
            )

    def _snapshot_meta(self, meta: dict[str, Any], *, context: str) -> tuple[str, str]:
        index_id = str(meta.get("index_id") or "")
        freshness = str(meta.get("freshness") or "unknown")
        if not index_id:
            raise DependencyError(f"Grafos did not report an index_id in {context}.")
        if freshness != "fresh":
            raise DependencyError(
                f"the Grafos index is not fresh ({freshness}); run `grafos index` and retry.",
                details={"freshness": freshness},
            )
        return index_id, freshness

    # -- public API ----------------------------------------------------------
    def status_data(self) -> dict[str, Any]:
        """Raw ``status`` data of the index (read-only; e.g. file counts)."""
        raw = self._run(["status"], read_only=True)
        envelope = self._parse_envelope(raw, context="status")
        data = envelope.get("data")
        return data if isinstance(data, dict) else {}

    def resolve(self, refs: list[str]) -> ResolvedRefs:
        """Verify claimed refs in ONE batch over ONE snapshot; empty refs still
        pin the snapshot via ``status``."""
        self._capabilities()
        if not refs:
            raw = self._run(["status"], read_only=True)
            envelope = self._parse_envelope(raw, context="status")
            index_id, freshness = self._snapshot_meta(envelope["meta"], context="status")
            return ResolvedRefs(index_id=index_id, freshness=freshness)

        manifest = {"queries": [{"id": f"q{i}", "op": BATCH_OP, "ref": ref}
                                for i, ref in enumerate(refs)]}
        descriptor, path = tempfile.mkstemp(prefix="proof-harness-batch-", suffix=".json")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, ensure_ascii=False)
            raw = self._run(["query", "batch", "--input", path],
                            read_only=True, require_fresh=True)
        finally:
            Path(path).unlink(missing_ok=True)
        envelope = self._parse_envelope(raw, context="query batch")
        index_id, freshness = self._snapshot_meta(envelope["meta"], context="query batch")

        items = {str(item.get("id")): item
                 for item in envelope["data"].get("results", [])}
        resolved = ResolvedRefs(index_id=index_id, freshness=freshness)
        for i, ref in enumerate(refs):
            item = items.get(f"q{i}")
            if item is None:
                resolved.quarantined.append((ref, "missing from the batch response"))
                continue
            if item.get("ok", False):
                data = item.get("data")
                canonical_id = data.get("id") if isinstance(data, dict) else None
                canonical = str(canonical_id) if canonical_id else ref
                resolved.verified.append(canonical)
                if canonical != ref:
                    resolved.expansions.append((ref, canonical))
            else:
                errors = item.get("errors") or ["unverifiable reference"]
                resolved.quarantined.append((ref, "; ".join(str(e) for e in errors)))
        return resolved
