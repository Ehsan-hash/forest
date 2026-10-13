import { chromium, request, expect } from '../../../apps/web/node_modules/@playwright/test/index.mjs';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';

const origin = process.env.FOREST_WEB_URL || 'http://127.0.0.1:18340';
const expectedAsset = process.env.FOREST_EXPECTED_ASSET;
const expectedCss = process.env.FOREST_EXPECTED_CSS;
const out = process.env.FOREST_READABILITY_OUTPUT || fileURLToPath(new URL('../screenshots/after-readability-final', import.meta.url));
await mkdir(out, { recursive: true });
const api = await request.newContext({ baseURL: origin });
const created = await api.post('/api/projects', { data: {
  name: `Readability fixture ${Date.now()}`,
  goal: 'Visual fixture only; no scientific execution or model request.',
  mode: 'manual', budget: { seconds: 120, allow_paid: false },
} });
expect(created.ok(), await created.text()).toBeTruthy();
const project = await created.json();
const graph = async () => (await api.get(`/api/projects/${project.id}/graph`)).json();
const command = async (operation, targets, params) => {
  const current = await graph();
  const result = await api.post(`/api/projects/${project.id}/graph/commands`, { data: {
    request_id: randomUUID(), expected_revision: current.revision,
    operation, targets, params, run: false,
  } });
  expect(result.ok(), await result.text()).toBeTruthy();
  return result.json();
};
await command('add_node', [], { type: 'goal', title: 'Readability fixture Goal', position: { x: 120, y: 150 } });
const beforeFork = await graph();
await command('fork_branch', [beforeFork.nodes[0].id], { name: 'Readability fixture second path' });
const current = await graph();
const file = await api.put(`/api/projects/${project.id}/file`, { data: {
  path: `${current.branches[0].workspace}/readability-fixture.txt`,
  content: 'Visual fixture only.\n', expected_revision: 0,
} });
expect(file.ok(), await file.text()).toBeTruthy();
const idea = await api.post('/api/ideas', { data: {
  project_id: project.id, title: 'Manual visual fixture',
  data: { hypothesis: 'Visual fixture wording only; no research finding.', mechanism: 'Inspect the actual label and body colors.' },
} });
expect(idea.ok(), await idea.text()).toBeTruthy();

const browser = await chromium.launch({ headless: true });
const report = { build: process.env.FOREST_BUILD_ID || '37500', expected_asset: expectedAsset, expected_css: expectedCss, project_id: project.id, fixture: true, model_requests: 0, observations: [], actions: [] };
const selectors = {
  projects: ['.project-description', '.project-card-bottom > span', '.new-project-card strong', '.local-label', '.sidebar-collapse'],
  library: ['.literature-search select'],
  ideas: ['.idea-prompt h3', '.idea-field > span', '.idea-field p'],
  runs: ['.dock-empty strong', '.dock-empty > .subtle-icon'],
  files: ['.file-tree button > span'],
  settings: ['.connection-card p', '.connection-card code', '.local-label'],
};
try {
  for (const theme of ['light', 'dark']) for (const width of [390, 768, 1440]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 }, reducedMotion: 'reduce' });
    await context.addInitScript(value => {
      localStorage.setItem('forest-language', 'en');
      localStorage.setItem('forest-theme', value);
      localStorage.setItem('forest-sidebar-collapsed', 'false');
    }, theme);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    for (const [route, targets] of Object.entries(selectors)) {
      const path = ['projects', 'settings'].includes(route) ? `/${route}` : `/projects/${project.id}/${route}`;
      await page.goto(origin + path);
      await page.waitForLoadState('networkidle');
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      if (expectedAsset) expect(await page.locator('script[src]').evaluateAll((els, expected) => els.some(el => new URL(el.src).pathname.endsWith('/' + expected)), expectedAsset)).toBeTruthy();
      if (expectedCss) expect(await page.locator('link[rel=stylesheet]').evaluateAll((els, expected) => els.some(el => new URL(el.href).pathname.endsWith('/' + expected)), expectedCss)).toBeTruthy();
      const metrics = await page.evaluate(targets => {
        const rgba = color => {
          const values = color.match(/[\d.]+/g)?.map(Number) || [0, 0, 0, 0];
          return [values[0], values[1], values[2], values[3] ?? 1];
        };
        const background = element => {
          const layers = [];
          for (let el = element; el; el = el.parentElement) layers.push(rgba(getComputedStyle(el).backgroundColor));
          let rgb = [255, 255, 255];
          for (const [r, g, b, alpha] of layers.reverse()) rgb = [r, g, b].map((v, i) => v * alpha + rgb[i] * (1 - alpha));
          return rgb;
        };
        const luminance = rgb => rgb.map(v => v / 255).map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4).reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
        const items = targets.flatMap(selector => [...document.querySelectorAll(selector)].filter(el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden').slice(0, 3).map(el => {
          const style = getComputedStyle(el);
          const fg = rgba(style.color).slice(0, 3);
          const bg = background(el);
          const a = luminance(fg), b = luminance(bg);
          return { selector, text: el.textContent?.trim(), color: style.color, background: bg, fontSize: style.fontSize, contrast: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) };
        }));
        return { scrollWidth: document.documentElement.scrollWidth, innerWidth, items };
      }, targets);
      expect(metrics.scrollWidth).toBe(width);
      for (const item of metrics.items) expect(item.contrast, `${theme} ${width} ${route}: ${item.selector}`).toBeGreaterThanOrEqual(4.5);
      if (route === 'ideas') expect(metrics.items.filter(item => item.selector === '.idea-field > span').length).toBeGreaterThan(0);
      if (route === 'files') expect(metrics.items.length).toBeGreaterThan(0);
      await page.screenshot({ path: `${out}/${route}-${theme}-${width}.png`, fullPage: true, animations: 'disabled' });
      report.observations.push({ route, theme, width, ...metrics, errors: [...errors] });
    }
    await page.goto(`${origin}/projects/${project.id}/workspace`);
    await page.waitForLoadState('networkidle');
    const beforeActions = await graph();
    const compare = page.getByRole('button', { name: 'Compare research paths', exact: true });
    await expect(compare).toBeVisible();
    await expect(compare).toBeEnabled();
    await compare.scrollIntoViewIfNeeded();
    await compare.click();
    await expect(page.getByRole('dialog', { name: 'Compare research paths', exact: true })).toBeVisible();
    await page.screenshot({ path: `${out}/compare-${theme}-${width}.png`, animations: 'disabled' });
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    const actions = page.getByRole('button', { name: 'Path actions', exact: true });
    await expect(actions).toBeVisible();
    await actions.click();
    await expect(page.getByRole('dialog', { name: 'Research path actions', exact: true })).toBeVisible();
    await page.screenshot({ path: `${out}/path-actions-${theme}-${width}.png`, animations: 'disabled' });
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    const finalGraph = await graph();
    expect(finalGraph.revision).toBe(beforeActions.revision);
    expect(errors).toEqual([]);
    report.actions.push({ theme, width, compare_visible: true, compare_opened: true, path_actions_visible: true, path_actions_opened: true, graph_revision: finalGraph.revision, errors });
    await context.close();
  }
} finally {
  await writeFile(`${out}/observations.json`, JSON.stringify(report, null, 2));
  await browser.close();
  await api.delete(`/api/projects/${project.id}`);
  await api.dispose();
}
console.log(JSON.stringify({ observations: report.observations.length, actions: report.actions.length, minimum_contrast: Math.min(...report.observations.flatMap(r => r.items.map(item => item.contrast))) }));
