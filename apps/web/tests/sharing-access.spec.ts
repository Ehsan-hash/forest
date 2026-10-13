import { expect, test } from "@playwright/test";

test("anonymous real share exposes only selected nodes without requesting owner data, then honors revocation", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: {
      name: `Anonymous share ${Date.now()}`,
      mode: "manual",
      budget: { allow_paid: false },
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  try {
    const graph = await (
      await request.get(`/api/projects/${project.id}/graph`)
    ).json();
    const added = await request.post(
      `/api/projects/${project.id}/graph/commands`,
      {
        data: {
          request_id: crypto.randomUUID(),
          expected_revision: graph.revision,
          operation: "add_node",
          params: { type: "idea", title: "Explicit public node", config: {} },
        },
      },
    );
    expect(added.ok()).toBeTruthy();
    const node = (await added.json()).graph.nodes.find(
      (item: any) => item.title === "Explicit public node",
    );
    const shared = await request.post(`/api/projects/${project.id}/share`, {
      data: { node_ids: [node.id], description: false },
    });
    expect(shared.ok()).toBeTruthy();
    const share = await shared.json();
    await page.setExtraHTTPHeaders({ "x-forwarded-for": "192.0.2.10" });
    const apiPaths: string[] = [];
    page.on("request", (value) => {
      const path = new URL(value.url()).pathname;
      if (path.startsWith("/api/")) apiPaths.push(path);
    });
    await page.goto(share.url);
    await expect(
      page.getByRole("heading", { name: project.name, exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: node.title, exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("dialog", { name: "Sign in", exact: true }),
    ).toHaveCount(0);
    await expect(page.locator(".sidebar")).toHaveCount(0);
    expect(apiPaths).toEqual([`/api/shares/${share.token}`]);
    const status = await page.evaluate(async () =>
      (await fetch("/api/auth/status")).json(),
    );
    expect(status.authenticated).toBe(false);
    expect(
      (await request.delete(`/api/shares/${share.token}`)).ok(),
    ).toBeTruthy();
    await page.reload();
    await expect(page.getByRole("alert")).toContainText("Share unavailable");
    await expect(
      page.getByRole("heading", { name: node.title, exact: true }),
    ).toHaveCount(0);
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});

test("actual authentication rejection opens sign-in and retains an invalid token for correction", async ({
  page,
}) => {
  await page.setExtraHTTPHeaders({ "x-forwarded-for": "192.0.2.10" });
  const rejection = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/settings" &&
      response.status() === 401,
  );
  await page.goto("/projects");
  await rejection;
  const dialog = page.getByRole("dialog", { name: "Sign in", exact: true });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Owner token").fill("invalid-isolated-owner-token");
  const failedLogin = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/auth/login",
  );
  await dialog
    .getByRole("button", { name: "Connect workspace", exact: true })
    .click();
  expect((await failedLogin).status()).toBe(401);
  await expect(page.locator(".toast")).toContainText(
    "Owner token is incorrect",
  );
  await expect(dialog.getByLabel("Owner token")).toHaveValue(
    "invalid-isolated-owner-token",
  );
  await expect(
    dialog.getByRole("button", { name: "Connect workspace", exact: true }),
  ).toBeEnabled();
});

test("actual cross-origin permission rejection shows a durable error instead of a project success state", async ({
  page,
}) => {
  await page.route("**/api/projects", async (route) => {
    const response = await route.fetch({
      headers: {
        ...route.request().headers(),
        origin: "https://denied.forest.invalid",
      },
    });
    await route.fulfill({ response });
  });
  const forbidden = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/projects" &&
      response.status() === 403,
  );
  await page.goto("/projects");
  await forbidden;
  await expect(page.getByRole("alert")).toContainText(
    "Cross-origin write/read denied",
  );
  await expect(
    page.getByRole("dialog", { name: "Sign in", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".project-card")).toHaveCount(0);
});
