import { expect, test } from "@playwright/test";

test("responsive graph exposes reachable path comparison and path actions at every compact width", async ({
  page,
  request,
}) => {
  test.setTimeout(60000);
  const created = await request.post("/api/projects", {
    data: {
      name: `Responsive graph actions ${Date.now()}`,
      mode: "manual",
      config: { publication_profile: { id: "operational" } },
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const graphPath = `/api/projects/${project.id}/graph`;
  try {
    const initial = await (await request.get(graphPath)).json();
    const added = await request.post(`${graphPath}/commands`, {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: initial.revision,
        operation: "add_node",
        targets: [],
        params: {
          type: "implementation",
          title: "Responsive path source",
          position: { x: 120, y: 120 },
          config: {
            kind: "command",
            command: ["python3", "-c", "print('source')"],
          },
        },
        run: false,
      },
    });
    expect(added.ok(), await added.text()).toBeTruthy();
    const addedGraph = (await added.json()).graph;
    const node = addedGraph.nodes.find(
      (value: { title: string }) => value.title === "Responsive path source",
    );
    const forked = await request.post(`${graphPath}/commands`, {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: addedGraph.revision,
        operation: "fork_branch",
        targets: [node.id],
        params: {
          name: "Responsive alternate path",
          copy_policy: { code: true, data: "reference", results: false },
        },
        run: false,
      },
    });
    expect(forked.ok(), await forked.text()).toBeTruthy();
    const alternate = (await forked.json()).graph.branches.find(
      (value: { id: string }) => value.id !== node.branch_id,
    );
    await expect
      .poll(async () => {
        const current = await (await request.get(graphPath)).json();
        return current.branches.find(
          (value: { id: string }) => value.id === alternate.id,
        )?.status;
      })
      .toBe("active");
    const before = await (await request.get(graphPath)).json();
    await page.setViewportSize({ width: 390, height: 1000 });
    await page.goto(`/projects/${project.id}/workspace`);
    await expect(page.locator(".research-node")).toHaveCount(before.nodes.length);

    for (const width of [390, 768, 1024, 1280]) {
      await page.setViewportSize({ width, height: 1000 });
      const compare = page.getByRole("button", {
        name: "Compare research paths",
        exact: true,
      });
      await expect(compare).toBeVisible();
      await compare.scrollIntoViewIfNeeded();
      await expect(compare).toBeEnabled();
      await compare.click();
      const comparison = page.getByRole("dialog", {
        name: "Compare research paths",
        exact: true,
      });
      await expect(comparison).toBeVisible();
      await expect(comparison.getByLabel("Left path")).toHaveValue(node.branch_id);
      await expect(comparison.getByLabel("Right path")).toHaveValue(alternate.id);
      const response = page.waitForResponse((value) => {
        const url = new URL(value.url());
        return (
          value.request().method() === "GET" &&
          url.pathname === "/api/branches/compare" &&
          url.searchParams.get("left") === node.branch_id &&
          url.searchParams.get("right") === alternate.id
        );
      });
      await comparison
        .getByRole("button", { name: "Compare", exact: true })
        .click();
      expect((await response).ok()).toBeTruthy();
      await expect(
        comparison.getByRole("heading", { name: "File differences" }),
      ).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(comparison).toHaveCount(0);
      await expect(compare).toBeFocused();

      const actions = page.getByRole("button", {
        name: "Path actions",
        exact: true,
      });
      await expect(actions).toBeVisible();
      await actions.scrollIntoViewIfNeeded();
      await actions.click();
      const operations = page.getByRole("dialog", {
        name: "Research path actions",
        exact: true,
      });
      await expect(operations).toBeVisible();
      await expect(
        operations.getByRole("combobox", { name: "Operation", exact: true }),
      ).toHaveValue("fork_branch");
      await page.keyboard.press("Escape");
      await expect(operations).toHaveCount(0);
      await expect(actions).toBeFocused();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBeTruthy();
    }
    expect(await (await request.get(graphPath)).json()).toEqual(before);
    expect(pageErrors).toEqual([]);
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});
