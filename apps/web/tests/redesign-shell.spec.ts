import { test, expect } from "@playwright/test";

test("five main entries retain all project pages and global settings at every requested width", async ({
  page,
  request,
}) => {
  const project = await (
    await request.post("/api/projects", {
      data: { name: "Responsive shell acceptance", mode: "manual" },
    })
  ).json();
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    for (const width of [390, 768, 1024, 1280, 1440, 1920]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(`/projects/${project.id}/workspace`);
      if (width <= 820)
        await page
          .getByRole("button", { name: "Open navigation", exact: true })
          .click();
      const sidebar = page.getByRole("complementary", {
        name: "Main navigation",
      });
      await expect(sidebar.locator("nav > a")).toHaveCount(5);
      for (const name of [
        "Workspace",
        "Library",
        "Experiments & Results",
        "Paper",
        "Files",
        "Settings & connections",
      ])
        await expect(
          sidebar.getByRole("link", { name, exact: true }),
        ).toBeVisible();
      await sidebar
        .getByRole("link", { name: "Experiments & Results", exact: true })
        .click();
      const views = page.getByRole("navigation", { name: "Workspace views" });
      for (const name of [
        "Experiments",
        "Runs",
        "Data & analysis",
        "Figure studio",
      ])
        await expect(
          views.getByRole("link", { name, exact: true }),
        ).toBeVisible();
      await views.getByRole("link", { name: "Runs", exact: true }).click();
      await expect(
        page.getByRole("heading", { name: "Runs", exact: true }),
      ).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBeTruthy();
    }
    await page.setViewportSize({ width: 768, height: 500 }); // 1536×1000 at 200% effective CSS viewport.
    await page.goto(`/projects/${project.id}/workspace`);
    await expect(
      page.getByRole("button", { name: "Open navigation", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("textbox", { name: "Search nodes", exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBeTruthy();
    expect(errors).toEqual([]);
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});

test("budget draft survives real SSE, route changes and reload with a retained revision conflict", async ({
  page,
  request,
}) => {
  const project = await (
    await request.post("/api/projects", {
      data: {
        name: "Controls draft acceptance",
        mode: "manual",
        budget: {
          seconds: 120,
          allow_paid: false,
          future_field: { preserved: true },
        },
      },
    })
  ).json();
  try {
    await page.goto(`/projects/${project.id}/overview`);
    const draft =
      '{"seconds":999,"allow_paid":false,"future_field":{"preserved":true}}';
    await page.getByLabel("Budget configuration").fill(draft);
    const updated = await request.patch(`/api/projects/${project.id}`, {
      data: {
        description: "Actual background update",
        expected_revision: project.revision,
      },
    });
    expect(updated.ok()).toBeTruthy();
    await expect(
      page.getByText("Project updated; save checks for conflicts", {
        exact: false,
      }),
    ).toBeVisible();
    await expect(page.getByLabel("Budget configuration")).toHaveValue(draft);
    await page.getByRole("link", { name: "Graph", exact: true }).click();
    await page.getByRole("link", { name: "Overview", exact: true }).click();
    await expect(page.getByLabel("Budget configuration")).toHaveValue(draft);
    await page.reload();
    await expect(page.getByLabel("Budget configuration")).toHaveValue(draft);
    const conflict = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" &&
        response.url().endsWith(`/api/projects/${project.id}`),
    );
    await page
      .getByRole("button", { name: "Save controls", exact: true })
      .click();
    expect((await conflict).status()).toBe(409);
    await expect(page.getByLabel("Budget configuration")).toHaveValue(draft);
    const persisted = await (
      await request.get(`/api/projects/${project.id}`)
    ).json();
    expect(persisted.budget.seconds).toBe(120);
    expect(persisted.budget.future_field).toEqual({ preserved: true });
    await page
      .getByRole("button", { name: "Load current values", exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Load current values", exact: true })
      .click();
    await expect(page.getByLabel("Budget configuration")).toHaveValue(
      JSON.stringify(persisted.budget, null, 2),
    );
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});

test("an actual controls acknowledgment preserves later budget and mode edits with its accepted revision", async ({
  page,
  request,
}) => {
  const project = await (
    await request.post("/api/projects", {
      data: {
        name: "Controls pending acknowledgment acceptance",
        mode: "manual",
        budget: {
          seconds: 120,
          allow_paid: false,
          future_field: { retained: true },
        },
      },
    })
  ).json();
  let release = () => {};
  try {
    let resolveAccepted: (value: {
      revision: number;
      budget: unknown;
    }) => void = () => {};
    const accepted = new Promise<{ revision: number; budget: unknown }>(
      (resolve) => {
        resolveAccepted = resolve;
      },
    );
    const hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    let held = false;
    await page.route(`**/api/projects/${project.id}`, async (route) => {
      if (route.request().method() !== "PATCH" || held) {
        await route.continue();
        return;
      }
      held = true;
      const response = await route.fetch();
      expect(response.ok(), await response.text()).toBeTruthy();
      resolveAccepted(await response.json());
      await hold;
      await route.fulfill({ response });
    });
    await page.goto(`/projects/${project.id}/overview`);
    const budget = page.getByLabel("Budget configuration");
    const mode = page.getByRole("combobox", { name: "Run mode", exact: true });
    const save = page.getByRole("button", {
      name: "Save controls",
      exact: true,
    });
    await budget.fill(
      '{"seconds":300,"allow_paid":false,"future_field":{"retained":true}}',
    );
    await mode.selectOption("assisted");
    await save.click();
    const saved = await accepted;
    const lateText = "{ invalid budget retained during acknowledgment";
    await budget.fill(lateText);
    await mode.selectOption("auto");
    const external = await request.patch(`/api/projects/${project.id}`, {
      data: {
        description: "Newer real revision while acknowledgment is held",
        expected_revision: saved.revision,
      },
    });
    expect(external.ok(), await external.text()).toBeTruthy();
    const latest = await external.json();
    await expect(
      page.getByText("Project updated; save checks for conflicts", {
        exact: false,
      }),
    ).toBeVisible();
    const acknowledged = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" &&
        new URL(response.url()).pathname === `/api/projects/${project.id}`,
    );
    release();
    expect((await acknowledged).ok()).toBeTruthy();
    await expect(save).toBeEnabled();
    await expect(budget).toHaveValue(lateText);
    await expect(mode).toHaveValue("auto");
    const key = `forest-controls-draft:${project.id}`;
    await expect
      .poll(() =>
        page.evaluate(
          (value) => JSON.parse(sessionStorage.getItem(value)!).revision,
          key,
        ),
      )
      .toBe(saved.revision);
    await page.reload();
    await expect(budget).toHaveValue(lateText);
    await expect(mode).toHaveValue("auto");
    expect(
      await page.evaluate(
        (value) => JSON.parse(sessionStorage.getItem(value)!).revision,
        key,
      ),
    ).toBe(saved.revision);
    const laterBudget =
      '{"seconds":450,"allow_paid":false,"future_field":{"retained":true},"later_key":{"kept":true}}';
    await budget.fill(laterBudget);
    const conflict = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" &&
        new URL(response.url()).pathname === `/api/projects/${project.id}`,
    );
    await save.click();
    const rejected = await conflict;
    expect(rejected.request().postDataJSON()).toMatchObject({
      expected_revision: saved.revision,
      mode: "auto",
      budget: JSON.parse(laterBudget),
    });
    expect(rejected.status()).toBe(409);
    await expect(budget).toHaveValue(laterBudget);
    await page
      .getByRole("button", { name: "Load current values", exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Load current values", exact: true })
      .click();
    await budget.fill(laterBudget);
    await mode.selectOption("auto");
    const finalSave = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" &&
        new URL(response.url()).pathname === `/api/projects/${project.id}`,
    );
    await save.click();
    const persisted = await finalSave;
    expect(persisted.ok()).toBeTruthy();
    expect(persisted.request().postDataJSON().expected_revision).toBe(
      latest.revision,
    );
    const final = await (
      await request.get(`/api/projects/${project.id}`)
    ).json();
    expect(final.budget).toEqual(JSON.parse(laterBudget));
    expect(final.mode).toBe("auto");
  } finally {
    release();
    await request.delete(`/api/projects/${project.id}`);
  }
});

test("palette searches actual projects and nodes, supports keyboard selection and missing routes recover", async ({
  page,
  request,
}) => {
  const project = await (
    await request.post("/api/projects", {
      data: { name: "Palette project acceptance", mode: "manual" },
    })
  ).json();
  const graph = await (
    await request.get(`/api/projects/${project.id}/graph`)
  ).json();
  const created = await (
    await request.post(`/api/projects/${project.id}/graph/commands`, {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_node",
        params: { title: "Unique navigable research node", type: "goal" },
        run: false,
      },
    })
  ).json();
  const node = created.graph.nodes.find(
    (value: { title: string }) =>
      value.title === "Unique navigable research node",
  );
  try {
    await page.goto(`/projects/${project.id}/workspace`);
    await expect(
      page
        .locator(".react-flow__node")
        .getByRole("heading", { name: node.title, exact: true }),
    ).toBeVisible();
    await page.keyboard.press("ControlOrMeta+k");
    const search = page.getByRole("combobox", {
      name: "Search pages, projects, nodes, runs or commands",
    });
    await search.fill("Unique navigable");
    await expect(page.getByRole("dialog").getByRole("option")).toContainText(
      node.id,
    );
    await search.press("Enter");
    await expect(page).toHaveURL(new RegExp(`node=${node.id}`));
    await expect(
      page.locator(".inspector").getByLabel("Node title"),
    ).toHaveValue(node.title);
    await page.keyboard.press("ControlOrMeta+k");
    await page.getByRole("dialog").getByRole("combobox").fill("Toggle light");
    await page.getByRole("dialog").getByRole("combobox").press("Enter");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.goto("/unknown-product-route");
    await expect(
      page.getByRole("heading", { name: "Page not found", exact: true }),
    ).toBeVisible();
    await expect(page).toHaveURL(/unknown-product-route/);
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});
