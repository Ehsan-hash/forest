# Persistent research agents

`run_agent(run_id, workspace, config)` uses the selected actual provider. There is
no project-lifetime total turn limit. Nodes without an explicit legacy hard
`agent_budget.steps` use a persisted evidence-gated allowance from
`step_budget_policy` (defaults: 30 initial, +30 after verified progress, 150
per node). Project-level values live under `Project.config.agent_execution`;
run-level values override them. Explicit outer limits always win. Optional
`agent_budget` fields are `steps`,
`input_tokens`, `output_tokens`, `cost`, and `active_seconds`; legacy `max_steps`
is interpreted as an explicit configured budget. Budget edits on the run take
effect at the next turn. Provider transport failures never produce replacement
responses.

Output requests are provider-aware and task-stage aware. Declare verified
`provider.config.model_capabilities.max_output_tokens` and
`context_window_tokens` when the provider exposes them. Ollama context windows
are read from local model metadata when available; no external capability
lookup is made. Unknown limits use the configurable
`adaptive_output_fallback_tokens` (conservative by default), which is an
allocation preference, never a claimed model limit. Projects can configure
`adaptive_output_budget.stage_targets`, `complexity`, `complexity_factor`, and
`growth_factor`; tasks can set `task_complexity` or `research_stage`. Each
request is bounded by the declared model output maximum and the remaining
declared context window. Every retry is charged to the same provider/project
budget. An explicit `max_output_tokens` is an initial request preference, not
a replacement model-capability claim or global ceiling.

Responses explicitly truncated at `max_output_tokens` are retained as failed
attempts with usage and cost. A finite retry can grow within a declared output
maximum or the remaining declared context window. If capacity is unknown or
exhausted, planning switches to separately validated JSON chunks or smaller
independently verifiable actions. JSON is merged only after all numbered chunks
are present, complete, and conflict-free. Partial JSON and tool actions never
count as success. There is no fixed project-lifetime step or output-token
counter; provider, context, project spending, compute, GPU and external
supervisor limits still apply.
The editable `agent_session.json` retains messages, reported token usage, public
action summaries, full tool observations, and the exact pending action ID.
`agent_transcript.json` is available to the model through paginated
`read_transcript`. Context compaction only changes model input; it does not erase
recorded turns. `research_memory.json` holds editable decision summaries and
unresolved questions. Session files are ordinary JSON, without content addressing
or read-only artifact restrictions.

## Real process tools

- Research Agent command tools use Bubblewrap by default and expose a minimal
  read-only runtime plus the writable task workspace. Explicit absolute
  `readonly_paths` are shared with `read_file`, mounted read-only, and must not
  include test or evaluator data. Select `execution_backend: local` only for
  intentionally trusted owner-controlled execution; local mode runs with the
  worker's ordinary host filesystem permissions.
- `start_process` / `run_command`: `command` is an argv list or shell string;
  `cwd` must resolve inside the workspace. `env` and `timeout` are optional.
- `python`: writes the supplied code to the requested workspace path and starts
  the current Python interpreter. It uses the same managed lifecycle.
- `inspect_process`: observes actual PID creation time, state, and memory use.
- `read_process_output`: reads stdout or stderr by byte offset and returns the
  next offset. Logs stay on disk rather than accumulating in process memory.
- `wait_for_process`: persists a wait and raises `AgentYield`. The worker releases
  the Agent executor slot, observes the child, and resumes the same session.
- `cancel_process`: terminates the actual process group, with escalation after a
  grace period. Parent worker cancellation also visits all managed children.

Bubblewrap command children cannot see the host root, home directory, unrelated
data, credentials or network. The worker's local backend is a trusted execution
choice and is not an operating-system sandbox.

Each launch records its request before the supervisor starts. Supervisors survive
Agent interpreter exits. A transient supervisor coordination lease prevents
concurrent replay of one action ID from executing twice; it does not lock code,
results, or project editing. PIDs are checked together with process creation time.
If a supervisor vanishes and exit status cannot be observed, the process is
reported as `lost`, never as successful. Agent reasoning can inspect actual files
and explicitly resume a task's own saved state or initiate a fresh attempt.

## Completion and integration

`AgentYield` carries `status`, `wait_for`, `resume_after`, and `reason`. Waiting
sessions re-enter through the same run and workspace; they must not have their
files overwritten by a fresh branch copy. Configured budget exhaustion is a
separate state, not successful completion.

`finish` rejects active child processes, missing declared files, and omitted
`required_outputs` (also accepts planner `expected_outputs`). `metrics_file`, or
an existing default `metrics.json`, is loaded from disk into `observed_metrics` only when an actual managed process has a successful completion receipt. Model-authored numeric files alone cannot satisfy completion. Metric evidence is labelled `FILE_OBSERVATION`; scientific validity still requires source, method, data, and independent-result checks.
The result includes actual execution receipts. The model summary is explicitly
labelled as a summary, not substituted for measured numeric data.

SQL tool records use the persisted action ID. Graph commands and experiment
submissions use that same ID for replay protection. Legacy submissions lacking
an idempotency interface return an inspection-required error after an uncertain
interruption rather than being silently submitted twice.

## Validation

`tests/test_agent_processes.py` runs actual processes, including concurrent launch
replay, interpreter-exit survival, incremental logs, cancellation, configured
timeout, numerical calculation, and editable outputs.

`FOREST_LIVE_AGENT_TEST=1 .venv/bin/python -m pytest
 tests/test_live_agent_runtime.py tests/test_live_research_controller.py -q -s`
uses a real local Ollama `qwen2.5:3b` provider. These checks never substitute a mock
model. The controller check begins with an empty graph and requires two actual
prime-count implementations and a verified result. Runtime acceptance does not
establish research novelty or conference-level scientific reasoning.
