import { chromium, request, expect } from '../../../apps/web/node_modules/@playwright/test/index.mjs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const origin = process.env.FOREST_WEB_URL || 'http://127.0.0.1:18340';
const build = process.env.FOREST_BUILD_ID;
const expectedAsset = process.env.FOREST_EXPECTED_ASSET;
const expectedCss = process.env.FOREST_EXPECTED_CSS;
if (!build || !expectedAsset || !expectedCss) throw new Error('Provide exact stable build and asset identity.');
const out = process.env.FOREST_ANALYSIS_OUTPUT || fileURLToPath(new URL(`../screenshots/analysis-${build}`, import.meta.url));
await mkdir(out, { recursive: true });
const smoke = JSON.parse(await readFile(new URL('../evidence/analysis-worker-smoke.json', import.meta.url), 'utf8'));
const api = await request.newContext({ baseURL: origin });
const savedGraph = await (await api.get(`/api/projects/${smoke.project_id}/graph`)).json();
const historyResponse = await api.get(`/api/projects/${smoke.project_id}/research/analysis`);
expect(historyResponse.ok(), await historyResponse.text()).toBeTruthy();
const savedHistory = await historyResponse.json();
const analysisRuns = savedHistory.runs;
const projectRun = analysisRuns.find(run => run.scope === 'project');
const nodeRun = analysisRuns.find(run => run.scope === 'node');
const branchRun = analysisRuns.find(run => run.scope === 'branch');
expect(projectRun && nodeRun && branchRun).toBeTruthy();
const savedResult = await (await api.get(`/api/runs/${projectRun.id}/research-analysis`)).json();
expect(savedResult.report.summary).toContain('PROTOCOL FIXTURE');
const created = await api.post('/api/projects', { data: { name: `Analysis empty visual fixture ${Date.now()}`, mode: 'manual', goal: 'Saved UI fixture only; no experiment or model is submitted.', budget: { seconds: 120, allow_paid: false }, config: { publication_profile: { id: 'operational' } } } });
expect(created.ok(), await created.text()).toBeTruthy();
const emptyProject = await created.json();
const browser = await chromium.launch({ headless: true });
const report = { build, expected_asset: expectedAsset, expected_css: expectedCss, saved_project_id: smoke.project_id, empty_project_id: emptyProject.id, retained_report_current: savedResult.current, retained_report_stale_reasons: savedResult.stale_reasons, model_submissions: [], observations: [], limits: ['Saved reports use real API/worker and authored local provider protocol responses; live model judgment quality is NOT RUN.', 'Pending/error screenshots use explicitly labelled browser-only status views of one saved completed run; server data is unchanged.'] };
const targets = '.reading-panel-tabs .tabs button, .research-analysis-panel h2, .research-analysis-panel h3, .research-analysis-panel h4, .research-analysis-panel strong, .research-analysis-panel p, .research-analysis-panel summary, .research-analysis-panel code, .research-analysis-panel a, .research-analysis-panel .field > span, .research-analysis-panel .button:not(:disabled), .research-analysis-panel .badge, .analysis-history button:not(.button) > span, .analysis-history small';
const capture = async (page, state, theme, width, source) => {
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await page.evaluate(() => document.fonts.ready);
  const values = await page.evaluate(selector => {
    const rgba = color => { const v = color.match(/[\d.]+/g)?.map(Number) || [0, 0, 0, 0]; return [v[0], v[1], v[2], v[3] ?? 1]; };
    const luminance = rgb => rgb.map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
    const panel = document.querySelector('.workspace-reading-panel');
    const bounds = panel.getBoundingClientRect();
    const items = [...document.querySelectorAll(selector)].filter(el => {
      const box = el.getBoundingClientRect();
      return el.getClientRects().length && box.bottom > bounds.top && box.top < bounds.bottom && el.textContent?.trim() && getComputedStyle(el).visibility !== 'hidden';
    }).map(el => {
      const layers = []; for (let parent = el; parent; parent = parent.parentElement) layers.push(rgba(getComputedStyle(parent).backgroundColor));
      let background = [255, 255, 255]; for (const [r, g, b, alpha] of layers.reverse()) background = [r, g, b].map((v, i) => v * alpha + background[i] * (1 - alpha));
      const style = getComputedStyle(el), a = luminance(rgba(style.color).slice(0, 3)), b = luminance(background);
      return { text: el.textContent?.trim().slice(0, 160), color: style.color, background, fontSize: style.fontSize, contrast: (Math.max(a, b) + .05) / (Math.min(a, b) + .05) };
    });
    return { items, documentWidth: document.documentElement.scrollWidth, viewportWidth: innerWidth, panelWidth: bounds.width, panelScrollWidth: panel.scrollWidth, graphMounted: !!document.querySelector('.react-flow'), staleNotice: document.querySelector('.analysis-stale')?.textContent?.trim() ?? null, scripts: [...document.querySelectorAll('script[src]')].map(el => new URL(el.src).pathname), styles: [...document.querySelectorAll('link[rel=stylesheet]')].map(el => new URL(el.href).pathname) };
  }, targets);
  expect(values.scripts.some(value => value.endsWith('/' + expectedAsset))).toBeTruthy();
  expect(values.styles.some(value => value.endsWith('/' + expectedCss))).toBeTruthy();
  expect(values.documentWidth).toBe(width);
  expect(values.panelScrollWidth).toBeLessThanOrEqual(values.panelWidth + 1);
  expect(values.graphMounted).toBeTruthy();
  for (const item of values.items) expect(item.contrast, `${state} ${theme} ${width}: ${item.text}`).toBeGreaterThanOrEqual(4.5);
  const filename = `${state}-${theme}-${width}.png`;
  await page.screenshot({ path: `${out}/${filename}`, animations: 'disabled' });
  report.observations.push({ state, theme, width, filename, source, ...values });
};
try {
  for (const theme of ['light', 'dark']) for (const width of [390, 768, 1440]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 }, reducedMotion: 'reduce' });
    await context.addInitScript(theme => { localStorage.setItem('forest-language', 'en'); localStorage.setItem('forest-theme', theme); localStorage.setItem('forest-sidebar-collapsed', 'false'); }, theme);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', req => { if (req.method() === 'POST' && /research\/analysis(?:$|\?)/.test(req.url())) report.model_submissions.push(req.url()); });
    await page.goto(`${origin}/projects/${emptyProject.id}/workspace?analysis=1`);
    await expect(page.locator('.analysis-request-form')).toBeVisible();
    await capture(page, 'actual-empty', theme, width, 'Actual saved independent project; no model submission.');
    for (const run of [projectRun, nodeRun, branchRun]) {
      const query = new URLSearchParams({ analysis: '1', analysis_scope: run.scope });
      if (run.node_id) query.set('node', run.node_id);
      if (run.branch_id) query.set('branch', run.branch_id);
      await page.goto(`${origin}/projects/${smoke.project_id}/workspace?${query}`);
      await expect(page.locator('.analysis-report .analysis-summary')).toContainText('PROTOCOL FIXTURE');
      await capture(page, `actual-${run.scope}-context`, theme, width, 'Actual saved context and stored protocol-fixture report after provider retirement; preserve returned current/stale state, no new request.');
      if (run.scope === 'project') {
        await page.locator('.analysis-report').evaluate(el => el.scrollIntoView({ block: 'start' }));
        await capture(page, 'saved-report-protocol-fixture', theme, width, 'Historical API/worker report from an authored local provider response, with actual context-change notice after provider retirement; live model quality NOT RUN.');
        await page.locator('.analysis-citation-list').evaluate(el => el.scrollIntoView({ block: 'start' }));
        await page.locator('.analysis-history > summary').click();
        await capture(page, 'saved-citations-history', theme, width, 'Actual retained Crossref metadata, citation IDs and analysis history. No citation import performed.');
      }
    }
    for (const state of ['pending', 'error']) {
      const status = state === 'pending' ? 'running' : 'failed';
      await page.route(`**/api/projects/${smoke.project_id}/research/analysis`, route => route.fulfill({ json: { ...savedHistory, runs: savedHistory.runs.map(run => run.id === projectRun.id ? { ...run, status, question: `LAYOUT FIXTURE ONLY: ${state} display; server run remains completed.` } : run) } }));
      await page.route(`**/api/runs/${projectRun.id}/research-analysis`, route => route.fulfill({ json: { ...savedResult, status, report: null, error: state === 'error' ? 'LAYOUT FIXTURE ONLY: provider transport failure view. No analysis was submitted.' : undefined } }));
      await page.goto(`${origin}/projects/${smoke.project_id}/workspace?analysis=1`);
      await expect(page.locator('.analysis-run-receipt')).toContainText('LAYOUT FIXTURE ONLY');
      await page.locator('.analysis-run-receipt').evaluate(el => el.scrollIntoView({ block: 'start' }));
      await capture(page, `${state}-layout-fixture`, theme, width, 'Browser-only explicitly labelled status view; original completed server run unchanged.');
      await page.unroute(`**/api/projects/${smoke.project_id}/research/analysis`);
      await page.unroute(`**/api/runs/${projectRun.id}/research-analysis`);
    }
    expect(errors).toEqual([]);
    report.observations.filter(row => row.theme === theme && row.width === width).forEach(row => { row.pageErrors = [...errors]; });
    await context.close();
  }
  const finalGraph = await (await api.get(`/api/projects/${smoke.project_id}/graph`)).json();
  expect(finalGraph.revision).toBe(savedGraph.revision);
  expect((await (await api.get(`/api/runs/${projectRun.id}`)).json()).status).toBe('completed');
  expect(report.model_submissions).toEqual([]);
} finally {
  await writeFile(`${out}/observations.json`, JSON.stringify(report, null, 2));
  await browser.close();
  await api.delete(`/api/projects/${emptyProject.id}`);
  await api.dispose();
}
console.log(JSON.stringify({ build, captures: report.observations.length, model_submissions: report.model_submissions.length, minimum_contrast: Math.min(...report.observations.flatMap(row => row.items.map(item => item.contrast))) }));
