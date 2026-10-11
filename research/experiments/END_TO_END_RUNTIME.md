# Shared from-scratch end-to-end execution

`end_to_end_runtime.py` supplies one scientific stage controller and public
action contract to FULL and native AIDE. It accepts any registered task with
an enabled `long_from_scratch` definition; no task IDs or family comparisons
are hardcoded. The actual fitter remains `training_runtime.build_fit_config`
and `StudyRuntime.execute_fit`, through the original `ComputeSupervisor`.

The 24 cells are the six enabled tasks, FULL/AIDE and repeats 0/1. Their
category is `LIVE_END_TO_END`, including when nested under `episode`.
Each cell keeps its original episode UUID as `work_id`; task, arm, repeat
and category must also be present in the normalized public payload. Each
cell has an independent start and independent actual fit/score identities.
Shared snapshots are excluded. Merely labeling an ordinary update episode
as end-to-end cannot implement this stage contract.

The current editable plan declares 1,200 GPU seconds per cell. Active wall
time remains an observation and does not cap model follow-up. Its nine-configuration public Cartesian space is enumerated
from the actual registered source task. Exploration starts with the anchor
at seed 17, permits at most four distinct configurations including that
anchor, and exposes additional legal configurations in lexical ID order.
The method decides which public action to request; the controller never
inventories a missing result as an observation or automatically chooses a
scientific winning configuration on the method's behalf.
The cell payload must carry its registered GPU allowance explicitly. Each fit
is bounded by the remaining shared GPU allowance; an additional per-fit
timeout applies only when explicitly registered in the payload.

After the second distinct completed exploratory fit, the controller records
the original objective and observed history and injects the task's registered
C1 objective. Current predictions are independently rescored under the new
objective before selection is offered. The explicit agent `select` action
uses the common observed-data tool: choose the best eligible non-anchor
under current constraints, with lexical ID ties; if none is eligible, retain
the best observed non-anchor and report the constraint failure.

The resulting comparison is an inspectable versioned record containing the
objective, exact selected configurations and source receipts. It precedes
the confirmation fits. It is a provenance record, not a file lock or freeze
operation. Existing receipts and objective history are retained. Later
scientific revisions require their own explicit identities and comparison
records instead of relabeling an old execution.
Further legal exploration and a new explicit selection remain available after
confirmation starts. Each new completed selection receives the next comparison
version and its own output identity; prior records remain inspectable.

The current registered long contract has confirmation seeds 1001, 1002 and
1003. This differs from the two main-update seeds. Each reached confirmation
seed requires fresh anchor and selected-candidate fits, independent rescoring
and a paired comparison. An exhausted GPU allowance leaves missing
computations explicit; no unused budget is transferred and no missing seed becomes a
complete result. A valid final submission requires the actual completed
confirmation sequence, an actual paired-comparison action, and the exact
selected configuration from the comparison record. Null or harmful measured
findings can satisfy this execution contract.

## StudyRuntime integration

Delegate end-to-end payloads at the start of `build_episode_request` to
`build_end_to_end_episode_request(runtime, payload)`. Route final FULL and
AIDE submissions to `verify_end_to_end_submission(runtime, payload, history,
submission)`. Delegate `end_to_end_select` and `end_to_end_compare` analysis
bindings to `execute_end_to_end_analysis(binding)`.

The public decision schema requires `action_work_id` on every action. This
disambiguates score/selection revisions sharing the same configuration and
seed. The common action resolver must match that exact binding ID. Current
frontier actions are advertised to the method; previously allocated bindings
remain available to reconcile a saved pending decision without regenerating
the decision or repeating completed training. Actual GPU work is deferred
to the single GPU lane and releases the model worker.

The payload must identify the registered source specification, researcher
ordered data-ID reference, dataset-version reference, and the actual resolved
calibration update-count artifact. That calibration artifact must certify
source validation and its calibration-only scope. No update count is filled
in when calibration has not succeeded. Source conditions, full configs,
receipts, original ledger IDs and physical attempts must reconcile exactly.

## Validation scope

`tests/test_end_to_end_runtime.py` contains explicitly nonformal infrastructure
fixtures. Synthetic source definitions and marked synthetic receipts exercise
the actual config builders, stage transitions, original SQLite persistence,
shared observed-data analysis and final completeness gate. No model/GPU
training is launched and these numbers are not campaign scientific evidence.
