import { chromium } from '../../../apps/web/node_modules/playwright-core/index.mjs';
import { randomUUID } from 'node:crypto';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// Actual local commands only. Projects and artifacts are retained for review.
const repository = fileURLToPath(new URL('../../..', import.meta.url));
const docs = path.join(repository, 'docs/ui-redesign');
const out = path.join(docs, 'screenshots/states');
const recordPath = path.join(docs, 'state-observations.json');
const origin = process.env.FOREST_WEB_URL || 'http://127.0.0.1:18340';
const python = process.env.FOREST_WORKER_PYTHON || path.join(repository, '.venv/bin/python');
const expectedAsset = process.env.FOREST_EXPECTED_ASSET;
const workload = path.join(repository, 'scripts/test_worker_case.py');
const terminal = ['completed', 'failed', 'cancelled', 'interrupted', 'skipped'];
const mode = process.argv[2] || 'all';
if (!['prepare', 'capture', 'static', 'focus', 'all'].includes(mode)) throw new Error('Use prepare, capture, static, focus or all');
await fs.mkdir(out, { recursive: true });
let record = { createdAt: new Date().toISOString(), origin, status: 'preparing', receipts: [], projects: [], runs: {}, screenshots: [], limits: [
  'Numerical values are actual trapezoidal integration outputs; they do not establish a research improvement or independently verified scientific evidence.',
  'The many-node project contains saved layout fixtures with no execution or scientific results.',
  'after_model is a real stopped debug callback, distinct from an Agent human-decision workflow.',
  'Agent human-decision execution: NOT RUN; no provider or Bubblewrap execution was used.',
] };
const persist = () => fs.writeFile(recordPath, JSON.stringify(record, null, 2));
const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const runSnapshot = run => ({
  id: run.id, projectId: run.project_id, nodeId: run.node_id, nodeRevision: run.node_revision,
  branchId: run.branch_id, kind: run.kind, status: run.status, pid: run.pid,
  exitCode: run.exit_code, error: run.error, outputPath: run.output_path,
  metrics: run.metrics, checkpoint: run.resource?.checkpoint,
  executionAttemptId: run.config?.execution_attempt?.id,
  command: run.config?.command, createdAt: run.created_at, finishedAt: run.finished_at,
});
async function api(endpoint, method = 'GET', body) {
  const response = await fetch(origin + '/api' + endpoint, {
    method, headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const value = await response.json();
  if (method !== 'GET') {
    record.receipts.push({
      timestamp: new Date().toISOString(), endpoint, method, status: response.status,
      requestId: body?.request_id, expectedRevision: body?.expected_revision,
      result: value.id ? { id: value.id, revision: value.revision, status: value.status } : {
        revision: value.revision ?? value.graph?.revision, requestId: value.request_id,
        nodeCount: value.nodes?.length ?? value.graph?.nodes?.length,
      },
    });
    await persist();
  }
  if (!response.ok) throw new Error(`${method} ${endpoint}: ${response.status} ${JSON.stringify(value)}`);
  return value;
}
async function waitRun(id, expected, seconds = 35) {
  const deadline = Date.now() + seconds * 1000;
  while (Date.now() < deadline) {
    const run = await api('/runs/' + id);
    if (expected.includes(run.status)) return run;
    if (terminal.includes(run.status)) throw new Error(`Run ${id}: ${run.status} ${run.error}`);
    await pause(150);
  }
  throw new Error(`Run ${id} did not reach ${expected}`);
}
async function submit(nodeId, key) {
  const run = await api('/nodes/' + nodeId + '/run', 'POST', { request_id: randomUUID(), scope: 'single' });
  record.runs[key] = { submitted: runSnapshot(run) };
  await persist();
  return run;
}
async function prepare() {
  const project = await api('/projects', 'POST', {
    name: `Actual visual states ${Date.now()}`, mode: 'manual',
    goal: 'Inspect actual local command, numerical integration and debug checkpoint states. This is an operational UI test.',
    budget: { seconds: 180, allow_paid: false }, config: { publication_profile: { id: 'operational' } },
  });
  record.projects.push({ id: project.id, name: project.name, purpose: 'Actual provider-free process and numerical states', retained: true });
  const graph = await api('/projects/' + project.id + '/graph');
  const computation = `import csv,json; from pathlib import Path\nrows=[]\nfor n in (10,100,500,1000,5000):\n h=1.0/n; observed=h*(sum((i*h)**2 for i in range(1,n))+0.5); rows.append({'intervals':n,'integral':observed,'absolute_error':abs(observed-1.0/3.0)})\nwith Path('integration.csv').open('w',newline='') as stream:\n writer=csv.DictWriter(stream,fieldnames=['intervals','integral','absolute_error']); writer.writeheader(); writer.writerows(rows)\nPath('metrics.json').write_text(json.dumps(rows[-1])); print(json.dumps(rows[-1]),flush=True)`;
  const definitions = [
    { key: 'running', title: 'Actual bounded 10-second command', type: 'implementation', command: [python, workload, '--seconds', '10'], kind: 'command' },
    { key: 'failure', title: 'Actual command failure: exit 7', type: 'implementation', command: [python, '-c', "import sys; print('actual failure command: deliberate exit 7',flush=True); print('bounded failure fixture stderr',file=sys.stderr,flush=True); sys.exit(7)"], kind: 'command' },
    { key: 'numerical', title: 'Measured trapezoidal integration of x²', type: 'experiment', command: [python, '-c', computation], kind: 'experiment', experiment_duty: 'effectiveness' },
    { key: 'checkpoint', title: 'Actual debug callback: after_model', type: 'implementation', command: [python, workload, '--seconds', '0.5', '--checkpoint', 'after_model'], kind: 'command' },
  ];
  record.nodes = {};
  const commands = definitions.map((definition, index) => {
    const id = randomUUID(); record.nodes[definition.key] = id;
    return { operation: 'add_node', targets: [], params: {
      id, type: definition.type, title: definition.title,
      instructions: definition.key === 'numerical' ? 'Execute the declared integration algorithm and retain its measured metrics and CSV.' : 'Execute only this declared local operational test command; no model provider is used.',
      position: { x: 100 + index * 300, y: 260 },
      config: { kind: definition.kind, command: definition.command, timeout: 30, metrics_file: 'metrics.json', ...(definition.experiment_duty ? { experiment_duty: definition.experiment_duty } : {}) },
    } };
  });
  await api('/projects/' + project.id + '/graph/batch', 'POST', { request_id: randomUUID(), expected_revision: graph.revision, commands });
  for (const key of ['failure', 'numerical']) {
    const run = await submit(record.nodes[key], key);
    const saved = await waitRun(run.id, [key === 'failure' ? 'failed' : 'completed']);
    record.runs[key].terminal = runSnapshot(saved);
    record.runs[key].output = await api('/runs/' + run.id + '/output');
    record.runs[key].verification = await api('/runs/' + run.id + '/verification');
    if (key === 'numerical') {
      if (saved.metrics.intervals !== 5000 || Math.abs(saved.metrics.integral - (1 / 3 + 1 / (6 * 5000 ** 2))) > 1e-14) throw new Error('Unexpected actual integration result');
      record.runs[key].dataPath = `${saved.output_path}/workspace/integration.csv`;
      record.runs[key].rows = await api('/data/' + run.id + '/rows?path=' + encodeURIComponent(record.runs[key].dataPath));
    }
    console.log(JSON.stringify({ actualRun: key, ...runSnapshot(saved) }));
  }
  const layout = await api('/projects', 'POST', {
    name: `100 saved layout fixtures ${Date.now()}`, mode: 'manual',
    goal: 'Observe graph layout and focused inspection using saved structural fixtures. No execution or scientific finding is represented.',
    budget: { seconds: 60, allow_paid: false }, config: { publication_profile: { id: 'operational' } },
  });
  const layoutGraph = await api('/projects/' + layout.id + '/graph');
  const layoutCommands = Array.from({ length: 100 }, (_, index) => ({ operation: 'add_node', targets: [], params: {
    id: randomUUID(), type: 'idea', title: `Layout fixture ${String(index + 1).padStart(3, '0')}`,
    instructions: 'Saved structural fixture for graph rendering and inspector access. No run, result or scientific assertion.',
    position: { x: (index % 10) * 300, y: Math.floor(index / 10) * 250 + 250 }, config: {},
  } }));
  await api('/projects/' + layout.id + '/graph/batch', 'POST', { request_id: randomUUID(), expected_revision: layoutGraph.revision, commands: layoutCommands });
  const savedLayout = await api('/projects/' + layout.id + '/graph');
  if (savedLayout.nodes.length !== layoutGraph.nodes.length + 100) throw new Error('Saved layout fixture count mismatch');
  record.projects.push({ id: layout.id, name: layout.name, purpose: '100 saved structural layout nodes; no executed scientific result', retained: true, nodeCount: savedLayout.nodes.length, fixtureNodeCount: 100, revision: savedLayout.revision });
  record.focusNodeId = layoutCommands[49].params.id;
  record.status = 'prepared'; await persist();
}

let browser;
const pages = [];
async function capture(state, route, setup = async () => {}, options = {}) {
  const captureOne = async entry => {
    const { page, theme, width } = entry;
    entry.events = { pageErrors: [], consoleMessages: [], httpFailures: [], requestFailures: [] };
    await page.goto(origin + route, { waitUntil: 'domcontentloaded' });
    await setup(page);
    await page.waitForFunction(theme => document.documentElement.dataset.theme === theme, theme);
    await page.evaluate(() => document.fonts.ready);
    const before = options.runId ? runSnapshot(await api('/runs/' + options.runId)) : null;
    if (options.expectedStatus && before?.status !== options.expectedStatus) throw new Error(`${state} changed before screenshot: ${before?.status}`);
    const filename = `${state}-${theme}-${width}.png`;
    await page.screenshot({ path: path.join(out, filename), fullPage: false, animations: 'disabled' });
    const after = options.runId ? runSnapshot(await api('/runs/' + options.runId)) : null;
    if (options.expectedStatus && after?.status !== options.expectedStatus) throw new Error(`${state} changed during screenshot: ${after?.status}`);
    const observation = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth > innerWidth,
      scrollWidth: document.documentElement.scrollWidth, viewportWidth: innerWidth,
      theme: document.documentElement.dataset.theme,
      focusedElement: document.activeElement?.getAttribute('aria-label') || document.activeElement?.tagName,
      renderedGraphNodes: document.querySelectorAll('.react-flow__node').length,
      visibleDialogs: [...document.querySelectorAll('[role=dialog]')].filter(value => value.getClientRects().length).map(value => value.getAttribute('aria-label') || value.getAttribute('aria-labelledby')),
      assetScripts: [...document.querySelectorAll('script[src]')].map(value => new URL(value.src).pathname),
    }));
    if (expectedAsset && !observation.assetScripts.some(value => value.endsWith('/' + expectedAsset))) throw new Error(`Unexpected build asset for ${state}: ${JSON.stringify(observation.assetScripts)}`);
    record.screenshots = record.screenshots.filter(value => value.filename !== `screenshots/states/${filename}`);
    record.screenshots.push({ state, route, theme, width, height: width === 390 ? 844 : 1000, filename: `screenshots/states/${filename}`, observedAt: new Date().toISOString(), before, after, ...observation, ...entry.events });
    await persist();
    console.log(JSON.stringify({ captured: filename, status: after?.status, overflow: observation.overflow }));
  };
  if (options.parallel) await Promise.all(pages.map(captureOne));
  else for (const entry of pages) await captureOne(entry);
}
async function captureStates() {
  const projectId = record.projects[0].id;
  browser = await chromium.launch({ headless: true });
  for (const theme of ['light', 'dark']) for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, reducedMotion: 'reduce' });
    await context.addInitScript(theme => {
      localStorage.setItem('forest-theme', theme); localStorage.setItem('forest-language', 'en');
      localStorage.setItem('forest-sidebar-collapsed', 'false'); localStorage.setItem('forest-reduced-transparency', 'false');
    }, theme);
    const page = await context.newPage();
    const entry = { page, context, theme, width, events: { pageErrors: [], consoleMessages: [], httpFailures: [], requestFailures: [] } };
    page.on('pageerror', error => entry.events.pageErrors.push(error.message));
    page.on('console', message => { if (['error', 'warn'].includes(message.type())) entry.events.consoleMessages.push({ type: message.type(), text: message.text() }); });
    page.on('response', response => { if (response.status() >= 400) entry.events.httpFailures.push({ path: new URL(response.url()).pathname, status: response.status() }); });
    page.on('requestfailed', request => { if (request.failure()?.errorText !== 'net::ERR_ABORTED') entry.events.requestFailures.push({ path: new URL(request.url()).pathname, error: request.failure()?.errorText }); });
    await page.goto(origin + '/projects', { waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { name: 'Projects', exact: true }).waitFor();
    pages.push(entry);
  }
  record.assetScripts = await pages[0].page.locator('script[src]').evaluateAll(nodes => nodes.map(node => new URL(node.src).pathname));
  if (mode !== 'focus') {
  const runRoute = id => `/projects/${projectId}/runs?run=${id}`;
  const expectRun = async (page, runId) => {
    await page.locator(`.run-output-toolbar code[title="${runId}"]`).waitFor();
    await page.locator('.run-list button.active').scrollIntoViewIfNeeded();
  };
  const failure = record.runs.failure.terminal;
  await capture('command-failed', runRoute(failure.id), async page => {
    await expectRun(page, failure.id);
    await page.locator('.run-output > pre').filter({ hasText: 'actual failure command' }).waitFor();
  }, { runId: failure.id, expectedStatus: 'failed' });
  const numerical = record.runs.numerical.terminal;
  await capture('numerical-completed', runRoute(numerical.id), async page => {
    await expectRun(page, numerical.id);
    await page.locator('.run-recorded-metrics summary').click();
    await page.locator('.run-recorded-metrics').filter({ hasText: String(numerical.metrics.integral) }).waitFor();
  }, { runId: numerical.id, expectedStatus: 'completed' });
  await capture('numerical-data', `/projects/${projectId}/data`, async page => {
    await page.locator('.raw-data-browser summary').click();
    await page.locator('.raw-data-browser').getByLabel('Source run').selectOption(numerical.id);
    await page.locator('.raw-data-browser').getByLabel('File path (blank for predictions)').fill(record.runs.numerical.dataPath);
    await page.getByRole('button', { name: 'Load data', exact: true }).click();
    const finalIntegral = String(record.runs.numerical.rows.rows.at(-1).integral);
    const lastValue = page.locator('.raw-data-browser').getByRole('cell', { name: finalIntegral, exact: true });
    await lastValue.waitFor();
    await lastValue.scrollIntoViewIfNeeded();
  }, { runId: numerical.id, expectedStatus: 'completed' });
  if (mode !== 'static') {
  const checkpointRun = await submit(record.nodes.checkpoint, 'checkpoint');
  const stopped = await waitRun(checkpointRun.id, ['waiting_input']);
  if (stopped.resource?.checkpoint?.stage !== 'after_model') throw new Error('Actual stopped debug stage mismatch');
  record.runs.checkpoint.stopped = runSnapshot(stopped); await persist();
  await capture('debug-checkpoint-waiting-input', runRoute(stopped.id), async page => {
    await expectRun(page, stopped.id);
    await page.getByRole('button', { name: 'Run advanced controls', exact: true }).click();
    await page.getByRole('dialog', { name: 'Run controls and diagnostics' }).getByText('Current stopped debug checkpoint', { exact: true }).waitFor();
    await page.getByLabel('Debug checkpoint override (JSON)').scrollIntoViewIfNeeded();
  }, { runId: stopped.id, expectedStatus: 'waiting_input' });
  const editorPage = pages[0].page;
  const override = { text: 'actual visual review callback override', retained_field: { review_fixture: true } };
  await editorPage.getByLabel('Debug checkpoint override (JSON)').fill(JSON.stringify(override));
  const patchPromise = editorPage.waitForResponse(response => response.request().method() === 'PATCH' && new URL(response.url()).pathname === `/api/runs/${stopped.id}/checkpoint`);
  await editorPage.getByRole('button', { name: 'Save checkpoint without resuming', exact: true }).click();
  const patch = await patchPromise;
  record.receipts.push({ timestamp: new Date().toISOString(), endpoint: `/runs/${stopped.id}/checkpoint`, method: 'PATCH', status: patch.status(), source: 'actual browser action', override });
  const saved = await api('/runs/' + stopped.id);
  if (!patch.ok() || saved.status !== 'waiting_input' || JSON.stringify(saved.resource.checkpoint.override) !== JSON.stringify(override)) throw new Error('Checkpoint override was not saved while stopped');
  record.runs.checkpoint.savedOverride = runSnapshot(saved);
  await editorPage.getByRole('dialog', { name: 'Run controls and diagnostics' }).getByRole('button', { name: 'Close', exact: true }).click();
  const resumePromise = editorPage.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === `/api/runs/${stopped.id}/resume`);
  await editorPage.locator('.run-output-toolbar').getByRole('button', { name: 'resume', exact: true }).click();
  const resume = await resumePromise;
  record.receipts.push({ timestamp: new Date().toISOString(), endpoint: `/runs/${stopped.id}/resume`, method: 'POST', status: resume.status(), source: 'explicit actual browser action' });
  const completed = await waitRun(stopped.id, ['completed']);
  if (completed.pid !== stopped.pid || completed.config.execution_attempt.id !== stopped.config.execution_attempt.id || JSON.stringify(completed.metrics.checkpoint_value) !== JSON.stringify(override)) throw new Error('Same-run debug resume identity or measured override mismatch');
  record.runs.checkpoint.completed = runSnapshot(completed); record.runs.checkpoint.output = await api('/runs/' + completed.id + '/output');
  await capture('debug-checkpoint-resumed-completed', runRoute(completed.id), async page => {
    await expectRun(page, completed.id); await page.locator('.run-recorded-metrics summary').click();
    await page.locator('.run-recorded-metrics').filter({ hasText: override.text }).waitFor();
  }, { runId: completed.id, expectedStatus: 'completed' });
  // Four already-warm pages capture one actual bounded 10s run concurrently.
  const active = await submit(record.nodes.running, 'running');
  record.runs.running.running = runSnapshot(await waitRun(active.id, ['running']));
  await capture('command-running', runRoute(active.id), async page => {
    await expectRun(page, active.id); await page.locator('.run-output > pre').filter({ hasText: 'actual_process_tick' }).waitFor();
  }, { runId: active.id, expectedStatus: 'running', parallel: true });
  record.runs.running.completed = runSnapshot(await waitRun(active.id, ['completed']));
  const layoutId = record.projects[1].id;
  await capture('many-node-layout-fixture', `/projects/${layoutId}/workspace`, async page => {
    await page.locator('.canvas-legend').filter({ hasText: String(record.projects[1].nodeCount) }).waitFor();
    await page.locator('.react-flow__node').first().waitFor();
  });
  }
  }
  const layoutId = record.projects[1].id;
  if (mode !== 'static') await capture('selected-node-focus', `/projects/${layoutId}/workspace?node=${record.focusNodeId}&view=focus`, async page => {
    await page.locator('.focus-heading').getByRole('heading', { name: 'Layout fixture 050', exact: true }).waitFor();
  });
  record.status = 'captured'; record.finishedAt = new Date().toISOString();
  record.findings = {
    actualRunIds: Object.fromEntries(Object.entries(record.runs).map(([key, value]) => [key, value.submitted.id])),
    screenshotCount: record.screenshots.length,
    overflow: record.screenshots.filter(value => value.overflow).map(value => value.filename),
    runtimeErrors: record.screenshots.filter(value => value.pageErrors.length || value.consoleMessages.length || value.httpFailures.length || value.requestFailures.length).map(value => ({ filename: value.filename, pageErrors: value.pageErrors, consoleMessages: value.consoleMessages, httpFailures: value.httpFailures, requestFailures: value.requestFailures })),
    pixelReview: 'PENDING',
  };
  await persist();
  await browser.close(); browser = undefined;
}
try {
  if (['capture', 'static', 'focus'].includes(mode)) {
    record = JSON.parse(await fs.readFile(recordPath, 'utf8'));
    if (mode === 'capture') {
      record.screenshots = [];
      record.retainedPriorRuns ||= [];
      for (const key of ['checkpoint', 'running']) {
        const previous = record.runs[key];
        if (previous) record.retainedPriorRuns.push({ key, ...runSnapshot(await api('/runs/' + previous.submitted.id)) });
      }
    }
    delete record.failure;
    record.status = 'prepared';
  }
  else await prepare();
  if (mode !== 'prepare') await captureStates();
} catch (error) {
  record.status = 'failed'; record.failure = { message: error.message, stack: error.stack };
  for (const value of Object.values(record.runs)) {
    const id = value.submitted?.id;
    if (!id) continue;
    try { const run = await api('/runs/' + id); if (!terminal.includes(run.status)) await api('/runs/' + id + '/cancel', 'POST', {}); } catch { /* Report the original capture error and retain fixtures. */ }
  }
  await persist(); if (browser) await browser.close(); throw error;
}
console.log(JSON.stringify({ status: record.status, projectIds: record.projects.map(value => value.id), screenshotCount: record.screenshots.length }));
