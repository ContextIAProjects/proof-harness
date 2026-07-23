"""Atomic writes and append-only JSONL, newline-normalized (house pattern)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml
from pydantic import BaseModel

from proof_harness.errors import ValidationError


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(normalized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def append_line(path: Path, line: str) -> None:
    """Append one pre-serialized line to an append-only log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_yaml_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    """Load a YAML document (e.g. a HarnessBundle) into a contract model."""
    if not path.is_file():
        raise ValidationError(f"required file does not exist: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return model.model_validate(raw)
    except (yaml.YAMLError, ValueError, OSError) as exc:
        raise ValidationError(f"invalid YAML in {path}: {exc}") from exc
