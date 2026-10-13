import { expect, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

async function createProject(request: APIRequestContext, name: string) {
  const response = await request.post("/api/projects", {
    data: {
      name: `${name} ${Date.now()}`,
      goal: "Validate editable graph invariants with retained actual run history.",
      mode: "manual",
      budget: { seconds: 120, allow_paid: false },
      config: { publication_profile: { id: "operational" } },
    },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).id as string;
}
async function graph(request: APIRequestContext, id: string) {
  return (await request.get(`/api/projects/${id}/graph`)).json();
}
async function command(
  request: APIRequestContext,
  id: string,
  operation: string,
  targets: string[],
  params: Record<string, unknown>,
) {
  const current = await graph(request, id);
  return request.post(`/api/projects/${id}/graph/commands`, {
    data: {
      request_id: crypto.randomUUID(),
      expected_revision: current.revision,
      operation,
      targets,
      params,
      run: false,
    },
  });
}
async function addNode(
  request: APIRequestContext,
  id: string,
  title: string,
  x: number,
) {
  const response = await command(request, id, "add_node", [], {
    type: "implementation",
    title,
    position: { x, y: 150 },
    config: {
      kind: "command",
      timeout: 5,
      command: [
        "python3",
        "-c",
        "print('actual retained history output',flush=True)",
      ],
      unknown_source_field: { retained: true },
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()).graph.nodes.find(
    (node: { title: string }) => node.title === title,
  );
}

test("actual explicit and implicit dependency cycles are rejected from path actions without changing graph revision", async ({
  page,
  request,
}) => {
  const id = await createProject(request, "Actual graph cycles");
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    const alpha = await addNode(request, id, "Cycle source", 120);
    const beta = await addNode(request, id, "Cycle consumer", 520);
    const connected = await command(request, id, "add_dependency", [], {
      source: alpha.id,
      target: beta.id,
      relation: "depends_on",
    });
    expect(connected.ok(), await connected.text()).toBeTruthy();
    const before = await graph(request, id);
    await page.goto(`/projects/${id}/workspace`);
    await expect(page.locator(".research-node")).toHaveCount(2);
    await page
      .getByRole("button", { name: "Path actions", exact: true })
      .click();
    const modal = page.getByRole("dialog", {
      name: "Research path actions",
      exact: true,
    });
    const cases = [
      {
        operation: "add_dependency",
        targets: [],
        params: { source: beta.id, target: alpha.id, relation: "depends_on" },
      },
      {
        operation: "edit_node",
        targets: [alpha.id],
        params: {
          inputs: [
            {
              node_id: beta.id,
              path: "retained.csv",
              destination: "retained.csv",
            },
          ],
        },
      },
    ];
    for (const value of cases) {
      await modal
        .getByRole("combobox", { name: "Operation", exact: true })
        .selectOption(value.operation);
      await modal
        .getByRole("listbox", { name: "Target nodes (multiple selection)" })
        .selectOption(value.targets);
      await modal
        .getByRole("textbox", { name: /^Parameters/ })
        .fill(JSON.stringify(value.params));
      const preview = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          new URL(response.url()).pathname ===
            `/api/projects/${id}/graph/preview`,
      );
      await modal
        .getByRole("button", { name: "Preview impact", exact: true })
        .click();
      const rejectedPreview = await preview;
      expect(rejectedPreview.ok()).toBeFalsy();
      expect(await rejectedPreview.json()).toMatchObject({
        detail: { code: "dependency_cycle" },
      });
      await expect(page.getByRole("status")).toContainText(
        "directed acyclic graph",
      );
      await expect(
        page.getByRole("dialog", { name: "Change impact", exact: true }),
      ).toHaveCount(0);
      expect(await graph(request, id)).toEqual(before);
      const rejectedApply = await command(
        request,
        id,
        value.operation,
        value.targets,
        value.params,
      );
      expect(rejectedApply.ok()).toBeFalsy();
      expect(await rejectedApply.json()).toMatchObject({
        detail: { code: "dependency_cycle" },
      });
      expect(await graph(request, id)).toEqual(before);
    }
    await modal.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.reload();
    await expect(page.locator(".research-node")).toHaveCount(2);
    expect(await graph(request, id)).toEqual(before);
    expect(errors).toEqual([]);
  } finally {
    expect((await request.delete(`/api/projects/${id}`)).ok()).toBeTruthy();
  }
});

test("actual UI Undo and Redo preserve a completed run identity, source configuration and output history", async ({
  page,
  request,
}) => {
  const id = await createProject(request, "Actual graph undo history");
  let runId = "";
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    const node = await addNode(request, id, "Original completed step", 180);
    const submitted = await request.post(`/api/nodes/${node.id}/run`, {
      data: { request_id: crypto.randomUUID(), scope: "single" },
    });
    expect(submitted.ok(), await submitted.text()).toBeTruthy();
    runId = (await submitted.json()).id;
    const readRun = async () =>
      (await request.get(`/api/runs/${runId}`)).json();
    await expect
      .poll(async () => (await readRun()).status, { timeout: 15000 })
      .toBe("completed");
    const completed = await readRun();
    const sourceConfig = completed.config;
    const sourceRevision = completed.node_revision;
    const originalNode = (await graph(request, id)).nodes.find(
      (value: { id: string }) => value.id === node.id,
    );
    const edited = await command(request, id, "edit_node", [node.id], {
      title: "Edited completed step",
    });
    expect(edited.ok(), await edited.text()).toBeTruthy();
    const editedGraph = await graph(request, id);
    await page.goto(`/projects/${id}/workspace?node=${node.id}`);
    await expect(page.getByLabel("Node title")).toHaveValue(
      "Edited completed step",
    );
    const draftKey = `forest-node-editor:${id}:${node.id}`;
    let originalDraftBase: unknown;
    for (const dirty of [false, true]) {
      if (dirty) {
        await page
          .getByLabel("Node title")
          .fill("Unsaved title protected through history");
        await page.getByRole("tab", { name: "Config", exact: true }).click();
        await page
          .getByLabel("Agent / model / tools / budget / parameters")
          .fill("{ invalid JSON protected through history");
        await expect
          .poll(() =>
            page.evaluate(
              (key) => Boolean(sessionStorage.getItem(key)),
              draftKey,
            ),
          )
          .toBeTruthy();
        originalDraftBase = await page.evaluate(
          (key) => JSON.parse(sessionStorage.getItem(key)!).base,
          draftKey,
        );
      }
      for (const [label, title] of [
        ["Undo graph edit", "Original completed step"],
        ["Redo graph edit", "Edited completed step"],
      ]) {
        const response = page.waitForResponse(
          (value) =>
            value.request().method() === "POST" &&
            new URL(value.url()).pathname ===
              `/api/projects/${id}/graph/commands`,
        );
        await page.getByRole("button", { name: label, exact: true }).click();
        expect((await response).ok()).toBeTruthy();
        await expect(
          page
            .locator(`.react-flow__node[data-id="${node.id}"]`)
            .getByRole("heading"),
        ).toHaveText(title);
        await expect(page.getByLabel("Node title")).toHaveValue(
          dirty ? "Unsaved title protected through history" : title,
        );
        if (dirty) {
          await expect(
            page.getByLabel("Agent / model / tools / budget / parameters"),
          ).toHaveValue("{ invalid JSON protected through history");
          expect(
            await page.evaluate(
              (key) => JSON.parse(sessionStorage.getItem(key)!).base,
              draftKey,
            ),
          ).toEqual(originalDraftBase);
        }
        const current = await graph(request, id);
        const liveNode = current.nodes.find(
          (value: { id: string }) => value.id === node.id,
        );
        expect(liveNode.title).toBe(title);
        expect(liveNode.last_run_id).toBe(runId);
        expect(liveNode.execution_status).toBe(originalNode.execution_status);
        expect(liveNode.outputs).toEqual(originalNode.outputs);
        const retained = await readRun();
        expect(retained.id).toBe(completed.id);
        expect(retained.status).toBe("completed");
        expect(retained.node_revision).toBe(sourceRevision);
        expect(retained.config).toEqual(sourceConfig);
        expect(retained.metrics).toEqual(completed.metrics);
        const runs = await (
          await request.get(`/api/projects/${id}/runs`)
        ).json();
        expect(runs.map((value: { id: string }) => value.id)).toEqual([runId]);
        const output = await (
          await request.get(`/api/runs/${runId}/output`)
        ).json();
        expect(output.text).toContain("actual retained history output");
      }
    }
    expect((await graph(request, id)).revision).toBe(editedGraph.revision + 4);
    await page.reload();
    await expect(page.getByLabel("Node title")).toHaveValue(
      "Unsaved title protected through history",
    );
    await expect(
      page.getByLabel("Agent / model / tools / budget / parameters"),
    ).toHaveValue("{ invalid JSON protected through history");
    expect(
      await page.evaluate(
        (key) => JSON.parse(sessionStorage.getItem(key)!).base,
        draftKey,
      ),
    ).toEqual(originalDraftBase);
    await page.goto(`/projects/${id}/runs?run=${runId}`);
    await expect(page.locator(".run-output > pre")).toContainText(
      "actual retained history output",
    );
    expect(errors).toEqual([]);
  } finally {
    if (runId) {
      const run = await (await request.get(`/api/runs/${runId}`)).json();
      if (
        ![
          "completed",
          "failed",
          "cancelled",
          "interrupted",
          "skipped",
        ].includes(run.status)
      ) {
        expect(
          (await request.post(`/api/runs/${runId}/cancel`, { data: {} })).ok(),
        ).toBeTruthy();
        await expect
          .poll(
            async () =>
              (await (await request.get(`/api/runs/${runId}`)).json()).status,
          )
          .toBe("cancelled");
      }
    }
    expect((await request.delete(`/api/projects/${id}`)).ok()).toBeTruthy();
  }
});
