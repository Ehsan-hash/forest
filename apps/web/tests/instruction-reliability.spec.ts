import { expect, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

async function createProject(request: APIRequestContext, name: string) {
  const response = await request.post("/api/projects", {
    data: {
      name: `${name} ${Date.now()}`,
      mode: "manual",
      goal: "Preserve reviewed instruction intent",
    },
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

test("formal instruction lost-response retry retains one actual saved receipt and request identity", async ({
  page,
  request,
}) => {
  const project = await createProject(request, "Instruction receipt");
  const submissions: Record<string, unknown>[] = [];
  try {
    await page.route(
      `**/api/projects/${project.id}/instructions`,
      async (route) => {
        submissions.push(route.request().postDataJSON());
        if (submissions.length === 1) {
          // Persist on the real backend, then deliberately lose the response.
          const accepted = await route.fetch();
          expect(accepted.ok()).toBeTruthy();
          await route.abort("failed");
        } else await route.continue();
      },
    );
    await page.goto(`/projects/${project.id}/overview`);
    await page
      .getByRole("button", { name: "Send as instruction", exact: true })
      .click();
    const dialog = page.getByRole("dialog", { name: "Send owner instruction" });
    const text =
      "Keep the original saved measurements and inspect their source.";
    await dialog
      .getByRole("textbox", { name: "Instruction", exact: true })
      .fill(text);
    await dialog
      .getByRole("combobox", { name: "Instruction boundary" })
      .selectOption("next_run");
    await dialog.locator("form").evaluate((form: HTMLFormElement) => {
      form.requestSubmit();
      form.requestSubmit();
    });
    await expect(
      dialog.getByText("Failed to fetch", { exact: true }),
    ).toBeVisible();
    await expect(
      dialog.getByRole("textbox", { name: "Instruction", exact: true }),
    ).toHaveValue(text);
    expect(submissions).toHaveLength(1);
    const first = await (
      await request.get(`/api/projects/${project.id}/interventions`)
    ).json();
    expect(first.filter((row: any) => row.intent.text === text)).toHaveLength(
      1,
    );
    await dialog
      .getByRole("button", { name: "Send instruction", exact: true })
      .click();
    await expect(dialog).toHaveCount(0);
    expect(submissions).toHaveLength(2);
    expect(submissions[1]).toEqual(submissions[0]);
    const receipts = await (
      await request.get(`/api/projects/${project.id}/interventions`)
    ).json();
    expect(
      receipts.filter((row: any) => row.intent.text === text),
    ).toHaveLength(1);
    expect(
      receipts.find((row: any) => row.intent.text === text).request_id,
    ).toBe(submissions[0].request_id);
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});

test("editing a failed instruction allocates a new intent while retaining the reviewed revision", async ({
  page,
  request,
}) => {
  const project = await createProject(request, "Changed instruction");
  const submissions: Record<string, unknown>[] = [];
  try {
    await page.route(
      `**/api/projects/${project.id}/instructions`,
      async (route) => {
        submissions.push(route.request().postDataJSON());
        if (submissions.length === 1)
          await route.fulfill({
            status: 500,
            contentType: "application/json",
            body: JSON.stringify({ detail: "Injected instruction failure" }),
          });
        else await route.continue();
      },
    );
    await page.goto(`/projects/${project.id}/overview`);
    await page
      .getByRole("button", { name: "Send as instruction", exact: true })
      .click();
    const dialog = page.getByRole("dialog", { name: "Send owner instruction" });
    const input = dialog.getByRole("textbox", {
      name: "Instruction",
      exact: true,
    });
    await input.fill("Original unsaved instruction");
    await dialog
      .getByRole("button", { name: "Send instruction", exact: true })
      .click();
    await expect(
      dialog.getByText("Injected instruction failure", { exact: true }),
    ).toBeVisible();
    await expect(dialog.getByRole("alert")).toContainText(
      "Injected instruction failure",
    );
    await expect(input).toHaveValue("Original unsaved instruction");
    await input.fill("Changed instruction after the failed request");
    await dialog
      .getByRole("button", { name: "Send instruction", exact: true })
      .click();
    await expect(dialog).toHaveCount(0);
    expect(submissions[1].request_id).not.toBe(submissions[0].request_id);
    expect(submissions[1].expected_revision).toBe(
      submissions[0].expected_revision,
    );
    const receipts = await (
      await request.get(`/api/projects/${project.id}/interventions`)
    ).json();
    expect(
      receipts.some(
        (row: any) => row.intent.text === "Original unsaved instruction",
      ),
    ).toBe(false);
    expect(
      receipts.find((row: any) => row.request_id === submissions[1].request_id)
        .intent.text,
    ).toBe("Changed instruction after the failed request");
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});

test("a concurrent project change requires explicit instruction re-review and preserves the draft", async ({
  page,
  request,
}) => {
  const project = await createProject(request, "Instruction conflict");
  try {
    await page.goto(`/projects/${project.id}/overview`);
    await page
      .getByRole("button", { name: "Send as instruction", exact: true })
      .click();
    const dialog = page.getByRole("dialog", { name: "Send owner instruction" });
    const text =
      "Review the updated project without replacing this instruction.";
    await dialog
      .getByRole("textbox", { name: "Instruction", exact: true })
      .fill(text);
    const graph = await (
      await request.get(`/api/projects/${project.id}/graph`)
    ).json();
    const changed = await request.patch(`/api/projects/${project.id}`, {
      data: {
        expected_revision: graph.revision,
        description: "Changed in another editor",
      },
    });
    expect(changed.ok()).toBeTruthy();
    const current = await changed.json();
    const conflict = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/projects/${project.id}/instructions`),
    );
    await dialog
      .getByRole("button", { name: "Send instruction", exact: true })
      .click();
    expect((await conflict).status()).toBe(409);
    await expect(
      dialog.getByRole("textbox", { name: "Instruction", exact: true }),
    ).toHaveValue(text);
    await expect(
      dialog.getByRole("button", { name: "Send instruction", exact: true }),
    ).toBeDisabled();
    const beforeReview = await (
      await request.get(`/api/projects/${project.id}/interventions`)
    ).json();
    expect(
      beforeReview.some(
        (row: any) => row.kind === "instruction" && row.intent.text === text,
      ),
    ).toBe(false);
    await dialog
      .getByRole("button", { name: "Review current project", exact: true })
      .click();
    await expect(dialog).toContainText(
      `Current project revision r${current.revision}`,
    );
    await expect(
      dialog.getByRole("button", { name: "Send instruction", exact: true }),
    ).toBeDisabled();
    await dialog
      .getByRole("button", {
        name: "Keep instruction and use reviewed revision",
        exact: true,
      })
      .click();
    await expect(
      dialog.getByRole("textbox", { name: "Instruction", exact: true }),
    ).toHaveValue(text);
    await dialog
      .getByRole("button", { name: "Send instruction", exact: true })
      .click();
    await expect(dialog).toHaveCount(0);
    const receipts = await (
      await request.get(`/api/projects/${project.id}/interventions`)
    ).json();
    const saved = receipts.find(
      (row: any) => row.kind === "instruction" && row.intent.text === text,
    );
    expect(saved.project_id).toBe(project.id);
    expect(saved.observed_revision).toBe(current.revision);
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});

test("run instruction choices use only current active Agent state and force next_request", async ({
  page,
  request,
}) => {
  // Run states are fixtures: this proves UI availability, not actual worker execution.
  const project = await createProject(request, "Instruction run choices");
  let agentStatus = "running";
  const fixture = (
    id: string,
    kind: string,
    status: string,
    projectId = project.id,
  ) => ({
    id,
    kind,
    status,
    project_id: projectId,
    node_id: null,
    branch_id: null,
    config: {},
    node_revision: 0,
    created_at: "2026-10-13T00:00:00Z",
    started_at: null,
    finished_at: null,
    exit_code: null,
    pid: null,
    error: null,
    metrics: {},
    output_path: "",
  });
  try {
    await page.route(`**/api/projects/${project.id}/runs*`, (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify([
          fixture("selected-agent", "agent", agentStatus),
          fixture("completed-agent", "agent", "completed"),
          fixture("active-command", "command", "running"),
          fixture("foreign-agent", "agent", "running", "another-project"),
        ]),
      }),
    );
    await page.goto(`/projects/${project.id}/overview`);
    await page
      .getByRole("button", { name: "Send as instruction", exact: true })
      .click();
    const dialog = page.getByRole("dialog", { name: "Send owner instruction" });
    await dialog
      .getByRole("textbox", { name: "Instruction", exact: true })
      .fill("Inspect the next request context");
    const boundary = dialog.getByRole("combobox", {
      name: "Instruction boundary",
    });
    await boundary.selectOption("next_run");
    await dialog
      .getByRole("combobox", { name: "Instruction scope" })
      .selectOption("run");
    await expect(boundary).toHaveValue("next_request");
    await expect(boundary.locator('option[value="next_run"]')).toHaveAttribute(
      "disabled",
      "",
    );
    const target = dialog.getByRole("combobox", { name: "Instruction run" });
    await expect(target.locator("option")).toHaveCount(2);
    await expect(target.locator("option[value=selected-agent]")).toHaveCount(1);
    await target.selectOption("selected-agent");
    await expect(
      dialog.getByRole("button", { name: "Send instruction", exact: true }),
    ).toBeEnabled();
    agentStatus = "completed";
    await page.evaluate(() =>
      window.dispatchEvent(new Event("forest-refresh")),
    );
    await expect(target.locator('option[value="selected-agent"]')).toHaveText(
      /Unavailable run/,
    );
    await expect(
      dialog.getByRole("button", { name: "Send instruction", exact: true }),
    ).toBeDisabled();
    await expect(
      dialog.getByRole("textbox", { name: "Instruction", exact: true }),
    ).toHaveValue("Inspect the next request context");
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});

test("an older-history refresh cannot append into another project or a later visit to the same project", async ({
  page,
  request,
}) => {
  // History rows and a delayed response are fixtures; project navigation is real.
  const first = await createProject(request, "First history project");
  const second = await createProject(request, "Second history project");
  const receipt = (index: number, historical = false) => ({
    id: `receipt-${index}`,
    project_id: first.id,
    request_id: `request-${index}`,
    actor: "owner",
    kind: "instruction",
    observed_revision: 0,
    applied_revision: 1,
    intent: {
      text: historical ? `OLD HISTORY ${index}` : `Current receipt ${index}`,
    },
    impact: {},
    status: "accepted",
    accepted_at: "2026-10-13T00:00:00Z",
    applied_at: null,
    created_at: "2026-10-13T00:00:00Z",
    updated_at: "2026-10-13T00:00:00Z",
    effects: [],
  });
  let delayRefresh = false;
  let release = () => {};
  let started = () => {};
  let finished = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const delayedStarted = new Promise<void>((resolve) => {
    started = resolve;
  });
  const delayedFinished = new Promise<void>((resolve) => {
    finished = resolve;
  });
  try {
    await page.route(
      `**/api/projects/${first.id}/interventions*`,
      async (route) => {
        const older = new URL(route.request().url()).searchParams.has("before");
        if (older && delayRefresh) {
          started();
          await gate;
          await route
            .fulfill({
              contentType: "application/json",
              body: JSON.stringify(
                Array.from({ length: 20 }, (_, index) =>
                  receipt(index + 21, true),
                ),
              ),
            })
            .catch(() => {});
          finished();
        } else
          await route.fulfill({
            contentType: "application/json",
            body: JSON.stringify(
              Array.from({ length: older ? 20 : 21 }, (_, index) =>
                receipt(index + (older ? 21 : 0), older),
              ),
            ),
          });
      },
    );
    await page.goto(`/projects/${first.id}/overview`);
    await page
      .getByRole("button", { name: "Load older records", exact: true })
      .click();
    await expect(
      page.getByText("OLD HISTORY 21", { exact: true }),
    ).toBeVisible();
    delayRefresh = true;
    await page.evaluate(() =>
      window.dispatchEvent(new Event("forest-refresh")),
    );
    await delayedStarted;
    await page.goto(`/projects/${second.id}/overview`);
    await expect(page.getByText("OLD HISTORY 21", { exact: true })).toHaveCount(
      0,
    );
    await page.goto(`/projects/${first.id}/overview`);
    await expect(
      page.getByText("Current receipt 0", { exact: true }),
    ).toBeVisible();
    release();
    await delayedFinished;
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await expect(page.getByText("OLD HISTORY 21", { exact: true })).toHaveCount(
      0,
    );
  } finally {
    release();
    await request.delete(`/api/projects/${first.id}`);
    await request.delete(`/api/projects/${second.id}`);
  }
});

test("failed and stale decision answers retain the edited action and reason", async ({
  page,
  request,
}) => {
  // Decision states/answers are fixtures; no Agent request or tool is executed.
  const project = await createProject(request, "Decision draft");
  let status = "pending";
  let answers = 0;
  const decision = () => ({
    id: "reviewed-decision",
    project_id: project.id,
    run_id: "exact-reviewed-run",
    action_id: "action-one",
    attempt_id: null,
    observed_revision: 0,
    proposed: {
      tool: "write_file",
      arguments: { path: "note.txt", content: "Original" },
    },
    status,
    answer: {},
    answered_at: null,
    consumed_at: null,
    created_at: "2026-10-13T00:00:00Z",
    updated_at: "2026-10-13T00:00:00Z",
    run_status: "waiting_input",
    can_resume: false,
  });
  try {
    await page.route(`**/api/projects/${project.id}/decisions*`, (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify([decision()]),
      }),
    );
    await page.route("**/api/decisions/reviewed-decision/answer", (route) => {
      answers += 1;
      if (answers === 1)
        return route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Injected decision failure" }),
        });
      status = "stale";
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(decision()),
      });
    });
    await page.goto(`/projects/${project.id}/overview`);
    await page
      .getByRole("button", { name: "Review action", exact: true })
      .click();
    const dialog = page.getByRole("dialog", { name: "Review pending action" });
    await dialog.getByRole("combobox").selectOption("edit");
    const edited = JSON.stringify({
      tool: "write_file",
      arguments: { path: "note.txt", content: "Retained edit" },
    });
    await dialog.getByLabel("Edited action").fill(edited);
    await dialog
      .getByLabel("Decision reason")
      .fill("Retained scientific reason");
    await dialog.locator("form").evaluate((form: HTMLFormElement) => {
      form.requestSubmit();
      form.requestSubmit();
    });
    await expect(
      dialog.getByText("Injected decision failure", { exact: true }),
    ).toBeVisible();
    await expect(dialog.getByRole("alert")).toContainText(
      "Injected decision failure",
    );
    expect(answers).toBe(1);
    await expect(dialog.getByLabel("Edited action")).toHaveValue(edited);
    await expect(dialog.getByLabel("Decision reason")).toHaveValue(
      "Retained scientific reason",
    );
    await dialog
      .getByRole("button", { name: "Save decision and continue", exact: true })
      .click();
    await expect(dialog.getByText(/this action is stale/)).toBeVisible();
    await expect(dialog.getByLabel("Edited action")).toHaveValue(edited);
    await expect(dialog.getByLabel("Decision reason")).toHaveValue(
      "Retained scientific reason",
    );
    await expect(
      dialog.getByRole("button", {
        name: "Save decision and continue",
        exact: true,
      }),
    ).toBeDisabled();
    await expect(
      dialog.getByRole("link", { name: "Open this run", exact: true }),
    ).toHaveAttribute(
      "href",
      `/projects/${project.id}/runs?run=exact-reviewed-run`,
    );
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});
