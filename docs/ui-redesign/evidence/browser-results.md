# Consolidated browser results

Stable production assets `index-BIrSAtO6.js`, Chromium, isolated API/worker at `http://127.0.0.1:18340`. Start 2026-10-13T15:23:22.531Z. **108 passed; 0 skipped, 0 unexpected, 0 flaky.** Duration 196.30 seconds.

Every row records actual Playwright interaction in Chromium. The [acceptance matrix](../test-matrix.md) distinguishes actual API/worker execution, controlled transport faults and authored response fixtures. A browser PASS does not make a fixture into scientific execution or validate AI judgment quality.

The [stdout](e2e-delivery.txt) and [complete JSON results](e2e-delivery.json) retain exact names, durations, results and attachment paths. Earlier diagnostics and the passing build before the final dependency/PDF corrections remain separate evidence.

| Test name | Result | Browser duration | Source / assertions |
| --- | --- | --- | --- |
| recomputing analysis ignores repeated clicks while the request is pending | PASS | 2.31s | [data-analysis.spec.ts:3](../../../apps/web/tests/data-analysis.spec.ts#L3) |
| experiment launch ignores repeated clicks until its request finishes | PASS | 2.08s | [experiment-launch.spec.ts:3](../../../apps/web/tests/experiment-launch.spec.ts#L3) |
| a failed literature search clears results from the previous query | PASS | 0.52s | [failed-literature-search-clears-results.spec.ts:3](../../../apps/web/tests/failed-literature-search-clears-results.spec.ts#L3) |
| switching figures warns before discarding unsaved edits | PASS | 0.76s | [figure-switch-confirms-unsaved-draft.spec.ts:3](../../../apps/web/tests/figure-switch-confirms-unsaved-draft.spec.ts#L3) |
| reordering figures does not replace the selected unsaved draft | PASS | 0.48s | [figure-switch-confirms-unsaved-draft.spec.ts:69](../../../apps/web/tests/figure-switch-confirms-unsaved-draft.spec.ts#L69) |
| background refresh rereads the selected file cursor while explicit Refresh restarts the scan | PASS | 0.63s | [file-pagination-freshness.spec.ts:31](../../../apps/web/tests/file-pagination-freshness.spec.ts#L31) |
| file events during Next defer to its new cursor and an older refresh cannot override Previous | PASS | 0.58s | [file-pagination-freshness.spec.ts:87](../../../apps/web/tests/file-pagination-freshness.spec.ts#L87) |
| switching from saved CSV preview back to editing keeps the unsaved draft | PASS | 1.25s | [file-preview-preserves-csv-draft.spec.ts:3](../../../apps/web/tests/file-preview-preserves-csv-draft.spec.ts#L3) |
| browser uploads actual binary bytes and downloads the same persisted file after reload | PASS | 0.58s | [files-binary.spec.ts:4](../../../apps/web/tests/files-binary.spec.ts#L4) |
| actual explicit and implicit dependency cycles are rejected from path actions without changing graph revision | PASS | 0.92s | [graph-history.spec.ts:66](../../../apps/web/tests/graph-history.spec.ts#L66) |
| actual UI Undo and Redo preserve a completed run identity, source configuration and output history | PASS | 1.98s | [graph-history.spec.ts:166](../../../apps/web/tests/graph-history.spec.ts#L166) |
| formal instruction lost-response retry retains one actual saved receipt and request identity | PASS | 0.64s | [instruction-reliability.spec.ts:16](../../../apps/web/tests/instruction-reliability.spec.ts#L16) |
| editing a failed instruction allocates a new intent while retaining the reviewed revision | PASS | 0.63s | [instruction-reliability.spec.ts:85](../../../apps/web/tests/instruction-reliability.spec.ts#L85) |
| a concurrent project change requires explicit instruction re-review and preserves the draft | PASS | 0.70s | [instruction-reliability.spec.ts:151](../../../apps/web/tests/instruction-reliability.spec.ts#L151) |
| run instruction choices use only current active Agent state and force next_request | PASS | 0.57s | [instruction-reliability.spec.ts:236](../../../apps/web/tests/instruction-reliability.spec.ts#L236) |
| an older-history refresh cannot append into another project or a later visit to the same project | PASS | 0.77s | [instruction-reliability.spec.ts:323](../../../apps/web/tests/instruction-reliability.spec.ts#L323) |
| failed and stale decision answers retain the edited action and reason | PASS | 0.63s | [instruction-reliability.spec.ts:430](../../../apps/web/tests/instruction-reliability.spec.ts#L430) |
| two live tabs preserve reviewed subtree scope and a dirty node draft | PASS | 2.86s | [interventions.spec.ts:3](../../../apps/web/tests/interventions.spec.ts#L3) |
| a new Manual project defaults autonomy off until explicitly enabled | PASS | 0.59s | [manual-autonomy-default.spec.ts:3](../../../apps/web/tests/manual-autonomy-default.spec.ts#L3) |
| switching an unset Assisted project to Manual turns autonomy off | PASS | 0.50s | [manual-autonomy-default.spec.ts:78](../../../apps/web/tests/manual-autonomy-default.spec.ts#L78) |
| an explicitly saved Manual autonomy setting remains enabled on Overview load | PASS | 1.19s | [manual-autonomy-default.spec.ts:139](../../../apps/web/tests/manual-autonomy-default.spec.ts#L139) |
| a failed research-state load keeps autonomy controls disabled | PASS | 0.40s | [manual-autonomy-default.spec.ts:189](../../../apps/web/tests/manual-autonomy-default.spec.ts#L189) |
| owner executes a numerical graph node and exports actual measurements and source artifact | PASS | 3.49s | [numerical-workflow.spec.ts:4](../../../apps/web/tests/numerical-workflow.spec.ts#L4) |
| Paper only offers completed runs with actual numeric metrics | PASS | 9.78s | [paper-evidence-eligibility.spec.ts:3](../../../apps/web/tests/paper-evidence-eligibility.spec.ts#L3) |
| paper revision ignores repeated clicks while the request is pending | PASS | 2.36s | [paper-revise.spec.ts:3](../../../apps/web/tests/paper-revise.spec.ts#L3) |
| manuscript draft keeps source, references, layout, instruction and revision through panel/full-page/reload | PASS | 3.27s | [paper-session.spec.ts:17](../../../apps/web/tests/paper-session.spec.ts#L17) |
| an accepted save from the hidden panel keeps newer typing in the full editor | PASS | 1.15s | [paper-session.spec.ts:158](../../../apps/web/tests/paper-session.spec.ts#L158) |
| paper saves real source, compiles through the worker, downloads current PDF and rejects the actual stale PDF | PASS | 4.73s | [paper-session.spec.ts:255](../../../apps/web/tests/paper-session.spec.ts#L255) |
| actual run titles, textual states, output and measured table values remain readable at compact widths in both themes | PASS | 4.24s | [populated-readability.spec.ts:4](../../../apps/web/tests/populated-readability.spec.ts#L4) |
| owner creates a project, runs silent success/failure, and follows a scoped UTF-8 source | PASS | 13.92s | [progress.spec.ts:4](../../../apps/web/tests/progress.spec.ts#L4) |
| a delayed real progress response cannot cross a project switch | PASS | 0.74s | [progress.spec.ts:233](../../../apps/web/tests/progress.spec.ts#L233) |
| projects search matches a description when the goal is empty | PASS | 0.44s | [project-description-search.spec.ts:3](../../../apps/web/tests/project-description-search.spec.ts#L3) |
| a repeated in-flight Duplicate click creates only one copy | PASS | 0.58s | [project-duplicate-double-submit.spec.ts:3](../../../apps/web/tests/project-duplicate-double-submit.spec.ts#L3) |
| publication edits preserve unknown fields and concurrent untouched requirements, while a conflict keeps the complete draft | PASS | 0.92s | [project-requirements.spec.ts:32](../../../apps/web/tests/project-requirements.spec.ts#L32) |
| actual structural protocol validation creates no run; guarded protocol revisions preserve drafts and original history | PASS | 1.05s | [project-requirements.spec.ts:152](../../../apps/web/tests/project-requirements.spec.ts#L152) |
| real literal writing review applies Unicode source spans to the draft while evidence judgments remain with the author | PASS | 0.82s | [project-requirements.spec.ts:301](../../../apps/web/tests/project-requirements.spec.ts#L301) |
| fresh protocol reload reads an external version and structural checks become stale when publication scope changes | PASS | 0.98s | [project-requirements.spec.ts:398](../../../apps/web/tests/project-requirements.spec.ts#L398) |
| an accepted protocol version with a lost response replays the same persisted request after reload | PASS | 0.87s | [project-requirements.spec.ts:553](../../../apps/web/tests/project-requirements.spec.ts#L553) |
| delayed raw rows cannot replace a later A to B to A run selection | PASS | 0.53s | [raw-data-freshness.spec.ts:59](../../../apps/web/tests/raw-data-freshness.spec.ts#L59) |
| a stale path failure cannot clear the current pending read or show an obsolete error | PASS | 0.55s | [raw-data-freshness.spec.ts:117](../../../apps/web/tests/raw-data-freshness.spec.ts#L117) |
| system appearance follows actual media changes and reduced transparency removes chrome blur | PASS | 0.53s | [redesign-accessibility.spec.ts:3](../../../apps/web/tests/redesign-accessibility.spec.ts#L3) |
| compact manuscript panel keeps keyboard focus and Escape returns to the selected graph node | PASS | 0.47s | [redesign-accessibility.spec.ts:30](../../../apps/web/tests/redesign-accessibility.spec.ts#L30) |
| five main entries retain all project pages and global settings at every requested width | PASS | 2.26s | [redesign-shell.spec.ts:3](../../../apps/web/tests/redesign-shell.spec.ts#L3) |
| budget draft survives real SSE, route changes and reload with a retained revision conflict | PASS | 1.25s | [redesign-shell.spec.ts:79](../../../apps/web/tests/redesign-shell.spec.ts#L79) |
| an actual controls acknowledgment preserves later budget and mode edits with its accepted revision | PASS | 1.33s | [redesign-shell.spec.ts:149](../../../apps/web/tests/redesign-shell.spec.ts#L149) |
| palette searches actual projects and nodes, supports keyboard selection and missing routes recover | PASS | 0.66s | [redesign-shell.spec.ts:298](../../../apps/web/tests/redesign-shell.spec.ts#L298) |
| actual analysis preview follows full node/path URLs and retains independent drafts without model calls | PASS | 1.50s | [research-analysis.spec.ts:149](../../../apps/web/tests/research-analysis.spec.ts#L149) |
| uncertain analysis transport replays original UUID/fingerprint and preserves edits made before acknowledgment | PASS | 1.17s | [research-analysis.spec.ts:298](../../../apps/web/tests/research-analysis.spec.ts#L298) |
| rapid node selection waits for matching actual focus before initializing the new draft | PASS | 1.29s | [research-analysis.spec.ts:413](../../../apps/web/tests/research-analysis.spec.ts#L413) |
| completed report refreshes staleness, exports full IDs, explicitly imports citations and hides changed artifacts | PASS | 1.01s | [research-analysis.spec.ts:455](../../../apps/web/tests/research-analysis.spec.ts#L455) |
| compact shared pane traps focus and preserves analysis drafts across Paper and node editing | PASS | 0.88s | [research-analysis.spec.ts:556](../../../apps/web/tests/research-analysis.spec.ts#L556) |
| actual same-revision source changes reject the retained analysis fingerprint until explicit context review | PASS | 0.74s | [research-analysis.spec.ts:608](../../../apps/web/tests/research-analysis.spec.ts#L608) |
| an uncertain actual paired submission keeps the same request identity through language change and reload | PASS | 1.61s | [research-tools-reliability.spec.ts:3](../../../apps/web/tests/research-tools-reliability.spec.ts#L3) |
| paired inference uses the uploaded table and retains its exact input artifact | PASS | 2.56s | [research-tools.spec.ts:31](../../../apps/web/tests/research-tools.spec.ts#L31) |
| independent CSV integrity checks bind to the actual producer and declared file scope | PASS | 4.43s | [research-tools.spec.ts:99](../../../apps/web/tests/research-tools.spec.ts#L99) |
| repository preparation retains the actual GitHub commit in its run manifest | PASS | 4.35s | [research-tools.spec.ts:186](../../../apps/web/tests/research-tools.spec.ts#L186) |
| figure render and exported SVG and PDF use the actual numerical run | PASS | 5.50s | [research-tools.spec.ts:233](../../../apps/web/tests/research-tools.spec.ts#L233) |
| responsive graph exposes reachable path comparison and path actions at every compact width | PASS | 1.61s | [responsive-actions.spec.ts:3](../../../apps/web/tests/responsive-actions.spec.ts#L3) |
| actual stopped debug checkpoint saves an override separately from same-run Resume | PASS | 4.46s | [run-lifecycle.spec.ts:95](../../../apps/web/tests/run-lifecycle.spec.ts#L95) |
| actual pause and Resume keep the run identity, and exact cancel leaves a neighboring run intact | PASS | 7.76s | [run-lifecycle.spec.ts:208](../../../apps/web/tests/run-lifecycle.spec.ts#L208) |
| Data and Figure Studio refresh run statuses on project updates | PASS | 0.57s | [run-list-refresh.spec.ts:3](../../../apps/web/tests/run-list-refresh.spec.ts#L3) |
| invalid connection configuration remains editable with a durable form error | PASS | 0.46s | [settings-draft.spec.ts:3](../../../apps/web/tests/settings-draft.spec.ts#L3) |
| global settings draft survives real refresh and navigation, saving only changed keys | PASS | 0.81s | [settings-draft.spec.ts:36](../../../apps/web/tests/settings-draft.spec.ts#L36) |
| changing the default provider preserves valid unknown settings and invalid JSON drafts | PASS | 0.79s | [settings-draft.spec.ts:121](../../../apps/web/tests/settings-draft.spec.ts#L121) |
| anonymous real share exposes only selected nodes without requesting owner data, then honors revocation | PASS | 0.39s | [sharing-access.spec.ts:3](../../../apps/web/tests/sharing-access.spec.ts#L3) |
| actual authentication rejection opens sign-in and retains an invalid token for correction | PASS | 0.37s | [sharing-access.spec.ts:75](../../../apps/web/tests/sharing-access.spec.ts#L75) |
| actual cross-origin permission rejection shows a durable error instead of a project success state | PASS | 0.31s | [sharing-access.spec.ts:107](../../../apps/web/tests/sharing-access.spec.ts#L107) |
| storage cleanup uses the saved retention period | PASS | 0.66s | [storage-cleanup-retention.spec.ts:3](../../../apps/web/tests/storage-cleanup-retention.spec.ts#L3) |
| cleanup stays disabled for invalid saved retention until corrected | PASS | 0.41s | [storage-cleanup-retention.spec.ts:14](../../../apps/web/tests/storage-cleanup-retention.spec.ts#L14) |
| cleanup requires an explicit value when saved settings fail to load | PASS | 0.67s | [storage-cleanup-retention.spec.ts:30](../../../apps/web/tests/storage-cleanup-retention.spec.ts#L30) |
| Succeeded filter includes completed runs in the table and CSV export | PASS | 0.41s | [succeeded-run-filter.spec.ts:3](../../../apps/web/tests/succeeded-run-filter.spec.ts#L3) |
| a repeated in-flight web-page import submits only once | PASS | 0.50s | [web-page-import-double-submit.spec.ts:3](../../../apps/web/tests/web-page-import-double-submit.spec.ts#L3) |
| workspace branch and node URL state survives reload and history without editing the graph | PASS | 1.05s | [workspace-navigation.spec.ts:65](../../../apps/web/tests/workspace-navigation.spec.ts#L65) |
| temporary inspector keeps complete dirty drafts through dismissal, focus, reload and resize | PASS | 0.85s | [workspace-navigation.spec.ts:141](../../../apps/web/tests/workspace-navigation.spec.ts#L141) |
| full-ID run links show actual worker output, history and recoverable missing or cross-project selections | PASS | 3.19s | [workspace-navigation.spec.ts:218](../../../apps/web/tests/workspace-navigation.spec.ts#L218) |
| explicit branch execution uses the branch receipt and can skip a queued descendant | PASS | 7.61s | [workspace-navigation.spec.ts:349](../../../apps/web/tests/workspace-navigation.spec.ts#L349) |
| configuration acknowledgment retains a patch edited during the actual pending save | PASS | 2.68s | [workspace-navigation.spec.ts:460](../../../apps/web/tests/workspace-navigation.spec.ts#L460) |
| a delayed actual Retry receipt retains history without redirecting a newer selected Run ID | PASS | 4.54s | [workspace-navigation.spec.ts:539](../../../apps/web/tests/workspace-navigation.spec.ts#L539) |
| an authoritative graph refresh preserves an active drag until its actual position receipt | PASS | 1.01s | [workspace-navigation.spec.ts:689](../../../apps/web/tests/workspace-navigation.spec.ts#L689) |
| queued approved actions do not offer an invalid continuation | PASS | 0.43s | [workspace.spec.ts:23](../../../apps/web/tests/workspace.spec.ts#L23) |
| Files folders expand and collapse nested entries | PASS | 1.09s | [workspace.spec.ts:70](../../../apps/web/tests/workspace.spec.ts#L70) |
| keyboard node movement persists its position | PASS | 1.24s | [workspace.spec.ts:133](../../../apps/web/tests/workspace.spec.ts#L133) |
| keyboard position saves recover from a graph revision conflict | PASS | 1.19s | [workspace.spec.ts:232](../../../apps/web/tests/workspace.spec.ts#L232) |
| a lost position-save response replays the original command receipt | PASS | 0.87s | [workspace.spec.ts:326](../../../apps/web/tests/workspace.spec.ts#L326) |
| discard position retries for nodes deleted by a concurrent edit | PASS | 1.18s | [workspace.spec.ts:397](../../../apps/web/tests/workspace.spec.ts#L397) |
| Workspace edges expose their relationship and support keyboard deletion | PASS | 0.67s | [workspace.spec.ts:482](../../../apps/web/tests/workspace.spec.ts#L482) |
| filtering an edge off the canvas prevents its keyboard deletion | PASS | 0.55s | [workspace.spec.ts:619](../../../apps/web/tests/workspace.spec.ts#L619) |
| a stale editor cannot overwrite a file recreated after deletion | PASS | 1.07s | [workspace.spec.ts:718](../../../apps/web/tests/workspace.spec.ts#L718) |
| Files page navigates bounded file-list pages | PASS | 0.62s | [workspace.spec.ts:773](../../../apps/web/tests/workspace.spec.ts#L773) |
| Files page discards an in-flight page after switching projects | PASS | 0.86s | [workspace.spec.ts:815](../../../apps/web/tests/workspace.spec.ts#L815) |
| Files upload preflight detects conflicts outside the visible page | PASS | 0.40s | [workspace.spec.ts:877](../../../apps/web/tests/workspace.spec.ts#L877) |
| the newest CSV text read wins when preview toggles overlap | PASS | 0.97s | [workspace.spec.ts:911](../../../apps/web/tests/workspace.spec.ts#L911) |
| a failed CSV text read shows an error with a retry action | PASS | 0.50s | [workspace.spec.ts:961](../../../apps/web/tests/workspace.spec.ts#L961) |
| mounted Workspace refreshes after silent success | PASS | 4.53s | [workspace.spec.ts:1001](../../../apps/web/tests/workspace.spec.ts#L1001) |
| mounted Workspace refreshes after failure traceback progress | PASS | 4.70s | [workspace.spec.ts:1001](../../../apps/web/tests/workspace.spec.ts#L1001) |
| mounted Workspace refreshes after stdout progress | PASS | 4.58s | [workspace.spec.ts:1001](../../../apps/web/tests/workspace.spec.ts#L1001) |
| RunPanel shows an Agent final summary alongside its output log | PASS | 0.47s | [workspace.spec.ts:1082](../../../apps/web/tests/workspace.spec.ts#L1082) |
| graph updates remain stable and persist a node created through the inspector UI | PASS | 0.72s | [workspace.spec.ts:1123](../../../apps/web/tests/workspace.spec.ts#L1123) |
| a stale editor cannot recreate an untracked file after another tab renames it | PASS | 1.73s | [workspace.spec.ts:1163](../../../apps/web/tests/workspace.spec.ts#L1163) |
| Overview reload and continue preserve the saved autonomous-planning setting | PASS | 0.67s | [workspace.spec.ts:1262](../../../apps/web/tests/workspace.spec.ts#L1262) |
| another editor changing the node presents a conflict without overwriting server content | PASS | 1.89s | [workspace.spec.ts:1310](../../../apps/web/tests/workspace.spec.ts#L1310) |
| saved CSV preview reads real file rows and paginates without altering the file | PASS | 0.58s | [workspace.spec.ts:1360](../../../apps/web/tests/workspace.spec.ts#L1360) |
| guided demonstration follows ordinary project pages without automatically executing research | PASS | 0.69s | [workspace.spec.ts:1392](../../../apps/web/tests/workspace.spec.ts#L1392) |
| search reveals an offscreen node in a large graph and reopens its inspector | PASS | 5.92s | [workspace.spec.ts:1432](../../../apps/web/tests/workspace.spec.ts#L1432) |
| search reveals nodes hidden by the current branch or a collapsed ancestor | PASS | 0.97s | [workspace.spec.ts:1500](../../../apps/web/tests/workspace.spec.ts#L1500) |
| narrow workspace opens a dismissible inspector and keeps the searched node visible after closing | PASS | 6.07s | [workspace.spec.ts:1576](../../../apps/web/tests/workspace.spec.ts#L1576) |
| Paper full-manuscript picker lists completed runs from this project | PASS | 2.81s | [workspace.spec.ts:1681](../../../apps/web/tests/workspace.spec.ts#L1681) |
| Paper full-manuscript picker keeps the empty-project control | PASS | 0.58s | [workspace.spec.ts:1827](../../../apps/web/tests/workspace.spec.ts#L1827) |
