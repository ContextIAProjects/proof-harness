# proof-harness

**The experimental substrate for proof-carrying harness adaptation: canonical
run artifacts, an append-only experience store, and code references verified
against a [Grafos](https://github.com/ContextIAProjects/grafos) index. No
adaptive behavior yet — by design.**

Long-horizon coding agents accumulate experience that is usually opaque:
prompts tweaked in place, lessons buried in transcripts, rules nobody can
audit. This project explores the opposite contract: **every learned artifact
must carry verifiable evidence, provenance, scope, validity and a rollback
path**. Before any learning happens, the substrate has to exist — that is what
this repository is, and *all* it is today.

## What increment 1 provides

- **Four canonical artifacts** as JSON Schema v1 (the contract) mirrored by
  Pydantic models (the implementation), cross-checked by tests:
  [`HarnessBundle`](schemas/harness_bundle.schema.json) ·
  [`TaskFeatures`](schemas/task_features.schema.json) ·
  [`TrajectoryEnvelope`](schemas/trajectory_envelope.schema.json) ·
  [`ExecutionExperience`](schemas/execution_experience.schema.json).
- **`proof-harness run ingest`** — validates a trajectory, verifies its
  claimed code references against a fresh Grafos index (public JSON CLI only,
  never its database), normalizes it into a deterministic
  `ExecutionExperience`, and appends everything to an append-only store.
  References that do not resolve go to a local quarantine, never into the
  experience.
- **Runner-agnostic by contract.** A trajectory declares its execution surface
  (`runner: {name, version}` — `claude-code`, `codex`, `opencode`,
  `synthetic-fixture`, …); nothing in the schema assumes one provider's event
  format. Each future runner plugs in via a telemetry→envelope adapter.
- **Determinism.** Same inputs → byte-identical store. Canonical JSON (sorted
  keys, compact separators), no wall-clock timestamps in stored lines,
  content-addressed artifacts, and a derived, deterministic `experience_id`.

## What it deliberately does NOT do yet

No selector, no pattern distillation, no promotion, no model calls, no live
runner adapters. Those arrive in later increments, each behind its own
evaluation gate. Until then, any claim of "learning" would be a lie, so this
README does not make it.

## Install

```bash
uv sync --dev            # Python >= 3.12
uv run proof-harness --help
```

## Quickstart

```bash
proof-harness run ingest trajectory.json \
  --task-features features.json \
  --outcome outcome.json \
  --ref context_runtime.services.build_context:build_context \
  --code-root ../context_runtime
```

The command needs a Git checkout with a **fresh Grafos index** at
`--code-root` (run `grafos index` there first): the resulting experience pins
`repository_revision` and `grafos_index_id`, and each `--ref` is verified
against that snapshot. Verified references land in
`validity.referenced_symbols`; unverifiable ones land in quarantine with a
reason — quarantining never deletes.

The store lives under `.proof-harness/` (gitignored, append-only):

```
.proof-harness/
├── runs/trajectories.jsonl     # raw envelopes, canonical, append-only
├── runs/artifacts/<sha256>.json# content-addressed blobs (e.g. task features)
├── experience/experiences.jsonl# normalized experiences, canonical
└── quarantine/quarantine.jsonl # {run_id, ref, reason} lines
```

Every command speaks the ecosystem envelope under `--json`
(`schema_version/ok/command/data/warnings/errors`) with stable exit codes:
`0` ok · `2` domain/validation error · `3` external dependency (missing Git
repo, missing/stale Grafos index) · `4` internal.

Re-ingesting the same trajectory is idempotent: identical content returns the
stored experience with a warning; same `run_id` with different content is a
conflict, not a silent overwrite.

## Development

```bash
uv run pytest
uv run ruff check .
uv run mypy src        # strict
```

`docs/` (Spanish) documents the data model rules: what enters each canonical
hash, why external identifiers travel verbatim, and the validity/quarantine
semantics.

## License

MIT. See [LICENSE](LICENSE).
