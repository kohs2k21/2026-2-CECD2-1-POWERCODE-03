# Baseline SR recommendation bundle

This package owns TRAIN reference/application splitting, summary construction,
the one-slot Rule v3 schema, response validation, and service-draft conversion.
It uses the existing fixed Rule LoRA in `../feature_rule_spec/models/rule`;
no new weights, HR generation, observed SR generation, or training is included.
The tested local service prototype retained its existing Feature v5 adapter.
This publication contains research files only; backend integration and deployment
are owned by the backend developer.

The full service policy, seven Korean labels, state/action semantics, and
deployment boundaries are defined in
[the backend handoff](BACKEND_HANDOFF.md). It is self-contained within `research/`.

## API

```python
from research.models.baseline_rule_recommendation import (
    split_train, build_request, output_schema,
    validate_prediction, convert_suggestion, NEEDED_LABELS,
)

partition = split_train(train_records)
record = build_request(train_records, feature_definition, context={
    "partition": partition,
    "source_id": source_id,
    "snapshot_id": snapshot_id,
    "feature_set_id": feature_set_id,
    "split_policy": "train-internal-80-20-chronological-v1",
})
# Generate a real local model response using record["messages"] and output_schema().
# This package contains no expected-response generator or saved-response fallback.
errors = validate_prediction(record, actual_prediction)
if not errors:
    draft = convert_suggestion(record, actual_prediction)  # None for non-proposed states
```

Each record needs `observation_id`, `transaction_key`, timezone-aware
`event_time` (the confirmed `persisted_at` collection clock), `features`,
`missing`, and `label`. These are TRAIN records only. Definitions contain
`name`, `definition_sha`, `dtype`, `unit`, `expression`, and `meaning`.
The model receives summary statistics and aggregate lineage hashes.
It receives no raw rows, per-row IDs, or per-row hash lists.

The temporal 80:20 boundary preserves whole transactions and timestamp ties.
Unsafe boundaries block recommendation. Only known anomaly labels are removed
from reference estimation; unlabeled values do not imply normality. Current
observations retain anomaly labels. The exact Decimal p95 and the explicit
JSON-number boundary are recorded separately; service conversion preserves the
latter without substituting a different exact value or shortening the number.

The schema restricts structural fields; it does not force a correct decision
state or threshold. Validation checks the actual response. At the service
boundary, one repair is permitted; both attempts and their failures are retained.
The tested local prototype used compact JSON grammar to prevent repeated
whitespace from consuming the response budget. Its server-side grammar tests
are historical evidence and are not included here; the backend must implement
the same decoding settings documented in BACKEND_HANDOFF.md.
The validator's narrow p95 policy is deterministic. Passing it demonstrates
contract compliance, not discovery of new business rules or a proven SLA.
Natural-language meaning still requires administrator review.

## CPU verification

From the repository root, with the project environment:

```bash
/home/msko20702/'#종합설계'/data/.venv/bin/python -B -m unittest discover \
  -s research/models/baseline_rule_recommendation/tests -v
```

Use the project's approved Python environment; the absolute path above is the
existing research environment, not a required deployment location. Pure input
construction and validation require `jsonschema==4.26.0`. No installation is
performed by these commands.

The effective baseline-only JSON Schema is
[`contracts/baseline.schema.json`](contracts/baseline.schema.json), identical to
`output_schema()`. `contracts/rule.schema.json` is the copied generic Rule v3 base
schema; consumers must use the effective schema for this profile.

There is no server exporter, DB access, queue worker, or deployment command in
this delivery. Import the package directly from the repository root or package
it with its `contracts/` and `manifest.json` assets. The backend supplies model
inference and connects validated drafts to its existing administrator review API.
Model paths, decoding settings, one-repair protocol, and required integration
checks are in [BACKEND_HANDOFF.md](BACKEND_HANDOFF.md).
