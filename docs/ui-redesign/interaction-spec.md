# FOREST interaction and persistence contracts

## Graph and node editing

Selection is local navigation; it is not a graph mutation. Escape closes the top temporary surface, then clears the graph selection when appropriate. Blank canvas dismissal leaves the editor session recoverable. Search reveals offscreen, filtered and collapsed nodes without changing their saved coordinates. Full node/branch query IDs survive reload and browser history.

Node editing stores title, execution configuration, inputs/context, instructions and original revision in a project/node editing session. Background graph updates do not overwrite a dirty draft. A revision conflict preserves local content and requires explicit current-state review. Graph commands retain existing impact preview, expected revision, request identity, undo and redo behavior. Position saves keep pending/in-flight intent until the server receipt is reconciled. Native measured dimensions and active drag position survive reconciliation; authoritative deletion still removes the node.

## Runs and human intervention

Node/selection/branch submission uses its supported API scope and returned complete Run ID. Acceptance is presented separately from persisted running/terminal status. Run inspection verifies the project identity, fetches selected historical runs outside the bounded list, and keeps unavailable selections visible rather than falling back. Logs follow the server byte cursor. Recorded metrics identify the executed node revision and Run ID; presence of metrics does not assert scientific verification.

Pause, Resume, Cancel, Retry and Skip are gated by actual run state. Retry creates a new run. Priority, configuration patches, diagnostics, repository provenance and editable stopped debug checkpoints are reachable in advanced controls. Saving a checkpoint override does not Resume. Checkpoint drafts retain their original run/stage/payload identity; a mismatched older draft remains separately recoverable. Configuration acknowledgments clear only the submitted text, retaining later typing.

Formal instructions retain their reviewed project revision, exact scope and adoption boundary. Active Agent run scope is restricted to `next_request`. An uncertain response retries the same intent/request ID; changed text creates a new intent. Actual 409 requires explicit review/acknowledgment and preserves the draft. Saved, accepted, applied and scientifically effective are separate states. Human decision answers and continuation are separate operations; failure or stale acknowledgment retains edited action/reason. Older history responses cannot append into a different project or a later visit.

## Manuscript and evidence

Graph panel and full editor share one manuscript editing session, including source, bibliography, template, layout, revision instruction, tab/editor position, dirty state and base revision. Hiding/expanding does not discard it. Save acknowledgment retains edits made while the request was pending. External changes do not become a dirty draft's new reviewed base.

Compilation has an actual Run identity and outcome. Saved prior PDF is distinct from current compiled revision. Current PDF export uses explicit PDF format and expected revision; stale PDF is rejected. Unapplied layout must be applied and compiled before current export. Source ZIP and actual PDF are separate actions. Literal writing review applies exact Unicode source spans; evidence judgments and unsupported recommendations remain with the author.

Figure source Run IDs, data bindings, revision and rendered outputs remain explicit. Numerical values are read from saved results; display precision does not alter stored/exported values. Independent checks keep producer/file scope separate from unsupported numerical scope and Goal applicability. No command receipt, artifact presence or execution completion is promoted to scientific success.

## Files, settings and failures

File reads use project, path and generation guards. Background refresh revalidates the current pagination cursor; navigation defers refresh until the new cursor is committed. Explicit refresh may restart the scan. Upload conflict preflight is independent of the visible page. Saved preview and unsaved editor text stay distinct. Blob downloads attach the download anchor and release the URL after activation.

Settings drafts preserve invalid JSON, unknown fields and original baselines across refresh/navigation/reload. Saving submits changed top-level keys to the server's shallow-merge endpoint. First PATCH initializes the same defaults as GET. Invalid provider configuration produces a recoverable form error. Public share does not mount owner settings/navigation. Real 401 opens authentication; 403 remains a permission error rather than an authentication prompt.

Publication requirements preserve unknown configuration and untouched external changes with a fresh three-way comparison. Evaluation protocol versions retain explicit revision/request identity and saved history. Structural validation creates no run and is not publication approval. The existing publication PATCH and checkpoint PATCH lack atomic stage/revision guards for their respective final read/write race; this client does not invent such guarantees.

Actual regression and runtime boundaries are in [test-matrix.md](test-matrix.md), [workspace-audit.md](workspace-audit.md), [manuscript-audit.md](manuscript-audit.md) and [project-requirements-audit.md](project-requirements-audit.md).
