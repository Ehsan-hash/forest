# Frontend repository map

Source-audited at `ff16c3f656f94d053d1bba65c6b3a07a5feaccde`.
All source paths are relative to the FOREST repository root. Recheck current implementations;
this map does not establish that a source-confirmed candidate has been reproduced at runtime.

## Entry, routes and shared UI

| Source | Read when | Actual responsibility |
| --- | --- | --- |
| `apps/web/src/main.tsx` | Boot/router failures | React 19 StrictMode, BrowserRouter, PageBoundary, global CSS |
| `apps/web/src/App.tsx` → `App` | Navigation/preferences | UIContext, language/theme storage, auth modal, command palette, Routes |
| `App.tsx` → `ProjectShell` | Project header/live refresh | Project/system/run loads, SSE subscription, `forest-refresh`, active run controls |
| `App.tsx` → `Projects`, `ProjectForm` | Project creation/editing | List/filter, create/edit/conflict workflows, navigate to Workspace |
| `App.tsx` → `Overview`, research-controller UI | Budget/controller/progress | Project budget/mode editing, run summary, controller and ProgressPanel |
| `apps/web/src/ui.tsx` | Shared state and controls | `useLoad`, UIContext/action, Modal/focus helpers, Tabs, ErrorBox |
| `apps/web/src/DemoGuide.tsx` | Guided walkthrough | `demo=1`, `demo_step`, `demoLink`; ordinary project pages, no automatic research run |

Route table in App is authoritative. Project children are `overview`, `workspace`, `library`,
`ideas`, `theory`, `experiments`, `data`, `figures`, `paper`, `files`.
Run IDs appear in API operations, dock buttons, evidence components and scoped source identities;
there is no run detail route in this snapshot. Workspace never parses branch/run query parameters.
`interventions/InterventionPanel.tsx` emits `/projects/:id/runs?run=...` for decisions/run effects
and `/projects/:id/workspace?branch=...` for materialization effects. These are source-confirmed
producer/consumer mismatches; the actual browser redirect or missing selection still needs reproduction.
Do not label materialization's `effect.run_id` as the wrong ID just from its name: the backend
`services/interventions/workspaces.py` creates that effect with `run_id=plan['branch_id']` and later
looks it up as a Branch. It is an overloaded wire field; trace `action`/`target` before changing it.

## Workspace, graph and evidence

| Source | Responsibility and invariants |
| --- | --- |
| `apps/web/src/Workspace.tsx` → `WorkspaceInner` | ReactFlowProvider, visible nodes/edges, local branch/selection/view state, collapse storage, search reveal, layout, commands, run dock |
| `Workspace.tsx` → graph mutation/position helpers | Per-project queues/maps, expected revisions, pending positions, stale project guard, flush on unmount; inspect before changing optimistic movement |
| `Workspace.tsx` → `NodeInspector` | Scoped instruction drafts, edit base and conflict dialog, config/inputs/context edits, related runs; does not reset every local field from incoming node snapshots |
| `Workspace.tsx` → `RunPanel` | Local selected ID or first run fallback; action permissions by status; output offset polling every 2s with cleanup and a pending guard |
| `apps/web/src/RunEvidence.tsx` | Run lineage/session/verification/acceptance; resets identity-specific state; Promise.allSettled gives independent errors rather than hiding an entire panel |
| `apps/web/src/interventions/InterventionPanel.tsx` | Human instruction/decision scope, preview/apply/history integration |
| `apps/web/src/interventions/ApplicabilityReview.tsx` | Server evidence applicability interpretation |
| `apps/web/src/interventions/DependencyImpact.tsx` | Dependency/change impact display |
| `apps/web/src/api.ts` → `executionLinks`, `hasCycle` | Includes implicit verification/input consumption dependencies; only appropriate execution relations participate in cycle checks |

`WorkspaceInner` Escape sets `selected` to empty; the `[graph.nodes, selected]` effect then selects
the first node whenever selection is empty. This is a source-confirmed candidate for dismissal testing.
`branch` starts at `all` and stays local. Search can temporarily reveal an archived node and clear
a branch filter to show its target; these navigation actions should not write graph commands.
`RunPanel` polls the selected complete run ID. Switching IDs resets output and marks old loads inactive.
Do not replace this with a shared unscoped output string or truncate the ID in a request URL.

## Page state and adapters

| Source | Read when |
| --- | --- |
| `apps/web/src/pages.tsx` → `useProjectRecords`, `RecordEditor` | CRUD, revisions, generic research resources |
| `pages.tsx` → `LibraryPage` | Search/import; sequence guard and clearing prior results at new search |
| `pages.tsx` → `ResearchPage`, `TheoryPage` | Ideas and theory operations |
| `pages.tsx` → `ExperimentForm`, `ExperimentsPage` | Experiment definition, launch and pending-click protection |
| `pages.tsx` → `DataPage` | Run status filters, selected IDs, CSV export, analysis launch |
| `pages.tsx` → `FiguresPage`, `FigureEditor` | Stable figure ID, dirty switching confirmation, bound run IDs, figure render/export |
| `pages.tsx` → `PaperPage`, `PaperGeneration`, `PaperFigureInsertion` | Dirty manuscript/revision state, save-before-compile/generate/insertion, run evidence selection |
| `pages.tsx` → `FilesPage` | Bounded file pages, project generation guard, editor/preview switch, scoped session drafts, revision conflicts |
| `apps/web/src/fileSelection.ts` | Selection `{path,generation}`; A → B → A reads must not accept an old A response |
| `apps/web/src/files/fileTree.ts` | File tree ordering/filtering/collapse |
| `pages.tsx` → `SettingsPage` | Providers, defaults/budgets, resource and retention controls |
| `apps/web/src/settingsDraft.ts` | Update default provider in a valid draft while preserving other fields |
| `apps/web/src/ExecutionSettings.tsx` | Edit execution/acceptance/review settings without replacing other config |
| `apps/web/src/editors.tsx`, `components/` | Code editor, diff, table preview, PDF/formula, terminal |

Overview's `[p]` effect replaces `budget` and `mode` from every incoming project object.
Its budget text has no dirty guard in this snapshot. Observe unsaved editing during project refresh
before claiming a reproduced loss; compare with draft-preserving patterns already used elsewhere.

`PaperPage` currently stores only source/BibTeX/editingRevision in its scoped sessionStorage draft;
template/layout dirty state and revision instruction remain instance-local. A shared manuscript
surface must retain those too across panel hide, expansion and route unmount. The paper GET can
create the project's initial persisted draft through `services/api/resources.py:ensure_paper`;
distinguish that existing behavior from generated research content and from a failed load.

## Transport and freshness

`api.ts` uses same-origin fetch, `/api` prefix, JSON/FormData handling, `ApiError`, 401 auth event,
direct JSON response bodies and 204 → undefined. Its `download` helper obtains a blob and revokes
the object URL. `apiClient.ts` provides typed templates/options from generated contract paths,
encodes path values and serializes arrays as repeated query keys; it adds no retries.
The API contract is generated, not runtime validation of flexible scientific JSON.

`ui.tsx` `useLoad` tracks `currentPath` and a request `generation`. Same-path overlapping reloads
coalesce and schedule another fetch; unmount/path changes invalidate replies. `setData` invalidates
old reads too. New custom fetches must preserve equivalent scope and draft-version checks.

`progress/events.ts` shares project EventSource instances and coalesces event bursts for 150ms.
`progress/useProgress.ts` combines subscription and 2s polling, subscribes before its initial load,
checks project ID and epoch/generation, coalesces pending loads and disables old responses on cleanup.
`progress/SourceExplorer.tsx` resolves `?source=` JSON, refuses cross-project references, polls scoped
source/inventory and keeps source inspection separate from the editor. `ProgressPanel.tsx` `sourceLink`
builds the Files URL; a segment ID is inside the source JSON, not a separate `segment` query key.

## Styling, integration and verification

`apps/web/src/styles.css` owns tokens, layout and repeated responsive blocks; inspect the final cascade.
At ≤820px the sidebar becomes an icon rail, settings/footer entries hide, and inspector becomes an
absolute overlay. Workspace initializes inspector from matchMedia once. At ≤620px node search hides,
toolbar wraps and connection text hides; there is no implemented hamburger navigation entry in App.
`run-evidence.css` and `progress/progress.css` contain feature styling outside the main stylesheet.

`apps/web/vite.config.ts` binds 127.0.0.1:5173 and proxies `/api` and `/ws` to 127.0.0.1:8000.
`apps/web/package.json` scripts: `dev`, `build` (`tsc -b && vite build`), `preview`, `test`, `test:e2e`,
`format`. It has no npm lint/typecheck script. Vitest runs `src/**/*.test.{ts,tsx}` in Node.
`apps/web/playwright.config.ts` does not start a server. See the browser-testing skill for isolation.

For contract work, consult `docs/API_CONTRACTS.md`, `docs/FRONTEND_API_GUIDE.md`,
`packages/contracts/{openapi.json,api.generated.ts,INTERFACES.md}` and the matching `services/api/` handler.
`scripts/export_api_contract.py --check` checks generated outputs in a temporary configuration.
Use `npm --prefix apps/web run build`, targeted Vitest and browser checks appropriate to the change;
review source test bodies to distinguish fixtures, transport fault injection and real worker execution.
