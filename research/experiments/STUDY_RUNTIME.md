# Study runtime

`study_runtime.py` consumes the campaign's explicit `study_execution_manifest.json` and records work state in the existing SQLite ledger named by `--ledger` and `--ledger-id`. It creates only `forest_study_*` tables; pre-existing campaign, event, and resource rows are preserved.

The runner registers full fit jobs from `execution_state.task_queue`, as well as later `work_items` and fully specified runnable cells. It reloads the manifest while running, so new manifest revisions can add work without resetting the campaign. A queued, never-claimed item with no model call, fit attempt, or accepted supervisor work can receive a versioned planned-work revision; the ledger preserves both complete payloads and inbound edges. Claimed or executed work keeps its original conditions. Required-execution graph edges block a child until its parent succeeds; `requires_settled` waits for any terminal outcome, including a documented failure; `derived_from` records lineage without creating a scheduling dependency. `claim_work` and `run_claimed` are the scheduler API; a scheduler can restrict claims to explicit IDs or kinds, heartbeat a lease, and release an unstarted claim safely.

## Run

Run from the FOREST source root after the original supervisor allowance and saved provider have been configured:

```bash
python -m research.experiments.study_runtime run \
  --campaign /home/space/FOREST_Paper_Compact_v2/campaigns/forest-paper-compact-v2-20261010 \
  --ledger /home/space/FOREST_Paper_Compact_v2/campaigns/forest-paper-compact-v2-20261010/study.sqlite \
  --ledger-id forest-paper-compact-v2-20261010:study.sqlite \
  --manifest /home/space/FOREST_Paper_Compact_v2/campaigns/forest-paper-compact-v2-20261010/study_execution_manifest.json \
  --provider-id 072563f0-4a12-4202-8191-05e822ef42bd \
  --model gpt-6-luna --reasoning-effort medium --gpu-device GPU-UUID --daemon
```

`--provider-id` must match the saved provider reference in the manifest, which must resolve to the signed-in local Codex CLI provider; paid providers are refused. New calls use the manifest's `gpt-6-luna` medium reasoning setting. The manifest provider is loaded when episode/replay work is reached, including work appended while the daemon is running. The daemon keeps reloading newly added manifest work; it exits on its first idle poll without `--daemon`.

## Manifest inputs

Each runnable fit queue entry requires a stable `work_id`, exact `configuration_path`, `output_directory`, finite `fit_timeout_seconds`, and the complete serialized training config. The config file is passed intact to `research.experiments.training_runtime`; queue fields that duplicate config fields must match. The successful receipt must reproduce that exact config and state `hidden_test_opened: false`.

Episodes require `work_id`, `kind`, registered `task_id`, an evidence treatment arm (`FULL`, `DAG_ID`, `FLAT`, or `AIDE`), and stream (`EVAL_UPDATE` or `TRAIN_UPDATE`) plus factual change and source references. The runner builds the model messages and action bindings from that episode contract, registered configs and existing receipts. `FULL` carries typed evidence nodes and relations, `DAG_ID` carries node IDs and untyped edges, `FLAT` carries a flat artifact index and raw logs, and `AIDE` runs the declared native Python search through the same registered operation executor. Missing or incomplete artifacts stay unknown. The model chooses only a declared action binding. Supported labels are `infer`, `train`, `score`, `select`, `compare`, and `inspect`. A model call may choose actions; the runner persists each result, requests an evidence-aware follow-up, and completes the episode only after a separate `submit` response passes source-bound checks. AIDE action programs are cached by node ID and source text, and their printed claims count only when independently matched to a successful common-executor result.

Inference bindings identify a checkpoint, full evaluation config, output path, allowed validation-noise-only config changes, and timeout. The worker loads existing weights, performs zero optimizer updates, writes validation predictions and a receipt, and independently checks metrics with the NumPy scorer. `score` bindings identify an existing fit config, its original prediction file and successful fit receipt, the explicit researcher-only data root, and an output path. It requires exact validation ID order, then independently recomputes metrics and compares them with the fit receipt. No evaluator or hidden split is searched or opened. With `defer_gpu=True`, episode workers queue fit/inference children for the scheduler's single GPU lane and return without waiting; retries reuse the saved decision and child IDs.

Selection and comparison bindings reference actual successful receipts and explicitly declare metric/direction or paired work IDs. Selection emits the receipt-backed winning configuration ID, actual eligible set, tied IDs and whether side constraints were satisfied. Paired comparison emits the raw right-minus-left differences and mean; it does not silently substitute an inferential procedure that the manifest did not specify. The snapshot stage remains retryable while any of its generated snapshot fits lacks a completed supervisor record and valid formal receipt.

Fit recovery keeps the logical work ID and scientific configuration unchanged. Each physical supervisor attempt is recorded in `forest_study_fit_attempts` with its own supervisor ID and output folder. An already accepted job is reconciled by its original ID. The only automatic technical retry is one failed source-preflight attempt with no execution receipt; other failures, completed attempts, and ambiguous receipts are not relaunched. GPU allowance is summed over all physical attempts for the logical fit before another dispatch, while the original supervisor ledger continues to meter global and wall usage. Registered GPU allowances remain binding for live compute. Episode model-call counts, token totals, and accumulated active-wall seconds are recorded for analysis but do not cap follow-up turns. Replay cells can use existing observations and CPU analysis but cannot launch physical GPU work. Queue wait is excluded from active work time.

## Evidence limits

SQLite records each model request, full delivered messages, actual response text, model/response identifiers, reported usage and cost source, plus start/finish times and latency. Unknown cost remains `null`. Infrastructure fixture tests exercise the delivery/dispatch chain only; they are not evidence of research behavior. A model recommendation does not count as an experiment result unless its chosen deterministic operation completed and a separate final submission agrees with independently recomputed operation results. Invalid or missing proposals remain failed or unresolved work.
