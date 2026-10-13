import http from 'node:http';
import fs from 'node:fs/promises';
import { chromium } from '../../../apps/web/node_modules/playwright-core/index.mjs';

const origin = process.env.FOREST_WEB_URL || 'http://127.0.0.1:18340';
const dataDir = process.env.FOREST_DATA_DIR;
if (!dataDir) throw new Error('Provide the explicitly isolated FOREST_DATA_DIR for this observation.');
const out = new URL('../runtime-observations.json', import.meta.url);
const record = { origin, dataDir, evidence: 'Actual Chromium, API and EventSource; controlled transport disconnection', observations: [] };
const browser = await chromium.launch({ headless: true });
const request = async (path, method = 'GET', body) => {
  const response = await fetch(`${origin}/api${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${await response.text()}`);
  return response.json();
};
let project;
let proxy;
try {
  const authContext = await browser.newContext({ extraHTTPHeaders: { 'x-forwarded-for': '192.0.2.10' } });
  const page = await authContext.newPage();
  await page.goto(`${origin}/projects`);
  const dialog = page.getByRole('dialog', { name: 'Sign in', exact: true });
  await dialog.waitFor({ state: 'visible' });
  const ownerToken = (await fs.readFile(`${dataDir}/owner-token`, 'utf8')).trim();
  await dialog.getByLabel('Owner token').fill(ownerToken);
  const loginResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/auth/login');
  await dialog.getByRole('button', { name: 'Connect workspace', exact: true }).click();
  const login = await loginResponse;
  if (login.status() !== 200) throw new Error('Actual owner login failed.');
  await dialog.waitFor({ state: 'hidden' });
  await page.getByRole('heading', { name: 'Projects', exact: true }).waitFor();
  const authenticated = await page.evaluate(async () => (await fetch('/api/auth/status')).json());
  const retained = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
  if (!authenticated.authenticated || retained.includes(ownerToken) || page.url().includes(ownerToken)) throw new Error('Login/storage contract failed.');
  record.observations.push({ test: 'Actual owner authentication after real401', result: 'PASS', status: login.status(), authenticated: true, tokenInURLOrWebStorage: false });
  await authContext.close();

  project = await request('/projects', 'POST', { name: 'Actual SSE reconnection observation', goal: 'Inspect runtime freshness without a model', mode: 'manual', budget: { allow_paid: false, seconds: 120 } });
  const streams = new Set();
  let connections = 0;
  const base = new URL(origin);
  proxy = http.createServer((incoming, outgoing) => {
    const upstream = http.request({ hostname: base.hostname, port: base.port, path: incoming.url, method: incoming.method, headers: { ...incoming.headers, host: base.host } }, response => {
      outgoing.writeHead(response.statusCode, response.headers);
      if (incoming.url.startsWith(`/api/projects/${project.id}/events`)) {
        connections += 1;
        streams.add(outgoing);
        outgoing.on('close', () => { streams.delete(outgoing); response.destroy(); upstream.destroy(); });
      }
      response.pipe(outgoing);
    });
    upstream.on('error', () => { if (!outgoing.headersSent) outgoing.writeHead(502); outgoing.end(); });
    incoming.pipe(upstream);
  });
  await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve));
  const proxyOrigin = `http://127.0.0.1:${proxy.address().port}`;
  const context = await browser.newContext();
  await context.addInitScript(() => {
    const Native = window.EventSource;
    window.__forestObservedSSE = { opens: 0, errors: 0 };
    window.EventSource = class extends Native {
      constructor(...args) {
        super(...args);
        this.addEventListener('open', () => { window.__forestObservedSSE.opens += 1; });
        this.addEventListener('error', () => { window.__forestObservedSSE.errors += 1; });
      }
    };
  });
  const live = await context.newPage();
  await live.goto(`${proxyOrigin}/projects/${project.id}/overview`);
  await live.waitForFunction(() => window.__forestObservedSSE.opens >= 1);
  await live.getByLabel('Budget configuration').fill('{"allow_paid":false,"seconds":999}');
  for (const stream of streams) stream.end();
  const current = await request(`/projects/${project.id}`);
  const updated = await request(`/projects/${project.id}`, 'PATCH', { expected_revision: current.revision, name: 'Actual state after SSE disconnect' });
  await live.waitForFunction(() => window.__forestObservedSSE.opens >= 2, undefined, { timeout: 15000 });
  await live.getByRole('heading', { name: updated.name, exact: true }).waitFor();
  const text = await live.getByLabel('Budget configuration').inputValue();
  if (text !== '{"allow_paid":false,"seconds":999}') throw new Error('Reconnect overwrote dirty budget.');
  const state = await live.evaluate(() => window.__forestObservedSSE);
  record.observations.push({ test: 'Actual EventSource reconnect refetches authoritative state and preserves dirty budget', result: 'PASS', projectId: project.id, revision: updated.revision, connections, ...state, dirtyBudget: text });
  await context.close();
} finally {
  await browser.close();
  if (project) await request(`/projects/${project.id}`, 'DELETE');
  if (proxy) { proxy.closeAllConnections(); await new Promise(resolve => proxy.close(resolve)); }
}
await fs.writeFile(out, JSON.stringify(record, null, 2));
console.log(JSON.stringify({ observations: record.observations }));
