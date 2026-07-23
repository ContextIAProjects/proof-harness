"""Canonical serialization and hashing: same input, same bytes, same hash.

Canonical form: JSON with sorted keys, compact separators, no ASCII escaping.
Optional fields whose value is ``None`` are omitted by the model dump layer
("absent means unknown"), which is why no contract field is both required and
nullable.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_dump(model: BaseModel) -> dict[str, Any]:
    """JSON-mode dump with ``None`` values omitted (absent means unknown)."""
    return model.model_dump(mode="json", exclude_none=True)


def canonical_model_json(model: BaseModel) -> str:
    return canonical_json(canonical_dump(model))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prefixed_sha256(text: str) -> str:
    return "sha256:" + sha256_hex(text)
