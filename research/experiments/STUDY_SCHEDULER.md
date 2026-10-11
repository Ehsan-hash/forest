# Persistent study scheduling

`study_scheduler` dispatches the work already registered by `StudyRuntime` in
the original study SQLite ledger. It changes execution concurrency; it does not
change the manifest's experiments, seeds, datasets, decision contracts, model
bindings, eligibility, or resource allowance. It has no cumulative step or
project round limit.

## Execution lanes

- **Model:** bounded independent episode, replay, and calibration decision
  requests. Every dispatch receives its own runtime manifest snapshot and model
  client. Messages, saved decisions, and nonreusable results are not shared
  between episodes. Work-specific provider bindings take precedence over the
  campaign provider.
- **GPU:** one dedicated dispatcher for ready fit and inference work. It runs
  before model or analysis admission on each scheduling pass. A live or
  uncertain accepted physical GPU job keeps the lane occupied, even after the
  original model worker has yielded. `ComputeSupervisor` remains authoritative
  for physical device identity, original process ownership, allocation, actual
  elapsed usage, and the shared GPU/wall hard allowances.
- **CPU:** bounded registered analysis, rescoring, and stage snapshots. These
  follow the same persisted dependencies and do not imply a successful fit.

Model work uses `run_claimed(..., defer_gpu=True)`. The runtime saves the actual
decision and registers each fit/inference child before returning `retryable`.
The dedicated GPU worker claims that child. A parent with pending child work is
not repeatedly redispatched: it becomes eligible when the original child is
terminal, and the runtime reconciles its saved decision and receipt. A completed
process alone does not make a scientific result valid.

## Concurrency selection

Supply the original measured scheduling pilot JSON through `--pilot`. Its model
and reasoning effort must match the saved campaign profile. Only successful,
finite timing samples within the authorized maximum are eligible. Initial
capacity is selected by observed completed calls per wall second; p95 latency
breaks throughput ties. No unmeasured speedup is asserted.

The scheduler records subsequent completed model waves with request IDs,
actual timestamps, elapsed time, throughput, p95, and model profiles. It can
measure the next degree within `--maximum-model-concurrency` after a successful
full wave, then selects the best observed degree. Underfilled waves cannot prove
a higher degree. Observed request failures reduce simultaneous calls and retain
the failed source's original records. Two consecutive identical source errors
block further blind calls from that provider configuration while independent
eligible work continues. A corrected provider configuration is a distinct,
readable source identity, without a content digest.

This capacity is a bound on simultaneous requests, not a lifetime work limit.
The coordinator refills a free inference slot while other requests remain in
flight. Measuring a concurrency wave never acts as a barrier for dispatch.
Live samples use overlapping request intervals to determine actual concurrency;
sequential native-agent calls do not count as concurrent requests.
Recorded wave policies and the coordinator identity survive restart. Subsequent
throughput samples can differ from the initial pilot; estimates include that
uncertainty and never remove registered work to meet an ETA.

## Ownership and recovery

The additive `forest_study_schedulers`, `forest_study_dispatches`,
`forest_study_scheduler_intervals`, `forest_study_queue_spans`,
`forest_study_scheduler_decisions`, and `forest_study_scheduler_circuits` tables
preserve the existing study and GPU ledger rows.

A dispatch owner and intent are committed before the atomic SQLite work claim
and before external effects. The coordinator's hostname, PID, and actual process
creation time identify its session. A live coordinator cannot be replaced by a
second process. A coordinator on another host requires reconciliation; an
expired wall-clock lease alone never proves that its work stopped.

After a proven coordinator process exit:

- An unstarted `claimed` item returns to the queue under the same work ID.
- A saved model decision or original GPU submission resumes its existing action
  and receipt identity.
- A requested model call with an unknown response remains
  `awaiting_reconciliation`; the model is not invoked again as a recovery guess.
- A completed model response without its accepted decision is also quarantined.
- An interrupted native analysis without a recovery receipt remains awaiting
  reconciliation rather than being silently repeated.

The runtime retains all original requests, responses, decisions, action bindings,
negative results, and usage. Scheduler shutdown/restart does not reset a work ID,
GPU receipt, or accounting ledger. In-process claims receive heartbeat updates.
Actual model requests retain their provider's individual transport timeout;
GPU execution and cancellation retain the supervisor's real hard limits.

## Timing and readback

`status` reports observed intervals and their references. Each category contains
both `summed_seconds` (resource time across independent operations) and
`wall_union_seconds` (the union of its actual time intervals). Categories can
overlap and **must not be added together as project wall time**.

- `model_wait`: actual request start to original response/failure completion;
  an in-process active request is provisionally observed through the current time.
- `gpu_work`: original accepted GPU process start to its confirmed stopped
  endpoint. Startup without a launch clock is not GPU execution.
- `gpu_ready_wait`: time an actual dependency-ready fit awaited admission.
- `gpu_queue_wait`: the part of that wait overlapping other measured GPU work.
- `gpu_dispatch_wait`: accepted submission to actual process start, separately
  exposing startup and audit overhead.
- `audit` and `scheduling`: actual coordinator claim/audit and scheduling spans.
- `unreconciled_model_interval`: an unresolved request's possible interval,
  excluded from the observed wall union; it is not a claimed completed request.

Readback includes completed states, remaining ready items, blocked/waiting items,
explicit `not_run` records, measured completed throughput, and an estimate for
currently ready work. Blocked work has no invented ETA. A heterogeneous item ETA
is labeled as such; a model call and a fit are different work sizes. The physical
resource ledger, rather than interval visualizations, determines budget usage.

## CLI

Configure the actual hard allowance with `gpu_supervisor` first. Then run:

```sh
python -m research.experiments.study_scheduler run \
  --campaign /absolute/campaign \
  --manifest /absolute/campaign/study_execution_manifest.json \
  --ledger /absolute/campaign/study.sqlite \
  --ledger-id original-ledger-id \
  --gpu-device GPU-physical-UUID \
  --pilot /absolute/campaign/development/scheduling_pilot/pilot-id/summary.json \
  --maximum-model-concurrency 2 \
  --cpu-concurrency 1 --daemon
```

`--daemon` continues watching for newly eligible work or manifest revisions until
the process is stopped. The GPU supervisor's compute wall policy continues to
govern GPU admission; it does not stop Agent inference. Without it, execution
returns when no dispatch or reconcilable live original computation remains.
Unresolved work then produces `blocked`, rather than a claim that the study
completed; dependency, source, and reconciliation states remain in the ledger.
`--wait-timeout` bounds only the caller's wait; it does not change a scientific
work item or cancel a separately owned original GPU process. Graceful close waits
for active local requests before releasing coordinator ownership.

Use the same arguments with `status` for state/timing readback. No model request
or fit is started by `status`. Start with a validated observed degree; authorize a
larger simultaneous capacity only when that is appropriate for the saved local
provider and actual shared resources.

## Validation scope

`tests/test_study_scheduler.py` uses real SQLite transactions, two-process claims,
actual coordinator SIGKILL/restart, independent Python children, the real
`ComputeSupervisor` CPU receipt chain, and the real `StudyRuntime` CLI analysis
path. Scripted infrastructure timing is labeled as such. These tests do not
claim to be Codex model measurements, GPU training, or scientific results. Real
pilot/model/GPU evidence must come from the original campaign execution records.

## Current comparison scope

`formal_progress` and formal-stage throughput use only the current manifest's
`cells[].episode_id` identities. Historical failed attempts, previous protocol
batches and planned cells withdrawn by a selection amendment remain in the
ledger and resource intervals, but cannot inflate the current denominator or
completion rate. A preparation fit's throughput is not used as a substitute for
formal episode throughput. No observed current completion means no formal ETA.

`paused_no_progress` remains recoverable, separate from scientific failure. Such
an item is excluded from automatic claims while its diagnosed proposal,
validation and evidence state are unchanged. Other ready independent items keep
running; readback includes the held IDs and preserves their original records.
