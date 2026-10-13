# Evidence contract and source map

Snapshot inspected: `ff16c3f656f94d053d1bba65c6b3a07a5feaccde` (2026-10-13).
All source paths below are FOREST-root relative. Recheck the actual target HEAD.
Tests below are inspected contract evidence, not a report of test execution.

## Read by consumer

| Task | Source pointers | Reason |
| --- | --- | --- |
| Producer/verifier binding | `services/api/verification.py:latest_node_run`, `_binding_valid`, `_evaluate`, `verification_for_run`, `prepare_verification_enqueue`, `finish_verification` | Current revision/attempt, service receipts, artifact re-read |
| Verification request/check grammar | `services/api/verification.py:submit_verification`, `RESERVED_CONFIG`; `research/validation/handoff.py:KINDS`, `validate_contract`, `check_contract` | Supported actual-file checks; forbidden caller verdict identities |
| Numerical consumption | `services/api/verification.py:numerical_coverage_for_run`, `verification_gate`, `evidence_sources`; `services/interventions/acceptance.py:acceptance_gate` | Path/pointer coverage and purpose gates |
| Goal relevance | `services/interventions/applicability.py:goal_scope_snapshot`, `goal_applicability`, `decide_applicability`; `services/interventions/contracts.py`; `services/research_evolution/service.py` | Scoped directions, current goal-contract versions, reuse/exclusion |
| Stale locations | `services/interventions/dependencies.py:record_bindings`, `validate_bindings`, `invalidate_dependents`; `research/paper/dependencies.py` | Explicit same-project source/target locations and transitive invalidation |
| Lineage/source freshness | `services/api/research_runtime.py:run_lineage`; `services/worker/main.py` current-result promotion; `services/worker/scheduler.py:enqueue` | Saved run versus current working files, retry/current distinction |
| Manuscript evidence | `research/paper/evidence.py:read_metrics_artifact`, `manuscript_metrics_eligibility`, `collect_evidence`; `research/paper/manuscript.py`; `research/paper/model_draft.py` | Actual finite numeric files, pointers, claims/references and rereads |
| Paper application/export | `services/api/resources.py` paper handlers; `services/api/paper_state.py`; `research/paper/dependencies.py` | Snapshot protection, proposal acceptance, compiled revision |
| Comparisons/statistics | `research/validation/comparability.py`; `research/analysis/statistics.py`; `research/planning/scoreboard.py`; `docs/STATISTICAL_PRESENTATION.md` | Declared conditions versus verified coverage, paired independent units |
| Figures | `research/figures/workflow.py`; `research/figures/render.py`; `research/figures/statistical.py`; `docs/SCIENTIFIC_VISUAL_NARRATIVE.md` | Real source data/output paths and conceptual-versus-measured visuals |
| Existing evidence UI | `apps/web/src/RunEvidence.tsx`; `apps/web/src/interventions/ApplicabilityReview.tsx`; `DependencyImpact.tsx` in that directory; `apps/web/src/PaperLayout.tsx` | Independent readback/errors, historical selection, stale locations |
| API/docs | `packages/contracts/openapi.json`; `packages/contracts/api.generated.ts`; `services/api/contracts.py`; `docs/EVIDENCE_WORKFLOW.md`; `docs/WORKFLOW_INTERVENTIONS.md`; `docs/FRONTEND_API_GUIDE.md` | Wire format and implemented limits |
| Canonical research behavior | Four `research/agents/skills/*/SKILL.md`; `research/agents/policy.py`; `research/validation/protocol.py` | Preserve evolving research and original scientific requirements |

## Current and historical identity

Node revision identifies the configuration that produced a run; project revision
guards graph edits. `latest_run_id`, `results_current`, `result_revision`, and
rerun_generation determine current-result promotion. Historical retry preserves
its saved node_revision/configuration/provider context; new Node Run uses current
node config. Do not select the most recent timestamp without those guards.

`GET /api/runs/{ident}/lineage` walks same-project dependencies and returns
missing sources as `{id,missing:true}`. Its `current` is node-revision/result
flags, not a blanket latest/verification/semantic assertion. `source_freshness`
compares saved workspace files with existing current branch working files;
statuses include `matches_working_file` and `working_file_changed`. An empty
comparison list does not establish all files are current. API config excludes
provider_snapshot/env; keep redaction and do not reconstruct secrets for display.

## Source-bound verification

`GET /api/runs/{ident}/verification` is the current authoritative receipt view.
Verifier view names bound producer node/run/revision, check results, paths,
numerical scope, and reason. Producer view aggregates current linked verifiers.
At this HEAD, aggregation chooses rejected before accepted before inconclusive,
then unverified when no linked verdict exists. Accepted path union contains only
accepted checks; never promote one accepted subcheck into acceptance of everything.

Binding validity requires current producer revision and latest run of that
revision, completed source, current result flags, matching verifier config/revision/
latest identity, and unchanged execution specification. Source configuration
edited after execution, archived/deleted sources, stale verifiers, or changed
observed artifacts revoke the old receipt. Actual checks reread files on admission.
Copying both source/repeat files to the same new value does not authorize reuse
of a prior execution receipt.

`POST /api/verification/run` requires project_id; optional node_id selects a
same-project node with config.kind verification. task config includes a declared
`verification:{producer_node_id,checks,...}`. request_id replay validates verifier,
node, and normalized contract. Source/verdict/binding fields are service-owned:
caller cannot submit `_verification_binding,_verification_contract,
_verification_sources,verification_result,verification_status` as top-level config.
The response is a queued Run, not independently verified evidence.

## Actual verification checks

Each check has a unique id and supported kind. Read `validate_contract()` when
building an editor; it rejects unknown parameters and unsafe/absolute paths.
The contract allows only producer_node_id, checks, description, version (1).

| Kind | Actual checked scope and fields |
| --- | --- |
| `numeric_compare` | Source JSON versus independent repeat JSON, optional exact pointers; finite values and explicit abs/relative tolerances. |
| `byte_compare` | Full source/repeat bytes; equality is limited to the named artifact. |
| `csv_integrity` | Declared workspace producer/verifier (default producer), required columns, unique_by, numeric_columns, optional expected cells. |
| `csv_coverage` | CSV integrity plus required explicit expected coverage cells matching unique identity and labels/values. |
| `paired_recompute` | Actual source CSV and producer results JSON; unit/baseline/candidate columns, direction, computed-field JSON-pointer bindings. Optional sampling declares independent units, seed/bootstrap/confidence and CI bindings. |
| `data_contract` | CSV or JSON record arrays, typed columns with explicit nullable/unit, identity_columns, units_file, and split column/disjoint identities/expected counts; optional extra columns. |

numeric/byte repeated-result checks require a successful independent command
execution receipt from the actual backend. Producer-authored pass JSON and equal
files alone are insufficient. CSV integrity is not numerical metric coverage.
Paired CI is not accepted without declared sampling/CI bindings. Data-contract
unit-label/schema checks do not establish physical units or study validity.

`numerical_scope` records the file, artifact_scope and checked JSON pointers.
`numerical_coverage_for_run()` requires one unambiguous accepted scope, every
finite numeric leaf checked, and saved metrics consistent with the file for
metric artifacts. Show missing_pointers/recorded_metric_mismatches when surfaced.
Verification policy can be optional/required, and explicit verifier guards still
apply. A UI must preserve these server controls rather than impose a new gate.

## Applicability and purpose acceptance

| View | Endpoint/values and interpretation |
| --- | --- |
| Goal relevance | `GET /api/runs/{ident}/applicability`: current/approved ready; excluded/needs_review/unknown not ready. Computational verification stays independent. |
| Evidence-use decision | `POST /api/runs/{ident}/applicability/decisions`: request_id, reviewed project expected_revision, choice reuse/exclude, nonempty reason; completed source only. Decision binds goal, scoped direction, goal-contract version. |
| Handoff acceptance | `GET /api/runs/{ident}/acceptance`: ready,purpose,scope,contract,failures,source_checks,comparison,confirmations. Read current result, do not synthesize pass from execution status. |

Execution may span multiple Goal values. Preserve execution_goals, scoped history,
and goal-contract version rather than stamp every computation with the final
request's Goal. Reuse/exclusion changes use against the reviewed current Goal;
it neither reruns computation nor proves unchecked numerical results valid.

AcceptanceContract has purpose, artifact_paths, confirmation_run_ids, comparison_fields.
`exploratory` (also omitted contract) has no scientific acceptance claim.
`raw_data` requires explicit paths and accepted data_contract checks.
`comparison` requires actual completed same-project sources, independent current
verification, current Goal relevance, complete numerical coverage, matching
declared comparison conditions. `major_claim` adds distinct actual completed
research_phase confirmation runs with independent current numerical verification
and matching conditions. Show each failure. Scientific importance/protocol quality
and publication readiness require scientific judgment beyond these checks.

## Structured dependencies and artifacts

`dependency_bindings` entries retain source_kind, source_id, target_path plus
artifact_path, source_revision, metric_pointer/metric_id, source_run_id, passage_id,
target_artifact_path when present. Source kinds: run,node,figure,claim,source,
analysis,file. Validate source project/path ownership and JSON target paths.
Legacy typed IDs such as run_ids/source_run_ids/figure_id remain meaningful;
plain prose mentioning an ID creates no dependency. Relevant changes mark records
needs_update and preserve exact stale_dependencies target locations transitively.
Layout-only edits do not invalidate the same scientific bindings.

Figures retain kind, run_ids/source_run_ids, metric, actual data, style, code/origin,
caption/purpose, outputs, visual_selection/review. Output maps identify available
real project-relative files; missing formats are unavailable. A schematic or
method illustration is conceptual and cannot substitute for measured plots.

Paper generation explicitly selects completed experiment/command/agent run_ids
with finite readable numeric artifacts; manuscript_evidence.ready reflects that
metric-artifact eligibility, not independent verification or submission readiness.
Default metrics resolution prefers run-root metrics.json, otherwise workspace plus
configured metrics_file. Every leaf gets an original run ID and exact pointer.
Renderer token bindings `[[metric:m0]]`/`[[source:ID]]` reject absent IDs and omitted
supplied central-claim contrary references; they do not mechanically establish
semantic entailment or novelty. Metric copies are reread before rendering.

Paper data preserves bindings,numeric_bindings,source_run_ids,figure_bindings,
source/bibtex, actual pdf_path/source_dir. Export current PDF requires
compiled_revision == paper revision; STALE_PDF needs recompilation. Generated
bundle applies only against its saved matching unedited paper snapshot; otherwise
retain the complete editable review proposal. Scientific review, writing proposal,
compilation, and export remain separate outcomes.

## Risk states and focused regressions

| Risk | Required observation and source tests |
| --- | --- |
| Artifact/config changed after pass | Verdict becomes inconclusive/unverified with reason; `tests/test_verification_service.py:test_both_artifacts_edited_cannot_reuse_execution`, `test_post_execution_source_configuration_change_requires_new_run`. |
| Wrong verifier/partial scope | Consumer waits rather than consumes unchecked output; same file tests `test_unrelated_accepted_verifier_cannot_authorize_input`, `test_csv_check_does_not_authorize_unchecked_metrics_or_paper`, `test_partial_numeric_scope_does_not_authorize_whole_json`. |
| Spoofed pass/no rerun | Inconclusive or 422 reserved config; `test_numeric_check_requires_actual_command_not_prepared_pass`, `test_standalone_verification_current_config_and_idempotency`. |
| Reverified files but old recorded metrics | No promotion of old numbers; `test_reverified_edited_file_cannot_promote_old_recorded_numbers`. |
| Deleted/historical source | Missing/inconclusive, no fresh replacement; `test_deleted_producer_is_inconclusive`; `tests/test_research_verification_workflow.py`; `tests/test_run_budget_resume.py` historical retry cases. |
| Changed/scoped Goal | Computation remains verified, downstream use gated; `tests/test_intervention_worker.py:test_goal_edit_preserves_actual_verification_but_gates_downstream_use`, `test_scoped_goal_edit_preserves_computation_and_requires_new_use_decision`. |
| Purpose mismatch | Explain failed data/comparison/confirmation gate; `tests/test_acceptance_worker.py`; `tests/test_research_comparability.py`. |
| Structured stale locations | Preserve unrelated prose while bound paragraph/figure becomes needs_update; `tests/test_control_validation.py`; `tests/test_interventions.py` dependencies/layout_dependencies cases. |
| Real paper metrics/references | No fallback or fabricated measurements; `tests/test_evidence_workflow.py:test_actual_numeric_artifact_to_editable_latex`, `test_no_case_fallback_or_fabricated_reference`, `test_contrary_evidence_must_be_referenced`, `test_edits_change_artifacts_without_locking_and_stale_macro_is_detected`. |
| Evidence-facing UI | `apps/web/src/RunEvidence.test.tsx`; `apps/web/tests/paper-evidence-eligibility.spec.ts`; retain independent errors and actual eligibility. |
| Scientific figure bindings | `tests/test_diagram_review_projection.py`; `tests/test_statistical_presentation.py`; evidence and uncertainty stay unchanged by presentation edits. |

Choose cases affected by the implementation; do not run every research suite for
a display change. Integrated evidence changes require an isolated real local
backend/worker workflow with actual artifacts, guided by the browser-testing skill;
fixture-only evidence cannot replace it. Inspect harness cleanup before execution.
Do not start existing user research tasks or use an unauthorized paid provider.
