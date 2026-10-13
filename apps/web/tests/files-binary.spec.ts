import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

test("browser uploads actual binary bytes and downloads the same persisted file after reload", async ({
  page,
  request,
}, info) => {
  const created = await request.post("/api/projects", {
    data: {
      name: `Binary persistence ${Date.now()}`,
      mode: "manual",
      budget: { allow_paid: false },
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  const filename = "binary-retention.npy";
  // A declared arbitrary byte fixture checks file plumbing; it is not a scientific artifact.
  const bytes = Buffer.from(
    Array.from({ length: 4096 }, (_, index) => index % 256),
  );
  try {
    await page.goto(`/projects/${project.id}/files`);
    const response = page.waitForResponse(
      (value) =>
        value.request().method() === "POST" &&
        new URL(value.url()).pathname === `/api/projects/${project.id}/upload`,
    );
    await page
      .locator('input[type="file"]')
      .setInputFiles({
        name: filename,
        mimeType: "application/octet-stream",
        buffer: bytes,
      });
    const uploadedResponse = await response;
    expect(uploadedResponse.ok()).toBeTruthy();
    const uploaded = await uploadedResponse.json();
    expect(uploaded.path).toBe(`uploads/${filename}`);
    await expect(
      page
        .locator(".file-tree")
        .getByRole("button")
        .filter({ hasText: filename }),
    ).toBeVisible();
    await page.reload();
    const row = page
      .locator(".file-tree")
      .getByRole("button")
      .filter({ hasText: filename });
    await expect(row).toBeVisible();
    await row.click();
    const pending = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download", exact: true }).click();
    const download = await pending;
    expect(download.suggestedFilename()).toBe(filename);
    const saved = await readFile((await download.path())!);
    expect(saved.equals(bytes)).toBe(true);
    const actual = await request.get(`/api/projects/${project.id}/download`, {
      params: { path: uploaded.path },
    });
    expect(actual.ok()).toBeTruthy();
    expect((await actual.body()).equals(bytes)).toBe(true);
    await info.attach("actual-binary-roundtrip", {
      body: JSON.stringify({
        projectId: project.id,
        path: uploaded.path,
        bytes: saved.length,
        equal: true,
      }),
      contentType: "application/json",
    });
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});
