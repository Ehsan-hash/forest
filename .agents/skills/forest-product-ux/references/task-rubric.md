# Task, parity, and integration rubric

Use this for full redesign phases and when an action moves between surfaces. It specifies evidence to collect, not a prefilled success report.

## Feature record

For each real capability record: Feature ID, name, existing route/component/entry, API endpoint, payload, response type, required IDs, revision handling, persistence, errors, new entry, tests, and verification status. For pure local actions, state the actual local state effect instead of inventing an endpoint.

Cover project/goal/planning; graph/nodes/branches; literature/ideas/hypotheses/theory; experiments/runs/data/figures; manuscript editing/compilation/validation/export; evidence; interventions/decisions; agents/providers/execution/budgets; files/logs/terminal; import/export/share; and additional capabilities discovered at HEAD. Mark requested but unsupported operations explicitly rather than giving them dead buttons.

## Action record

| Field | Evidence required |
| --- | --- |
| User intent and scope | Object and operation described by the control |
| Entry and availability | Current selection/status/prerequisites; disabled reason |
| API or local effect | Handler → service → endpoint, or actual local transition |
| Identity | Project, node, branch, run, document and applicable revision |
| Feedback | Submitted / accepted / persisted / completed / verified as separately supported |
| Failure | Actual error category, retained input, retry/conflict recovery |
| Persistence | Reload/readback or documented transient preference |
| Test | Test name, method, command, evidence file, result |

For graph commands also record selection, parameters, impact preview support, expected revision, and undo support. For bulk run operations record each successful/failed request and remaining active runs.

## Journey measurement

Choose matching initial data, viewport, and user task. Count deliberate UI activations as clicks; report typing separately. A context switch changes page, main view, or active editor. Required decisions count choices needed to finish, not keystrokes. State any more suitable counting definition before measuring.

| Journey | Before clicks / switches / decisions | After clicks / switches / decisions | Evidence |
| --- | --- | --- | --- |
| Create project → set goal → open graph | Observe baseline | Observe redesigned flow | Trace/screenshots |
| Create/edit node → save → run → inspect result | Observe baseline | Observe redesigned flow | Run/node IDs and readback |
| Inspect request for decision → decide → resume if needed | Observe baseline | Observe redesigned flow | Actual decision/run state |
| Submit scoped formal instruction → inspect effects | Observe baseline | Observe redesigned flow | Receipt and lifecycle |
| Edit manuscript → compile → download | Observe baseline | Observe redesigned flow | Revision + actual output |
| Inspect figure/data → source run/evidence/file | Observe baseline | Observe redesigned flow | Source identities |

Do not populate these rows with guesses. Reduce task friction without discarding scope, impact, provenance, or mandatory user decisions.

## Full-redesign records

During implementation, maintain the requested `docs/ui-redesign/` component/feature inventories, audit, information architecture, design system, interaction specification, API coverage, test matrix, visual comparison, and final report. Inventories include parent, route, state/API ownership, actions, problem, and actual change. Remove confirmed unused implementations only after parity and callers are checked.

Use bounded phases: baseline → reliability → design system → shell → workspace → other pages → architecture → E2E → final audit. Finish each phase with the relevant checks and a working build. Final evidence covers feature preservation, persistence, correct identity and semantics, errors, accessibility, responsiveness, performance, and real scientific workflow separately.
