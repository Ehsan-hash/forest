import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

async function fixture(request: APIRequestContext) {
  const created = await request.post("/api/projects", {
    data: {
      name: `Analysis browser fixture ${Date.now()}`,
      goal: "Inspect saved frontend analysis context; no scientific claim.",
      mode: "manual",
      budget: { seconds: 120, allow_paid: false },
      config: {
        provider_selection_required: true,
        publication_profile: { id: "operational" },
      },
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  const add = async (title: string) => {
    const before = await (
      await request.get(`/api/projects/${project.id}/graph`)
    ).json();
    const receipt = await request.post(
      `/api/projects/${project.id}/graph/commands`,
      {
        data: {
          request_id: crypto.randomUUID(),
          expected_revision: before.revision,
          operation: "add_node",
          targets: [],
          run: false,
          params: {
            type: "implementation",
            title,
            instructions: `${title} saved instructions`,
            position: { x: title.includes("Alpha") ? 120 : 460, y: 120 },
            config: {
              kind: "command",
              command: ["python3", "-c", "print('fixture')"],
              custom_unknown: { preserved: true },
            },
          },
        },
      },
    );
    expect(receipt.ok()).toBeTruthy();
    return (await receipt.json()).graph.nodes.find(
      (node: any) => node.title === title,
    );
  };
  const alpha = await add("Alpha analysis node"),
    beta = await add("Beta analysis node");
  return { project, alpha, beta };
}
const pane = (page: any) => page.locator(".research-analysis-panel");
const context = (page: any) =>
  pane(page).getByRole("region", { name: "Saved analysis context" });
function protocolRun(projectId: string, id: string) {
  return {
    id,
    project_id: projectId,
    node_id: null,
    branch_id: null,
    kind: "research_analysis",
    status: "queued",
    created_at: "2026-10-13T12:00:00Z",
    config: {},
    metrics: {},
  };
}
function reportFixture(
  projectId: string,
  node: any,
  revision: number,
  id: string,
) {
  return {
    run_id: id,
    status: "completed",
    error: null,
    artifact_error: null,
    snapshot: {
      project_revision: revision,
      fingerprint: "protocol-fixture-original",
    },
    current: true,
    stale_reasons: [],
    report: {
      summary:
        "PROTOCOL FIXTURE: saved report rendering, not AI quality or scientific verification.",
      experiments: [
        {
          node_id: node.id,
          run_ids: [],
          what_was_done: "Saved command intent",
          result: "No execution claimed",
          problems: "Not executed",
        },
      ],
      findings: [
        {
          title: "Inspect saved configuration",
          detail: "Protocol fixture references the saved node.",
          severity: "info",
          evidence_label: "REPORTED",
          node_ids: [node.id],
          run_ids: [],
          citation_ids: ["fixture-citation-full-id"],
        },
      ],
      recommendations: [
        {
          title: "Read source metadata",
          action: "Read the cited abstract",
          rationale: "Reading scope is metadata/abstract",
          priority: "medium",
          kind: "reading",
          node_ids: [],
          run_ids: [],
          citation_ids: ["fixture-citation-full-id"],
        },
      ],
      limitations: ["Authored frontend protocol fixture; no model call."],
      coverage: { node_ids: [node.id], run_ids: [], resource_ids: [] },
    },
    citations: [
      {
        id: "fixture-citation-full-id",
        title: `Explicit metadata fixture ${projectId}`,
        authors: ["Fixture author"],
        year: 2026,
        source: "crossref",
        read_scope: "metadata/abstract",
        url: "https://example.org/analysis-fixture",
        abstract: "Authored browser regression metadata.",
      },
    ],
    literature_searches: [
      { query: "fixture", source: "crossref", status: "not_run", count: 1 },
    ],
    artifacts: {
      context: `runs/${id}/analysis_input.json`,
      report: `runs/${id}/research_analysis.json`,
    },
  };
}

test("actual analysis preview follows full node/path URLs and retains independent drafts without model calls", async ({
  page,
  request,
}) => {
  const { project, alpha, beta } = await fixture(request);
  for (const change of [
    {
      operation: "edit_node",
      targets: [alpha.id],
      params: {
        inputs: [{ node_id: beta.id, path: "saved-source-fixture.txt" }],
      },
    },
    {
      operation: "add_dependency",
      targets: [],
      params: { source: alpha.id, target: beta.id, relation: "supports" },
    },
  ]) {
    const graph = await (
      await request.get(`/api/projects/${project.id}/graph`)
    ).json();
    const changed = await request.post(
      `/api/projects/${project.id}/graph/commands`,
      {
        data: {
          request_id: crypto.randomUUID(),
          expected_revision: graph.revision,
          ...change,
          run: false,
        },
      },
    );
    expect(changed.ok()).toBeTruthy();
  }
  let posts = 0;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      /research\/analysis$/.test(request.url())
    )
      posts++;
  });
  try {
    const before = await (
      await request.get(`/api/projects/${project.id}/graph`)
    ).json();
    await page.goto(
      `/projects/${project.id}/workspace?analysis=1&analysis_scope=node&node=${alpha.id}&demo=1`,
    );
    await expect(
      context(page).getByText(alpha.id, { exact: true }).first(),
    ).toBeVisible();
    await context(page)
      .getByText("Paths, nodes and dependency details", { exact: true })
      .click();
    await context(page)
      .getByText(/^Alpha analysis node · r\d+$/)
      .click();
    const alphaDetails = context(page)
      .locator("details")
      .filter({
        has: page.locator(":scope > summary", {
          hasText: /^Alpha analysis node · r\d+$/,
        }),
      });
    const alphaDependencies = alphaDetails.locator(
      "h4 + .analysis-bound-references",
    );
    await expect(alphaDependencies.nth(0).getByRole("link")).toContainText(
      beta.id,
    );
    await expect(alphaDependencies.nth(1).getByRole("link")).toHaveCount(0);
    await expect(
      pane(page).getByRole("button", { name: "Request AI analysis" }),
    ).toBeDisabled();
    await pane(page)
      .getByLabel("Analysis question")
      .fill("Retained Alpha question");
    await expect(page.locator("#node-inspector")).toBeHidden();
    await pane(page).getByLabel("Analysis node").selectOption(beta.id);
    await expect(
      context(page).getByText(beta.id, { exact: true }).first(),
    ).toBeVisible();
    await context(page)
      .getByText("Paths, nodes and dependency details", { exact: true })
      .click();
    await context(page)
      .getByText(/^Beta analysis node · r\d+$/)
      .click();
    const betaDetails = context(page)
      .locator("details")
      .filter({
        has: page.locator(":scope > summary", {
          hasText: /^Beta analysis node · r\d+$/,
        }),
      });
    const betaDependencies = betaDetails.locator(
      "h4 + .analysis-bound-references",
    );
    await expect(betaDependencies.nth(0).getByRole("link")).toHaveCount(0);
    await expect(betaDependencies.nth(1).getByRole("link")).toContainText(
      alpha.id,
    );
    await expect(pane(page).getByLabel("Analysis question")).toHaveValue("");
    await pane(page)
      .getByLabel("Analysis question")
      .fill("Retained Beta question");
    await page.goBack();
    await expect(pane(page).getByLabel("Analysis question")).toHaveValue(
      "Retained Alpha question",
    );
    await page.goForward();
    await page.reload();
    await expect(pane(page).getByLabel("Analysis question")).toHaveValue(
      "Retained Beta question",
    );
    await pane(page).getByRole("tab", { name: "Project", exact: true }).click();
    await expect(
      pane(page).getByRole("tab", { name: "Project", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await page
      .locator(".react-flow__node")
      .filter({ hasText: "Alpha analysis node" })
      .click();
    await expect(
      pane(page).getByRole("tab", { name: "Node", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await expect(pane(page).getByLabel("Analysis question")).toHaveValue(
      "Retained Alpha question",
    );
    await pane(page).getByRole("tab", { name: "Path", exact: true }).click();
    await pane(page).getByLabel("Analysis path").selectOption(alpha.branch_id);
    await expect(
      context(page).getByText(alpha.branch_id, { exact: true }).first(),
    ).toBeVisible();
    await page.reload();
    await expect(pane(page).getByLabel("Analysis path")).toHaveValue(
      alpha.branch_id,
    );
    expect(posts).toBe(0);
    expect(
      await (await request.get(`/api/projects/${project.id}/graph`)).json(),
    ).toEqual(before);
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});

test("uncertain analysis transport replays original UUID/fingerprint and preserves edits made before acknowledgment", async ({
  page,
  request,
}) => {
  const { project, alpha } = await fixture(request);
  const actual = await (
    await request.get(
      `/api/projects/${project.id}/research/analysis/context?scope=node&node_id=${alpha.id}`,
    )
  ).json();
  const requests: any[] = [],
    runId = crypto.randomUUID();
  let release!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route(
    `**/api/projects/${project.id}/research/analysis/context?*`,
    async (route) => {
      const response = await route.fetch();
      const value = await response.json();
      await route.fulfill({
        response,
        json: {
          ...value,
          provider: {
            available: true,
            id: "explicit-ui-protocol-fixture",
            name: "Authored browser fixture",
            model: "no model call",
            kind: "fixture",
          },
        },
      });
    },
  );
  await page.route(
    `**/api/projects/${project.id}/research/analysis`,
    async (route) => {
      if (route.request().method() === "GET") {
        await route.continue();
        return;
      }
      requests.push(route.request().postDataJSON());
      if (requests.length === 1) {
        await route.abort("failed");
        return;
      }
      await held;
      await route.fulfill({ json: protocolRun(project.id, runId) });
    },
  );
  await page.route(`**/api/runs/${runId}/research-analysis`, (route) =>
    route.fulfill({
      json: {
        run_id: runId,
        status: "queued",
        report: null,
        snapshot: actual.snapshot,
        citations: [],
        literature_searches: [],
        current: true,
        stale_reasons: [],
        artifacts: {},
      },
    }),
  );
  try {
    await page.goto(
      `/projects/${project.id}/workspace?analysis=1&analysis_scope=node&node=${alpha.id}`,
    );
    await pane(page)
      .getByLabel("Analysis question")
      .fill("Original submission question");
    await pane(page)
      .getByRole("button", { name: "Request AI analysis" })
      .click();
    await expect(
      pane(page).getByText("This submission identity is retained", {
        exact: false,
      }),
    ).toBeVisible();
    await pane(page)
      .getByRole("button", { name: "Request AI analysis" })
      .click();
    await expect.poll(() => requests.length).toBe(2);
    await pane(page)
      .getByLabel("Analysis question")
      .fill("Later text after submit");
    release();
    await expect(
      pane(page)
        .locator(".analysis-run-receipt")
        .getByText(runId, { exact: true }),
    ).toBeVisible();
    await expect(pane(page).getByLabel("Analysis question")).toHaveValue(
      "Later text after submit",
    );
    expect(requests[1]).toEqual(requests[0]);
    expect(requests[0].expected_fingerprint).toBe(actual.snapshot.fingerprint);
    expect(requests[0].expected_revision).toBe(
      actual.snapshot.project_revision,
    );
    await page.reload();
    await expect(pane(page).getByLabel("Analysis question")).toHaveValue(
      "Later text after submit",
    );
    expect(
      (await (await request.get(`/api/projects/${project.id}/runs`)).json())
        .length,
    ).toBe(0);
  } finally {
    release();
    await request.delete(`/api/projects/${project.id}`);
  }
});

test("rapid node selection waits for matching actual focus before initializing the new draft", async ({
  page,
  request,
}) => {
  const { project, alpha, beta } = await fixture(request);
  let release!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route(
    `**/api/projects/${project.id}/research/analysis/context?*`,
    async (route) => {
      if (
        new URL(route.request().url()).searchParams.get("node_id") === beta.id
      )
        await held;
      await route.continue();
    },
  );
  try {
    await page.goto(
      `/projects/${project.id}/workspace?analysis=1&analysis_scope=node&node=${alpha.id}`,
    );
    await pane(page)
      .getByLabel("Analysis question")
      .fill("Alpha independent question");
    await pane(page).getByLabel("Analysis node").selectOption(beta.id);
    await expect(page).toHaveURL(new RegExp(`node=${beta.id}`));
    await expect(pane(page).getByLabel("Analysis question")).toHaveCount(0);
    release();
    await expect(
      context(page).getByText(beta.id, { exact: true }).first(),
    ).toBeVisible();
    await expect(pane(page).getByLabel("Analysis question")).toHaveValue("");
    await pane(page).getByLabel("Analysis node").selectOption(alpha.id);
    await expect(pane(page).getByLabel("Analysis question")).toHaveValue(
      "Alpha independent question",
    );
  } finally {
    release();
    await request.delete(`/api/projects/${project.id}`);
  }
});

test("completed report refreshes staleness, exports full IDs, explicitly imports citations and hides changed artifacts", async ({
  page,
  request,
}) => {
  const { project, alpha } = await fixture(request);
  const savedGraph = await (
    await request.get(`/api/projects/${project.id}/graph`)
  ).json();
  const runId = crypto.randomUUID(),
    result = reportFixture(project.id, alpha, savedGraph.revision, runId);
  let detailReads = 0;
  await page.route(`**/api/projects/${project.id}/research/analysis`, (route) =>
    route.fulfill({
      json: {
        runs: [
          {
            id: runId,
            scope: "project",
            status: "completed",
            created_at: "2026-10-13T12:00:00Z",
            question: "Authored frontend protocol fixture",
            project_revision: savedGraph.revision,
            report_available: true,
          },
        ],
      },
    }),
  );
  await page.route(`**/api/runs/${runId}/research-analysis`, (route) => {
    detailReads++;
    return route.fulfill({ json: result });
  });
  try {
    await page.goto(`/projects/${project.id}/workspace?analysis=1`);
    const report = pane(page).getByRole("article", {
      name: "Saved research analysis",
    });
    await expect(
      report.getByText(result.report.summary, { exact: true }),
    ).toBeVisible();
    await expect(
      report.getByText("crossref · Read scope: metadata/abstract", {
        exact: true,
      }),
    ).toBeVisible();
    const download = page.waitForEvent("download");
    await report.getByRole("button", { name: "Export JSON" }).click();
    const exported = await download;
    const path = await exported.path();
    expect(JSON.parse(await readFile(path!, "utf8"))).toMatchObject({
      project_id: project.id,
      run_id: runId,
      snapshot: result.snapshot,
      report: result.report,
    });
    await report
      .getByRole("button", { name: "Save citation to library" })
      .click();
    await expect(
      report.getByText("Saved library record", { exact: false }),
    ).toBeVisible();
    const papers = await (
      await request.get(`/api/library?project_id=${project.id}`)
    ).json();
    expect(papers).toHaveLength(1);
    expect(papers[0].data.reading_scope).toBe("metadata/abstract");
    expect(papers[0].data.origin.run_id).toBe(runId);
    expect(
      await (await request.get(`/api/projects/${project.id}/graph`)).json(),
    ).toEqual(savedGraph);
    const before = detailReads;
    result.current = false;
    result.stale_reasons = ["Recorded source changed"];
    await page.evaluate(() =>
      window.dispatchEvent(new Event("forest-refresh")),
    );
    await expect(
      report.getByText("The saved analysis context has changed.", {
        exact: false,
      }),
    ).toBeVisible();
    expect(detailReads).toBeGreaterThan(before);
    result.artifact_error = "The report file changed after analysis" as any;
    result.report = null as any;
    await page.evaluate(() =>
      window.dispatchEvent(new Event("forest-refresh")),
    );
    await expect(
      pane(page).getByText("The report file changed after analysis", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(report).toHaveCount(0);
    await expect(
      pane(page).getByRole("button", { name: "Export JSON" }),
    ).toHaveCount(0);
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});

test("compact shared pane traps focus and preserves analysis drafts across Paper and node editing", async ({
  page,
  request,
}) => {
  const { project, alpha } = await fixture(request);
  try {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(
      `/projects/${project.id}/workspace?analysis=1&analysis_scope=node&node=${alpha.id}`,
    );
    const dialog = page.getByRole("dialog", {
      name: "Research analysis panel",
      exact: true,
    });
    await expect(dialog).toBeVisible();
    await expect(page.locator(".workspace-primary")).toHaveAttribute(
      "inert",
      "",
    );
    await pane(page)
      .getByLabel("Analysis question")
      .fill("Compact retained question");
    await page.locator("#workspace-reading-tabs-tab-paper").click();
    await expect(
      page.getByRole("dialog", { name: "Manuscript panel", exact: true }),
    ).toBeVisible();
    await page.locator("#workspace-reading-tabs-tab-analysis").click();
    await expect(pane(page).getByLabel("Analysis question")).toHaveValue(
      "Compact retained question",
    );
    await page.getByRole("button", { name: "Close analysis panel" }).focus();
    await page.keyboard.press("Tab");
    expect(
      await page.evaluate(
        () => !!document.activeElement?.closest(".workspace-reading-panel"),
      ),
    ).toBeTruthy();
    await pane(page)
      .getByRole("button", { name: "Edit selected node" })
      .click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByLabel("Node title")).toHaveValue(alpha.title);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});

test("actual same-revision source changes reject the retained analysis fingerprint until explicit context review", async ({
  page,
  request,
}) => {
  const { project, alpha } = await fixture(request);
  const original = await (
    await request.get(
      `/api/projects/${project.id}/research/analysis/context?scope=node&node_id=${alpha.id}`,
    )
  ).json();
  const submitted: any[] = [];
  await page.route(
    `**/api/projects/${project.id}/research/analysis/context?*`,
    async (route) => {
      const response = await route.fetch();
      const value = await response.json();
      await route.fulfill({
        response,
        json: {
          ...value,
          provider: {
            available: true,
            id: "ui-enablement-fixture-no-model",
            name: "Browser enablement fixture",
            model: "no model call",
            kind: "fixture",
          },
        },
      });
    },
  );
  page.on("request", (call) => {
    if (
      call.method() === "POST" &&
      call.url().endsWith(`/api/projects/${project.id}/research/analysis`)
    )
      submitted.push(call.postDataJSON());
  });
  try {
    await page.goto(
      `/projects/${project.id}/workspace?analysis=1&analysis_scope=node&node=${alpha.id}`,
    );
    await pane(page)
      .getByLabel("Analysis question")
      .fill("Question retains original source intent");
    const imported = await request.post("/api/library/import", {
      data: {
        project_id: project.id,
        paper: {
          title: `Actual source refresh fixture ${Date.now()}`,
          source: "browser-regression",
          reading_scope: "metadata",
        },
      },
    });
    expect(imported.ok()).toBeTruthy();
    const changed = await (
      await request.get(
        `/api/projects/${project.id}/research/analysis/context?scope=node&node_id=${alpha.id}`,
      )
    ).json();
    expect(changed.snapshot.project_revision).toBe(
      original.snapshot.project_revision,
    );
    expect(changed.snapshot.fingerprint).not.toBe(
      original.snapshot.fingerprint,
    );
    await page.evaluate(() =>
      window.dispatchEvent(new Event("forest-refresh")),
    );
    await expect(
      pane(page).getByText(
        "Context changed; this draft retains its original revision.",
        { exact: true },
      ),
    ).toBeVisible();
    const rejection = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response
          .url()
          .endsWith(`/api/projects/${project.id}/research/analysis`),
    );
    await pane(page)
      .getByRole("button", { name: "Request AI analysis" })
      .click();
    const rejected = await rejection;
    expect(rejected.status()).toBe(409);
    expect((await rejected.json()).detail.code).toBe("CONTEXT_CHANGED");
    expect(submitted[0].expected_fingerprint).toBe(
      original.snapshot.fingerprint,
    );
    await expect(pane(page).getByLabel("Analysis question")).toHaveValue(
      "Question retains original source intent",
    );
    expect(
      (await (await request.get(`/api/projects/${project.id}/runs`)).json())
        .length,
    ).toBe(0);
    await pane(page)
      .getByRole("button", { name: "Use current context" })
      .click();
    await expect(
      pane(page).getByText(
        "Context changed; this draft retains its original revision.",
        { exact: true },
      ),
    ).toHaveCount(0);
    const stored = await page.evaluate(
      ({ projectId, nodeId }) =>
        JSON.parse(
          sessionStorage.getItem(
            `forest-analysis-draft:${JSON.stringify([projectId, "node", nodeId])}`,
          )!,
        ),
      { projectId: project.id, nodeId: alpha.id },
    );
    expect(stored.baseFingerprint).toBe(changed.snapshot.fingerprint);
    expect(stored.intent).toBeUndefined();
    expect(stored.question).toBe("Question retains original source intent");
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});
