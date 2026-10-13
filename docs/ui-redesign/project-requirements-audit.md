# Project requirements and literal writing review

The advanced requirements surface on Overview uses the existing project publication and research-evolution APIs. It remains collapsed until requested. Expanding it reads current saved audit/profile, evaluation versions, default publication policy and writing policy. These reads do not generate a scientific review or enqueue model work.

| Entry | Actual API and semantics |
| --- | --- |
| Saved delivery audit | `GET /api/projects/{ident}/publication` reports the saved publication profile and actual delivery gaps. It does not establish conference/journal acceptance. |
| Publication JSON | `PATCH /api/projects/{ident}/publication` shallow-merges profile fields and normalizes the resulting profile. The frontend submits only changed fields, keeps unknown untouched fields and rejects detected changes to the same fields using a fresh read. |
| Default policy | `GET /api/publication-profile` and `GET /api/writing-policy` return actual service policy/configuration. |
| Structural validation | `POST /api/projects/{ident}/protocol/validate` validates the edited definition and returns `structurally_checked` with the explicit structure-only scope. Full-submission requirements additionally invoke structural submission-design validation. No experiment or model review executes. |
| Evaluation version history | `GET /api/projects/{project_id}/evaluation-protocols` returns original stored definitions, classification, feedback provenance and parent version IDs. |
| Record evaluation version | `POST /api/projects/{project_id}/evaluation-protocols` uses actual project and protocol revision guards plus a persisted request identity. Accepted replay reuses the same intent; conflicting revisions retain the draft. Existing feedback is preserved by the service. |
| Literal writing review | `POST /api/projects/{ident}/writing/review` returns deterministic exact phrase matches. Literal edits may be applied to the draft; evidence-dependent recommendations remain with the author. |

The publication PATCH handler has no atomic `expected_revision` guard in the current contract. A fresh three-way field comparison prevents known conflicting edits and a sparse patch protects unrelated updates. It cannot prevent another writer changing the same field between that read and the unguarded PATCH. The UI does not claim an atomic guard that the service lacks. Switching to the explicit operational profile follows backend normalization, which canonicalizes the profile to its operational fields; full-submission profiles preserve unknown extension fields.

Publication and protocol drafts live in project-scoped session storage with their original bases. Server refreshes cannot replace dirty JSON. Saving retains text entered during a pending operation. Protocol submission persists the complete request identity and guards across failures/remounts; retries with the same intent replay instead of creating duplicate history. Explicit reload warns before discarding dirty edits. Independent-confirmation classification is presented as declared scope and cannot substitute for actual execution, verification or independent review.

Literal review reports are tied to the exact source and project. Applying a reviewed edit validates the actual original Unicode span. Old reports cannot apply after any source change. The manuscript retains its current paper revision until the explicit Save action succeeds.

## Validation

Five Playwright scenarios passed against the final consolidated production build and real isolated API at `http://127.0.0.1:18340` (5.7 seconds). They create their own manual projects, inspect actual request/response data and remove only those projects afterward. No scenario created a research run.

- A full-submission profile edit sends only its changed target. Original unknown nested profile fields, an unrelated project configuration object and concurrently added untouched requirements remain saved. A conflicting target retains the complete original draft through reload.
- The real structural validator returns `structurally_checked` and its structure-only scope. An actual project revision conflict retains the protocol JSON through reload. Explicit reload advances the guard, and a later accepted version retains the original definition and actual `parent_version_id`.
- An external editor creates protocol r2 while r1 is being edited. The local guarded request returns 409. Explicit fresh reload reads r2, then the new accepted r3 points to that external version; r1 and r2 remain unchanged as complete JSON records. A changed publication profile marks the retained edited-draft structural report stale. Automatic clean saved-protocol refresh withdraws its prior report.
- A real protocol POST is accepted by the service, then its response is deliberately dropped at the browser transport. After reload, retry sends the exact same stored request identity, guards and body. The service replays the accepted record and the actual history contains one version. This scenario injects transport failure; it does not mock the backend acceptance or its replay.
- The actual deterministic writing endpoint returns exact spans after a non-BMP Unicode character. Only its literal replacement is applied to the draft; the evidence-judgment phrase has no apply action. The old report becomes stale after editing, and the persisted manuscript changes only after explicit Save.

Four helper tests additionally cover Unicode offsets, rejecting changed/evidence-dependent spans, sparse publication conflict protection and canonical object-key comparison. These are client unit checks. The fourteen-test focused manuscript/layout/helper set and TypeScript compilation passed. Model-backed review and scientific validation were not run.
