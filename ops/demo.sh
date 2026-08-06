#!/usr/bin/env bash
# ops/demo.sh — Kronika end-to-end demonstration script
#
# Steps:
#   1. Seed canonical healthcare dataset (raw_patients, staging_patients, mart_billing, mart_demographics)
#   2. Simulate quality assertion failure on raw_patients.billing_amount via webhook
#   3. Demonstrate governance policy violation (pii_must_have_owner on raw_patients)
#   4. Execute safety pre-check (POST /q/analyze) for proposed schema change

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "========================================================================"
echo "               KRONIKA END-TO-END DEMONSTRATION                         "
echo "========================================================================"
echo ""

cd "${REPO_ROOT}"

echo "Step 1: Seeding canonical healthcare dataset..."
uv run python ops/seed_healthcare.py > /dev/null
echo "✓ Dataset initialized: raw_patients, staging_patients, mart_billing, mart_demographics"
echo ""

echo "Step 2: Simulating quality assertion failure on raw_patients.billing_amount..."
uv run python -c "
from ops.seed_healthcare import get_healthcare_dataset, _urn
from application.datahub.reader import HttpDataHubReader
from application.datahub.writer import HttpDataHubWriter
from application.storage.cache import DuckDBEvidenceStore
from application.agent.runtime import DecisionEpisodeRunner
from kronika.engine import PublicEngine
from kronika.types import EventKind, MetadataEvent

seed = get_healthcare_dataset()
reader = HttpDataHubReader(mock_data=seed)
writer = HttpDataHubWriter(mock_mode=True)
store = DuckDBEvidenceStore(':memory:')
engine = PublicEngine()
runner = DecisionEpisodeRunner(engine, reader, writer, store)

evt = MetadataEvent(
    event_id='demo-evt-001',
    kind=EventKind.QUALITY_OBSERVATION,
    source_urn=_urn('raw_patients'),
    columns=frozenset({'billing_amount'}),
    payload=(),
    occurred_at='2026-07-25T22:00:00Z'
)

decision, actions = runner.run_episode(evt)

print('Outcomes:')
for urn, o in decision.evidence.outcomes.items():
    print(f'  - {urn.split(\",\")[1]}: {o.recommendation.value}')

print('\nActions generated:')
for a in actions:
    print(f'  - [{a.kind}] {a.target_urn.split(\",\")[1]}: {a.rationale}')
"
echo ""

echo "Step 3: Governance Policy Check & Human-in-the-Loop Approval Queue..."
uv run python -c "
from ops.seed_healthcare import get_healthcare_dataset, _urn
from application.datahub.reader import HttpDataHubReader
from application.datahub.writer import HttpDataHubWriter
from application.storage.cache import DuckDBEvidenceStore
from application.agent.runtime import DecisionEpisodeRunner
from kronika.engine import PublicEngine
from kronika.types import EventKind, MetadataEvent

seed = get_healthcare_dataset()
runner = DecisionEpisodeRunner(PublicEngine(), HttpDataHubReader(mock_data=seed), HttpDataHubWriter(mock_mode=True), DuckDBEvidenceStore(':memory:'))
evt = MetadataEvent('demo-evt-002', EventKind.QUALITY_OBSERVATION, _urn('raw_patients'), None, (), '2026-07-25T22:00:00Z')
runner.run_episode(evt)

pending = runner.store.list_pending_actions()
print(f'Pending human-gated actions ({len(pending)}):')
for p in pending:
    print(f'  - ID: {p[\"action_id\"]} | Target: {p[\"target_urn\"].split(\",\")[1]} | Kind: {p[\"kind\"]}')
"
echo ""

echo "Step 4: Safety Pre-check (POST /q/analyze) for proposed SchemaChange..."
uv run python -c "
from ops.seed_healthcare import get_healthcare_dataset, _urn
from application.datahub.reader import HttpDataHubReader
from application.datahub.builder import build_context
from kronika.engine import PublicEngine
from kronika.types import EventKind, MetadataEvent

seed = get_healthcare_dataset()
ctx = build_context(HttpDataHubReader(mock_data=seed))
engine = PublicEngine()
engine.observe(ctx)

proposed = MetadataEvent('precheck-001', EventKind.SCHEMA_CHANGE, _urn('staging_patients'), frozenset({'billing_amount'}), (), '2026-07-25T22:00:00Z')
decision = engine.reason(proposed)
print('Pre-check Analysis Result:')
print(f'  Containment Objective: {decision.evidence.containment.objective}')
print(f'  Halt Set: {[u.split(\",\")[1] for u in decision.evidence.containment.halt_set]}')
"
echo ""

echo "========================================================================"
echo "               DEMONSTRATION COMPLETED SUCCESSFULLY                     "
echo "========================================================================"
