# Navigation map from FOREST source

Snapshot: `ff16c3f656f94d053d1bba65c6b3a07a5feaccde`, inspected 2026-10-13. Paths below are relative to the FOREST repository, not the skill preparation workspace. Verify the current HEAD before using this map.

## Existing entry ownership

| Entry | Source owner | Proposed destination |
| --- | --- | --- |
| `/projects` | `App.tsx`: `Projects` and project editor | All projects / project switcher |
| `/projects/:id/overview` | `App.tsx`: `Overview`, `ResearchControls` | Workspace overview / goal / controls |
| `/projects/:id/workspace` | `Workspace.tsx` | Workspace graph / focus / branches |
| `/projects/:id/library` | `pages.tsx`: `LibraryPage` | Library |
| `/projects/:id/ideas` | `pages.tsx`: `ResearchPage` with `resource="ideas"` | Workspace ideas view |
| `/projects/:id/theory` | `pages.tsx`: `TheoryPage` | Workspace theory view |
| `/projects/:id/experiments` | `pages.tsx`: `ExperimentsPage` | Experiments & Results / Experiments |
| `/projects/:id/data` | `pages.tsx`: `DataPage` | Experiments & Results / Data |
| `/projects/:id/figures` | `pages.tsx`: `FiguresPage` | Experiments & Results / Figures |
| `/projects/:id/paper` | `pages.tsx`: `PaperPage`; `PaperLayout.tsx` | Paper / same document side panel |
| `/projects/:id/files` | `pages.tsx`: `FilesPage`; `files/fileTree.ts` | Files |
| `/settings` | `pages.tsx`: `SettingsPage`; `ExecutionSettings.tsx`, `ProviderUsage.tsx` | Bottom global Settings entry |
| `/share/:token` | `App.tsx`: `SharedProject` | Preserve read-only shared view |

Frontend paths in the table are under `apps/web/src/`. `App.tsx` owns global preferences, authentication modal, project shell, project bulk run actions, route list, and palette. `DemoGuide.tsx` owns demo linking. Progress and intervention views are embedded in overview/workspace via `progress/ProgressPanel.tsx` and `interventions/InterventionPanel.tsx`.

`GET /api/papers/{ident}` can resolve a document ID or project ID; `services/api/resources.py:paper` calls `ensure_paper` for the project and persists an initial draft when none exists. A side panel must account for this read side effect and show that actual draft rather than assume null means no manuscript. An initial template is not a generated evidence-supported paper. Opening the panel must not implicitly trigger generate/revise/compile.

There is no project `/runs` route in the inspected `App.tsx` route list; API run routes are a separate concern. Existing run inspection is embedded. Register a compatible run view or redirect only to a surface that truly consumes `run` and displays that entity. Source inspection establishes the mismatch; browser reproduction remains required.

The palette is presently a filtered list of page links. The project switch entry returns to `/projects`; it is not a searchable selector yet. Do not document the requested upgraded behavior as already implemented.

## Trace capability rather than invent it

Search `api(`, `download(`, form handlers, and navigation calls in the owner above; trace to `api.ts`, `apiClient.ts`, `packages/contracts/api.generated.ts`, `packages/contracts/openapi.json`, and `services/api/`. Requests use API paths, whereas React Router uses browser paths. An API endpoint existing does not establish a reachable browser page.

Use `docs/FRONTEND_API_GUIDE.md`, `docs/API_CONTRACTS.md`, `docs/WORKFLOW_INTERVENTIONS.md`, and `docs/EVIDENCE_WORKFLOW.md` when the affected task crosses those domains. Preserve current import/export/share, project archive/duplicate/delete, terminal/logs, file editing, budget/provider settings, and graph operation entries after confirming them in source.

## Compatibility checklist

- Load an old URL directly, reload it, then use Back/Forward after changing its selected object.
- Switch projects while a request is outstanding; the response must not select an entity from another project.
- Preserve relevant query fields through aliases; validate identity rather than blindly transferring project-specific IDs to another project.
- Check demo links through the real `demoLink` implementation; do not drop `demo` while normalizing a route.
- A missing selected object must yield a specific recoverable state, not a different default object presented as the requested one.
- Keep every frequent action reachable at 390, 768, 1024, 1280, 1440, and 1920 px and at 200% zoom.
