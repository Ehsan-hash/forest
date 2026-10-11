# Source-bound mechanism replay

`research.experiments.mechanism_replay` implements artifact-only dependency
mechanism evaluation. StudyRuntime obtains the actual model decision through the
saved provider, writes its complete request, response and usage, and invokes the
controller hooks. The controller does not launch a model, training or inference
process.

## Evidence and conditions

Each cell has its own stable work ID and persisted `forest_replay_sessions` row.
The initial controller binds the registered task contract, treatment, factual
changes, all original snapshot configurations and paired seeds, complete original
configurations and receipts, and checkpoint/prediction file references. Missing
original artifacts prevent preparation. Changed original evidence enters
reconciliation; it never authorizes a substitute fit. A work ID cannot be reused
for changed facts or a different treatment.

Scientific applicability is derived by direct comparison of readable training
data, training recipe, validation input and evaluation contract values. Dependency
closure includes checkpoint, prediction, metric, selection, comparison and claim
records. Data aliases with unchanged ordered IDs and dataset provenance do not
change these conditions. No mutation label selects a special implementation.

## Treatment delivery

- FULL and FULL_NO_GATE expose the typed dependency graph and original evidence.
- DAG_ID exposes identifiers and untyped edges, with the same observed evidence.
- FLAT exposes the records without graph propagation.
- ID_ONLY retains record identities and observations without graph propagation.
- RERUN_ALL requires a virtual operation for every executable record, including
  recomputation and revalidation of comparison records.

The initial model view omits the controller mutation label and private answers.
It does not include the validator's affected, invalidated or reusable sets.
Large ordered lists remain complete in both persisted source conditions and
model views. Public projection removes controller answers while preserving
every declared scientific input value in its original order.

## Virtual operations and scientific status

The public operations are `inspect`, `train`, `infer`, `score`, `select`, and
`compare`. Every request selects an exact registered `action_work_id`.
`forest_replay_actions` uniquely binds that identity to its parent, operation and
original record. Concurrent execution and process restart return the same
original action receipt. Actual controller execution time is recorded once in
the shared active interval ledger within the action transaction.

`state: completed` with `kind: replay_transition` means the virtual controller
operation was recorded. It does not mean a fit or inference ran. Results include
`replay_only: true`, `physical_fit_executed: false`,
`physical_inference_executed: false`, and `gpu_actual_seconds: 0`.

If a declared change needs outputs not certified by a registered original
artifact, the result is `REPLAY_ACTION_UNAVAILABLE`. This can still establish
that a proposed mechanism repair plan chose the required operation. It cannot
establish revised predictive performance, a repaired scientific result, or a
successful physical fit. Calibration artifacts from different seeds or
configurations are not substituted. Virtual training cost is labeled as an
original measured reference and never added to actual GPU consumption.

Selection and comparison use only observed original paired values under an
applicable evaluation contract. An unobserved metric or changed data, recipe or
validation input produces unavailable output rather than an inferred metric.
Model claims are retained as unverified text.

## Independent verification

The validator reads the persisted controller and action receipts, checks exact
invalidation/reuse sets and required operation coverage, rejects forged history,
and checks any selected configuration against applicable observed evidence.
`valid` and `mechanistic_plan_valid` describe the mechanism plan only.
`scientific_repair_complete` and `scientific_metric_values_generated` remain false.

FULL_NO_GATE records the proposal with an advisory validation result. An invalid
proposal retains `valid: false`; advisory acceptance does not fabricate validity.
The common StudyRuntime is responsible for the replay protocol's actual physical
request, token, active time and feedback limits. Provider retries and output
recovery consume the same persisted per-cell request ledger. This module does
not impose a separate fixed output ceiling.

## Hooks

```python
prepare_replay(runtime, payload) -> prepared_episode
execute_replay_action(runtime, episode, action) -> virtual_action_receipt
verify_replay_submission(runtime, episode, history, submission) -> validation
```

Preparation returns `messages`, `decision_schema`, `action_bindings`,
`allowed_decisions` and the normalized scientific episode. The other hooks use
`episode['work_id']` and `episode['payload']`. No additional model dispatch path
or physical work registration is required.

The regression fixtures exercise the actual StudyRuntime, SQLite transactions,
provider request guard, restart and one feedback revision. Their scripted model
responses and source files are explicitly infrastructure fixtures; they are not
scientific experiment results or replacements for the campaign's real calls.
