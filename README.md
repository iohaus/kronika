# kronika — Autonomous Metadata Decision Engine for DataHub

## Overview

DataHub already knows how your data connects — which table feeds which dashboard,
which column belongs to which report, who owns what. What it doesn't know is what
to *do* when something goes wrong. A quality check fails, an owner changes, someone
proposes a schema migration — DataHub can show you the graph, but it can't tell you
what's actually at risk, what's safe to leave running, or who needs to know. Today
that's still a person, tracing lineage by hand, usually mid-incident, usually
guessing — and usually landing on one of two bad options: halt the whole pipeline
and take out things that were never actually affected, or do nothing and hope.

Kronika closes that gap. It reads DataHub's own context graph — lineage, tags,
ownership, governance rules, quality signals — and computes, in real time, exactly
which downstream assets are genuinely affected by an event, what the safest response
is, and why. Then it writes that decision straight back into DataHub as real
incidents, tags, and governance rules, so the next engineer — or the next agent —
inherits the answer instead of re-deriving it from scratch.

Every recommendation ships with its evidence: which rule was violated, which lineage
path carried the impact, which assets are genuinely clear. Nothing is flagged
without a reason a human can check.

## Features

- **Precision containment, not a circuit breaker.** The same quality event can leave
  one downstream mart untouched while flagging another for containment — because
  Kronika traces the actual lineage path and column-level relevance, not just "is
  this downstream at all."
- **Lives on DataHub's real context graph.** Reads lineage, tags, ownership, and
  glossary terms straight from a running DataHub instance via the DataHub Agent
  Context Kit and GraphQL — no separate database, no static export to go stale.
- **Writes decisions back into DataHub, not just a dashboard.** Incidents and tags
  land as first-class DataHub entities — visible in DataHub's own UI, inherited by
  every other tool and teammate on your stack, the moment they're created.
- **Governance rules that live in the graph, not in Kronika.** Define a policy once
  and it's written into DataHub itself as real metadata — it survives a restart, and
  any other tool inspecting that dataset can see it, with or without Kronika running.
- **Ask "is this safe?" before you touch anything.** Run a proposed schema change
  through the same reasoning engine first — see exactly what would be affected,
  without a single row of data actually changing.
- **Humans stay in the loop.** High-impact actions like a pipeline halt wait for
  explicit approval; Kronika recommends, evidence in hand — it doesn't act alone on
  the decisions that matter most.
- **Explainable by design.** Every decision is deterministic and reproducible, with
  a full evidence trail — the same event always produces the same answer, and you
  can always see why.

---

## Getting started

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (package manager)
- A running DataHub instance, reachable at `DATAHUB_SERVER_URL`. For a live demo,
  load one of the DataHub hackathon's [Sample Datasets](https://docs.datahub.com)
  (e.g. `healthcare`) into it first — Kronika reads whatever lineage-connected
  datasets, tags, and ownership already exist in that instance; it does not seed
  DataHub itself. `tests/fixtures.py` provides an in-memory mock of an equivalent
  dataset shape for tests and offline development, but it is never written to a
  real DataHub instance.

### Install

```bash
uv sync --extra dev
```

### Run the quality gate

```bash
./scripts/verify.sh
```

### Run the application

```bash
uv run uvicorn application.main:app --reload
```

---

## Project layout

```
src/kronika/      Pure reasoning core. Zero external dependencies.
                  All logic here is deterministic, pure, and testable
                  without any infrastructure.

src/application/  FastAPI application, DataHub adapter, LLM adapter,
                  DuckDB cache. All I/O lives here.

tests/core/       Tests for src/kronika/. No infrastructure required.
tests/application/Tests for src/application/. May require DataHub.
tests/fixtures.py In-memory mock dataset shared by the test suite.
scripts/          Developer tooling (verify.sh).
kronika-console/  PyQt6 desktop console app (not used in the current demo path).
```

---

## Configuration

Required environment variables:

| Variable | Purpose |
|---|---|
| `DATAHUB_SERVER_URL` | DataHub GMS server URL |
| `DATAHUB_TOKEN` | DataHub personal access token |
| `OPENAI_API_KEY` | OpenAI API key (for explanation generation) |

Optional (defaults shown):

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_MODEL` | `gpt-4o` | Model for explanation generation |
| `KRONIKA_POLL_INTERVAL_SECONDS` | `60` | Quality assertion poll interval |
| `KRONIKA_CONFIDENCE_THRESHOLD` | `0.90` | Minimum confidence for autonomous enrichment |
| `KRONIKA_LLM_TIMEOUT_SECONDS` | `30` | LLM call timeout |
| `DUCKDB_PATH` | `:memory:` | DuckDB cache path |
| `KRONIKA_WRITER_MOCK_MODE` | `false` | If `true`, `HttpDataHubWriter` never performs live writes (used by the test suite to keep automated runs from mutating a real DataHub instance) |

Governance rules read by the Verification Engine are themselves stored in DataHub —
`POST /q/policy-rules` writes a rule to `datasetProperties.customProperties` on its
scope asset (DataHub has no native concept of Kronika's business-rule predicates, so
this is Kronika's own registered extension point, not a repurposed access-control
API); `GET`-equivalent reads happen automatically on the next `list_policy_rules()`
call, from any process, including a freshly started one.
