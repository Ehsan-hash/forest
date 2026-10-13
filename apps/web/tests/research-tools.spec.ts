import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

async function project(request: any) {
  const response = await request.post("/api/projects", {
    data: {
      name: `Research tool acceptance ${Date.now()}`,
      mode: "manual",
      budget: { allow_paid: false, seconds: 120 },
      config: { publication_profile: { id: "operational" } },
    },
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function terminal(request: any, runId: string) {
  await expect
    .poll(
      async () =>
        ["completed", "failed", "cancelled", "interrupted", "skipped"].includes(
          (await (await request.get(`/api/runs/${runId}`)).json()).status,
        ),
      { timeout: 60000 },
    )
    .toBeTruthy();
  const result = await (await request.get(`/api/runs/${runId}`)).json();
  expect(result.status, JSON.stringify(result)).toBe("completed");
  return result;
}

test("paired inference uses the uploaded table and retains its exact input artifact", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90000);
  const p = await project(request);
  const csv = "unit,baseline,candidate\na,1,2\na,3,4\nb,4,6\nc,5,8\n";
  try {
    const upload = await request.post(`/api/projects/${p.id}/upload`, {
      multipart: {
        file: {
          name: "paired.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(csv),
        },
      },
    });
    expect(upload.ok()).toBeTruthy();
    const uploaded = await upload.json();
    await page.goto(`/projects/${p.id}/data`);
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
    await page.getByLabel("Improvement direction").selectOption("higher");
    const accepted = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        r.url().endsWith(`/api/projects/${p.id}/statistics/paired`),
    );
    await page
      .getByRole("button", { name: "Run paired inference", exact: true })
      .click();
    const run = await (await accepted).json();
    expect(run.project_id).toBe(p.id);
    await expect(page.getByRole("status")).toContainText(run.id);
    const done = await terminal(request, run.id);
    expect(done.metrics.unit_count).toBe(3);
    expect(done.metrics.observation_count).toBe(4);
    expect(done.metrics.improvement).toBeCloseTo(2);
    expect(done.metrics.baseline_mean).toBeCloseTo(11 / 3);
    expect(done.metrics.candidate_mean).toBeCloseTo(17 / 3);
    const provenance = done.metrics.input_provenance;
    const artifact = await request.get(`/api/projects/${p.id}/download`, {
      params: { path: provenance.artifact_path },
    });
    expect(await artifact.text()).toBe(csv);
    await page
      .getByRole("link", { name: new RegExp(`Inspect actual run ${run.id}`) })
      .click();
    await page.getByText("Recorded metrics", { exact: true }).click();
    await expect(page.locator(".run-output")).toContainText('"unit_count": 3');
    await testInfo.attach("actual-paired-inference", {
      body: JSON.stringify({ run: done, fixture: csv }, null, 2),
      contentType: "application/json",
    });
  } finally {
    await request.delete(`/api/projects/${p.id}`);
  }
});

test("independent CSV integrity checks bind to the actual producer and declared file scope", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(90000);
  const p = await project(request);
  try {
    const graph = await (
      await request.get(`/api/projects/${p.id}/graph`)
    ).json();
    const added = await request.post(`/api/projects/${p.id}/graph/commands`, {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_node",
        params: {
          type: "experiment",
          title: "Actual CSV producer",
          branch_id: graph.branches[0].id,
          config: {
            kind: "experiment",
            command: [
              "python3",
              "-c",
              "from pathlib import Path; import json; Path('observations.csv').write_text('unit,value\\na,1\\nb,2\\n'); Path('metrics.json').write_text(json.dumps({'row_count': 2}))",
            ],
          },
        },
      },
    });
    expect(added.ok()).toBeTruthy();
    const node = (await added.json()).graph.nodes.find(
      (n: any) => n.title === "Actual CSV producer",
    );
    const produced = await (
      await request.post(`/api/nodes/${node.id}/run`, {
        data: { request_id: crypto.randomUUID() },
      })
    ).json();
    await terminal(request, produced.id);
    await page.goto(`/projects/${p.id}/runs`);
    await page
      .getByText("Independent evidence checks", { exact: true })
      .click();
    await expect(page.getByLabel("Producer node")).toContainText(node.id);
    await page.getByLabel("Producer node").selectOption(node.id);
    await page.getByLabel("Declared checks (JSON array)").fill(
      JSON.stringify([
        {
          id: "actual-rows",
          kind: "csv_integrity",
          source: "observations.csv",
          required_columns: ["unit", "value"],
          unique_by: ["unit"],
          numeric_columns: ["value"],
        },
      ]),
    );
    const accepted = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        r.url().endsWith("/api/verification/run"),
    );
    await page
      .getByRole("button", { name: "Submit independent checks", exact: true })
      .click();
    const run = await (await accepted).json();
    await terminal(request, run.id);
    const verdict = await (
      await request.get(`/api/runs/${produced.id}/verification`)
    ).json();
    expect(verdict.verification_status).toBe("accepted");
    expect(verdict.check_scope_paths).toEqual(["observations.csv"]);
    expect(verdict.numerical_scope).toEqual([]);
    await testInfo.attach("actual-source-bound-check", {
      body: JSON.stringify(
        { producerRunId: produced.id, verificationRunId: run.id, verdict },
        null,
        2,
      ),
      contentType: "application/json",
    });
  } finally {
    await request.delete(`/api/projects/${p.id}`);
  }
});

test("repository preparation retains the actual GitHub commit in its run manifest", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(120000);
  const p = await project(request);
  try {
    await page.goto(`/projects/${p.id}/files`);
    await page.getByText("Repository input", { exact: true }).click();
    await page
      .getByLabel("GitHub repository URL")
      .fill("https://github.com/octocat/Hello-World");
    const accepted = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        r.url().endsWith(`/api/projects/${p.id}/repositories/clone`),
    );
    await page
      .getByRole("button", { name: "Prepare repository", exact: true })
      .click();
    const run = await (await accepted).json();
    await terminal(request, run.id);
    const manifest = await (
      await request.get(`/api/runs/${run.id}/repository`)
    ).json();
    expect(manifest.run_id).toBe(run.id);
    expect(manifest.repository.commit).toMatch(/^[a-f0-9]{40}$/);
    expect(manifest.repository.url).toBe(
      "https://github.com/octocat/Hello-World.git",
    );
    const files = await (
      await request.get(`/api/projects/${p.id}/files`, {
        params: { limit: 500 },
      })
    ).json();
    expect(
      files.files.some((f: any) => f.path.endsWith("/source/README")),
    ).toBeTruthy();
    await testInfo.attach("actual-repository-manifest", {
      body: JSON.stringify(manifest, null, 2),
      contentType: "application/json",
    });
  } finally {
    await request.delete(`/api/projects/${p.id}`);
  }
});

test("figure render and exported SVG and PDF use the actual numerical run", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(120000);
  const p = await project(request);
  try {
    const sample = JSON.parse(
      await readFile(
        new URL("../../../examples/cli/node.json", import.meta.url),
        "utf8",
      ),
    );
    const graph = await (
      await request.get(`/api/projects/${p.id}/graph`)
    ).json();
    const added = await request.post(`/api/projects/${p.id}/graph/commands`, {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_node",
        params: { ...sample, branch_id: graph.branches[0].id },
      },
    });
    expect(added.ok()).toBeTruthy();
    const node = (await added.json()).graph.nodes.find(
      (n: any) => n.title === sample.title,
    );
    const run = await (
      await request.post(`/api/nodes/${node.id}/run`, {
        data: { request_id: crypto.randomUUID() },
      })
    ).json();
    const measured = await terminal(request, run.id);
    const created = await request.post("/api/figures", {
      data: {
        project_id: p.id,
        title: "Computed trapezoidal integral",
        data: {
          kind: "bar",
          run_ids: [run.id],
          metric: "integral",
          data: {
            rows: [
              {
                dataset: "x² on [0, 1]",
                method: "Trapezoidal, 5,000 intervals",
                integral: measured.metrics.integral,
              },
            ],
          },
          style: {
            metric: "integral",
            width: 7,
            height: 4,
            xlabel: "Computed result",
            ylabel: "Integral",
          },
          caption:
            "Trapezoidal integration of x² over [0, 1], 5,000 intervals.",
        },
      },
    });
    expect(created.ok()).toBeTruthy();
    const figure = await created.json();
    await page.goto(`/projects/${p.id}/figures`);
    const accepted = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        r.url().endsWith(`/api/figures/${figure.id}/render`),
    );
    await page
      .getByRole("button", { name: "Render figure", exact: true })
      .click();
    const render = await (await accepted).json();
    await terminal(request, render.id);
    await page.reload();
    const img = page.locator(".figure-image img");
    await expect(img).toBeVisible();
    await expect
      .poll(() =>
        img.evaluate(
          (el: HTMLImageElement) => el.complete && el.naturalWidth > 0,
        ),
      )
      .toBeTruthy();
    for (const format of ["svg", "pdf"]) {
      const downloaded = page.waitForEvent("download");
      await page
        .locator(".figure-canvas")
        .getByRole("button", { name: format.toUpperCase(), exact: true })
        .click();
      const file = await downloaded;
      const bytes = await readFile((await file.path())!);
      if (format === "svg") expect(bytes.toString()).toContain("<svg");
      else expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");
      await file.saveAs(testInfo.outputPath(`actual-figure.${format}`));
    }
    const saved = await (await request.get(`/api/figures/${figure.id}`)).json();
    expect(saved.data.run_ids).toEqual([run.id]);
    await testInfo.attach("actual-figure-source", {
      body: JSON.stringify({ figure: saved, sourceRun: run }, null, 2),
      contentType: "application/json",
    });
  } finally {
    await request.delete(`/api/projects/${p.id}`);
  }
});
