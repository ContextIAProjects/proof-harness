"""Every registered bundle must parse AND carry its correct policy hash."""

from __future__ import annotations

from pathlib import Path

import pytest

from proof_harness.persistence import load_yaml_model
from proof_harness.schemas import HarnessBundle, bundle_content_hash

HARNESSES_DIR = Path(__file__).resolve().parents[1] / "harnesses"

BUNDLE_FILES = sorted(HARNESSES_DIR.rglob("*.yaml"))


def test_registry_is_not_empty() -> None:
    assert BUNDLE_FILES, "the registry must hold at least harness-000000"


@pytest.mark.parametrize("path", BUNDLE_FILES, ids=lambda p: p.name)
def test_bundle_parses_and_hash_is_honest(path: Path) -> None:
    bundle = load_yaml_model(path, HarnessBundle)
    assert bundle.harness_id == path.stem
    assert bundle.content_hash == bundle_content_hash(bundle), (
        "content_hash must equal the canonical policy hash - lifecycle "
        "metadata stays out, policy changes must change it"
    )
