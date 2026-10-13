# Manuscript surface audit

Source inspected: `ff16c3f656f94d053d1bba65c6b3a07a5feaccde`. This is source inspection; browser acceptance is recorded separately.

`PaperPage` in `apps/web/src/pages.tsx` supplies source and BibTeX editing, bindings, evidence checks, template/layout controls, generation from selected eligible runs, insertion of reviewed figures, model revision requests, compilation, logs/preflight, export and review proposals. The existing Workspace manuscript tab replaces the graph rather than showing a secondary surface.

`GET /api/papers/{ident}` resolves a paper ID or project ID. Reading a project with no manuscript creates its initial persisted template draft. Opening a manuscript must not trigger generation, revision, compilation or model work.

| Operation | Contract and state |
| --- | --- |
| Save | `PATCH /papers/{ident}` with the editing paper revision and source/BibTeX; response is the saved paper with incremented revision. Preserve the draft on conflict. |
| Compile | `POST /papers/{ident}/compile` queues a run for the saved paper snapshot. Acceptance does not establish a PDF; include `expected_revision`. |
| Layout | `POST /papers/{ident}/layout` requires paper `expected_revision`, template and layout. It queues a layout run; layout results and compilation are separate from evidence checks. |
| Generate | `POST /papers/{ident}/generate` selects completed same-project experiment/command/agent runs with actual finite readable metric artifacts and current Goal applicability. Browser `manuscript_evidence.ready` reflects metric eligibility only. |
| Figure insertion | `POST /papers/{ident}/figures` requires paper revision, a same-project available/reviewed rendered figure and a unique source anchor. It returns the updated paper. |
| Revise | `POST /papers/{ident}/revise` queues a revision against the saved paper snapshot; retain the request instruction on failure. |
| Source ZIP | `POST /papers/{ident}/export` can export the exact editing source/BibTeX using the editing revision. |
| Current PDF | The same export endpoint requires `format: "pdf"` and paper `expected_revision`; `PDF_UNAVAILABLE`, `STALE_PDF` and `REVISION_CONFLICT` are separate server errors. A path or a queued compile is insufficient. |
| Review proposals | Existing review lists and exact-edit apply flow retain original paper-revision guards. |

Current draft persistence stores source, BibTeX and revision only. Template, layout, revision instruction and dirty flags are lost on unmount. The paper snapshot effect restores source on every read; a complete shared editing session must protect its document and base revision while either source or layout is dirty. A successful save must also retain edits made while its request was pending.

The current PDF tab directly opens `pdf_path` without indicating whether `compiled_revision` matches the saved paper revision. Export is labelled generically and currently creates a source ZIP. The redesign needs explicit format controls and a visible current/stale compilation state; no stale document may be labelled as the current PDF.

The compact surface will reuse `PaperPage embedded` and the same Monaco/PDF components. The shell owns visibility, resizing and expansion. A project-scoped editing-session hook will preserve source, BibTeX, base revision, template, layout, revision instruction and dirty flags across panel hiding, full-page navigation and refresh without introducing another authoritative paper cache.

## Implemented behavior

The manuscript module is now `apps/web/src/pages/PaperPage.tsx`. `apps/web/src/paperDraft.ts` supplies a shared editing session scoped to a project. Session storage retains source, BibTeX, the editing paper revision, template, all layout fields, revision instruction, source/preview tabs, compile run ID and dirty flags across panel hiding, expansion, route changes and reload. Clean sessions follow authoritative saved paper reads. Dirty source or layout keeps its original base revision when a refreshed paper arrives. Accepted saves advance the base while retaining text entered during the pending request. Busy/error state is shared by the compact and full editors, so expansion cannot submit a duplicate operation. Monaco view/cursor state has distinct source and BibTeX session keys.

Current PDF and source ZIP have separate controls. Current PDF always uses the actual export endpoint with `format: "pdf"` and the saved editing revision. Unapplied layout edits require applying the layout and compiling first; that guard does not save the source or submit an export. The explicit saved/previous PDF preview remains available and identifies its compiled revision. Missing, stale and conflicting exports retain their distinct server error codes. The compile control displays the actual queued run ID/status; it does not imply a current PDF before compilation finishes.

`apps/web/src/PaperWritingReview.tsx` exposes the existing deterministic wording endpoint. It reviews only the explicitly submitted current source and applies one exact span to the draft. Unicode offsets use Python-compatible code points. Changing the source makes the report stale. Phrases needing scientific evidence judgments retain their original wording and cannot be automatically applied. Applying a literal edit does not save the manuscript; the existing guarded Save action persists it. Opening the panel or wording section performs no model call.

## Validation

Source inspection covers the handlers, generated contract types and editing-state boundaries. Fourteen focused Vitest tests passed across the shared draft session, layout behavior and wording/publication helpers, including an accepted save reply arriving after a newer authoritative paper revision.

Four targeted Playwright scenarios passed against the final consolidated build with the real isolated API and normal worker at `http://127.0.0.1:18340`, with independent data under `/tmp/forest-redesign-runtime-20261013`. Three scenarios use real manuscript endpoints: complete draft persistence through panel/full-page/reload plus an actual revision conflict; an accepted real save whose response is delayed while newer text is typed after expansion; and source save, normal Tectonic compilation, current PDF download, unapplied-layout export rejection without save/export, explicit saved PDF preview, actual stale-PDF rejection and source ZIP download. The fourth existing repeat-click regression uses mocked revision responses. This is functional validation, not scientific evidence validation.

The earlier targeted real compile was `e2c0799f-8571-4342-a00a-9768dfa98f16` for isolated project `193e97b8-b2d3-49df-be99-f06e7bc11e14`, paper revision 1 with `compiled_revision: 1`. The downloaded current PDF contained `%PDF-` bytes (3,815 bytes); PDF and ZIP artifacts remain under `apps/web/test-results/manuscript-redesign`. Test projects were removed after their checks. Paid/model-backed generation and revision were not executed.

The final build-63005 production regression passed again in 6.7 seconds with a real Tectonic compile for project `8d6ec611-4ad4-4724-8f06-0e4276cd3ee0`, run `4d1bae24-5c5a-4d73-b1b9-c40b085474ff`, saved/compiled paper revision 1 and a genuine 3,815-byte downloaded PDF. The case now also delays browser GET delivery of those actual compiled bytes, closes/reopens the preview twelve times, verifies final rendered canvas ink and asserts zero browser page errors. The complete final 108-case browser suite includes this regression. [The targeted lifecycle receipt](evidence/pdf-preview-lifecycle-targeted.json) records cancellation/render evidence; it does not represent twelve real worker compilations.

A read-only browser fixture using an earlier actual PDF reproduced six PDF.js worker-termination page errors in sixteen rapid preview cycles before the fix. [The same probe after the fix](evidence/pdf-cleanup-probe-fixed-dev.json) recorded zero. `PDFViewer` now performs an owned abortable, credentialed HTTP read and constructs the worker only from complete bytes. Genuine read/parse failures remain visible and created loading/render tasks retain cleanup. First display now waits for the full PDF download; large-file streaming/startup comparison was not run. This scoped lifecycle change neither changes the PDF.js dependency nor suppresses global browser errors.

Final compact/expanded visuals, actual stale/current PDF labels, dirty state and 300px pane editor geometry are recorded in [visual-comparison.md](visual-comparison.md). The final shared right pane uses Analysis/Paper tabs while retaining the same project manuscript session and guarded export behavior.
