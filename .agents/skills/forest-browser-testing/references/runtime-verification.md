# Isolated runtime verification

Read this when starting services, selecting runtime evidence or reproducing an asynchronous interaction.
Commands assume the FOREST repository root and already-installed repository dependencies.
They are procedures for a future authorized verification task, not claims that they were run when
the skill was written. Verify the current source, selected test and available browser first.

## 1. Establish the test boundary

Use a fresh directory, fresh SQLite database and an available non-production port.
Do not infer the backend from an open browser tab. Record `/api/health`, chosen base URL and data path.
Override both `FOREST_DATA_DIR` and `FOREST_DATABASE_URL`: `--data-dir` alone leaves an inherited
database URL in effect. The API also reads repository `.env`; environment overrides take precedence.
Use `FOREST_MODEL=''` and a fresh DB for local checks that do not need a provider.
Set these overrides before importing `services.api.config` or `services.api.db` in
custom helpers: config import loads `.env` and initializes data directories. Reading
Python source or parsing its AST does not have those runtime side effects.
No provider creation/model test is needed for command/file/graph/ordinary progress checks.
Keep provider experiments separate and bounded by their existing authorization and budgets.

Dependency preparation, only if needed for the actual task:

```bash
npm --prefix apps/web ci
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip install --no-deps -e .
```

The `.venv` must already exist; repository setup/install scripts can create it but have broader effects.
Inspect those scripts before using them. Node 22 and Python 3.11 are CI baselines, not proof of availability.
For bundled Chromium, use `cd apps/web && npx playwright install chromium`, then set
`FOREST_BROWSER_CHANNEL=chromium`. Linux CI uses `--with-deps`; this is not an automatic macOS command.
Alternatively keep the default `chrome` only if Chrome is installed. Do not fabricate browser success
when browser binaries, Python/Node libraries, isolation tooling or runtime artifacts are missing.

## 2. Production build with normal API and worker

This bash example runs the no-mock progress scenario on a single isolated origin. Choose another port
if 18340 is occupied. It leaves test data and logs available for diagnosis and stops only its own server.

```bash
set -euo pipefail
forest_test_dir="$(mktemp -d "${TMPDIR:-/tmp}/forest-browser.XXXXXX")"
export FOREST_DATA_DIR="$forest_test_dir"
export FOREST_DATABASE_URL="sqlite:///$forest_test_dir/forest.db"
export FOREST_MODEL=''
export FOREST_WEB_URL='http://127.0.0.1:18340'
export FOREST_BROWSER_CHANNEL='chromium'
.venv/bin/python -c 'import socket; sock=socket.socket(); sock.bind(("127.0.0.1", 18340)); sock.close()'
npm --prefix apps/web run build
.venv/bin/python scripts/start.py --port 18340 \
  --data-dir "$FOREST_DATA_DIR" --database-url "$FOREST_DATABASE_URL" \
  > "$forest_test_dir/server.log" 2>&1 &
forest_test_server_pid=$!
trap 'kill "$forest_test_server_pid" 2>/dev/null || true; wait "$forest_test_server_pid" 2>/dev/null || true' EXIT
forest_test_ready=0
for forest_test_attempt in $(seq 1 50); do
  if curl --fail --silent "$FOREST_WEB_URL/api/health" >/dev/null; then
    forest_test_ready=1
    break
  fi
  if ! kill -0 "$forest_test_server_pid" 2>/dev/null; then break; fi
  sleep 1
done
if [ "$forest_test_ready" -ne 1 ]; then
  cat "$forest_test_dir/server.log"
  exit 1
fi
npm --prefix apps/web run test:e2e -- progress.spec.ts \
  --grep 'owner creates a project'
```

If invoking the installed CLI, replace the start line with `forest serve --port 18340
--data-dir "$FOREST_DATA_DIR" --database-url "$FOREST_DATABASE_URL"` and preserve all isolation env.
The supervisor starts separate API and worker processes and migrates only the configured DB.
API health confirms API readiness; actual command transition confirms worker readiness and execution.
Worker execution may require the platform isolation runtime. Follow real logs/preflight instructions;
do not hide an unavailable execution backend by mocking a completed run.
Keep `FOREST_DATA_DIR` in the test environment for specs accessing externally written project files.
Use a fresh browser context so saved language/theme/drafts do not accidentally alter selectors.

Production is preferable for full integration because it serves compiled web and API on the same URL.
Do not start a new Vite server that silently chooses a fallback port while tests still target 5173.
For dev verification, `scripts/dev.sh` starts API:8000, worker and Vite:5173. Set the same isolation env
before launch; verify those ports are unused. Vite's API/WS proxy target is fixed at 8000, so an API
`--port` change alone does not redirect the dev proxy. Use production mode for alternate-port isolation.

## 3. Target checks to the change

Examples, after the isolated server is healthy:

```bash
npm --prefix apps/web test -- src/fileSelection.test.ts
npm --prefix apps/web test -- src/RunPanel.test.tsx src/Modal.test.tsx
npm --prefix apps/web run test:e2e -- file-preview-preserves-csv-draft.spec.ts
npm --prefix apps/web run test:e2e -- workspace.spec.ts --grep 'narrow workspace'
npm --prefix apps/web run test:e2e -- progress.spec.ts --grep 'delayed real progress'
npm --prefix apps/web run test:e2e -- data-analysis.spec.ts experiment-launch.spec.ts
```

The first two commands are unit tests. The async progress case is real response transport injection.
Analysis/experiment repeated-click tests use mocked response data and establish UI deduplication only.
For a broad route/state refactor, expand to relevant neighboring pages or the full e2e suite after
checking its filesystem, runtime and cleanup requirements; do not make this every small-edit default.
`npm --prefix apps/web run test:e2e -- --list` lists selected tests without executing browser actions.

Use response gates or actual awaited state for custom regressions. Prefer `waitForResponse`, expect.poll
and accessible assertions to a fixed sleep. Distinct title/IDs/text make contamination observable.
Keep one created project per scenario where feasible and delete only its confirmed ID in cleanup.
Test failure cleanup is a mutation, too; disposable data is still required for mocked-browser cases
that create/delete real projects through the Playwright request fixture.

## 4. Critical frontend observables

| Area | Meaningful success criterion |
| --- | --- |
| Graph mutation | Correct scoped command accepted; returned revision/state and refetched graph agree; conflict retains edit |
| Node movement | Position survives reload; pending flush settles before dependent edit; switching projects does not apply old graph |
| Search/navigation | Requested node ID visible, selected and inspector usable despite offscreen/filter/collapse; graph unchanged |
| Branch/run deep link | Current source has no branch/run URL synchronization; test reload/back/forward only if implementing it |
| Run control | Correct full run ID receives allowed action; actual persisted status and output reflect it |
| Run output | Switching runs clears old output; a released old response cannot append into the new run |
| File editor | Current path/generation content shown; dirty draft survives preview/source refresh; save uses loaded revision |
| File conflict | Changed/deleted/renamed/recreated content cannot be overwritten silently; draft and server state remain distinguishable |
| Budget draft | Distinct unsaved budget survives project refresh when that behavior is being implemented |
| Selection dismissal | Intended state remains after Escape and effects settle; source auto-selection is a candidate to observe |
| Pending actions | Repeat activation creates one request until completion; real error permits a deliberate retry |
| Figure/paper | Stable selected identity; dirty edits survive list refresh; save/compile/render states and real artifacts agree |
| Responsive | Required action entry points reachable; focus/control text usable; narrow inspector can open/close; no unintended document overflow |

At the audited revision, source confirms Escape clearing followed by automatic first-node selection,
Overview's unconditional `[p]` budget/mode reset, local branch/run selection, narrow icon navigation,
hidden settings/sidebar footer at ≤820px and hidden node search at ≤620px. Do not turn these into
browser-reproduced claims without the corresponding interaction and observation on the current build.
InterventionPanel links to the missing project runs route and an unconsumed branch query. Its
materialization effect `run_id` is populated with a branch ID by the backend, so the route/query
mismatch is established in source while an ID-type bug is not established by the name alone.
Use desktop (1440×1000), around-boundary narrow width (e.g. 786×800) and mobile (390×844) as useful
starting cases. Also resize an existing session: inspector initial matchMedia state is not a resize listener.
Intentional canvas/editor/table scroll is not document overflow; check visibility and reachable controls.

## 5. Evidence and shutdown

Capture page errors, unexpected failed API requests and relevant server/worker logs.
For local screenshots/traces, use a scoped QA directory and descriptive route/state filenames.
Use `--trace on` on a targeted Playwright run when interaction evidence is useful; output goes to
`apps/web/test-results` by default. Preserve failure outputs before rerunning if they are needed.
Report actual commands, exit status and the assertions/observables established. Distinguish fixtures,
real persistence, response delays and real worker execution in the validation summary.
Freshly executed command smoke is platform evidence. It does not establish a scientific improvement.
Scientific claims require actual saved predictions/measurements, provenance and appropriate independent
checks; mock `accuracy:0.9`, fixture runs, demo tables and qualification oracles cannot substitute.

Stop the supervisor with its recorded PID or foreground Ctrl+C. It terminates its API/worker children;
worker shutdown preserves detached work for recovery, so complete/cancel your bounded test work first.
Do not kill unrelated services, remove shared `var/`, or invoke settings cleanup to reset the environment.
Retain isolated data/logs until diagnosis is complete; then remove only the known disposable directory
if cleanup is appropriate. No paid provider connection test, demo workload, long soak or persistent
qualification service is necessary for these browser checks.

## 6. Full redesign: actual numerical artifact journey

Use `examples/cli/README.md`, `goal.md`, `node.json`, and `project-config.json` as an existing
provider-free workflow. The declared experiment executes a real standard-library trapezoidal
integration calculation and writes `metrics.json`. Read the command; do not replace it with a
prepared metrics response. The sample's explicitly selected `operational` publication profile
limits this test to runtime integration; it does not prove full-submission readiness or novelty.
Do not change the user's scientific projects to this profile.

On the isolated server above, implement a browser journey that creates a manual test project,
sets the actual goal and sample project config, creates the experiment node with the sample
execution config, submits it once, and records the returned full node/run IDs. Follow observed
running/terminal status, inspect actual logs and worker-created `metrics.json`, and compare its
numeric output with the executed method rather than with a fabricated API fixture. Inspect
lineage, source run/revision, actual artifact path and evidence receipt; unverified evidence
must remain unverified unless a real supported verification check is run.

Open the related result through the UI, download the actual artifact and project export using
supported controls, validate the downloaded file content/type, reload, and confirm the same
run, measurements and provenance persist. The example CLI can independently inspect/export
the same project; point it explicitly to the isolated server with its verified `--server`
option and a separate empty CLI working directory. CLI checks supplement browser actions.

Treat manuscript draft creation, source editing, compilation, current PDF export and evidence
validation as separate checks. A local numerical run or existing PDF does not establish those.
Use current API capabilities for evidence binding; do not enqueue model-backed manuscript
generation to fill this test. If worker isolation, Python execution, compilation tools or a
required external model are unavailable, record the affected step as NOT RUN with its actual
missing condition, retain logs, and do not replace it with mocked completion.
