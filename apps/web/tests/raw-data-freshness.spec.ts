import { expect, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

function latch() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

async function createProject(request: APIRequestContext) {
  const created = await request.post("/api/projects", {
    data: {
      name: `Raw data scope ${Date.now()}`,
      mode: "manual",
      goal: "Inspect exact source rows",
    },
  });
  expect(created.ok()).toBeTruthy();
  return created.json();
}

async function fixtureRuns(page: Page, projectId: string) {
  // Explicit response fixtures exercise browser request ordering, not real measurements.
  await page.route(`**/api/projects/${projectId}/runs*`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(
        ["raw-run-a", "raw-run-b"].map((id) => ({
          id,
          project_id: projectId,
          kind: "experiment",
          status: "completed",
          metrics: {},
          config: {},
          created_at: "2026-10-13T00:00:00Z",
          output_path: `runs/${id}`,
        })),
      ),
    }),
  );
}

const rows = (value: string) => ({
  columns: ["source"],
  rows: [{ source: value }],
  total: 1,
  origin: "fixture.csv",
});
const settleRendering = (page: Page) =>
  page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      }),
  );

test("delayed raw rows cannot replace a later A to B to A run selection", async ({
  page,
  request,
}) => {
  const project = await createProject(request);
  const old = latch(),
    completed = latch();
  let calls = 0;
  try {
    await fixtureRuns(page, project.id);
    await page.route("**/api/data/raw-run-a/rows*", async (route) => {
      calls++;
      if (calls === 1) {
        await old.promise;
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify(rows("OLD RUN RESPONSE")),
        });
        completed.release();
      } else
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify(rows("CURRENT RUN RESPONSE")),
        });
    });
    await page.goto(`/projects/${project.id}/data`);
    await page.getByText("Raw predictions & tables", { exact: true }).click();
    const controls = page.locator(".raw-data-browser");
    const started = page.waitForRequest((req) =>
      req.url().includes("/data/raw-run-a/rows"),
    );
    await controls
      .getByRole("button", { name: "Load data", exact: true })
      .click();
    await started;
    await controls.getByLabel("Source run").selectOption("raw-run-b");
    await controls.getByLabel("Source run").selectOption("raw-run-a");
    await controls
      .getByRole("button", { name: "Load data", exact: true })
      .click();
    await expect(
      controls.getByRole("cell", { name: "CURRENT RUN RESPONSE", exact: true }),
    ).toBeVisible();
    old.release();
    await completed.promise;
    await settleRendering(page);
    await expect(
      controls.getByRole("cell", { name: "CURRENT RUN RESPONSE", exact: true }),
    ).toBeVisible();
    await expect(
      controls.getByText("OLD RUN RESPONSE", { exact: true }),
    ).toHaveCount(0);
  } finally {
    old.release();
    await request.delete(`/api/projects/${project.id}`);
  }
});

test("a stale path failure cannot clear the current pending read or show an obsolete error", async ({
  page,
  request,
}) => {
  const project = await createProject(request);
  const old = latch(),
    current = latch(),
    oldCompleted = latch();
  let calls = 0;
  try {
    await fixtureRuns(page, project.id);
    await page.route("**/api/data/raw-run-a/rows*", async (route) => {
      calls++;
      if (calls === 1) {
        await old.promise;
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "OBSOLETE PATH FAILURE" }),
        });
        oldCompleted.release();
      } else {
        await current.promise;
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify(rows("CURRENT PATH RESPONSE")),
        });
      }
    });
    await page.goto(`/projects/${project.id}/data`);
    await page.getByText("Raw predictions & tables", { exact: true }).click();
    const controls = page.locator(".raw-data-browser");
    const path = controls.getByLabel("File path (blank for predictions)");
    const load = controls.getByRole("button", {
      name: "Load data",
      exact: true,
    });
    await path.fill("a.csv");
    const firstStarted = page.waitForRequest((req) =>
      req.url().includes("/data/raw-run-a/rows"),
    );
    await load.click();
    await firstStarted;
    await path.fill("b.csv");
    await path.fill("a.csv");
    const secondStarted = page.waitForRequest((req) =>
      req.url().includes("/data/raw-run-a/rows"),
    );
    await load.click();
    await secondStarted;
    await expect(load).toBeDisabled();
    old.release();
    await oldCompleted.promise;
    await settleRendering(page);
    await expect(load).toBeDisabled();
    await expect(
      controls.getByText("OBSOLETE PATH FAILURE", { exact: true }),
    ).toHaveCount(0);
    await expect(path).toHaveValue("a.csv");
    current.release();
    await expect(
      controls.getByRole("cell", {
        name: "CURRENT PATH RESPONSE",
        exact: true,
      }),
    ).toBeVisible();
    await expect(load).toBeEnabled();
  } finally {
    old.release();
    current.release();
    await request.delete(`/api/projects/${project.id}`);
  }
});
