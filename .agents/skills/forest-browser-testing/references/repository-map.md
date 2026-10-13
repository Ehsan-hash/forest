# Browser verification repository map

Source-audited at `ff16c3f656f94d053d1bba65c6b3a07a5feaccde`.
All paths are relative to the FOREST repository root. Existing tests are reusable scenarios,
not a record of execution in the current session. Read their current bodies before relying on coverage.

## Configuration and test layers

| File | Actual behavior |
| --- | --- |
| `apps/web/package.json` | `build`: `tsc -b && vite build`; `test`: `vitest run`; `test:e2e`: `playwright test`; also `dev`, `preview`, `format`; no lint/typecheck script |
| `apps/web/vitest.config.ts` | `src/**/*.test.{ts,tsx}`, React plugin, Node environment; these do not replace browser integration |
| `apps/web/playwright.config.ts` | `tests/`, 30s default, one worker, not fully parallel, list reporter, headless, 1440×1000; no webServer |
| Same Playwright config | `FOREST_WEB_URL` default 127.0.0.1:5173, `FOREST_BROWSER_CHANNEL` default `chrome`, output `apps/web/test-results` |
| `apps/web/vite.config.ts` | 127.0.0.1:5173; proxies `/api` and `/ws` to API 127.0.0.1:8000 |
| `scripts/test.sh` | Python pytest, frontend build, Vitest; no browser test invocation |
| `.github/workflows/ci.yml` | Separate local checks and observation integration; builds production web and explicitly starts `forest serve` at 18340 before selected e2e |

## Select by observable behavior

| Test source | Coverage to reuse and limitations |
| --- | --- |
| `apps/web/tests/progress.spec.ts`, first case | Real production API/worker: owner project creation, silent command success/failure, event-driven progress, reload, actual UTF-8 source/segment, dirty file/conflict and mobile overflow; no route mocks in this case |
| `progress.spec.ts`, second case | Fetches an actual progress response and delays its transport with route.fetch/fulfill; verifies old project data cannot leak after navigation |
| `storage-cleanup-retention.spec.ts` | Mocked `/api/settings`: retained days, invalid/failure states, control enablement; does not click Clean up or prove deletion |
| `workspace.spec.ts` | Mixed integration: per-test real project creation/deletion, real files/graphs and selected command runs, plus fabricated decisions/runs/file inventories and fault routes; read the selected case |
| `workspace.spec.ts` search cases | Real graph batches, 1000-node/offscreen targeting, hidden branch/collapsed ancestor and 786px inspector; asserts durable graph unchanged |
| `workspace.spec.ts` file cases | Revision conflicts, delete/recreate, external rename requiring `FOREST_DATA_DIR`, pagination, stale project response, CSV preview races/failures |
| `interventions.spec.ts` | Real two-tab graph/intervention interaction; dirty node draft and reviewed subtree scope |
| `figure-switch-confirms-unsaved-draft.spec.ts` | Real created figures; dirty switch confirmation and a mocked reordered list |
| `file-preview-preserves-csv-draft.spec.ts` | Real saved CSV and unsaved text surviving preview |
| `run-list-refresh.spec.ts` | Mocked run states plus `forest-refresh`; Data/Figure selectors refresh; fixture metrics are not measurements |
| `succeeded-run-filter.spec.ts` | Mocked completed run with `accuracy:0.9`; verifies UI/CSV status filter only |
| `experiment-launch.spec.ts`, `data-analysis.spec.ts` | Mocked pending, failure and retry responses; repeated-click handling; they do not execute research |
| `paper-evidence-eligibility.spec.ts` | Includes both fabricated evidence states and actual producer execution; read the particular case before describing coverage |
| `paper-revise.spec.ts` | Paper revise UI/revision submission; inspect its mocked queue response boundary |
| `failed-literature-search-clears-results.spec.ts` | Mocked search success/failure verifies stale result removal; not a live source retrieval check |
| `manual-autonomy-default.spec.ts` | Real project/controller state plus selected response faults; no provider is required |
| Double-submit specs | Project duplicate / web-page import request deduplication; inspect route mocks and cleanup |

Relevant unit sources include `api.test.tsx`, `fileSelection.test.ts`, `RunPanel.test.tsx`,
`RunEvidence.test.tsx`, `Modal.test.tsx`, `Tabs.test.tsx`, `PaperLayout.test.tsx`,
`VerificationGraph.test.ts`, `progress/ProgressPanel.test.ts`, `files/fileTree.test.ts`.
Select the one tied to the changed invariant rather than assuming all snapshots are browser tests.

## Startup, data and side effects

`scripts/start.py` requires the checkout's `.venv/bin/python` and calls `forest_cli.server.serve`.
The supervisor initializes the configured DB, then launches API and normal worker; non-dev serving
uses `apps/web/dist`, building when absent. `--dev` also starts Vite. `--headless` omits the web app.
`forest serve` is the installed CLI equivalent; `--port`, `--data-dir`, `--database-url` are supported.
`scripts/dev.sh` uses `start.py --dev`; `scripts/demo.sh` actually starts the normal supervisor.
Neither shell wrapper is itself the demonstration workload.

`services/api/config.py` loads `.env` with `FOREST_` names. Defaults point at source `var/` and
`var/forest.db`. `forest_cli/server.py` copies the caller environment. Data-dir only creates a
default SQLite URL when no explicit/inherited database URL exists. Pass both for isolation.
API startup seeds Ollama only when `settings.model` is nonempty and the DB has no provider.
A new DB and explicit empty model keep ordinary local frontend checks provider-free.

Browser mutation tests commonly delete their created projects afterward. The project-delete API
cancels active work and recursively removes that project's directory. This requires a disposable backend.
The settings cleanup API removes eligible terminal run history/output across that backend; inspect
scope before testing actual cleanup. The existing retention spec does not exercise that operation.

`scripts/browser_check.cjs` is a fixed legacy demo probe: hardcoded Chrome and 5173, reads
`var/demo.json`, writes `var/qa/workspace-desktop.png`; it does not honor `FOREST_WEB_URL`.
`scripts/browser_acceptance.cjs` reads that same marker, honors `FOREST_WEB_URL`/browser channel,
checks routes/overflow, saves `var/qa/*`; it is a route smoke, not all action acceptance or scientific QA.
Both require the marker, an existing seeded project and an existing output directory; they do not seed
or start a backend. Do not use an unrelated stale demo marker to test an isolated new database.

`scripts/demo.py` performs real API mutations and runs a chosen existing provider's connection test,
an Agent task, literature/theory/experiments/analysis/paper. It is model/compute work, not safe UI seeding.
`scripts/qualify_runtime.py` launches actual digits training across many nodes and writes `var/qa`.
`scripts/qualify_release.py` has agents/graph/compute/soak modes; Agent mode requires a real provider;
`--allow-paid` enables paid qualification with spend checks. It retains evidence projects/artifacts.
`.github/workflows/paid-qualification.yml` is explicit paid manual dispatch, separate from local CI.
`scripts/qualification_service.py install/remove` manages a persistent qualification service.
None of these is a routine frontend-regression prerequisite.

Qualification prediction CSVs in `scripts/qualification/fixtures/` are copies of prior real outputs
with provenance, reused as inputs. They are not new browser-run measurements, matched causal comparisons
or proof of scientific novelty. Follow their README/provenance if a task actually uses them.
