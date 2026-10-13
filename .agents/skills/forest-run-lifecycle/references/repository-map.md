# Run lifecycle contract and source map

Snapshot inspected: `ff16c3f656f94d053d1bba65c6b3a07a5feaccde` (2026-10-13).
Paths are FOREST-root relative. Recheck affected source at the target HEAD.
Listed tests were inspected as contract evidence; execute relevant cases for a
real change before claiming verification.

## Read by task

| Task | Source pointers | What they decide |
| --- | --- | --- |
| Submit/inspect/action/output | `services/api/main.py:run_node`, `run_branch`, `runs`, `run_action`, `output`, `events`; `services/api/schemas.py:RunRequest` | Response unions, action routing, byte cursors and SSE |
| Scheduler/state/saved intent | `services/worker/scheduler.py:ACTIVE`, `TERMINAL`, `enqueue`, `enqueue_nodes`, `enqueue_selected` | Actual states, dependencies, request reuse, saved node/provider revision |
| Pause/resume | `services/interventions/lifecycle.py:resume_available`, `prepare_resume`, `accept_lifecycle`, `control_run`, `finish_lifecycle` | Admission, elapsed budgets, external confirmation |
| Cancel/stop-during-edit | `services/interventions/application.py:record_effects`, `accept_cancel_in_session`, `cancel_run`, `process_effects`, `summarize` | Durable outbox, attempt fencing, effect readback |
| Instructions/decisions | `services/api/interventions.py`; `services/interventions/contracts.py`; `services/interventions/controls.py` | Exact action/revision, boundary, successful request-delivery receipts |
| Config/session/lineage | `services/api/research_runtime.py:configure_run`, `agent_session`, `run_lineage` | Shallow config merge, managed identities, separate usage ledger |
| Debug checkpoint | `services/api/resources.py` checkpoint handler; `research/execution/checkpoint.py` | Stopped checkpoint payload edits versus separate Resume |
| Actual worker/runtime | `services/worker/main.py`; `services/worker/execute.py`; `research/execution/recovery.py`; `research/execution/process_manager.py`; `research/execution/transaction_fence.py` | Dispatch/recovery/current-result promotion; safe process boundary |
| Controller/run-mode settings | `services/worker/controller.py`; `research/planning/loop.py`; `docs/CONTINUOUS_RESEARCH.md`; `docs/WORKFLOW_INTERVENTIONS.md` | Autonomous planning versus execution mode, work-round continuation |
| Current frontend | `apps/web/src/Workspace.tsx:RunPanel`; `apps/web/src/pages.tsx`; `apps/web/src/RunEvidence.tsx`; `apps/web/src/interventions/InterventionPanel.tsx`; `apps/web/src/api.ts`; `apps/web/src/apiClient.ts` | Refresh lifecycle, readback UI, transport error handling |
| Wire reference | `packages/contracts/openapi.json`; `packages/contracts/api.generated.ts`; `services/api/contracts.py`; `docs/FRONTEND_API_GUIDE.md` | Null/unknown fields, operation-specific types, request IDs |
| Portable authority | `services/interventions/history.py`; `services/interventions/origin_history.py`; `services/api/main.py` import/duplicate handlers; `docs/WORKFLOW_INTERVENTIONS.md` | Preserve history, quarantine executable controls |

## Submission identities and shapes

RunRequest scopes: `single`, `ancestors`, `descendants`, `affected`, `to_here`,
`from_here`, `subtree`; default `single`. Config is extensible JSON and
request_id optional on the backend; use an explicit retained ID in the frontend.

| Operation | Returned receipt |
| --- | --- |
| `POST /api/nodes/{ident}/run` | One Run when exactly one scheduled; otherwise `{runs,run_ids}` |
| `POST /api/branches/{ident}/run` | `{runs}`; handler uses descendants even when another scope is supplied |
| `POST /api/projects/{ident}/runs/selected` | `{runs,run_ids}`; selected `node_ids` belong to project |
| `GET /api/projects/{ident}/runs` | Bare array, descending creation, `limit` capped 500, pagination required |
| `GET /api/runs/{ident}` | Saved Run plus tools; removes config.provider_snapshot |
| `POST /api/runs/{ident}/retry` | New Run preserving selected snapshot; not same ID |

Generic scheduler reuse returns the existing project/request-ID run before
rechecking a new payload; do not assume global payload equality. Specialized
repository/verifier/layout and intervention handlers validate their own identity.
Persist exact submitted intent alongside its ID and clear pending UI only after
accepted/rejected/readback is known. A different input gets a new ID.

## Source-checked controls

| Action | Admitted source states/effect at inspected HEAD |
| --- | --- |
| `pause` | `queued,waiting,waiting_input,budget_exhausted,running,paused`; reject a pending lifecycle request. Undispatched work becomes paused without creating an executor; live work waits for safe reconciliation. |
| `resume` | `paused,waiting_input,waiting,budget_exhausted`; also specific legacy Agent context-budget failures accepted by `resume_available()`. Branch must be active; remaining time/permissions/budget apply. |
| `cancel` | Durable cancellation; terminal runs return without new execution. Queued/never-dispatched targets cancel immediately; live process/container/SSH effects need readback. |
| `retry` | Only TERMINAL; enqueue saved config, dependencies, original node_revision and requested timeout. Preserve provider snapshot and source context. |
| `skip` | Queued only; marks skipped and finished. |
| `priority` | Body priority converted with `int`; server does not restrict it to an assumed UI status subset. |

`ACTIVE=(queued,running,pausing,paused,waiting_input,waiting,budget_exhausted)`;
`TERMINAL=(completed,failed,cancelled,interrupted,skipped)`. Unknown strings remain
visible. A stopped debug checkpoint or budget exhaustion can be resumable;
ordinary failed execution requires retry, apart from the exact legacy exception.

Lifecycle/cancel response includes persisted Run and `intervention`. Fields:
parent `id,request_id,observed_revision,applied_revision,intent,impact,status,
accepted_at,applied_at,effects`; effect `run_id,attempt_id,action,status,
observation,error,attempts,retry_after`. Parent states observed in source:
`accepted,applied,partially_applied,needs_attention,superseded`. Effect transitions
include `pending,applying,applied,uncertain,superseded`; aggregation also handles
failed effects. These wire fields are strings, not closed UI enums.
`pausing` is used while both Pause and Resume reconcile; label by pending action.

`services/interventions/workspaces.py:record` deliberately stores a Branch ID in
effect.run_id for `action:materialize_workspace`; its processor resolves Branch
with that same field. Route effect targets by action: workspace materialization
links to the branch workspace, lifecycle controls to the run. Do not rename IDs,
rewrite server records, or infer a broken link solely from the field name. Verify
that the destination screen actually consumes its branch selection/query.

Prune applies `hold_scheduling` to queued runs as `waiting_input`; running work
continues. Restore sets active branch but does not launch held runs. Each selected
run needs Resume. Process pause waits for DB transactions to finish to avoid
holding locks needed for receipt persistence. Uncertain effects retry via the
outbox after restart; this is not arbitrary-external-service exactly-once delivery.

## Instructions and action review

`POST /api/projects/{ident}/instructions` requires request_id, project
expected_revision, nonempty text, `scope:project|branch|node|run`, target_id where
needed, `boundary:next_request|next_run`. Ordinary comments stay annotations.
The allowed combinations come from `services/interventions/controls.py:send_instruction`:
run scope requires `kind:agent`, status in scheduler `ACTIVE`, and `next_request`.
Run + `next_run` or non-Agent target returns `INVALID_SCOPE`; stopped run returns
`RUN_STOPPED`. Project/branch/node may use either boundary with validated target
ownership. Do not expose all scope × boundary combinations as equally executable.
The accepted instruction becomes applied after a successful physical model
request includes it fully or through complete recorded pages. In-flight requests
retain original context; late responses cannot confirm a newer attempt.

`GET /api/projects/{ident}/decisions`,
`POST /api/decisions/{ident}/answer`: expected_revision, `choice:accept|edit|reject`,
reason, optional edited action, `resume:true` default. Answer state and run resume
are separate; expose resume_error, run_status, can_resume. Already answered
decisions use ordinary Resume rather than repeat Answer. Changed project/action
state makes an approval stale. Source decision states include `pending,accepted,
rejected,consumed,stale`. Cancel marks pending/unused accepted actions stale;
rejection remains rejected, with its original answer retained.
Intervention list uses limit and paired `before,before_id` for timestamp ties.

## Configuration, current results, and recovery

`PATCH /api/runs/{ident}/configuration` shallow-merges supplied keys and preserves
unknown editable keys. No revision guard. Reject runner-owned `provider_snapshot,
execution_attempt,resolved_inputs,project_goal,_repository_source,
_verification_binding,_verification_contract,_verification_sources,
verification_result,verification_status`. Started runs cannot change provider
identity (`PROVIDER_BOUND`); enqueue a new run. Scientific changes after execution
mark configuration_changed_after_execution and verification unverified.
Do not round-trip public redacted config as a complete replacement.

`PATCH /api/runs/{ident}/checkpoint` needs waiting_input with resource.checkpoint;
sets override from body.payload, does not resume. Explicit Resume follows.
Session `summary=true` returns saved research lifecycle plus usage from the
separate matching `agent_usage.json` ledger. Missing/invalid ledger is usage:null
with diagnostics, never zero. `waiting + wait_for.type:work_round` means automatic
continuation with same run, checkpoint, and ledger. Preserve manual autonomy
default off when unset, saved start/continue choice, and independent ready task
slots (1..32, default one); UI must not manufacture dispatch decisions.

Current-result promotion in `services/worker/main.py` is guarded by matching
node_revision/latest_run_id and rerun_generation/dependency state. Scheduler
historical retry only updates node execution/current-run identity when revisions
match. History remains inspectable after edits; a recent creation timestamp is
not enough to select current evidence.

Import/duplicate supersedes effects, stales decisions, interrupts active runs,
and disables pending workspace branches; restore quarantines pending controls
and pauses controllers. Preserve inspectable history, then explicitly authorize
fresh execution. Lost process identity requires attention, not blind replacement.

## Accounting and transports

Project seconds is cumulative wall allowance. Resume/admission recompute remaining
time including prior usage, other-run reservations, and live paused/yielded work.
Legacy max_runs is metadata and does not cap research lifetime. Keep project cost
and reserved cost separate; provider allowance has `scope:all_projects`.
Unknown total/limit/unpriced request cost is null/unknown, not zero. Requested task
timeout remains an independent cap when project budget grows.

Output `GET /api/runs/{ident}/output?offset=...` returns text and a byte offset;
search filters returned text but offset still advances through read bytes. Small
multibyte pages may read enough bytes to complete the next character. Deduplicate
retried chunks and drain the final terminal chunk.
SSE `/api/projects/{ident}/events` persists integer sequence ID and named event;
replay uses Last-Event-ID. `cursor_reset` supplies resume_after_sequence; reload
REST state and resume. Terminal `/ws/projects/{ident}/terminal` is a separate
owner-cookie/loopback PTY; its closure does not describe worker completion.

## Focused regressions

| Changed behavior | Existing isolated cases to inspect/run |
| --- | --- |
| Submit/control/readback UI | `apps/web/src/RunPanel.test.tsx`; `apps/web/tests/run-list-refresh.spec.ts`; `apps/web/tests/succeeded-run-filter.spec.ts`; inspect actual completed backend status rather than filename wording. |
| Reconciliation/branch hold | `tests/test_intervention_worker.py:test_pause_does_not_suspend_executor_with_database_transaction`, `test_intervention_crosses_actual_worker_and_transport_boundaries`, `test_prune_restore_holds_queued_runs_until_individually_resumed`. |
| Historical retry/provider | `tests/test_control_validation.py:test_role_provider_priority_and_historical_retry_use_real_saved_snapshot`; `tests/test_run_budget_resume.py` historical node/recreated-ID retry cases. |
| Same-run resource resume | `tests/test_run_budget_resume.py`; `tests/test_owner_budget_resume.py`; `tests/test_budget_lifecycle.py`; task cap/project growth and other-run consumption remain distinct. |
| Durable decisions/instructions | `tests/test_interventions.py` rejected_consumption/resume_visibility cases; `tests/test_intervention_worker.py` instruction/cancellation cases. |
| Recovery/import authority | `tests/test_portable_execution_history.py`; `tests/test_intervention_restore.py`; `tests/test_execution_reconciliation.py`; `tests/test_recovery_runtime.py`. |
| Session/usage continuity | `apps/web/src/RunEvidence.test.tsx`; `tests/test_worker_round_continuation.py`; `tests/test_context_capacity_resume.py`. |
| Byte output/events | `tests/test_api_contracts.py`; `tests/test_api.py`; `tests/test_postgres.py` cursor/replay cases when event ownership is touched. |

Run the relevant local cases with cleanup; broaden only for a concrete failure
or changed shared owner. Integrated control changes require isolated real backend/
worker effects and actual artifacts, following the browser-testing skill. Fixture
responses alone cannot demonstrate reconciliation. Do not start existing user
research tasks or admit work through an unauthorized paid provider. Schema/type
checks complement observable state/recovery tests.
