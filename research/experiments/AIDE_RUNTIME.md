# Native AIDE adapter

`aide_runtime.py` imports the declared WecoAI/aideml clone's actual `Agent`,
`Journal`, `Node`, `ExecutionResult` and serialization code. It preserves
native search policy and draft/debug/improve generation, then splits the
native synchronous step at the scientific execution boundary. Once actual
execution is ready, native `parse_exec_result` and `Journal.append` complete
the step. The durable completion event and journal update share a SQLite
transaction. Dispatch count is not native step count.

The adapter uses the existing study SQLite database and external ledger ID.
It adds native session and request identity tables to that database. It does
not clone the ledger or replace source conditions. Generation code, selected
parent, RNG state, returned execution identities and complete journal survive
restart. A pending node is restored before execution is reconsidered. Cached
completed native inference requests can be replayed; an unsettled request
requires reconciliation and is never silently sent again.

Each worker owns a local signed-in Codex `ModelClient` matching the original
registered provider identity, model, payment policy and explicit reasoning
effort. This campaign registers `gpt-6-luna` with `medium` reasoning.
Context-local query and RNG routers keep
independent episodes isolated even when their model requests run concurrently.
No upstream API backend is called. The original native prompt, complete policy
messages, actual transport payload, raw full response and usage are recorded
in `forest_study_model_calls`. The CLI does not support a temperature setting
or promise a hard output-token ceiling; the record identifies that limitation.
Each query uses the provider's adaptive experiment-stage output allocation,
bounded by provider and per-request transport limits. Accumulated episode
calls, tokens, and active wall time are recorded but do not cap later turns;
cached responses consume no new model request or usage.

## Scientific execution integration

`StudyRuntime.run_claimed` dispatches explicit AIDE episode arms to:

```python
run_aide_episode(runtime, episode_item, client=client,
                 gpu_devices=gpu_devices, defer_gpu=defer_gpu)
```

The runtime supplies:

```python
execute_aide_node(episode_item, *, node_id, code, client=None,
                  gpu_devices=(), defer_gpu=True) -> dict
episode_budget_status(episode_item) -> dict
```

`execute_aide_node` saves a node's emitted decision and execution identity,
uses `run_action_program(code)` for the actual CPU program, validates that
decision with the common public schema, and routes it through
`execute_decision_actions` and `verify_episode_submission`. Its existing
registered fit/inference children are executed only by the scheduler's GPU
lane and original `ComputeSupervisor`. Full saved configuration, exact seed,
current receipt and physical attempt identity must reconcile before completion.
The hook must reuse its saved decision for the same `node_id` and `code`; it
must not regenerate a decision or re-execute completed training on resume.

The hook returns the following explicit fields:

| Field | Meaning |
| --- | --- |
| `node_id` | Exact native generated node identity |
| `state` | `completed`, `failed`, `retryable`, `awaiting_reconciliation`, or `budget_exhausted` |
| `action_work_ids`, `pending_work_ids` | Registered logical children and pending subset |
| `action_results` | Actual common executor results, including receipt/configuration references |
| `stdout`, `exec_time`, `exc_type` | Actual public code and scientific execution output and failure |
| `verified_metrics` | Independently recomputed finite `value`, `maximize`, `configuration_id`, `seed`, and `receipt_path` observations |
| `submission_validation` | Common measured submission verification result, only when the emitted program submits |

Native feedback supplies the metric stored in the native journal. A metric
that is absent from the independently verified observations marks the node
buggy with upstream `WorstMetricValue`; the adapter does not overwrite it
with the best observed metric. A successful final outcome requires the common
submission validator. The native journal's selected best node remains
inspectable and does not replace the model's emitted scientific submission.
An invalid final submission remains recoverable through another native step;
the journal and actual failed verification remain available to native debugging.

`episode_budget_status` returns measured active wall and GPU seconds under
the same registered resource policy as FULL. The adapter accepts the shared
runtime's nested `used` fields and its explicit measured-field representation.
The campaign's registered GPU allowances remain binding: 480 GPU seconds for
LIVE_MAIN and 1,200 GPU seconds for LIVE_END_TO_END. Active wall time is
measured and reported without stopping model follow-up. GPU queue waiting is
excluded from active wall usage and does not hold a model worker. The adapter
has no artificial lifetime inference-step cap.

## Public code capacity

AIDE's generated single-file Python programs request the same public
scientific actions available to FULL. A strict AST validator allows JSON
import, literal decisions, variable assignment and exactly one
`print(json.dumps(decision))`. The validated code runs in a real CPU Python
child using an isolated temporary working directory, `-I -S`, a minimal
environment and a timeout. It has no filesystem, model API, shell or direct
training operations. Scientific numbers in its output have no authority;
the common deterministic executor computes and checks empirical outcomes.

This adapts AIDE's unrestricted Kaggle interpreter/prompt execution surface
to the study's shared source-bound public tool capacity. Native search and
its journal tree are retained. The adapter and this adaptation must be
reported as part of the baseline's operational configuration.

## Dependencies and nonformal fixtures

The actual clone must be present and its import dependencies installed in
the framework Python environment. The clone is selected through
`aide_upstream_root` in the episode or manifest, with the campaign's
`executors/aide_upstream` as the default. No substitute implementation is
loaded when the clone is missing.

`tests/test_aide_runtime.py` contains nonformal infrastructure fixtures.
They use scripted inference responses only to exercise native upstream
search/journal persistence, and actual CPU child programs to exercise code
execution. They launch no real model or GPU training and produce no formal
scientific evidence. Set `FOREST_AIDE_UPSTREAM` to the official local clone
when running the native integration fixtures.
