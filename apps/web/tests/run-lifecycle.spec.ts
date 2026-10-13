import { expect, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";
import { resolve } from "node:path";

const workload = resolve(process.cwd(), "../../scripts/test_worker_case.py");
const runtimePython = process.env.FOREST_WORKER_PYTHON || "python3";
const terminal = ["completed", "failed", "cancelled", "interrupted", "skipped"];

async function createProject(request: APIRequestContext, name: string) {
  const response = await request.post("/api/projects", {
    data: {
      name: `${name} ${Date.now()}`,
      goal: "Validate isolated actual process controls without a model provider.",
      mode: "manual",
      budget: { seconds: 120, allow_paid: false },
      config: { publication_profile: { id: "operational" } },
    },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).id as string;
}
async function enqueue(
  request: APIRequestContext,
  projectId: string,
  title: string,
  seconds: number,
  checkpoint = false,
) {
  const graph = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  const response = await request.post(
    `/api/projects/${projectId}/graph/commands`,
    {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_node",
        params: {
          type: "implementation",
          title,
          position: { x: 120 + graph.nodes.length * 300, y: 120 },
          config: {
            kind: "command",
            timeout: seconds + 20,
            command: [
              runtimePython,
              workload,
              "--seconds",
              String(seconds),
              ...(checkpoint ? ["--checkpoint", "after_model"] : []),
            ],
          },
        },
      },
    },
  );
  expect(response.ok(), await response.text()).toBeTruthy();
  const updated = await response.json();
  const node = updated.graph.nodes.find(
    (item: { title: string }) => item.title === title,
  );
  const submitted = await request.post(`/api/nodes/${node.id}/run`, {
    data: { request_id: crypto.randomUUID(), scope: "single" },
  });
  expect(submitted.ok(), await submitted.text()).toBeTruthy();
  return submitted.json();
}
async function readRun(request: APIRequestContext, id: string) {
  const response = await request.get(`/api/runs/${id}`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function cleanup(
  request: APIRequestContext,
  projectId: string,
  runIds: string[],
) {
  for (const id of runIds) {
    const run = await readRun(request, id);
    if (!terminal.includes(run.status)) {
      expect(
        (await request.post(`/api/runs/${id}/cancel`, { data: {} })).ok(),
      ).toBeTruthy();
      await expect
        .poll(async () => (await readRun(request, id)).status)
        .toBe("cancelled");
    }
  }
  expect(
    (await request.delete(`/api/projects/${projectId}`)).ok(),
  ).toBeTruthy();
}

test("actual stopped debug checkpoint saves an override separately from same-run Resume", async ({
  page,
  request,
}) => {
  test.setTimeout(45000);
  const projectId = await createProject(
    request,
    "Actual browser debug checkpoint",
  );
  const runIds: string[] = [];
  try {
    const run = await enqueue(
      request,
      projectId,
      "Provider-free debug callback",
      0.5,
      true,
    );
    runIds.push(run.id);
    await expect
      .poll(
        async () => {
          const saved = await readRun(request, run.id);
          return (
            saved.status === "waiting_input" &&
            saved.resource?.checkpoint?.stage
          );
        },
        { timeout: 30000 },
      )
      .toBe("after_model");
    const stopped = await readRun(request, run.id);
    expect(stopped.resource.checkpoint.payload).toEqual({
      text: "original test callback payload",
    });
    await page.goto(`/projects/${projectId}/runs?run=${run.id}`);
    await page
      .getByRole("button", { name: "Run advanced controls", exact: true })
      .click();
    const dialog = page.getByRole("dialog", {
      name: "Run controls and diagnostics",
    });
    await expect(
      dialog.getByText("Current stopped debug checkpoint", { exact: true }),
    ).toBeVisible();
    await expect(dialog).toContainText("after_model");
    await expect(dialog).toContainText("original test callback payload");
    const editor = dialog.getByLabel("Debug checkpoint override (JSON)");
    await expect
      .poll(async () => JSON.parse(await editor.inputValue()))
      .toEqual(stopped.resource.checkpoint.payload);
    const override = {
      text: "actual browser edited callback payload",
      unknown_callback_field: { retained: true },
    };
    await editor.fill(JSON.stringify(override));
    const resumeRequests: string[] = [];
    page.on("request", (value) => {
      if (
        value.method() === "POST" &&
        new URL(value.url()).pathname === `/api/runs/${run.id}/resume`
      )
        resumeRequests.push(value.url());
    });
    const patch = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" &&
        new URL(response.url()).pathname === `/api/runs/${run.id}/checkpoint`,
    );
    await dialog
      .getByRole("button", {
        name: "Save checkpoint without resuming",
        exact: true,
      })
      .click();
    expect((await patch).ok()).toBeTruthy();
    const savedOverride = await readRun(request, run.id);
    expect(savedOverride.status).toBe("waiting_input");
    expect(savedOverride.resource.checkpoint.payload).toEqual(
      stopped.resource.checkpoint.payload,
    );
    expect(savedOverride.resource.checkpoint.override).toEqual(override);
    expect(resumeRequests).toEqual([]);
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await page
      .locator(".run-output-toolbar")
      .getByRole("button", { name: "resume", exact: true })
      .click();
    await expect
      .poll(async () => (await readRun(request, run.id)).status, {
        timeout: 20000,
      })
      .toBe("completed");
    const completed = await readRun(request, run.id);
    expect(completed.id).toBe(stopped.id);
    expect(completed.metrics.checkpoint_value).toEqual(override);
    expect(completed.metrics.ticks).toBeGreaterThan(0);
    const records = await (
      await request.get(`/api/projects/${projectId}/runs`)
    ).json();
    expect(records.map((record: { id: string }) => record.id)).toEqual([
      run.id,
    ]);
    const metrics = page.locator(".run-recorded-metrics");
    await metrics.getByText("Recorded metrics", { exact: true }).click();
    await expect(metrics).toContainText(run.id);
    await expect(metrics).toContainText(override.text);
    await expect(metrics).toContainText("unknown_callback_field");
  } finally {
    await cleanup(request, projectId, runIds);
  }
});

test("actual pause and Resume keep the run identity, and exact cancel leaves a neighboring run intact", async ({
  page,
  request,
}) => {
  test.setTimeout(45000);
  const projectId = await createProject(
    request,
    "Actual browser process controls",
  );
  const runIds: string[] = [];
  try {
    const target = await enqueue(
      request,
      projectId,
      "Controlled actual process",
      30,
    );
    runIds.push(target.id);
    const neighbor = await enqueue(
      request,
      projectId,
      "Unaffected actual neighbor",
      6,
    );
    runIds.push(neighbor.id);
    for (const id of runIds)
      await expect
        .poll(async () => (await readRun(request, id)).status, {
          timeout: 30000,
        })
        .toBe("running");
    const running = await readRun(request, target.id);
    await page.goto(`/projects/${projectId}/runs?run=${target.id}`);
    const toolbar = page.locator(".run-output-toolbar");
    await toolbar.getByRole("button", { name: "pause", exact: true }).click();
    await expect
      .poll(async () => (await readRun(request, target.id)).status)
      .toBe("paused");
    await expect(
      toolbar.getByRole("button", { name: "resume", exact: true }),
    ).toBeEnabled();
    await toolbar.getByRole("button", { name: "resume", exact: true }).click();
    await expect
      .poll(async () => (await readRun(request, target.id)).status)
      .toBe("running");
    const resumed = await readRun(request, target.id);
    expect(resumed.id).toBe(running.id);
    expect(resumed.pid).toBe(running.pid);
    expect(resumed.config.execution_attempt.id).toBe(
      running.config.execution_attempt.id,
    );
    await toolbar.getByRole("button", { name: "cancel", exact: true }).click();
    await expect
      .poll(async () => (await readRun(request, target.id)).status)
      .toBe("cancelled");
    expect(["running", "completed"]).toContain(
      (await readRun(request, neighbor.id)).status,
    );
    await expect
      .poll(async () => (await readRun(request, neighbor.id)).status, {
        timeout: 20000,
      })
      .toBe("completed");
    expect((await readRun(request, neighbor.id)).metrics.ticks).toBeGreaterThan(
      0,
    );
    const records = await (
      await request.get(`/api/projects/${projectId}/runs`)
    ).json();
    expect(records.map((record: { id: string }) => record.id).sort()).toEqual(
      runIds.slice().sort(),
    );
    const neighborOutput = await (
      await request.get(`/api/runs/${neighbor.id}/output`)
    ).json();
    expect(neighborOutput.text).toContain("actual_process_finished");
  } finally {
    await cleanup(request, projectId, runIds);
  }
});
