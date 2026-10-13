import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("actual run titles, textual states, output and measured table values remain readable at compact widths in both themes", async ({ browser, request }, info) => {
  test.setTimeout(90000);
  const sample = JSON.parse(await readFile(new URL("../../../examples/cli/node.json", import.meta.url), "utf8"));
  const created = await request.post("/api/projects", { data: { name: `Populated readability ${Date.now()}`, mode: "manual", budget: { seconds: 180, allow_paid: false }, config: { publication_profile: { id: "operational" } } } });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  const graphPath = `/api/projects/${project.id}/graph`;
  const add = async (title: string, config: any) => {
    const graph = await (await request.get(graphPath)).json();
    const response = await request.post(`${graphPath}/commands`, { data: { request_id: crypto.randomUUID(), expected_revision: graph.revision, operation: "add_node", targets: [], params: { type: config.kind === "experiment" ? "experiment" : "implementation", title, position: { x: 100, y: 100 }, config }, run: false } });
    expect(response.ok()).toBeTruthy();
    return (await response.json()).graph.nodes.find((node: { title: string }) => node.title === title);
  };
  const readings: unknown[] = [];
  try {
    const measured = await add("Actual trapezoidal integration of x squared with retained measurements", { ...sample.config, timeout: 30 });
    const failed = await add("Actual command failure with retained output", { kind: "command", timeout: 30, command: ["python3", "-c", "print('deliberate readability fixture failure',flush=True);raise SystemExit(7)"] });
    const launch = async (nodeId: string) => {
      const response = await request.post(`/api/nodes/${nodeId}/run`, { data: { request_id: crypto.randomUUID() } });
      expect(response.ok(), await response.text()).toBeTruthy();
      return (await response.json()).id;
    };
    const measuredId = await launch(measured.id), failedId = await launch(failed.id);
    for (const [id, status] of [[measuredId, "completed"], [failedId, "failed"]]) await expect.poll(async () => (await (await request.get(`/api/runs/${id}`)).json()).status, { timeout: 45000 }).toBe(status);
    const run = await (await request.get(`/api/runs/${measuredId}`)).json();
    expect(run.metrics.intervals).toBe(5000);
    const contrast = async (page: any, selector: string) => {
      const values = await page.locator(selector).evaluateAll((elements: Element[]) => elements.filter(el => el.getClientRects().length && el.textContent?.trim()).map(el => {
        const rgba = (value: string) => { const v = value.match(/[\d.]+/g)?.map(Number) || [0, 0, 0, 0]; return [v[0], v[1], v[2], v[3] ?? 1]; };
        const layers = []; for (let parent: Element | null = el; parent; parent = parent.parentElement) layers.push(rgba(getComputedStyle(parent).backgroundColor));
        let background = [255, 255, 255]; for (const [r, g, b, alpha] of layers.reverse()) background = [r, g, b].map((v, i) => v * alpha + background[i] * (1 - alpha));
        const luminance = (rgb: number[]) => rgb.map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
        const a = luminance(rgba(getComputedStyle(el).color).slice(0, 3)), b = luminance(background);
        return { text: el.textContent?.trim().slice(0, 80), ratio: (Math.max(a, b) + .05) / (Math.min(a, b) + .05) };
      }));
      expect(values.length).toBeGreaterThan(0);
      for (const value of values) expect(value.ratio, value.text).toBeGreaterThanOrEqual(4.5);
      return values;
    };
    for (const theme of ["light", "dark"]) {
      const context = await browser.newContext({ baseURL: process.env.FOREST_WEB_URL || "http://127.0.0.1:18340", reducedMotion: "reduce" });
      await context.addInitScript(value => localStorage.setItem("forest-theme", value), theme);
      const page = await context.newPage(); const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
      try {
        for (const width of [390, 768, 1440]) {
          await page.setViewportSize({ width, height: 1000 });
          await page.goto(`/projects/${project.id}/runs?run=${measuredId}`);
          await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
          await expect(page.locator(".run-output pre").first()).toContainText('"intervals": 5000');
          for (const [node, status] of [[measured, "completed"], [failed, "failed"]] as const) {
            const row = page.locator(".run-list > button").filter({ hasText: node.title });
            const badge = row.locator(`.badge.status-${status}`);
            await expect(badge).toBeVisible(); await badge.scrollIntoViewIfNeeded();
            const dimensions = await row.evaluate((el: Element) => { const row = el.getBoundingClientRect(), list = el.parentElement!.getBoundingClientRect(), badge = el.querySelector(".badge")!.getBoundingClientRect(), title = el.children[1].getBoundingClientRect(); return { rowTop: row.top, rowBottom: row.bottom, listTop: list.top, listBottom: list.bottom, badgeTop: badge.top, badgeBottom: badge.bottom, titleTop: title.top, titleBottom: title.bottom }; });
            expect(dimensions.badgeTop).toBeGreaterThanOrEqual(dimensions.listTop - 1); expect(dimensions.badgeBottom).toBeLessThanOrEqual(dimensions.listBottom + 1);
            expect(dimensions.titleTop).toBeGreaterThanOrEqual(dimensions.rowTop - 1); expect(dimensions.titleBottom).toBeLessThanOrEqual(dimensions.rowBottom + 1);
          }
          readings.push({ theme, width, surface: "output", values: await contrast(page, ".run-output pre") });
          await page.goto(`/projects/${project.id}/data`);
          await expect(page.locator(".table-surface tbody tr").filter({ hasText: measuredId.slice(0, 8) }).locator("td.numeric")).toHaveCount(3);
          readings.push({ theme, width, surface: "measured table", values: await contrast(page, `.table-surface tbody tr:has(input[aria-label="Select ${measuredId}"]) td.numeric`) });
          readings.push({ theme, width, surface: "measured table headers", values: await contrast(page, ".table-surface th") });
          expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
        }
        expect(errors).toEqual([]);
      } finally { await context.close(); }
    }
    await info.attach("actual-populated-readability", { body: JSON.stringify({ projectId: project.id, measuredId, failedId, metrics: run.metrics, readings }, null, 2), contentType: "application/json" });
  } finally { await request.delete(`/api/projects/${project.id}`); }
});
