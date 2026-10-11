# Paired study matrix selection

`study_selection` reduces an existing registered matrix using a public selection
plan. It does not generate tasks, change scientific configurations, read outcomes,
or launch model or GPU work. Every chosen context retains every declared arm.
Execution IDs and logical matrix IDs remain separate in the selection report.

## Selection rules

Each category declares its number of **contexts**, comparison arms, context
identity fields, family order, and local ordinal order. Context count multiplied
by arm count gives experimental units. Families are visited in round-robin order;
local candidates use their original task/context ordinals. The lowest registered
repeat ordinal is retained unless repeat filtering is disabled explicitly.

Optional rules support:

- Alternating existing streams, without choosing a different stream per arm.
- Unique task or change axes across selected contexts.
- Matching a pre-existing category's task/change assignments.
- Declared local ordinal offsets for a diagonal selection among task families.

Selection never uses completed/failed state, measured metrics, model responses,
resource consumption, hashes, or random draws. Input JSON order does not affect
membership. A missing comparison arm, infeasible rule, conflicting selection
field, or wrong declared total produces an error without relaxing the plan.

## Declaring and checking a revision

A plan has `selection_revision_id`, `expected_counts` (`total`, `core`,
`supplement`), and one `SelectionRule` object per category. For example:

```json
{
  "selection_revision_id": "registered-scope-revision",
  "expected_counts": {"total": 24, "core": 24, "supplement": 0},
  "rules": [{
    "category": "LIVE_MAIN",
    "context_count": 8,
    "arms": ["FULL", "FLAT", "DAG_ID"],
    "context_fields": ["task_id", "stream", "repeat"],
    "group_field": "family",
    "group_order": ["darcy", "bloodmnist", "etth1"],
    "ordinal_fields": ["task_id", "stream", "repeat"],
    "unique_axes": [["task_id"]],
    "alternating_field": "stream",
    "alternating_order": ["EVAL_UPDATE", "TRAIN_UPDATE"],
    "balance_fields": ["family", "stream"]
  }]
}
```

```sh
python -m research.experiments.study_selection \
  --manifest study_execution_manifest.json \
  --plan protocol/amendments/scale_selection.json \
  --output selection_report.json
```

The report includes every selected and excluded logical matrix ID and execution
ID, selected original cells, public context assignments, arm counts, and balance
counts. Record the plan and report as the experiment's scope amendment before
dispatch. The current progress denominator uses selected cells, while the
original matrix and previous results remain historical records. Category-level
comparisons use the same selected contexts across arms; missing strata should
be reported explicitly rather than presented as a complete original matrix.

## Queue withdrawal and history

`apply_selection(original_study_ledger, campaign_id=..., external_ledger_id=...,
report=...)` applies queue membership in one SQLite transaction. It retains the
campaign's original external resource ledger and stores the complete selection
report in `forest_study_selections` plus a `study_selection_applied` event.

Excluded untouched queued cells become `not_run`, with an explicit
`protocol_scope_reduction` reason. They cannot be claimed by the runtime. Original
payloads, edges, completed results, failed attempts, execution receipts, and
resource rows are preserved. Claimed/running/ambiguous exclusions require executor
reconciliation before application; a partial selection is rolled back. The same
revision and same report are idempotent. Reusing a revision for different membership
is rejected. A later explicit revision can restore a previously unexecuted scope
exclusion, retaining both selection records.

A queued cell can retain an earlier planning-only `NOT_RUN` receipt after a
missing dependency has been supplied. This receipt permits withdrawal only when
it explicitly records zero physical model requests and zero actual GPU seconds,
with no lease, supervisor, decision, or dispatch evidence in the original ledger.
The original receipt and dependency-resolution history are retained verbatim.
Missing usage observations or a real dispatch still require reconciliation.

Manifest editing and queue withdrawal should run while the scheduler is between
dispatch rounds. Apply the ledger selection before restarting a scheduler using
the revised selected-cell manifest. The helper does not restart or terminate
workers, and it does not reset any ledger.
