# Frontend redesign implementation and acceptance plan

Baseline source: `ff16c3f656f94d053d1bba65c6b3a07a5feaccde`, inspected 2026-10-13. Baseline feature/API/component preservation joins are complete. Final production browser regression passes **108/108 in 196.30 seconds**, with zero skipped/unexpected/flaky cases; units pass **102/102 across 20 files**. Exact final assets and all per-case receipts are in the [test matrix](test-matrix.md). The [named backend run](backend-analysis-results.md) passes 35 checks with two PostgreSQL variants NOT RUN; its 13 analysis cases include complete version/intervention history and same-revision effect freshness. Actual three-scope worker/HTTP transport uses authored model prose and observed Crossref metadata. Final scoped visual review is complete: all 32 runtime PNGs opened jointly, 48 analysis captures reviewed through original-resolution crops, and final main/expanded/readability observations recorded with explicit inspected subsets. No live AI quality or 56/56 acceptance claim follows from these counts.

The required navigation has Workspace, Library, Experiments & Results, Paper and Files as project destinations, with Settings at the bottom globally. Workspace retains Overview/Goal/Graph/Ideas/Theory/Branches/progress/intervention and adds the requested right-side project/node/path analysis. Results retains Experiments/Runs/Data/Figures. Preserve old route aliases, full object IDs, selected query context, demo query and browser history. Missing requested objects get meaningful states, not fallback objects or a redirect masking failure.

## Ordered delivery phases

| Phase | Concrete work | Exit evidence | Current plan status |
| --- | --- | --- | --- |
| 0. Baseline | Record clean upstream SHA; source/API/component/feature audit; isolated dependencies/production API+worker; existing build/unit/browser baseline; real light/dark screenshots and matching journey counts | Command exits, chosen URL/data path, worker/API logs, before screenshots and observed clicks/switches/decisions | Complete: source audit, actual before screenshots and baseline observations; backend platform failures retained in [matrix B0/V1](test-matrix.md#recorded-runs-and-artifacts) |
| 1. Reliability | Durable run/branch/node routes; correct missing-object behavior; Escape/blank selection; responsive inspector; dirty project budget base; instruction/decision validation/pending/conflict recovery; stale history/data reads | Targeted unit/browser cases with real readback plus labelled transport faults; working build | Implemented; scoped navigation/draft/conflict/race passes recorded in [matrix](test-matrix.md). Live Agent consumption/decisions remain unverified |
| 2. Design system | Sage light/dark tokens, typography, spacing, controls, focus/error/disabled states, readable badges, restrained frosted navigation/overlays | Build and actual rendered pages at required sizes/themes; contrast/focus review | Implemented; final production/102 units/108 browser checks pass. Compact graph actions and actual focus restoration pass at 390/768/1024/1280. Final 32 populated captures have zero geometry/runtime errors and all 32 PNGs were opened jointly, including a settled dark390 Focus replacement. Final scoped main/expanded/readability/analysis theme review is complete; [rows 49–53](test-matrix.md#acceptance-matrix) retain broader keyboard boundaries |
| 3. Shell and information architecture | Five main nav groups; secondary views; real project switcher; context/entity palette; compact scoped project header and bottom Settings; aliases/history | Every inventory row has reachable new entry; direct/reload/back/forward and project-switch tests | Implemented; six-width navigation, exact query/history, keyboard palette, actual delayed project-switch and missing-route cases pass. Actual anonymous share/revocation/401/403 checks also pass; [rows 09–14, 40–44, 51](test-matrix.md#acceptance-matrix) |
| 4. Workspace | Graph largest surface; temporary inspector/focus/context menu; supported node/selection/branch/controller run scope; status/run drawer; complete shared manuscript session with hide/resize/pin/expand/focus preference; impact/revision protections | Graph search/drag/commands/review/draft/responsive cases; before/after journey measurement; actual run IDs | Implemented; real edits/impact conflict/branch run/queued Skip, search, keyboard/drag positions, cycle rejection, UndoRedo, process controls/checkpoint and shared manuscript pass. Before/after clicks are equal; Goal/manuscript main-view switches fall from two to zero. [Matrix](test-matrix.md), [journeys](journey-comparison.md) |
| 5. Domain pages | Library/Ideas/Theory/Experiments/Runs/Data/Figures/Paper/Files/Settings retain actions; durable local form errors and exact provenance; explicit advanced API-supported entries; actual downloads | Feature/API matrix updated against code; source values/IDs/revisions round-trip; binary MIME/content checks | Implemented; real numerical/paired/check/repository/figure/TeX/export/settings journeys and labelled fixture recovery pass. API-only and model/platform gaps remain explicit; [rows 19–39, 45–48, 54–56](test-matrix.md#acceptance-matrix) |
| 6. Cohesive architecture | Extract stable shell/routes/session/domain boundaries needed by the implementation; retain one authoritative server owner/shared SSE and native lazy integrations; remove only proven unused code | Build/unit/full relevant browser checks; callers and parity checked; no duplicate caches/subscriptions | Complete source extraction/parity; final production build, 102/102 units across 20 files and 108/108 browser tests pass. Shared analysis/manuscript sessions and final PDF cancellation cleanup are included. See [component inventory](component-inventory.md) and [matrix run records](test-matrix.md#recorded-runs-and-artifacts) |
| 7. Integration and visual acceptance | 56 scenarios below plus discovered critical cases; provider-free numerical artifact workflow; actual current PDF path if real TeX available; all visual state/size/theme comparisons and accessibility/performance observations | Honest per-case real/mock/NOT RUN evidence with logs/trace/screenshots and limitation | Final 108/108 production browser regression passes. Actual numerical metrics, figure SVG/PDF, current manuscript PDF after 12 cancellations, binary bytes, authentication/share, SSE reconnect and bounded native 200% reachability verified. Final state geometry/all 32 opened runtime pixels and readability regression pass. Scoped main/expanded/readability/48-analysis visual review is complete; captures and inspected subsets remain distinct. Broader conditions remain explicit in the [56-row matrix](test-matrix.md) |
| 8. Final audit | Reconcile implemented entries versus all F01–F102/G01–G10 and A01–A57/API gaps; review state/provenance/data safety; final report reflects actual evidence | No unmapped existing capability, dead action, false success or stale-current substitution; material NOT RUN visible | Feature/API/component reconciliation and functional execution evidence complete, including added F103/A58. Final scoped main-route/analysis visual review is complete. Model/platform/API-only and broader acceptance limitations remain visible; no aggregate 56/56 or all-185-operation browser claim |
| 9. Added workspace analysis | Source-grounded read-only project/node/path preview and explicit model-backed advice, real citations, per-scope drafts/history and revision/freshness recovery | Source coverage/saved-intent/citation validation; actual worker transport; new production browser and visual checks | Backend/API/frontend implemented. Final named checks 35 PASS / 2 PostgreSQL NOT RUN; actual worker/HTTP transport three scopes completed with authored prose and actual Crossref metadata. All six analysis cases pass in final 108/108 production suite, including strict dependency preview/source conflict/import and labelled transport/report fixtures. Final 48 analysis captures were reviewed in both themes at 390/768/1440 with no measured overflow/page errors and affected text contrast at least 5.11:1; broader Pin/resize keyboard/full-overlay traversal remains NOT RUN; [analysis extension](test-matrix.md#added-analysis-acceptance) keeps live scientific AI quality NOT RUN |

## Highest-priority baseline source gaps

| Priority | Concrete source finding | Required correction and observable |
| --- | --- | --- |
| P1 workflow | Intervention run links target absent project runs route; Workspace branch query ignored | Run and branch links recover exact same-project objects through reload/back/forward; materialization effect run_id is actually a branch ID and must remain interpreted by action |
| P1 draft | Overview `[p]` resets mode/budget on every project snapshot | Distinct unsaved values and base revision survive SSE; conflict requires explicit review before guarded save |
| P1 provenance | Paper preview uses pdf_path presence; export control currently emits source ZIP | Current compilation identity shown separately; explicit format:pdf download guarded by compiled_revision=paper revision and actual artifact |
| P1 form | Formal instruction form allows run scope with next_run/non-Agent/terminal ID | Limit run scope to current active Agent with next_request; scope/boundary validation; pending guard; preserved failed/conflicted draft and reviewed request intent |
| P1 draft | Embedded/full PaperPage instances only persist source/BibTeX/base revision | Preserve template/layout/revision instruction/dirty flags too through hide/expand/unmount/background refresh |
| P2 navigation | Escape clears selected ID, auto-select effect selects first node again | Close top temporary surface then clear selection after effects settle; editor/terminal shortcuts preserved |
| P2 responsive | Settings footer hidden at ≤820; graph search hidden at ≤620; inspector only initialized from matchMedia | Access all required entries at 390/768/1024/1280/1440/1920 and 200% zoom; resize live desktop session too |
| P2 recovery | Project bulk Promise.all loses individual failure outcomes | Report accepted/failed IDs and current remaining active state independently; no false all-success |
| P2 freshness | Custom RawDataBrowser/history pages use promise callbacks without full current-scope/generation checks | Delay A, switch B, release A; no foreign source/receipt/render or draft overwrite |
| P2 action parity | Right-click launches fork form, menu lacks supported reopen/revise-Goal; API-only advanced capabilities not explicit | Actual contextual actions and reachable supported Advanced paths, with exact scope/impact/revision state |

These are source-confirmed findings. Browser reproduction or API mutation must be recorded before claiming runtime failure/fix.

## Test evidence labels

`REAL API` means ordinary requests persist on the disposable backend and readback/reload agrees. `REAL WORKER` additionally means the normal scheduler/worker executes the actual task and produces observed state/output/artifacts. `FAULT` is labelled transport/API response injection proving UI recovery only. `MOCK` is fabricated state and never substitutes for real execution/evidence. `BROWSER` denotes actual rendering/interactions/local preference or keyboard behavior. A single spec may contain several labels.

The 56 scenarios are the required acceptance index. Statuses record only the precisely scoped evidence gathered in this audit; broader results must be joined from actual validation reports. Existing spec references alone are reuse candidates, never recorded current passes.

| Test | Intended observable | Execution evidence required | Existing reuse / new coverage | Status |
| --- | --- | --- | --- | --- |
| 01 | Create project → exact returned project Workspace | REAL API + BROWSER, saved Goal/name after reload | progress owner journey; project creation | [PASS](test-matrix.md#acceptance-matrix) |
| 02 | UI add node → reload exact node persists | REAL API + BROWSER, graph revision/node ID | Workspace inspector-create case | [PASS](test-matrix.md#acceptance-matrix) |
| 03 | Edit/save node → reloaded fields retain unknown config and source revision | REAL API + BROWSER | Workspace concurrent node edit plus new readback | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 04 | Select node → Escape settles unselected and inspector closed appropriately | BROWSER, wait for effects; editor exclusion | New selection dismissal regression | [PASS](test-matrix.md#acceptance-matrix) |
| 05 | Drag/keyboard move → reloaded saved position | REAL API + BROWSER; flush before dependent mutation | Workspace keyboard move/conflict/lost-response | [PASS](test-matrix.md#acceptance-matrix) |
| 06 | Add dependency → actual relation/direction and graph update | REAL API + BROWSER | Workspace edge relationship; graph API | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 07 | Invalid explicit or implicit execution cycle rejected; graph unchanged | REAL API + BROWSER and helper UNIT | VerificationGraph + actual backend rejection | [PASS](test-matrix.md#acceptance-matrix) |
| 08 | Undo/redo content revisions work and preserve completed run history | REAL API + BROWSER | New graph operation journey; existing kernel tests | [PASS](test-matrix.md#acceptance-matrix) |
| 09 | Branch filter query/preference reloads exact branch without graph write | REAL API + BROWSER | New filter/history case | [PASS](test-matrix.md#acceptance-matrix) |
| 10 | Branch deep link selects exact full B, missing B stays missing | REAL API + BROWSER direct/reload | New branch route case | [PASS](test-matrix.md#acceptance-matrix) |
| 11 | Node deep link selects full N in same P across reload/back | REAL API + BROWSER, wrong-project guard | New node route case | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 12 | Search reveals offscreen/filtered/collapsed node; durable graph unchanged | REAL API + BROWSER | Workspace three search cases incl 1,000 nodes | [PASS](test-matrix.md#acceptance-matrix) |
| 13 | Node Run returns actual full R and requested scope | REAL API + REAL WORKER | Progress command plus sample numerical journey | [PASS](test-matrix.md#acceptance-matrix) |
| 14 | Returned R appears in run drawer and Runs route, no wrong fallback | REAL API + BROWSER | New returned-R query/drawer case | [PASS](test-matrix.md#acceptance-matrix) |
| 15 | Run status follows queued/running/terminal readback | REAL WORKER + BROWSER | Real progress success/failure | [PASS](test-matrix.md#acceptance-matrix) |
| 16 | Logs show actual output and returned byte cursor, not JS length | REAL WORKER + BROWSER; UTF-8/chunk/old-response guard | Progress UTF-8; RunPanel unit/new run switch fault | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 17 | Pause/resume changes exact supported R; saved continuation and cumulative use correct | REAL WORKER + BROWSER; unsupported state explicit | Lifecycle backend tests + bounded running command | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 18 | Cancel exact selected R; neighboring R unaffected | REAL WORKER + BROWSER | New two-run scope case | [PASS](test-matrix.md#acceptance-matrix) |
| 19 | Repeated in-flight submission once; deliberate failed retry works; supported receipt same ID | REAL API + FAULT + BROWSER | Launch/analysis/duplicate/web/revise specs; no worker claim from mocks | [PASS](test-matrix.md#acceptance-matrix) |
| 20 | Full run history/source revision survives reload | REAL API + REAL WORKER + BROWSER | Progress/numerical + historical retry | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 21 | Formal project/branch/node instruction saved with reviewed scope/boundary | REAL API + BROWSER | interventions live tabs; new run-scope choices | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 22 | Actual instruction receipt appears with returned request/entity IDs | REAL API + BROWSER | Intervention contract/UI case | [PASS](test-matrix.md#acceptance-matrix) |
| 23 | Accepted/applied/effect lifecycle accurately read back | REAL API + real bounded delivery if supported; no semantic obedience claim | Intervention backend lifecycle; provider path condition | [NOT RUN](test-matrix.md#acceptance-matrix) |
| 24 | Pending decision opens exact saved proposed action/R/revision | REAL API + BROWSER or explicitly MOCK state presentation | Actual supported Agent/human wait setup; unavailable condition explicit | [MOCK UI PASS / REAL NOT RUN](test-matrix.md#acceptance-matrix) |
| 25 | Supported accept/edit/reject saves decision; separate continuation error retained | REAL API + BROWSER; actual decision owner | Decision backend/live wait test; labelled FAULT continuation | [MOCK UI PASS / REAL NOT RUN](test-matrix.md#acceptance-matrix) |
| 26 | Decision link opens exact full historical/current R | REAL API + BROWSER + optional MOCK decision rendering | New compatible Runs route/query | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 27 | Evidence source link reaches exact P/B/R/path/segment | REAL API + REAL WORKER + BROWSER | Real scoped UTF-8 SourceExplorer journey | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 28 | Missing/rejected/inconclusive/stale source never shown verified | REAL API + BROWSER; MOCK only for additional rendering states | RunEvidence unit; source-bound verification backend | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 29 | Actual measured metrics preserve raw values, status, source run/node revision | REAL WORKER + REAL API + BROWSER | Numerical metrics journey; filters fixtures supplemental | [PASS](test-matrix.md#acceptance-matrix) |
| 30 | Figure displays actually rendered artifact with correct revision | REAL WORKER + BROWSER | Provider-free measured plot; artifact MIME/output readback | [PASS](test-matrix.md#acceptance-matrix) |
| 31 | Figure source/data/run IDs inspectable, conceptual image distinguishable | REAL API + BROWSER | Actual figure bindings; no fake measurement | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 32 | Paper opens returned same-project D; initial saved template accurately labelled | REAL API + BROWSER | New paper ID readback; GET creation contract | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 33 | Complete paper session survives graph panel hide/full editor/view switch | BROWSER + REAL API read unchanged | New source/BibTeX/template/layout/instruction/base/dirty case | [PASS](test-matrix.md#acceptance-matrix) |
| 34 | Complete paper draft survives actual background refresh and conflict | REAL API + BROWSER; concurrent tab/explicit event | New paper session refresh/base guard | [PASS](test-matrix.md#acceptance-matrix) |
| 35 | Save manuscript persists exact edited content and revision | REAL API + BROWSER after reload | Manual no-provider source/BibTeX save | [PASS](test-matrix.md#acceptance-matrix) |
| 36 | Compile displays actual queue/run/log/error/current artifact result | REAL WORKER with real TeX + BROWSER | Actual TeX required; no fixture PDF | [PASS](test-matrix.md#acceptance-matrix) |
| 37 | Source ZIP/current PDF actual download, MIME/size/content/revision; stale PDF rejected | REAL API + REAL WORKER + BROWSER | Source draft ZIP; current-PDF TeX journey | [PASS](test-matrix.md#acceptance-matrix) |
| 38 | Browser upload actual bytes and reload destination | REAL API + BROWSER | Files upload plus off-page conflict preflight | [PASS](test-matrix.md#acceptance-matrix) |
| 39 | File download content equals uploaded/worker-created bytes | REAL API + BROWSER | Actual text/binary/artifact download | [PASS](test-matrix.md#acceptance-matrix) |
| 40 | Project switching never mixes graph/run/file/paper data or drafts | REAL API + BROWSER + FAULT delay | Existing delayed progress/file page; new paper/run | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 41 | Back/forward restores meaningful page and branch/node/run selection | BROWSER + REAL API | New route alias/query history suite | [PASS](test-matrix.md#acceptance-matrix) |
| 42 | Required-auth UI/log-in works; no secret logs/URL/storage | Controlled actual auth backend, plus labelled FAULT 401 UI | Loopback auto-owner must not be misdescribed as auth proof | [PASS](test-matrix.md#acceptance-matrix) |
| 43 | 403 yields permission/read-only state, no false enabled owner action | Actual share policy + labelled FAULT browser error | Share read-only plus contextual 403 case | [PASS](test-matrix.md#acceptance-matrix) |
| 44 | Missing route/project/node/run/artifact has meaningful recovery | REAL API 404 + BROWSER; no universal redirect | New missing entity/route states | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 45 | 409 retains node/project/file/paper/instruction edits and reviewed base | REAL API concurrent edit + BROWSER | Existing node/file two-tab; new budget/paper/instruction | [PASS](test-matrix.md#acceptance-matrix) |
| 46 | 500/network/invalid JSON cannot clear draft or announce success | FAULT + BROWSER, server readback unchanged | Existing launch/analysis/search faults plus new scoped forms | [PASS](test-matrix.md#acceptance-matrix) |
| 47 | SSE reconnect/open refetches actual current state and source freshness | REAL API/events + BROWSER; controlled disconnect | Shared events/progress behavior + new reconnect case | [PASS](test-matrix.md#acceptance-matrix) |
| 48 | Rapid A→B→A switch rejects oldest graph/file/run/history/data/paper responses | REAL API + FAULT response gate + BROWSER | Existing file/project guards; new custom read generation cases | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 49 | Dark theme usable across main and secondary surfaces | BROWSER real screenshots/interactions | Final scoped route/state/analysis pixels and measurements; limits in matrix | [PASS](test-matrix.md#acceptance-matrix) |
| 50 | Light theme usable across main and secondary surfaces | BROWSER real screenshots/interactions | Final matched scoped dark/light comparisons; limits in matrix | [PASS](test-matrix.md#acceptance-matrix) |
| 51 | Mobile/intermediate nav/search/settings/run/object actions reachable | BROWSER 390/768/1024/1280/1440/1920 and desktop resize | Narrow inspector plus new navigation/search/graph action entries | [PASS](test-matrix.md#acceptance-matrix) |
| 52 | Keyboard nav/palette/graph/menu/tabs/dialogs and 200% zoom work | BROWSER accessibility/focus observation | Tabs/graph move helper units supplemental | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 53 | Modal/drawer closes and restores expected trigger focus | BROWSER; UNIT supplemental | Modal.test.tsx and actual nested temporary surfaces | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 54 | Advanced configs retain unknown top-level and nested fields | REAL API + BROWSER; exact JSON readback | ExecutionSettings/settingsDraft/connectionPayload and forms | [PASS](test-matrix.md#acceptance-matrix) |
| 55 | Large graph search/pan/select/edit remains usable with durable geometry | REAL API + BROWSER, record measured conditions/timing if claimed | Existing 1,000-node search; no invented milliseconds | [PARTIAL PASS](test-matrix.md#acceptance-matrix) |
| 56 | Real numerical node creates actual metrics artifact and provenance persists/export/reload | REAL WORKER + REAL API + BROWSER no prepared output | `examples/cli/node.json` standard-library calculation | [PASS](test-matrix.md#acceptance-matrix) |

Additional required discovered checks: mixed bulk control results; lost-response same-intent graph/run receipt recovery; actual branch materialization links; instruction run scope validation; older history stale-scope guard; complete paper layout/revision instruction session; raw data run/path read races; actual Figure download/source; unknown configuration retention; operator provider retirement and explicit cleanup control scope.

## Provider-free full integration journey

Use the actual existing `examples/cli/{goal.md,node.json,project-config.json}`. The experiment executes standard-library trapezoidal integration of x² on [0,1] with 5,000 intervals, writes `metrics.json`, and prints actual output. Do not replace it with prepared JSON. A manual disposable project with the example operational profile is a bounded runtime smoke; it does not modify any user's scientific project or prove full_submission readiness.

1. Through the browser, create the project and actual Goal/config, create the experiment node with its real command, save and read back N/project revision.
2. Submit once, retain returned complete R, observe actual running/terminal state and exit, inspect actual logs and worker-created metrics artifact. Compare the method/result with the true integral 1/3 and reported error/intervals; don't claim independent scientific verification from arithmetic agreement alone.
3. Inspect saved lineage/source run/node revision/config/artifact path. Missing verification remains unverified; independent verification is a separate actual execution/check contract.
4. Open Data/related result, download actual metrics and project export through supported browser controls, inspect MIME/file contents and ZIP manifest, reload and verify the same identities/measurements/provenance remain.
5. Edit/save/check the real initial manuscript separately. Compile/current-PDF export only with installed actual TeX toolchain; unavailable compiler or execution isolation means NOT RUN with logs. No paid/provider-backed manuscript generation is used to fill the journey.

## Runtime boundary and selected commands

Use a new data directory **and** explicit new SQLite database URL; set `FOREST_MODEL=''` before imports/startup. Pass a verified available separate port to the production supervisor (`scripts/start.py` or `forest serve`), normal API and worker, then confirm health **and actual command transitions**. Vite's dev proxy is fixed to API8000 and does not follow a custom supervisor port; production serves build and API together. Tests may delete only their disposable created projects. Retain logs/failure artifacts; stop only the owned supervisor PID. Provider connection tests, demo/model qualification, paid runs and background service installation are separate operations, not baseline prerequisites.

After dependencies are installed, real repository scripts are:

```sh
npm --prefix apps/web run build
npm --prefix apps/web test
npm --prefix apps/web run test:e2e -- progress.spec.ts
npm --prefix apps/web run test:e2e -- workspace.spec.ts interventions.spec.ts
npm --prefix apps/web run test:e2e -- --list
```

There is no npm lint/typecheck script. The build contains TypeScript checking. `scripts/test.sh` runs pytest/build/Vitest and does not run browser tests. Playwright has no webServer; the isolated URL must already be verified. Existing specs mix real API/processes with response fixtures, so classify selected case bodies rather than the whole filename. Backend relevant checks and contract exporter are selected from current handlers; PostgreSQL concurrency requires its actual environment, not SQLite inference.

## Visual, accessibility and journey evidence

Capture matched before/after empty project, active project, running research, pending decision, failed run, large graph, Paper editor, Figures, Files and Settings; both themes. Capture actual application pixels and identify fixture rendering when a pending-state fixture is used. Check every required width and 200% zoom, entry reachability, intended internal scroll versus document overflow, focus-visible/labels/control targets, dialog keyboard/restore, editor/terminal exclusions and reduced motion where applicable.

Count deliberate activations as clicks, typing separately, page/main-view/editor change as context switch, and necessary choices as decisions. Use the same initial project/state/viewport for before/after: project→Goal→Graph; edit node→save→run→result; pending decision→answer→continue; scoped formal instruction→effects; manuscript→compile→download; Figure/Data→run/evidence/source. Counts remain unfilled until observed; no estimated friction/performance improvements are acceptance evidence.

The final report must distinguish source inspection, mocked UI recovery, real API persistence, actual worker execution, rendered observation and scientific validation. Include exact commands/results/logs/screenshots/trace and remaining material NOT RUN conditions. No commit, push, PR or deployment is part of this local implementation authorization.

## Recorded intervention validation

2026-10-13, isolated URL `http://127.0.0.1:18340`; `FOREST_DATA_DIR=/tmp/forest-redesign-runtime-20261013`; Chromium. Every browser case creates/deletes only its own disposable project. These cases do not launch a research worker or use a provider.

```sh
npm --prefix apps/web test -- src/interventions/instructionDraft.test.ts
# 1 file, 5 tests PASS; 144ms.

FOREST_WEB_URL=http://127.0.0.1:18340 FOREST_BROWSER_CHANNEL=chromium \
FOREST_DATA_DIR=/tmp/forest-redesign-runtime-20261013 \
npm --prefix apps/web run test:e2e -- instruction-reliability.spec.ts \
--output=test-results/interventions-redesign
# 6 tests PASS; 4.5s. Output directory: apps/web/test-results/interventions-redesign.
```

| Case | Observed result | Evidence boundary / matrix linkage |
| --- | --- | --- |
| Lost instruction response and duplicate submit | Actual backend accepted the first POST; intercepted response was deliberately lost. Draft and original request remained; same-intent retry returned the same receipt. Actual GET showed exactly one matching saved receipt | REAL API + BROWSER + FAULT; A24/F50–F51; acceptance scenarios 21(project only), 22, 46 |
| Changed instruction after failed submit | Injected 500 left original text/revision. Edited text submitted a new request_id with the same reviewed revision; only changed instruction exists in actual backend readback | REAL API + BROWSER + FAULT; A24/F50; scenario 46 |
| Concurrent project revision | Actual PATCH changed the project; original instruction POST returned real 409. Draft survived; Send stayed disabled until explicit fetch/review/acknowledgment. Actual saved receipt bound the explicitly reviewed new revision | REAL API + BROWSER; A24/F50; scenarios 21(project only), 45(instruction only) |
| Run choices and delivery boundary | Only current project's active Agent fixture appeared; completed/non-Agent/foreign fixtures absent. Run scope forced next_request; stopped selected run remained unavailable and Send disabled, with retained text | MOCK UI + BROWSER; A24/F50; no worker/Agent delivery proof |
| Older-history project scope | Delayed fixture refresh could not append old history after A→B→A; original history state did not leak across project scopes | MOCK UI + BROWSER + FAULT; A24/F51; scenario 48(history only) |
| Failed/stale decision answer | Double synchronous submit made one request. Injected 500 preserved edited action/reason; stale 200 retained draft with disabled submit and exact full run href | MOCK UI + BROWSER + FAULT; A25/F52; scenarios 24(fixture), 26(href only), 46(decision only); no actual tool consumption/resume |

The first run reported four locator failures: existing ErrorBox lacked an alert role, and native option disabled status required a disabled-attribute assertion. Actual conflict/history cases already passed. Corrected test selectors then passed all six cases against the same production assets; no product-code change was needed for that correction. ErrorBox subsequently gained `role=alert` as a separate accessibility fix. The final consolidated production build was followed by the recorded rerun below.

The 56-scenario matrix remains a required acceptance index, not a claimed 56/56 pass. The narrow evidence above covers only the stated subsets. Actual branch/node instruction consumption, active Agent model boundary delivery, supported real decision action/resume and scientific compliance remain NOT RUN in this audit. RawDataBrowser additionally has two labelled delayed row-fixture regressions for run A→B→A success and path A→B→A failure/current-pending behavior; both passed against the final consolidated production build.

Final consolidated production-asset rerun:

```sh
FOREST_WEB_URL=http://127.0.0.1:18340 FOREST_BROWSER_CHANNEL=chromium \
FOREST_DATA_DIR=/tmp/forest-redesign-runtime-20261013 \
npm --prefix apps/web run test:e2e -- raw-data-freshness.spec.ts \
instruction-reliability.spec.ts --output=test-results/interventions-redesign-final
# 8 tests PASS; 11.0s: six intervention cases above and two row-order cases below.

FOREST_WEB_URL=http://127.0.0.1:18340 FOREST_BROWSER_CHANNEL=chromium \
FOREST_DATA_DIR=/tmp/forest-redesign-runtime-20261013 \
npm --prefix apps/web run test:e2e -- instruction-reliability.spec.ts \
--grep 'editing a failed instruction|failed and stale decision' \
--output=test-results/interventions-alert-final
# 2 tests PASS; 2.5s after adding explicit getByRole('alert') assertions.
# These rerun two existing cases, not two additional unique scenarios.
```

The row-order cases use actual disposable projects and explicitly mocked completed experiment/row responses. A delayed old success cannot replace newer rows after run A→B→A. A delayed old path 500 cannot expose an obsolete error or enable Load data while the current path A→B→A read is still pending; the current read then shows its own rows. They verify request ordering and identity, not actual experimental values or real worker output. The final accessibility assertions confirm the visible instruction and decision failures are exposed as alert roles in the running browser.
