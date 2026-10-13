import { expect, test } from "@playwright/test";

test("invalid connection configuration remains editable with a durable form error", async ({
  page,
}) => {
  await page.goto("/settings");
  await page.getByRole("tab", { name: "Model providers", exact: true }).click();
  await page.getByRole("button", { name: "Add", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "Connection & role configuration",
  });
  await dialog
    .getByLabel("Name", { exact: true })
    .fill("Retained invalid connection draft");
  await dialog
    .getByLabel("Model ID", { exact: true })
    .fill("unused-local-model");
  await dialog
    .getByRole("textbox", { name: "Configuration", exact: true })
    .fill('{"custom_unknown":');
  await dialog
    .getByRole("button", { name: "Save configuration", exact: true })
    .click();
  await expect(dialog.locator(".error-box")).toBeVisible();
  await expect(dialog.getByLabel("Name", { exact: true })).toHaveValue(
    "Retained invalid connection draft",
  );
  await expect(
    dialog.getByRole("textbox", { name: "Configuration", exact: true }),
  ).toHaveValue('{"custom_unknown":');
  await expect(
    dialog.getByRole("button", { name: "Save configuration", exact: true }),
  ).toBeEnabled();
});

test("global settings draft survives real refresh and navigation, saving only changed keys", async ({
  page,
  request,
}) => {
  const original = await (await request.get("/api/settings")).json();
  const unknownKey = `frontend_unknown_${Date.now()}`;
  const seeded = {
    ...original,
    retention_days: original.retention_days ?? 30,
    [unknownKey]: { untouched: "saved" },
  };
  expect(
    (await request.patch("/api/settings", { data: seeded })).ok(),
  ).toBeTruthy();
  try {
    await page.goto("/settings");
    await page
      .getByRole("tab", { name: "Budget & defaults", exact: true })
      .click();
    const editor = page.getByLabel("Global settings JSON");
    await expect.poll(async () => editor.inputValue()).toContain(unknownKey);
    const draft = {
      ...JSON.parse(await editor.inputValue()),
      retention_days: seeded.retention_days + 3,
    };
    const text = JSON.stringify(draft);
    await editor.fill(text);
    expect(
      (
        await request.patch("/api/settings", {
          data: {
            [unknownKey]: { untouched: "external-update" },
            external_frontend_setting: true,
          },
        })
      ).ok(),
    ).toBeTruthy();
    const refreshed = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        new URL(response.url()).pathname === "/api/settings",
    );
    await page.evaluate(() =>
      window.dispatchEvent(new Event("forest-settings-changed")),
    );
    await refreshed;
    await expect(editor).toHaveValue(text);
    await page.goto("/projects");
    await page.goto("/settings");
    await page
      .getByRole("tab", { name: "Budget & defaults", exact: true })
      .click();
    await expect(editor).toHaveValue(text);
    const save = page.waitForRequest(
      (value) =>
        value.method() === "PATCH" &&
        new URL(value.url()).pathname === "/api/settings",
    );
    await page
      .getByRole("button", { name: "Save settings", exact: true })
      .click();
    expect((await save).postDataJSON()).toEqual({
      retention_days: draft.retention_days,
    });
    await expect(
      page.getByText("Global settings saved", { exact: true }),
    ).toBeVisible();
    const saved = await (await request.get("/api/settings")).json();
    expect(saved[unknownKey]).toEqual({ untouched: "external-update" });
    expect(saved.external_frontend_setting).toBe(true);
    expect(saved.retention_days).toBe(draft.retention_days);
    await page.reload();
    await page
      .getByRole("tab", { name: "Budget & defaults", exact: true })
      .click();
    await expect
      .poll(async () => JSON.parse(await editor.inputValue())[unknownKey])
      .toEqual({ untouched: "external-update" });
  } finally {
    expect(
      (await request.patch("/api/settings", { data: original })).ok(),
    ).toBeTruthy();
  }
});

test("changing the default provider preserves valid unknown settings and invalid JSON drafts", async ({
  page,
  request,
}) => {
  const original = await (await request.get("/api/settings")).json();
  const providerResponse = await request.post("/api/providers", {
    data: {
      name: `Settings draft local provider ${Date.now()}`,
      kind: "ollama",
      model: "draft-test-model",
      base_url: "http://127.0.0.1:1",
      allow_paid: false,
    },
  });
  expect(providerResponse.ok()).toBeTruthy();
  const provider = await providerResponse.json();
  try {
    await page.goto("/settings");
    await page
      .getByRole("tab", { name: "Budget & defaults", exact: true })
      .click();
    const editor = page.getByLabel("Global settings JSON");
    await expect(editor).toBeEnabled();
    const draft = {
      ...JSON.parse(await editor.inputValue()),
      unknown_draft_key: { kept: true },
    };
    await editor.fill(JSON.stringify(draft));
    await page
      .getByRole("tab", { name: "Model providers", exact: true })
      .click();
    const card = page
      .locator(".connection-card")
      .filter({ hasText: provider.name });
    await card
      .getByRole("button", { name: "Use for new projects", exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (await (await request.get("/api/settings")).json())
            .default_provider_id,
      )
      .toBe(provider.id);
    await page
      .getByRole("tab", { name: "Budget & defaults", exact: true })
      .click();
    await expect
      .poll(async () => JSON.parse(await editor.inputValue()))
      .toEqual({ ...draft, default_provider_id: provider.id });
    expect(
      (await (await request.get("/api/settings")).json()).unknown_draft_key,
    ).toBeUndefined();
    await editor.fill('{"unknown_draft_key":');
    await page
      .getByRole("tab", { name: "Model providers", exact: true })
      .click();
    const applied = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" &&
        new URL(response.url()).pathname === "/api/settings",
    );
    await card
      .getByRole("button", { name: "Use for new projects", exact: true })
      .click();
    expect((await applied).ok()).toBeTruthy();
    await page
      .getByRole("tab", { name: "Budget & defaults", exact: true })
      .click();
    await expect(editor).toHaveValue('{"unknown_draft_key":');
    await page
      .getByRole("button", { name: "Save settings", exact: true })
      .click();
    await expect(page.locator(".settings-defaults .error-box")).toBeVisible();
    await expect(editor).toHaveValue('{"unknown_draft_key":');
  } finally {
    expect(
      (
        await request.patch("/api/settings", {
          data: {
            ...original,
            default_provider_id: original.default_provider_id ?? null,
          },
        })
      ).ok(),
    ).toBeTruthy();
    expect(
      (
        await request.post(`/api/providers/${provider.id}/retire`, { data: {} })
      ).ok(),
    ).toBeTruthy();
  }
});
