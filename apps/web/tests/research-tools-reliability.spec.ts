import { expect, test } from "@playwright/test";

test("an uncertain actual paired submission keeps the same request identity through language change and reload", async ({
  page,
  request,
}, info) => {
  test.setTimeout(60000);
  const created = await request.post("/api/projects", {
    data: {
      name: `Paired request identity ${Date.now()}`,
      mode: "manual",
      budget: { allow_paid: false, seconds: 120 },
      config: { publication_profile: { id: "operational" } },
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  const requests: any[] = [];
  let language = "en";
  let accepted: any;
  try {
    const upload = await request.post(`/api/projects/${project.id}/upload`, {
      multipart: {
        file: {
          name: "paired.csv",
          mimeType: "text/csv",
          buffer: Buffer.from("unit,baseline,candidate\na,1,2\nb,3,5\n"),
        },
      },
    });
    expect(upload.ok()).toBeTruthy();
    const uploaded = await upload.json();
    // Only the displayed preference is controlled; both submissions use the real backend.
    await page.route("**/api/settings", async (route) => {
      const response = await route.fetch();
      await route.fulfill({
        response,
        json: { ...(await response.json()), language },
      });
    });
    await page.route(
      `**/api/projects/${project.id}/statistics/paired`,
      async (route) => {
        requests.push(route.request().postDataJSON());
        if (requests.length === 1) {
          const response = await route.fetch();
          expect(response.ok()).toBeTruthy();
          accepted = await response.json();
          await route.abort("failed");
        } else await route.continue();
      },
    );
    await page.goto(`/projects/${project.id}/data`);
    await page
      .getByText("Paired inference & statistics review", { exact: true })
      .click();
    await page
      .getByLabel("Saved table project-relative path")
      .fill(uploaded.path);
    await page.getByLabel("Independent unit column").fill("unit");
    await page.getByLabel("Baseline column", { exact: true }).fill("baseline");
    await page
      .getByLabel("Candidate column", { exact: true })
      .fill("candidate");
    await page
      .getByRole("button", { name: "Run paired inference", exact: true })
      .click();
    await expect(
      page.locator(".advanced-tools").getByRole("alert"),
    ).toBeVisible();
    expect(accepted.id).toBeTruthy();
    language = "zh";
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("lang", "zh");
    await page.getByText("配对推断与统计审阅", { exact: true }).click();
    await expect(page.getByLabel("已保存数据表的项目相对路径")).toHaveValue(
      uploaded.path,
    );
    await page
      .getByRole("button", { name: "运行配对推断", exact: true })
      .click();
    await expect(
      page.locator(".advanced-tools").getByRole("status"),
    ).toContainText(accepted.id);
    expect(requests).toHaveLength(2);
    expect(requests[1]).toEqual(requests[0]);
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/runs/${accepted.id}`)).json()).status,
        { timeout: 30000 },
      )
      .toBe("completed");
    const history = await (
      await request.get(`/api/projects/${project.id}/runs`)
    ).json();
    expect(history).toHaveLength(1);
    expect(history[0].id).toBe(accepted.id);
    expect(history[0].metrics.unit_count).toBe(2);
    await info.attach("actual-paired-request-replay", {
      body: JSON.stringify({
        runId: accepted.id,
        requestId: requests[0].request_id,
        savedRuns: history.length,
        unitCount: history[0].metrics.unit_count,
      }),
      contentType: "application/json",
    });
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});
