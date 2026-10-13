# Graph contract and source map

Snapshot inspected: `ff16c3f656f94d053d1bba65c6b3a07a5feaccde` (2026-10-13).
Paths below are relative to the FOREST repository root. Recheck the affected
handler/kernel/types at the target HEAD before implementation. Test pointers
describe inspected assertions, not a claim that this skill ran those tests.

## Read by task

| Task | Source pointers to read | Why |
| --- | --- | --- |
| Wire requests/responses | `services/api/schemas.py:GraphCommand`; `services/api/contracts.py`; `packages/contracts/openapi.json`; `packages/contracts/api.generated.ts` | Required identity/revision, extensible node fields, documented response types |
| Graph fetch/node save | `services/api/main.py:graph`, `preview`, `command`, `edit_node` | Node PATCH guard is project revision; response is GraphCommandResult |
| Graph command semantics | `research/kernel/graph.py:OPERATIONS`, `GraphCommandService`, `ImpactAnalyzer` | Actual operation set, patch rules, DAG and impact categories |
| Transaction/receipt/effects | `services/interventions/application.py:existing`, `apply_in_session`, `apply_commands`, `record_effects` | Deduplication, intent equality, atomic structure and asynchronous controls |
| Batch/page | `services/api/research_runtime.py:graph_batch`, `graph_page` | Supported subset; incident edges can have endpoints outside page |
| Fork/merge/recovery | `research/kernel/artifacts.py:BranchWorkspace`; `services/interventions/workspaces.py` | Copy plans, actual source files, materialization fencing |
| Client canvas/editor | `apps/web/src/Workspace.tsx`; `apps/web/src/api.ts:executionLinks`, `hasCycle`; `apps/web/src/apiClient.ts` | Existing drafts, implicit edges, typed transport |
| Editor/impact policy | `docs/FRONTEND_API_GUIDE.md`; `docs/WORKFLOW_INTERVENTIONS.md`; `docs/API_REFERENCE.md` | Review revision retention, held work, history limitations |
| Scientific policy boundary | `research/agents/skills/forest-autonomous-research/SKILL.md`; `forest-research-judgment/SKILL.md`, `forest-experimentation/SKILL.md`, `forest-academic-writing/SKILL.md` in that directory; `research/agents/policy.py` | Keep existing research policy/runtime assembly intact |

## Wire shape and known documentation gaps

Command body: `{request_id, expected_revision, operation, targets:[], params:{}, run:false}`.
Optional `project_id` must match the path. `GraphCommand` rejects extra top-level
fields; `operation` is validated against the kernel, not a generated finite enum.
Preview returns `GraphImpact`, not a graph or receipt. Apply currently returns
`revision`, public `graph`, `impact`, `run_nodes`, `run_ids`, `intervention`, and
application/count fields. The intervention receipt covers effects separately.

`reopen_branch` and `revise_goal` are present in kernel `OPERATIONS` and tests
although some documentation operation summaries omit them. Inspect source before
exposing these actions rather than silently treating the summary as exhaustive.
At this HEAD, the batch handler delegates to the same service and returns its
full public result, while documented `GraphBatchResult` emphasizes counts/revision
and says no graph. Consume only verified fields or refetch graph; do not assume
the generated response type lists every currently returned field. If the task
changes this contract, fix source/schema together and regenerate deliberately.

`request_id` is project-scoped. Application receipt lookup precedes revision
comparison and compares saved intent. Reusing an ID for changed commands or the
wrong expected revision can return `REQUEST_ID_CONFLICT`. After a lost response,
retain the exact submitted command/ID; do not stamp it with the refreshed revision.
Node PATCH creates a server request ID, so it lacks this client replay handle.

## Operation-specific actions

| Operation | Targets/params and effect |
| --- | --- |
| `add_node` | Direct node fields or `node`; optional `parent_id`/targets. Parent branch must match; parent selection can add execution edges. |
| `edit_node` | Selected targets, direct fields or `patch`; deep merge; optional `stop_current_run`. `positions` must have exactly each target with finite numeric `x,y`. IDs, project, revision and execution fields are managed. |
| `apply_instruction_patch` | Selected node, `instructions` or exact `old_text`/`new_text`; old span must occur exactly once or 409 `patch_conflict`. |
| `delete_node` | Select IDs; with descendants choose `strategy:subtree|reconnect|visual_only`. Missing source references remain visible; history/files are retained. |
| `add_dependency`/`remove_dependency` | `source,target` or two targets; exact `relation`; addition retains `input_mapping`. |
| `fork_branch`/`clone_subtree` | Origin target; `name`, `node_ids`, `include_descendants`, `destination_branch_id`, `copy_policy`. Clones remap input/verifier identities; inherited outputs are not current results. |
| `insert_before`/`insert_after` | Pivot target plus node fields, insert into execution edges. |
| `reparent_subtree` | Pivot, `parent_id`/`new_parent_id`, `move_to_branch`, `input_map`; inspect rebinding requirements. |
| `merge_branches` | `left/right` or branch-ID forms, `name`, `resolution`, `dependencies`; require choices for reported source/config conflicts. |
| `split_node` | Original target and at least two `parts`; archive original, reconnect sequence, review dependent output bindings. |
| `group_nodes` | Targets, optional `group_id,title,collapsed`; display grouping, no experiment. |
| `prune_branch` | `branch_id` or node branch; `pruned`, scheduling hold for queued work. Existing execution continues. |
| `restore_branch`/`reopen_branch` | `branch_id`; optional `reason,evidence_run_ids,reconsider_node_ids`; active branch, monotonic branch revision/evolution history. Reconsideration stays in this branch. Held runs need explicit resume. |
| `revise_goal` | `objective,contract,success_criteria`, optional `reason,evidence_run_ids,affected_branch_ids`; goal impact refreshes context without relabeling prior computation. |
| `set_main_branch` | Select branch; main flag and active branch status. Do not model it as cancelling another branch. |
| `undo`/`redo` | Current graph revision; editable history, no reversal of completed external effects. |

Batch accepts 1..200 commands from this smaller set only:
`add_node`, `edit_node`, `add_dependency`, `remove_dependency`, `prune_branch`,
`restore_branch`, `set_main_branch`. One layout `edit_node` can contain positions
for more than 200 nodes; command limit is not node limit. The batch has one undo
snapshot. Workspace operations require a separate graph command.

Fork file policy defaults: copy code, reference data, exclude results,
`max_copy_bytes` 100 MiB. Strategies `copy|reference|exclude`; booleans map to
copy/exclude. Saved node result inheritance and file policy remain distinguishable.
Merge `resolution.files` maps paths to `left|right|base|delete` or `{content:...}`;
`resolution.config` is `left|right` or an explicit object. Missing base invokes
two-way comparison; do not invent a common ancestor.

## Dependencies and path coordinates

`EXECUTION={depends_on,consumes}`. `PROPAGATION` additionally includes
`derived_from,evidence,cites`; these do not all impose execution ordering.
Implicit consumes edges come from `inputs[].node_id`, `verification_node_id`,
`config.verification.producer_node_id`, `config.required_verification`.
Backend `execution_edges()` and frontend `executionLinks()` must agree.
Do not drop implicit references because no canvas edge exists.

File API paths are project-relative. InputReference `path` is source branch
workspace-relative unless `project_scope:true`; `branch_id/node_id` selects
the source. `destination` is relative to the receiving run workspace. Branch
workspace is project-relative; run `output_path` points to the saved run folder.
Node movement must not reinterpret these coordinates.

## Impact and risk states

Keep the server result, including `changed_nodes,affected_nodes,rerun_nodes,
refresh_nodes,reasons,categories,files,conflicts,actions,input_bindings_required`
where present. Layout/display/archive refresh locally and do not rerun; Goal
edits refresh applicable context; semantic edits propagate through relevant
dependencies; statistical edits reanalyze relevant analysis and refresh figures/
paper; figure style/paper wording do not retrain. See `_category()` and
`ImpactAnalyzer.resolve()` before modifying category behavior.

| State | Required UI behavior |
| --- | --- |
| 409 stale draft/preview | Preserve original draft/guard, reload changed fields/impact, re-review. |
| `conflicts` or missing/rebinding inputs | Explain specific source/target; do not announce complete/ready. |
| `materializing` branch | Show pending workspace receipt; no execution or owner file edits until confirmed. |
| `pruned` branch/`branch_scheduling_hold` | Preserve queued-run history; restore then explicitly resume selected held runs. |
| inherited outputs/`results_current:false` | Inspectable historic provenance; never assert new execution. |
| pending/uncertain intervention effect | Follow receipt; accepting graph edit is distinct from confirmed stop/copy. |
| unknown node type/status/config | Preserve and display raw value; do not coerce into success or erase. |

## Focused regression selection

| Behavior | Existing tests and expected observation |
| --- | --- |
| Draft/preview concurrency | `apps/web/tests/interventions.spec.ts`: two tabs, stale subtree preview yields 409; dirty draft survives background refresh and save conflict. |
| Graph guards/no side effects | `tests/test_graph.py:test_stale_editor_and_cross_project_rejected`, `test_preview_has_no_filesystem_or_graph_side_effects`: no mutation from rejected/preview operation. |
| Selective invalidation | `tests/test_graph.py:test_canvas_and_title_do_not_run_experiments`, `test_statistics_change_only_reanalyses_descendants`, `test_figure_style_and_paper_wording_do_not_retrain`, `test_reference_impact_without_canvas_edge`: preserve selective server impact. |
| Implicit DAG | `tests/test_verification_graph.py`; `apps/web/src/VerificationGraph.test.ts`: source/verifier/input edges deduplicate; implicit cycles rejected. |
| Fork/move/delete/history | `tests/test_graph.py:test_clone_subtree_copies_edges_without_claiming_execution`, `test_reparent_marks_or_rebinds_input`, `test_delete_requires_strategy_and_preserves_missing_material`, `test_undo_redo_restores_content_not_finished_process`. |
| Batch positions/reopened history | `tests/test_graph.py:test_batch_layout_updates_more_than_two_hundred_nodes_atomically`, `test_bulk_layout_rejects_missing_or_invalid_node_positions`, `test_branch_reopen_undo_preserves_decisions_and_monotonic_versions`. |
| Real branch hold/recovery | `tests/test_intervention_worker.py:test_prune_restore_holds_queued_runs_until_individually_resumed`; `tests/test_workspace_interventions.py`: isolated real files, restart/crash receipts. |
| Binding location/current output | `tests/test_control_validation.py`; `tests/test_interventions.py`: structured dependent locations invalidate while unrelated prose/layout remain intact. |

Run only the relevant cases for the actual change, then web build/typecheck if
frontend changed. Existing tests include real process/API workers with temporary
projects; inspect setup/cleanup before execution. Integrated graph/scheduling
changes additionally need isolated real backend/worker/artifact smoke verification;
fixture-only data is insufficient. Do not start existing user research tasks or
invoke an unauthorized paid provider. If an API shape changes, use
`scripts/export_api_contract.py --check` and contract tests after intentional export.
