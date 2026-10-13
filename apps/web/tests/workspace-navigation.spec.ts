import { expect, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

async function createProject(request: APIRequestContext, name: string) {
  const response = await request.post("/api/projects", {
    data: {
      name: `${name} ${Date.now()}`,
      goal: "Inspect isolated frontend execution and navigation.",
      mode: "manual",
      budget: { seconds: 120, allow_paid: false },
      config: { publication_profile: { id: "operational" } },
    },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).id as string;
}
async function command(
  request: APIRequestContext,
  projectId: string,
  operation: string,
  targets: string[],
  params: Record<string, unknown>,
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
        operation,
        targets,
        params,
        run: false,
      },
    },
  );
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function addNode(
  request: APIRequestContext,
  projectId: string,
  title: string,
  output: string,
) {
  const result = await command(request, projectId, "add_node", [], {
    type: "implementation",
    title,
    position: { x: title === "Alpha node" ? 120 : 460, y: 120 },
    config: {
      kind: "command",
      timeout: 5,
      command: ["python3", "-c", `print(${JSON.stringify(output)},flush=True)`],
      custom_unknown_key: { retained: true },
    },
  });
  return result.graph.nodes.find(
    (node: { title: string }) => node.title === title,
  );
}

test("workspace branch and node URL state survives reload and history without editing the graph", async ({
  page,
  request,
}) => {
  const projectId = await createProject(request, "Workspace URL");
  try {
    const alpha = await addNode(request, projectId, "Alpha node", "alpha");
    const fork = await command(request, projectId, "fork_branch", [alpha.id], {
      name: "Alternate path",
      copy_policy: { code: true, data: "reference", results: false },
    });
    const alternate = fork.graph.branches.find(
      (branch: { id: string }) => branch.id !== alpha.branch_id,
    );
    await expect
      .poll(
        async () =>
          (
            await (await request.get(`/api/projects/${projectId}/graph`)).json()
          ).branches.find(
            (branch: { id: string }) => branch.id === alternate.id,
          ).status,
      )
      .toBe("active");
    const before = await (
      await request.get(`/api/projects/${projectId}/graph`)
    ).json();
    await page.goto(
      `/projects/${projectId}/workspace?branch=${alternate.id}&demo=1`,
    );
    await expect(page.getByLabel("Current branch")).toHaveValue(alternate.id);
    await page.reload();
    await expect(page.getByLabel("Current branch")).toHaveValue(alternate.id);
    await page.getByLabel("Current branch").selectOption(alpha.branch_id);
    await expect(page).toHaveURL(new RegExp(`branch=${alpha.branch_id}`));
    await page.goBack();
    await expect(page.getByLabel("Current branch")).toHaveValue(alternate.id);
    await page.goForward();
    await expect(page.getByLabel("Current branch")).toHaveValue(
      alpha.branch_id,
    );
    await page.getByLabel("Search nodes").fill("Alpha node");
    await page
      .locator(".node-search-results")
      .getByRole("button", { name: "Alpha node", exact: true })
      .first()
      .click();
    await expect(page).toHaveURL(new RegExp(`node=${alpha.id}`));
    await expect(page).toHaveURL(/demo=1/);
    await page.reload();
    await expect(page.getByLabel("Node title")).toHaveValue("Alpha node");
    await page.goto(
      `/projects/${projectId}/workspace?branch=missing-branch&node=missing-node`,
    );
    await expect(
      page.getByText("Selected research path is unavailable."),
    ).toBeVisible();
    await expect(page.getByText("Selected node is unavailable.")).toBeVisible();
    await page.getByRole("button", { name: "Show all paths" }).click();
    await expect(page.getByLabel("Current branch")).toHaveValue("all");
    await page
      .getByRole("button", { name: "Clear selection", exact: true })
      .click();
    await expect(page).not.toHaveURL(/node=/);
    const after = await (
      await request.get(`/api/projects/${projectId}/graph`)
    ).json();
    expect(after.revision).toBe(before.revision);
    expect(after.nodes).toEqual(before.nodes);
  } finally {
    expect(
      (await request.delete(`/api/projects/${projectId}`)).ok(),
    ).toBeTruthy();
  }
});

test("temporary inspector keeps complete dirty drafts through dismissal, focus, reload and resize", async ({
  page,
  request,
}) => {
  const projectId = await createProject(request, "Workspace draft");
  try {
    const node = await addNode(request, projectId, "Alpha node", "alpha");
    await page.goto(`/projects/${projectId}/workspace?node=${node.id}`);
    const inspector = page.getByRole("complementary", {
      name: "Node inspector",
    });
    await expect(inspector).toBeVisible();
    await inspector
      .getByLabel("Node title")
      .fill("Unsaved complete node draft");
    await inspector.getByRole("tab", { name: "Config", exact: true }).click();
    const config = inspector.getByLabel(
      "Agent / model / tools / budget / parameters",
    );
    await config.fill(
      '{"custom_unknown_key":{"retained":true},"draft_only":7}',
    );
    await inspector.getByRole("button", { name: "Close inspector" }).click();
    await expect(inspector).toHaveCount(0);
    await page
      .getByRole("button", { name: "Toggle inspector", exact: true })
      .click();
    await expect(config).toHaveValue(
      '{"custom_unknown_key":{"retained":true},"draft_only":7}',
    );
    await page
      .locator(".workspace-toolbar")
      .getByRole("button", { name: "Focus", exact: true })
      .click();
    await expect(page.getByLabel("Node title")).toHaveValue(
      "Unsaved complete node draft",
    );
    await page.reload();
    await expect(page.getByLabel("Node title")).toHaveValue(
      "Unsaved complete node draft",
    );
    await page
      .locator(".workspace-toolbar")
      .getByRole("button", { name: "Forest", exact: true })
      .click();
    await expect(config).toHaveValue(
      '{"custom_unknown_key":{"retained":true},"draft_only":7}',
    );
    await page.setViewportSize({ width: 768, height: 900 });
    await expect(inspector).toHaveCount(0);
    await page
      .getByRole("button", { name: "Toggle inspector", exact: true })
      .click();
    await expect(config).toHaveValue(
      '{"custom_unknown_key":{"retained":true},"draft_only":7}',
    );
    await page
      .locator(".workspace-toolbar")
      .getByRole("button", { name: "Toggle inspector", exact: true })
      .focus();
    await page.keyboard.press("Escape");
    await expect(inspector).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(page.locator(".research-node.selected")).toHaveCount(0);
    await expect(page).not.toHaveURL(/node=/);
    const graph = await (
      await request.get(`/api/projects/${projectId}/graph`)
    ).json();
    expect(graph.nodes[0].title).toBe("Alpha node");
    expect(graph.nodes[0].config.draft_only).toBeUndefined();
  } finally {
    expect(
      (await request.delete(`/api/projects/${projectId}`)).ok(),
    ).toBeTruthy();
  }
});

test("full-ID run links show actual worker output, history and recoverable missing or cross-project selections", async ({
  page,
  request,
}) => {
  const projectId = await createProject(request, "Run URL");
  const otherId = await createProject(request, "Foreign run URL");
  try {
    const alpha = await addNode(
      request,
      projectId,
      "Alpha node",
      "actual alpha worker output",
    );
    const beta = await addNode(
      request,
      projectId,
      "Beta node",
      "actual beta worker output",
    );
    const foreign = await addNode(
      request,
      otherId,
      "Alpha node",
      "foreign worker output",
    );
    const submit = async (nodeId: string) => {
      const response = await request.post(`/api/nodes/${nodeId}/run`, {
        data: { request_id: crypto.randomUUID(), scope: "single" },
      });
      expect(response.ok(), await response.text()).toBeTruthy();
      return (await response.json()).id as string;
    };
    const [alphaRun, betaRun, foreignRun] = await Promise.all([
      submit(alpha.id),
      submit(beta.id),
      submit(foreign.id),
    ]);
    for (const runId of [alphaRun, betaRun, foreignRun])
      await expect
        .poll(
          async () =>
            (await (await request.get(`/api/runs/${runId}`)).json()).status,
          { timeout: 30000 },
        )
        .toBe("completed");
    await page.goto(`/projects/${projectId}/runs?run=${alphaRun}`);
    await expect(page.locator(".run-output-toolbar code")).toHaveAttribute(
      "title",
      alphaRun,
    );
    await expect(page.locator(".run-output > pre")).toContainText(
      "actual alpha worker output",
    );
    await page.reload();
    await expect(page.locator(".run-output-toolbar code")).toHaveAttribute(
      "title",
      alphaRun,
    );
    await page
      .locator(".run-list button")
      .filter({ hasText: betaRun.slice(0, 8) })
      .click();
    await expect(page).toHaveURL(new RegExp(`run=${betaRun}`));
    await expect(page.locator(".run-output > pre")).toContainText(
      "actual beta worker output",
    );
    await page.goBack();
    await expect(page.locator(".run-output > pre")).toContainText(
      "actual alpha worker output",
    );
    await page.getByRole("button", { name: "Run advanced controls" }).click();
    const dialog = page.getByRole("dialog", {
      name: "Run controls and diagnostics",
    });
    await dialog.getByLabel("Scheduling priority").fill("7");
    await dialog.getByRole("button", { name: "Save priority" }).click();
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/runs/${alphaRun}`)).json()).priority,
      )
      .toBe(7);
    await dialog.getByRole("button", { name: "Process diagnostics" }).click();
    await expect(dialog).toContainText('"run_id"');
    await dialog.getByRole("button", { name: "Repository provenance" }).click();
    await expect(dialog).toContainText('"repository"');
    await dialog
      .getByLabel("Configuration update (JSON patch)")
      .fill('{"priority":11}');
    await page.reload();
    await page.getByRole("button", { name: "Run advanced controls" }).click();
    await expect(
      dialog.getByLabel("Configuration update (JSON patch)"),
    ).toHaveValue('{"priority":11}');
    await dialog
      .getByRole("button", { name: "Apply configuration patch" })
      .click();
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/runs/${alphaRun}`)).json()).config
            .priority,
      )
      .toBe(11);
    const editedRun = await (await request.get(`/api/runs/${alphaRun}`)).json();
    expect(editedRun.config.custom_unknown_key).toEqual({ retained: true });
    expect(editedRun.config.command).toEqual(alpha.config.command);
    await page.goto(`/projects/${projectId}/runs?run=missing-full-run-id`);
    await expect(page).toHaveURL(/run=missing-full-run-id/);
    await expect(page.locator(".run-output .error-box")).toBeVisible();
    await expect(page.locator(".run-output-toolbar")).toHaveCount(0);
    await page.goto(`/projects/${projectId}/runs?run=${foreignRun}`);
    await expect(
      page.getByText(
        "Selected run belongs to another project. Choose a run from this project.",
      ),
    ).toBeVisible();
    await expect(page.locator(".run-output-toolbar")).toHaveCount(0);
    await expect(page.locator(".run-output")).not.toContainText(
      "foreign worker output",
    );
  } finally {
    expect(
      (await request.delete(`/api/projects/${projectId}`)).ok(),
    ).toBeTruthy();
    expect(
      (await request.delete(`/api/projects/${otherId}`)).ok(),
    ).toBeTruthy();
  }
});

test("explicit branch execution uses the branch receipt and can skip a queued descendant", async ({
  page,
  request,
}) => {
  const projectId = await createProject(request, "Branch execution entry");
  try {
    const firstResult = await command(request, projectId, "add_node", [], {
      type: "implementation",
      title: "Branch root command",
      config: {
        kind: "command",
        timeout: 10,
        command: [
          "python3",
          "-c",
          "import time; time.sleep(6); print('branch root finished', flush=True)",
        ],
      },
      position: { x: 120, y: 120 },
    });
    const root = firstResult.graph.nodes[0];
    const childResult = await command(request, projectId, "add_node", [], {
      type: "implementation",
      title: "Queued branch descendant",
      branch_id: root.branch_id,
      config: {
        kind: "command",
        timeout: 5,
        command: [
          "python3",
          "-c",
          "print('descendant should be skipped', flush=True)",
        ],
      },
      position: { x: 460, y: 120 },
    });
    const child = childResult.graph.nodes.find(
      (item: { title: string }) => item.title === "Queued branch descendant",
    );
    await command(request, projectId, "add_dependency", [], {
      source: root.id,
      target: child.id,
      relation: "depends_on",
    });
    await page.goto(`/projects/${projectId}/workspace`);
    await expect(
      page.getByRole("button", { name: "Run branch", exact: true }),
    ).toBeDisabled();
    await page.getByLabel("Current branch").selectOption(root.branch_id);
    await expect(
      page.getByRole("button", { name: "Run node", exact: true }),
    ).toBeDisabled();
    const accepted = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname ===
          `/api/branches/${root.branch_id}/run`,
    );
    await page.getByRole("button", { name: "Run branch", exact: true }).click();
    const response = await accepted;
    expect(response.ok(), await response.text()).toBeTruthy();
    const receipt = await response.json();
    expect(receipt.runs).toHaveLength(2);
    expect(receipt.runs.map((run: { node_id: string }) => run.node_id)).toEqual(
      [root.id, child.id],
    );
    await expect(page).toHaveURL(new RegExp(`run=${receipt.runs[0].id}`));
    const queued = receipt.runs[1];
    await page
      .locator(".run-list button")
      .filter({ hasText: queued.id.slice(0, 8) })
      .click();
    await page
      .getByRole("button", { name: "Run advanced controls", exact: true })
      .click();
    const dialog = page.getByRole("dialog", {
      name: "Run controls and diagnostics",
    });
    await expect(
      dialog.getByRole("button", { name: "Skip queued run", exact: true }),
    ).toBeEnabled();
    await dialog
      .getByRole("button", { name: "Skip queued run", exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/runs/${queued.id}`)).json()).status,
      )
      .toBe("skipped");
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/runs/${receipt.runs[0].id}`)).json())
            .status,
        { timeout: 20000 },
      )
      .toBe("completed");
    expect(
      (await (await request.get(`/api/runs/${queued.id}/output`)).json()).text,
    ).toBe("");
    await expect(
      dialog.getByRole("button", { name: "Skip queued run", exact: true }),
    ).toBeDisabled();
  } finally {
    expect(
      (await request.delete(`/api/projects/${projectId}`)).ok(),
    ).toBeTruthy();
  }
});

test("configuration acknowledgment retains a patch edited during the actual pending save", async ({
  page,
  request,
}) => {
  const projectId = await createProject(request, "Run patch acknowledgment");
  let release = () => {};
  try {
    const node = await addNode(
      request,
      projectId,
      "Alpha node",
      "actual patch worker output",
    );
    const submitted = await request.post(`/api/nodes/${node.id}/run`, {
      data: { request_id: crypto.randomUUID(), scope: "single" },
    });
    expect(submitted.ok()).toBeTruthy();
    const run = await submitted.json();
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/runs/${run.id}`)).json()).status,
        { timeout: 30000 },
      )
      .toBe("completed");
    await page.goto(`/projects/${projectId}/runs?run=${run.id}`);
    await page
      .getByRole("button", { name: "Run advanced controls", exact: true })
      .click();
    const dialog = page.getByRole("dialog", {
      name: "Run controls and diagnostics",
    });
    const editor = dialog.getByLabel("Configuration update (JSON patch)");
    const hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(`**/api/runs/${run.id}/configuration`, async (route) => {
      const response = await route.fetch();
      await hold;
      await route.fulfill({ response });
    });
    await editor.fill('{"priority":33}');
    await dialog
      .getByRole("button", { name: "Apply configuration patch", exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/runs/${run.id}`)).json()).config
            .priority,
      )
      .toBe(33);
    const lateText = '{"priority":44,"late_unsaved_key":{"kept":true}}';
    await editor.fill(lateText);
    release();
    await expect(
      dialog.getByRole("button", {
        name: "Apply configuration patch",
        exact: true,
      }),
    ).toBeEnabled();
    await expect(editor).toHaveValue(lateText);
    expect(
      (await (await request.get(`/api/runs/${run.id}`)).json()).config
        .late_unsaved_key,
    ).toBeUndefined();
    await page.reload();
    await page
      .getByRole("button", { name: "Run advanced controls", exact: true })
      .click();
    await expect(editor).toHaveValue(lateText);
  } finally {
    release();
    expect(
      (await request.delete(`/api/projects/${projectId}`)).ok(),
    ).toBeTruthy();
  }
});

test("a delayed actual Retry receipt retains history without redirecting a newer selected Run ID", async ({
  page,
  request,
}) => {
  const projectId = await createProject(request, "Retry selection identity");
  const runIds: string[] = [];
  let release = () => {};
  try {
    const alpha = await addNode(
      request,
      projectId,
      "Alpha node",
      "actual retry source output",
    );
    const beta = await addNode(
      request,
      projectId,
      "Beta node",
      "actual independent selected output",
    );
    for (const node of [alpha, beta]) {
      const response = await request.post(`/api/nodes/${node.id}/run`, {
        data: { request_id: crypto.randomUUID(), scope: "single" },
      });
      expect(response.ok(), await response.text()).toBeTruthy();
      runIds.push((await response.json()).id);
    }
    for (const id of runIds)
      await expect
        .poll(
          async () =>
            (await (await request.get(`/api/runs/${id}`)).json()).status,
        )
        .toBe("completed");
    const [sourceId, selectedId] = runIds;
    let resolveReceipt: (value: { id: string }) => void = () => {};
    const receipt = new Promise<{ id: string }>((resolve) => {
      resolveReceipt = resolve;
    });
    const hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(`**/api/runs/${sourceId}/retry`, async (route) => {
      const response = await route.fetch();
      expect(response.ok(), await response.text()).toBeTruthy();
      resolveReceipt(await response.json());
      await hold;
      await route.fulfill({ response });
    });
    await page.goto(`/projects/${projectId}/runs?run=${sourceId}`);
    await page
      .locator(".run-output-toolbar")
      .getByRole("button", { name: "retry", exact: true })
      .click();
    const retried = await receipt;
    runIds.push(retried.id);
    expect(retried.id).not.toBe(sourceId);
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/runs/${retried.id}`)).json()).status,
      )
      .toBe("completed");
    await page
      .locator(".run-list button")
      .filter({ hasText: selectedId.slice(0, 8) })
      .click();
    await expect(page.locator(".run-output-toolbar code")).toHaveAttribute(
      "title",
      selectedId,
    );
    await expect(page.locator(".run-output > pre")).toContainText(
      "actual independent selected output",
    );
    const acknowledged = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === `/api/runs/${sourceId}/retry`,
    );
    const reloaded = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        new URL(response.url()).pathname === `/api/projects/${projectId}/runs`,
    );
    release();
    expect((await acknowledged).ok()).toBeTruthy();
    expect((await reloaded).ok()).toBeTruthy();
    await expect(page).toHaveURL(new RegExp(`run=${selectedId}`));
    await expect(page.locator(".run-output-toolbar code")).toHaveAttribute(
      "title",
      selectedId,
    );
    await page.reload();
    await expect(page.locator(".run-output-toolbar code")).toHaveAttribute(
      "title",
      selectedId,
    );
    const records = await (
      await request.get(`/api/projects/${projectId}/runs`)
    ).json();
    expect(records.map((value: { id: string }) => value.id).sort()).toEqual(
      [...runIds].sort(),
    );
    const savedRetry = records.find(
      (value: { id: string }) => value.id === retried.id,
    );
    const savedSource = await (
      await request.get(`/api/runs/${sourceId}`)
    ).json();
    expect(savedRetry.node_id).toBe(savedSource.node_id);
    expect(savedRetry.node_revision).toBe(savedSource.node_revision);
    expect(savedRetry.config.command).toEqual(savedSource.config.command);
    expect(savedRetry.config.custom_unknown_key).toEqual(
      savedSource.config.custom_unknown_key,
    );
    expect(savedRetry.status).toBe("completed");
    const output = await (
      await request.get(`/api/runs/${retried.id}/output`)
    ).json();
    expect(output.text).toContain("actual retry source output");
  } finally {
    release();
    for (const id of runIds) {
      const run = await (await request.get(`/api/runs/${id}`)).json();
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
          (await request.post(`/api/runs/${id}/cancel`, { data: {} })).ok(),
        ).toBeTruthy();
        await expect
          .poll(
            async () =>
              (await (await request.get(`/api/runs/${id}`)).json()).status,
          )
          .toBe("cancelled");
      }
    }
    expect(
      (await request.delete(`/api/projects/${projectId}`)).ok(),
    ).toBeTruthy();
  }
});

test("an authoritative graph refresh preserves an active drag until its actual position receipt", async ({
  page,
  request,
}) => {
  const projectId = await createProject(request, "Drag refresh preservation");
  try {
    const node = await addNode(
      request,
      projectId,
      "Alpha node",
      "not executed",
    );
    await page.goto(`/projects/${projectId}/workspace`);
    const card = page.getByTestId(`rf__node-${node.id}`);
    await expect(card).toBeVisible();
    const bounds = await card.boundingBox();
    expect(bounds).not.toBeNull();
    const original = await card.evaluate(
      (element) => (element as HTMLElement).style.transform,
    );
    const x = bounds!.x + bounds!.width / 2;
    const y = bounds!.y + 30;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 100, y + 70, { steps: 6 });
    const dragged = await card.evaluate(
      (element) => (element as HTMLElement).style.transform,
    );
    expect(dragged).not.toBe(original);
    await command(request, projectId, "add_node", [], {
      type: "goal",
      title: "Concurrent authoritative graph edit",
      position: { x: 600, y: 120 },
    });
    await page.evaluate(() =>
      window.dispatchEvent(new Event("forest-refresh")),
    );
    await expect(
      page
        .locator(".research-node")
        .filter({ hasText: "Concurrent authoritative graph edit" }),
    ).toBeVisible();
    await expect
      .poll(() =>
        card.evaluate((element) => (element as HTMLElement).style.transform),
      )
      .toBe(dragged);
    await expect(card).toBeVisible();
    await page.mouse.up();
    const [positionX, positionY] = dragged
      .match(/translate\(([-\d.]+)px, ([-\d.]+)px\)/)!
      .slice(1)
      .map(Number);
    await expect
      .poll(async () => {
        const graph = await (
          await request.get(`/api/projects/${projectId}/graph`)
        ).json();
        return graph.nodes.find((item: { id: string }) => item.id === node.id)
          .position;
      })
      .toEqual({ x: positionX, y: positionY });
  } finally {
    await page.mouse.up();
    expect(
      (await request.delete(`/api/projects/${projectId}`)).ok(),
    ).toBeTruthy();
  }
});
