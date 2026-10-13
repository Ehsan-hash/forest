# Frontend route boundaries and global settings drafts

The original `pages.tsx` placed every project page, global connection editor, file editor and manuscript editor in one source module. Nine page owners now live in `apps/web/src/pages/`: Library, research ideas, theory, experiments, data, figures, files, settings and manuscript. `shared/records.tsx` retains the existing project-record loader/editor/action implementation; `shared/metrics.tsx` retains compact metric formatting. `pages.tsx` remains a compatibility export barrel, and App loads each route module on demand.

Extraction used TypeScript syntax nodes to retain function bodies and select only referenced import bindings. Existing API calls, refresh subscriptions, entity identities, file selection generations, revision guards, editor adapters, evidence interpretation and draft keys moved with their owners. The modules use the existing API transport and shared invalidation events.

Global Settings now stores an editing session separately from fetched settings. A background refresh preserves complete dirty JSON text and its original baseline, including invalid JSON. A default-provider change updates that field in valid drafts without discarding unknown keys; invalid drafts remain untouched. Navigation and reload restore unsaved text. Save submits changed top-level keys to the existing shallow-merge endpoint, preserving external changes to untouched server keys. Edits made during a pending save survive acknowledgment. API errors remain next to the form.

The connection editor catches invalid JSON inside its action flow, restores the Save button and retains form fields. Provider pricing preserves unknown nested configuration keys. Storage cleanup retains explicit scoped controls and confirmation.

## Validation

- Settings draft helper tests: PASS (4), including external refresh, provider changes, invalid JSON and edits during a pending save.
- Production build after module extraction and Settings changes: PASS.
- Actual isolated API/browser Settings cases: PASS (3). Dirty JSON survived actual GET refresh, navigation and reload; the observed PATCH body contained only the changed retention key, and an external unknown-key update survived. Default-provider changes preserved valid unknown draft fields and invalid JSON. Invalid connection JSON retained the form and produced a durable error without saving a provider.
- Existing storage retention/error browser regressions: PASS (3, mocked settings responses; cleanup deletion was not executed).
- Subsequent run and node controls: TypeScript check and targeted RunPanel/run-control/checkpoint/node-draft unit tests PASS (24 across four files); Settings helper tests separately PASS (4). Actual branch submission/queued Skip, configuration acknowledgment retaining later edits, real debug-checkpoint/Pause/Resume/Cancel, delayed Retry selection protection and clean/dirty Undo/Redo browser cases PASS against production assets.

`GET /settings` and the first `PATCH /settings` now share the same `DEFAULT_SETTINGS` initialization, allowing changed-key saves on fresh databases without dropping omitted defaults.

Project run settings also preserve edits made while Save is pending. Acknowledgment updates the saved baseline and revision from that exact response, retains later budget text or mode changes, and normalizes only fields unchanged since submission. Background snapshots wait during the pending Save, including when an edit returns a field to its earlier saved value. Six controls-draft helper tests and TypeScript validation pass. Two real API/browser controls regressions pass in `apps/web/test-results/controls-ack-final` (3.3 seconds): dirty drafts survive SSE/navigation/reload/conflict; a held successful PATCH retains later invalid budget text and mode, advances only its accepted baseline, and rejects a subsequent save against a newer external revision. Explicitly loading current values then permits a correctly guarded save, preserving unknown budget keys.
