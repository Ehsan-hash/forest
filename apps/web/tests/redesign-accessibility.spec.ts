import { expect, test } from "@playwright/test";

test("system appearance follows actual media changes and reduced transparency removes chrome blur", async ({
  page,
}) => {
  await page.addInitScript(() =>
    localStorage.setItem("forest-theme", "system"),
  );
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.goto("/settings");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByLabel("Reduce transparency").check();
  await expect(page.locator("html")).toHaveAttribute(
    "data-transparency",
    "reduced",
  );
  await expect
    .poll(() =>
      page
        .locator(".sidebar")
        .evaluate((el) => getComputedStyle(el).backdropFilter),
    )
    .toBe("none");
  await page.reload();
  await expect(page.getByLabel("Reduce transparency")).toBeChecked();
});

test("compact manuscript panel keeps keyboard focus and Escape returns to the selected graph node", async ({
  page,
  request,
}) => {
  const response = await request.post("/api/projects", {
    data: { name: `Accessible manuscript ${Date.now()}`, mode: "manual" },
  });
  expect(response.ok()).toBeTruthy();
  const project = await response.json();
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
          params: { type: "idea", title: "Keyboard selected node" },
        },
      },
    );
    expect(added.ok()).toBeTruthy();
    const node = (await added.json()).graph.nodes[0];
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(
      `/projects/${project.id}/workspace?node=${node.id}&paper=1`,
    );
    const panel = page.getByRole("dialog", {
      name: "Manuscript panel",
      exact: true,
    });
    await expect(panel).toBeVisible();
    await expect(page.locator(".workspace-primary")).toHaveAttribute(
      "inert",
      "",
    );
    await panel
      .getByRole("button", { name: "Pin manuscript panel", exact: true })
      .focus();
    await page.keyboard.press("Shift+Tab");
    expect(
      await panel.evaluate((el) => el.contains(document.activeElement)),
    ).toBeTruthy();
    await panel
      .getByRole("button", { name: "Close manuscript panel", exact: true })
      .focus();
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(
      page.locator(`.react-flow__node[data-id="${node.id}"] .research-node`),
    ).toHaveClass(/selected/);
    await expect(page).toHaveURL(new RegExp(`node=${node.id}`));
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});
