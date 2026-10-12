# Continuous, editable research

FOREST separates the scientific project's lifetime from a worker's bounded
execution round. Round completion saves state and queues the same Run again.
Legacy `max_steps`, expansion counts, `max_cycles` and `max_runs` do not terminate
research. Actual configured resource exhaustion, cancellation, verified goal
completion and an unresolved actionable blocker remain lifecycle conditions.

## State and recovery

The scientific checkpoint contains the Goal Contract, complete Graph reference
and revision, evidence and original artifact references, execution queue and
pending action, experiment configuration and code/data versions, seeds, and
decision history. Round snapshots have explicit record IDs. The transcript is
retained separately from the compact context presented to the model.

Resource totals belong to the supervisor's separate accounting ledger. A
checkpoint retains `external_ledger_id` and supervisor references, never a second
copy of usage totals. Continuations and Graph branches reconnect to that ledger.
Missing or inconsistent accounting requires reconciliation instead of a new
zero-usage ledger.

Managed work records its identity and full request before executing. Recovery
checks executor state, configuration, completion receipts, and original source
and artifact bytes. A running job is reattached; a verified completed job is
reused. An uncertain job enters reconciliation. Neither a missing PID nor a
completed chat message proves that an experiment ran successfully.
An uncertain, potentially live execution retains its actual wall clock and
capacity reservation. Its original supervisor deadline still applies; repeated
cancellation failures trigger a diagnostic fuse without declaring the job stopped.

Source/artifact records use explicit IDs and revisions with original snapshots.
File metadata caches avoid rereading unchanged files; changed metadata triggers
actual byte comparisons. These records do not use SHA256, content digests or
fingerprints for research identity, admission, caching or progress. Git and
security authentication retain their separate integrity mechanisms.

## Audit and implementation map

| Former restriction or failure path | Current implementation |
| --- | --- |
| `runtime.py` terminal step loop and `lifecycle.py` expansion allowance | Recoverable bounded rounds; the same Run continues without a cumulative cap |
| `scheduler.py` run-count admission and controller cycle count | Counts remain historical metadata; actual owner resource limits govern admission |
| Goal edits without an original contract/version | `services/research_evolution/` stores explicit parent versions and original conditions |
| Graph failure treated as a whole-project ending | `research_path.py` selects viable alternatives using dependencies, evidence and failed attempts |
| Reopened branches losing decision versions during undo | `kernel/graph.py` preserves prior decisions and advances route versions |
| Digest-based request, source, artifact, context or progress identity | Explicit work/record IDs, direct configurations and original byte snapshots in `processes.py`, `artifacts.py`, `context_store.py`, `lifecycle.py` |
| Renamed tasks or receipts counted as new information | Cross-round semantic tasks, actual evidence and equivalent completed work are compared directly |
| Changed evaluation scores pooled without scientific scope | `validation/comparability.py` and versioned evaluation contexts retain actual conditions and adaptive feedback |
| Paid parent reserving the entire allowance before child admission | `execution/submissions.py` persists intent; Worker settles the caller before ordinary child admission |
| Imported waiting state admitting a new side effect | `interventions/history.py` quarantines executable control fields as inspectable history |
| Copied denied data becoming a new authorized path | Copy-origin ACLs survive branch materialization, task preparation, import and duplication |
| Figure source permanently admitted by a fingerprint | Each render reads its supplied current source record and keeps factual checks for that render |
| Role-specific prompt additions missing new/fallback/direct calls | Canonical skill assembly at runtime and provider request boundaries |
| Direction review imposing a fixed counterexample quota | Evidence-driven questions and phase dispatch, with ordinary failure diagnosis |
| Manuscript revision rewriting sound paragraphs | Full nine-rule writing skill and exact-span, minimal-edit revision contract |

The outer resource ledger, actual model limits, permission boundaries, execution
timeouts and genuine scientific evidence checks remain authoritative. Editable
scientific decisions retain their prior conditions; edits do not rewrite results.

## Graph decisions and progress

Recovery selects viable existing branches using current dependencies, valid
evidence, failed attempts, explicit access constraints and scientific status.
Its decision records list selected and excluded paths with reasons. A technical
crash does not refute a hypothesis. An equivalent cloned task does not erase an
earlier failure or turn an unexecuted clone into successful evidence.

Progress persists across rounds. New verified observations, resolved questions,
meaningful method/configuration changes, evidence validity changes and relevant
Graph actions count. Repeated summaries, cosmetic source edits, duplicate work
receipts and newly named copies of the same task do not. Repeated errors trigger
diagnosis before another attempt. An exhausted set of legal paths pauses with
state intact; it does not create an unlimited replan loop.

## Editable goals and evaluation

Goal and protocol versions record their parents, reason, evidence, affected
branches and original conditions. Revising a goal can reopen a completed
controller. Earlier measurements remain attributable to their original goal,
protocol and saved experiment configuration. Feedback from evaluation used for
further optimization is classified as adaptive; it cannot later be silently
relabeled as independent evaluation.

The existing project PATCH and Graph command interfaces remain available. The
following routes expose complete version histories:

| Route | Action |
| --- | --- |
| `GET /api/projects/{id}/goal-contract` | Current Goal Contract and history |
| `POST /api/projects/{id}/goal-contract/versions` | Revise objective, criteria or contract with reason/evidence |
| `GET /api/projects/{id}/evaluation-protocols` | Evaluation version history |
| `POST /api/projects/{id}/evaluation-protocols` | Record a new evaluation definition and feedback classification |

Mutation requests support `request_id` and optimistic revision guards. Replaying
the same accepted request returns its original result. Reusing an ID for different
scientific intent returns a conflict. Import/export preserves version histories
and remaps their references with run, node and branch identities.

## Permissions

File and Bubblewrap command tools share explicit `readonly_paths` and
`denied_readonly_paths`. Resolved absolute paths use the same rules as relative
paths. Explicit denial wins even inside the workspace or runtime mount. Preflight
checks data readability, workspace, runtime and mount permissions before starting
work. Names such as `test`, `tests` or `heldout` are not access-control rules.
The trusted local execution backend uses the owner's actual operating-system
permissions; Bubblewrap provides the configured filesystem and network boundary.
Deterministic experiment and verification commands use the selected backend too.
Copy permission lineage is portable metadata outside writable Agent workspaces.
Original absolute sources remain recorded; separate relocated in-project aliases
retain the copied project's restrictions without relabeling historical sources.

## Original project skills and dispatch

Canonical runtime skills live in `research/agents/skills/`; matching
`.agents/skills/` files support Codex project discovery. They are original FOREST
skills, included in built packages and loaded by `assemble_agent_instructions`.
Missing mandatory resources fail visibly. `ensure_research_messages` applies
the policy at the model transport boundary, including resumed and fallback
requests and direct publication paths.

| Skill | Active use |
| --- | --- |
| `forest-autonomous-research` | Every research role: editable science, durable recovery, real execution |
| `forest-research-judgment` | Every research role: best estimate, confidence, evidence for/against, decisive uncertainty, useful next experiment |
| `forest-experimentation` | Experiment Designer, Experimenter, Baseline Reproducer, Engineer, Analyst, Evidence Verifier, Benchmark Curator |
| `forest-academic-writing` | Writer and manuscript, abstract, revision, caption and relevant figure stages |

All 19 registered roles share these policies: Orchestrator, Researcher, Theorist,
Engineer, Experimenter, Analyst, Reviewer, Writer, Figure Designer, Literature
Scout, Benchmark Curator, Experiment Designer, Baseline Reproducer, Visual
Selector, Layout Reviewer, Submission Reviewer, Evidence Verifier, Open Source
Research Advisor and Research Direction Reviewer. Judgment worksheets are used
for scientific decisions, not mechanically inserted into ordinary tool output
or manuscript paragraphs.

`delegate_research` dispatches a supported phase/role pair through the real Graph,
scheduler and worker. Experimentation covers design, execute, baseline, ablation,
statistics, reproducibility and validation; literature covers search, synthesis
and source checks; paper covers drafting, revision, abstract and visualization;
pipeline covers orchestration, integration and state updates; review covers
methodology, domain review, counterargument and editorial synthesis. The child
inherits applicable permissions, supervisor references and its parent's lineage.
A nonblocking `derived_from` edge preserves provenance without a dependency
deadlock. Submissions persist an action-bound intent before yielding the caller.
The worker settles the caller's real usage, admits supporting work through the
ordinary scheduler, and resumes the same caller after the submitted work settles.
The submission receipt is not independent scientific verification.

The writing skill includes the full Press-Release Principle and precise local
edits, with the required sentence: "Avoid a single semantic unit forming a
paragraph alone." Actual drafts, revisions and publication model requests use
the writing policy. Scientific source records can change between figure renders;
each individual render remains faithful to its supplied source and measurements.

## Validation and migration

Key regression files are `test_worker_round_continuation.py`,
`test_agent_output_recovery.py`, `test_submission_handoff.py`,
`test_scientific_checkpoint.py`, `test_process_reconciliation.py`,
`test_research_path_recovery.py`, `test_research_evolution.py`,
`test_support_workflow_dispatch.py`, `test_research_freedom_execution.py`,
`test_policy_assembly_skills.py`, `test_bubblewrap_isolation.py` and
`test_postgres.py`, plus `test_instruction_recovery.py`,
`test_portable_execution_history.py`, `test_execution_authorization.py` and
`test_small_window_instruction_recovery.py`.
They exercise continuation across old step boundaries,
actual execution/restart, original-artifact receipts, Graph alternatives,
versioned scientific changes, policy delivery, permission boundaries and real
database concurrency. The fitted Ridge regression test performs actual fitting,
prediction and validation under changed settings and preserves both outputs.

`tests/live_research_policy.py` is an opt-in signed-in Codex CLI check for actual
role-policy responses. Its synthetic decision fixtures validate policy behavior;
they are explicitly not research results. Logs retain real requests, responses
and usage, including failures. Subscription usage without price information has
an unknown cost, not zero cost.

Database migrations are additive and preserve old versions. Legacy hash fields
are not required to read scientific history. Legacy executor records whose
configuration and real completion cannot be established enter reconciliation;
migration cannot invent a successful receipt. Factory toolset version 9 updates
recognized old factory defaults while preserving explicit user tool choices.
