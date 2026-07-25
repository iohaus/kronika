# kronika — Autonomous Metadata Decision Engine for DataHub

Kronika reads DataHub metadata — assets, lineage, quality signals, governance
policies, and ownership — and produces auditable recommendations for organizational
action: which pipelines to halt, which are safe to continue, which governance rules
are violated, and whether a proposed schema change is safe before it is applied.

Every recommendation is traceable to a specific policy constraint, a lineage path,
and a concrete quality observation. No recommendation is generated without evidence.

---

## Getting started

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (package manager)
- DataHub instance (local: `docker compose up` in `ops/`)

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
scripts/          Developer tooling (verify.sh, seed scripts).
ops/              docker-compose.yml for local DataHub.
```

---

## Architecture

The reasoning core (`src/kronika/`) is completely independent of DataHub, OpenAI,
FastAPI, and NetworkX. It accepts plain Python dataclasses and returns plain Python
dataclasses. It can be tested, audited, and reasoned about in isolation.

The application layer (`src/application/`) connects the core to the outside world:
it reads from DataHub, runs the core reasoning, and writes results back to DataHub
as incidents, annotations, and governance assertions.

The public interface of the reasoning core is:

```python
engine.observe(data_context)         # load the current graph state
decision = engine.reason(event)      # compute impact, recommendations, evidence
actions = engine.plan(decision)      # translate decision to DataHub write-backs
new_ctx = engine.transition(actions) # update local graph state after write-backs
```

---

## Design principles

**The core is invisible.** Complexity lives inside the reasoning engine. The product
surface exposes only organizational outcomes: halt, continue, investigate, notify.

**Every recommendation has evidence.** No recommendation is emitted without a
traceable evidence path: which constraint was violated, which lineage path carries
the impact, which assets are safe.

**The engine degrades, not the correctness.** If the LLM explanation layer is
unavailable, the engine still computes and writes back to DataHub. If DataHub is
temporarily unavailable, the engine still reasons from its cached graph state.

**No recommendation is irreversible.** All autonomous write-backs to DataHub
(incident creation, annotations) are additive. Pipeline halt recommendations are
always human-gated in the current release.

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
