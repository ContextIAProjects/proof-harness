from __future__ import annotations

import json
from pathlib import Path

from proof_harness.canonical import canonical_json, prefixed_sha256, sha256_hex
from proof_harness.schemas import BundleStatus, Evidence, HarnessBundle, bundle_content_hash

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "examples"


def _baseline_bundle() -> HarnessBundle:
    document = json.loads(
        (EXAMPLES_DIR / "valid" / "harness_bundle.baseline.json").read_text(encoding="utf-8")
    )
    return HarnessBundle.model_validate(document)


def test_canonical_json_is_sorted_and_compact() -> None:
    assert canonical_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'


def test_hash_helpers_agree() -> None:
    assert prefixed_sha256("x") == "sha256:" + sha256_hex("x")


def test_bundle_hash_covers_policy_only() -> None:
    bundle = _baseline_bundle()
    original = bundle_content_hash(bundle)

    relabeled = bundle.model_copy(
        update={
            "status": BundleStatus.ACTIVE,
            "created_at": "2030-01-01T00:00:00Z",
            "evidence": Evidence(supporting_patterns=["PAT-001"], evaluation_id=None),
        }
    )
    assert bundle_content_hash(relabeled) == original, "lifecycle metadata must not change it"

    retuned = bundle.model_copy(
        update={
            "dimensions": bundle.dimensions.model_copy(
                update={
                    "generation": bundle.dimensions.generation.model_copy(
                        update={"temperature": 0.9}
                    )
                }
            )
        }
    )
    assert bundle_content_hash(retuned) != original, "policy changes must change it"
