import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

async function createProject(request: APIRequestContext, config = {}) {
  const response = await request.post("/api/projects", {
    data: {
      name: `Requirements browser acceptance ${Date.now()}`,
      goal: "Preserve editable scientific requirements and their original revision history",
      mode: "manual",
      budget: { max_runs: 2, seconds: 120, allow_paid: false },
      config,
    },
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function openRequirements(page: Page, id: string, section: string) {
  await page.goto(`/projects/${id}/overview`);
  await page
    .getByText("Publication requirements & evaluation protocols", {
      exact: true,
    })
    .click();
  await page.getByText(section, { exact: true }).click();
}

test("publication edits preserve unknown fields and concurrent untouched requirements, while a conflict keeps the complete draft", async ({
  page,
  request,
}) => {
  const project = await createProject(request, {
    custom_configuration: { retain: "outside publication profile" },
    publication_profile: {
      id: "full_submission",
      custom_requirement: {
        preserve: ["future", "fields"],
        nested: { threshold: 7 },
      },
    },
  });
  try {
    await openRequirements(page, project.id, "Edit publication requirements");
    const editor = page.getByLabel("Publication requirements JSON");
    await expect(editor).toHaveValue(/custom_requirement/);
    const initial = JSON.parse(await editor.inputValue());
    const local = { ...initial, target: "A specified archival venue" };
    await editor.fill(JSON.stringify(local, null, 2));
    const concurrent = await request.patch(
      `/api/projects/${project.id}/publication`,
      {
        data: { datasets: 4, future_requirement: { retained: true } },
      },
    );
    expect(concurrent.ok()).toBeTruthy();
    const savedResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/projects/${project.id}/publication`) &&
        response.request().method() === "PATCH",
    );
    await page
      .getByRole("button", {
        name: "Save publication requirements",
        exact: true,
      })
      .click();
    const saved = await savedResponse;
    expect(saved.ok()).toBeTruthy();
    expect(saved.request().postDataJSON()).toEqual({ target: local.target });
    const profile = (await saved.json()).profile;
    expect(profile).toMatchObject({
      target: local.target,
      datasets: 4,
      custom_requirement: initial.custom_requirement,
      future_requirement: { retained: true },
    });
    const savedProject = await (
      await request.get(`/api/projects/${project.id}`)
    ).json();
    expect(savedProject.config.custom_configuration).toEqual({
      retain: "outside publication profile",
    });
    await expect(editor).toHaveValue(/future_requirement/);

    const conflictDraft = { ...profile, target: "My retained unsaved venue" };
    await editor.fill(JSON.stringify(conflictDraft, null, 2));
    expect(
      (
        await request.patch(`/api/projects/${project.id}/publication`, {
          data: { target: "Another editor's saved venue" },
        })
      ).ok(),
    ).toBeTruthy();
    await page
      .getByRole("button", {
        name: "Save publication requirements",
        exact: true,
      })
      .click();
    await expect(
      page.locator(".project-requirements .error-box"),
    ).toContainText("changed in: target");
    expect(JSON.parse(await editor.inputValue())).toEqual(conflictDraft);
    await page.reload();
    await page
      .getByText("Publication requirements & evaluation protocols", {
        exact: true,
      })
      .click();
    await page
      .getByText("Edit publication requirements", { exact: true })
      .click();
    expect(JSON.parse(await editor.inputValue())).toEqual(conflictDraft);
    expect(
      (
        await (
          await request.get(`/api/projects/${project.id}/publication`)
        ).json()
      ).profile.target,
    ).toBe("Another editor's saved venue");
    expect(
      await (await request.get(`/api/projects/${project.id}/runs`)).json(),
    ).toEqual([]);
  } finally {
    await request.delete(`/api/projects/${project.id}`).catch(() => {});
  }
});

const definition = {
  research_question:
    "Does the declared candidate improve the paired numerical outcome?",
  hypothesis:
    "The candidate's paired effect exceeds the declared meaningful threshold.",
  baseline: "The fixed declared comparator",
  candidate: "The explicitly declared candidate",
  metric: "Paired mean outcome difference",
  statistical_unit: "Independent subject",
  split_policy: "Hold out confirmation subjects before exploratory selection",
  selection_policy: "Choose parameters using exploratory subjects only",
  decision_rule:
    "Report the paired interval and compare it to the declared threshold",
  argumentative_duty: "effectiveness",
  meaningful_effect: 0.1,
  phase: "exploratory",
  future_definition: { untouched: [1, 2, 3] },
};

test("actual structural protocol validation creates no run; guarded protocol revisions preserve drafts and original history", async ({
  page,
  request,
}) => {
  const project = await createProject(request, {
    publication_profile: { id: "operational" },
  });
  try {
    await openRequirements(
      page,
      project.id,
      "Experiment and evaluation protocol versions",
    );
    const editor = page.getByLabel("Evaluation protocol JSON");
    const draft = {
      name: "Declared paired evaluation",
      definition,
      reason: "Record the initial evaluation decisions",
      classification: "exploratory",
      feedback_run_ids: [],
    };
    await editor.fill(JSON.stringify(draft, null, 2));
    const validationResponse = page.waitForResponse(
      (response) =>
        response
          .url()
          .endsWith(`/api/projects/${project.id}/protocol/validate`) &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", {
        name: "Validate experiment protocol structure",
        exact: true,
      })
      .click();
    const validation = await validationResponse;
    expect(validation.ok()).toBeTruthy();
    expect((await validation.json()).status).toBe("structurally_checked");
    await expect(
      page.getByRole("region", { name: "Protocol structure result" }),
    ).toContainText("Protocol structure only");
    expect(
      await (await request.get(`/api/projects/${project.id}/runs`)).json(),
    ).toEqual([]);

    const firstResponse = page.waitForResponse(
      (response) =>
        response
          .url()
          .endsWith(`/api/projects/${project.id}/evaluation-protocols`) &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Record protocol version", exact: true })
      .click();
    const first = await firstResponse;
    expect(first.ok()).toBeTruthy();
    expect(first.request().postDataJSON()).toMatchObject({
      expected_revision: project.revision,
      expected_protocol_revision: 0,
    });
    expect(first.request().postDataJSON().request_id).toBeTruthy();
    const version1 = await first.json();
    expect(version1.definition).toEqual(definition);
    await expect(editor).toHaveValue(/Record|"reason": ""/);
    const revisedDraft = {
      ...JSON.parse(await editor.inputValue()),
      reason:
        "Raise the meaningful effect after a planning-only reconsideration",
      definition: { ...definition, meaningful_effect: 0.2 },
    };
    await editor.fill(JSON.stringify(revisedDraft, null, 2));
    const latestProject = await (
      await request.get(`/api/projects/${project.id}`)
    ).json();
    expect(
      (
        await request.patch(`/api/projects/${project.id}`, {
          data: {
            expected_revision: latestProject.revision,
            name: `${project.name} changed elsewhere`,
          },
        })
      ).ok(),
    ).toBeTruthy();
    const conflictResponse = page.waitForResponse(
      (response) =>
        response
          .url()
          .endsWith(`/api/projects/${project.id}/evaluation-protocols`) &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Record protocol version", exact: true })
      .click();
    expect((await conflictResponse).status()).toBe(409);
    await expect(
      page.locator(".project-requirements .error-box"),
    ).toContainText("REVISION_CONFLICT");
    expect(JSON.parse(await editor.inputValue())).toEqual(revisedDraft);
    await page.reload();
    await page
      .getByText("Publication requirements & evaluation protocols", {
        exact: true,
      })
      .click();
    await page
      .getByText("Experiment and evaluation protocol versions", { exact: true })
      .click();
    expect(JSON.parse(await editor.inputValue())).toEqual(revisedDraft);
    page.once("dialog", (dialog) => dialog.accept());
    await page
      .getByRole("button", { name: "Reload latest protocol", exact: true })
      .click();
    await expect(editor).toHaveValue(/"meaningful_effect": 0.1/);
    await editor.fill(JSON.stringify(revisedDraft, null, 2));
    const secondResponse = page.waitForResponse(
      (response) =>
        response
          .url()
          .endsWith(`/api/projects/${project.id}/evaluation-protocols`) &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Record protocol version", exact: true })
      .click();
    const second = await secondResponse;
    expect(second.ok()).toBeTruthy();
    const version2 = await second.json();
    expect(version2.protocol_id).toBe(version1.protocol_id);
    expect(version2.revision).toBe(2);
    expect(version2.parent_version_id).toBe(version1.id);
    const versions = await (
      await request.get(`/api/projects/${project.id}/evaluation-protocols`)
    ).json();
    expect(versions).toHaveLength(2);
    expect(versions[0]).toEqual(version1);
    expect(versions[1].definition).toMatchObject({
      meaningful_effect: 0.2,
      future_definition: definition.future_definition,
    });
    expect(
      await (await request.get(`/api/projects/${project.id}/runs`)).json(),
    ).toEqual([]);
  } finally {
    await request.delete(`/api/projects/${project.id}`).catch(() => {});
  }
});

test("real literal writing review applies Unicode source spans to the draft while evidence judgments remain with the author", async ({
  page,
  request,
}) => {
  const project = await createProject(request, {
    publication_profile: { id: "operational" },
  });
  try {
    const originalPaper = await (
      await request.get(`/api/papers/${project.id}`)
    ).json();
    await page.goto(`/projects/${project.id}/paper`);
    const editor = page.locator(".paper-source .monaco-editor");
    await expect(editor).toBeVisible();
    await editor.getByRole("textbox", { name: "Editor content" }).focus();
    await page.keyboard.press("ControlOrMeta+A");
    const source =
      "😀 We may potentially observe an effect. The result is a limited improvement.";
    await page.keyboard.insertText(source);
    await page.getByText("Literal writing review", { exact: true }).click();
    const response = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/projects/${project.id}/writing/review`) &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", {
        name: "Review current draft wording",
        exact: true,
      })
      .click();
    const review = await response;
    expect(review.ok()).toBeTruthy();
    expect(review.request().postDataJSON()).toEqual({ source });
    const report = await review.json();
    expect(report.edits).toHaveLength(2);
    expect(report.edits[0]).toMatchObject({
      start: 5,
      end: 20,
      original: "may potentially",
      replacement: "may",
      requires_evidence_judgment: false,
    });
    await expect(
      page.getByText(
        "Literal phrase matches only; no claim of exhaustive semantic review",
        { exact: true },
      ),
    ).toBeVisible();
    const judgment = page.locator(".paper-layout li").filter({
      has: page.locator("code", { hasText: "limited improvement" }),
    });
    await expect(judgment).toContainText(
      "Requires the author's evidence judgment",
    );
    await expect(
      judgment.getByRole("button", { name: "Apply to draft" }),
    ).toHaveCount(0);
    await page
      .getByRole("button", { name: "Apply to draft", exact: true })
      .click();
    await expect(editor).toContainText("😀 We may observe an effect.");
    await expect(
      page.getByText(
        "The draft changed. Review it again before applying edits.",
        { exact: true },
      ),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Apply to draft", exact: true }),
    ).toBeDisabled();
    const untouched = await (
      await request.get(`/api/papers/${project.id}`)
    ).json();
    expect(untouched.revision).toBe(originalPaper.revision);
    expect(untouched.data.source).toBe(originalPaper.data.source);
    await page
      .locator(".paper-actions")
      .getByRole("button", { name: "Save", exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/papers/${project.id}`)).json()).data
            .source,
      )
      .toBe(
        "😀 We may observe an effect. The result is a limited improvement.",
      );
    expect(
      await (await request.get(`/api/projects/${project.id}/runs`)).json(),
    ).toEqual([]);
  } finally {
    await request.delete(`/api/projects/${project.id}`).catch(() => {});
  }
});

test("fresh protocol reload reads an external version and structural checks become stale when publication scope changes", async ({
  page,
  request,
}) => {
  const project = await createProject(request, {
    publication_profile: { id: "operational" },
  });
  try {
    await openRequirements(
      page,
      project.id,
      "Experiment and evaluation protocol versions",
    );
    const editor = page.getByLabel("Evaluation protocol JSON");
    const draft = {
      name: "Concurrent evaluation",
      definition,
      reason: "Initial planning choices",
      classification: "exploratory",
      feedback_run_ids: [],
    };
    await editor.fill(JSON.stringify(draft, null, 2));
    const recorded = page.waitForResponse(
      (response) =>
        response
          .url()
          .endsWith(`/api/projects/${project.id}/evaluation-protocols`) &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Record protocol version", exact: true })
      .click();
    const original = await (await recorded).json();
    await expect(
      page.getByRole("button", {
        name: "Record protocol version",
        exact: true,
      }),
    ).toBeDisabled();
    const localDraft = {
      ...draft,
      definition: { ...definition, meaningful_effect: 0.2 },
      reason: "My pending changes",
    };
    await editor.fill(JSON.stringify(localDraft, null, 2));
    const currentProject = await (
      await request.get(`/api/projects/${project.id}`)
    ).json();
    const externalResponse = await request.post(
      `/api/projects/${project.id}/evaluation-protocols`,
      {
        data: {
          ...draft,
          protocol_id: original.protocol_id,
          expected_revision: currentProject.revision,
          expected_protocol_revision: original.revision,
          request_id: `external-version-${project.id}`,
          definition: { ...definition, meaningful_effect: 0.3 },
          reason: "An external editor revised the planning threshold",
        },
      },
    );
    expect(externalResponse.ok()).toBeTruthy();
    const external = await externalResponse.json();
    const conflict = page.waitForResponse(
      (response) =>
        response
          .url()
          .endsWith(`/api/projects/${project.id}/evaluation-protocols`) &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Record protocol version", exact: true })
      .click();
    expect((await conflict).status()).toBe(409);
    expect(JSON.parse(await editor.inputValue())).toEqual(localDraft);
    page.once("dialog", (dialog) => dialog.accept());
    await page
      .getByRole("button", { name: "Reload latest protocol", exact: true })
      .click();
    await expect(editor).toHaveValue(/"meaningful_effect": 0.3/);
    const restored = await page.evaluate(
      (id) =>
        JSON.parse(sessionStorage.getItem(`forest-protocol-draft:${id}`)!),
      project.id,
    );
    expect(restored.protocolRevision).toBe(external.revision);
    await expect(
      page.getByRole("combobox", { name: /^Evaluation protocol/ }),
    ).toContainText("r2");
    const finalDraft = {
      ...JSON.parse(await editor.inputValue()),
      definition: { ...definition, meaningful_effect: 0.4 },
      reason: "Review and revise the latest saved definition",
    };
    await editor.fill(JSON.stringify(finalDraft, null, 2));
    const savedResponse = page.waitForResponse(
      (response) =>
        response
          .url()
          .endsWith(`/api/projects/${project.id}/evaluation-protocols`) &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Record protocol version", exact: true })
      .click();
    const saved = await savedResponse;
    expect(saved.ok()).toBeTruthy();
    expect((await saved.json()).parent_version_id).toBe(external.id);
    await expect(
      page.getByRole("button", {
        name: "Record protocol version",
        exact: true,
      }),
    ).toBeDisabled();
    // Validate an edited draft, which is retained during authoritative refresh.
    await editor.fill(`\n${await editor.inputValue()}`);
    await page
      .getByRole("button", {
        name: "Validate experiment protocol structure",
        exact: true,
      })
      .click();
    const structure = page.getByRole("region", {
      name: "Protocol structure result",
    });
    await expect(structure).toContainText("Protocol structure only");
    await expect(structure).not.toContainText("Validate again");
    expect(
      (
        await request.patch(`/api/projects/${project.id}/publication`, {
          data: { id: "full_submission" },
        })
      ).ok(),
    ).toBeTruthy();
    await page
      .getByRole("button", { name: "Refresh audit", exact: true })
      .click();
    await expect(structure).toContainText(
      "The draft or publication requirements changed. Validate again.",
    );
    const history = await (
      await request.get(`/api/projects/${project.id}/evaluation-protocols`)
    ).json();
    expect(history).toHaveLength(3);
    expect(history[0]).toEqual(original);
    expect(history[1]).toEqual(external);
    expect(
      await (await request.get(`/api/projects/${project.id}/runs`)).json(),
    ).toEqual([]);
  } finally {
    await request.delete(`/api/projects/${project.id}`).catch(() => {});
  }
});

test("an accepted protocol version with a lost response replays the same persisted request after reload", async ({
  page,
  request,
}) => {
  const project = await createProject(request, {
    publication_profile: { id: "operational" },
  });
  let lostRequest: Record<string, unknown> | null = null;
  let acceptedVersion: Record<string, unknown> | null = null;
  await page.route(
    `**/api/projects/${project.id}/evaluation-protocols`,
    async (route) => {
      if (route.request().method() !== "POST" || lostRequest)
        return route.continue();
      lostRequest = route.request().postDataJSON();
      const response = await route.fetch();
      expect(response.ok()).toBeTruthy();
      acceptedVersion = await response.json();
      await route.abort("failed");
    },
  );
  try {
    await openRequirements(
      page,
      project.id,
      "Experiment and evaluation protocol versions",
    );
    const editor = page.getByLabel("Evaluation protocol JSON");
    const draft = {
      name: "Replayable evaluation",
      definition,
      reason: "Record a stable original intent",
      classification: "exploratory",
      feedback_run_ids: [],
    };
    await editor.fill(JSON.stringify(draft, null, 2));
    await page
      .getByRole("button", { name: "Record protocol version", exact: true })
      .click();
    await expect(
      page.locator(".project-requirements .error-box"),
    ).toBeVisible();
    expect(lostRequest?.request_id).toBeTruthy();
    expect(
      await (
        await request.get(`/api/projects/${project.id}/evaluation-protocols`)
      ).json(),
    ).toHaveLength(1);
    await page.reload();
    await page
      .getByText("Publication requirements & evaluation protocols", {
        exact: true,
      })
      .click();
    await page
      .getByText("Experiment and evaluation protocol versions", { exact: true })
      .click();
    expect(JSON.parse(await editor.inputValue())).toEqual(draft);
    const replayResponse = page.waitForResponse(
      (response) =>
        response
          .url()
          .endsWith(`/api/projects/${project.id}/evaluation-protocols`) &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Record protocol version", exact: true })
      .click();
    const replay = await replayResponse;
    expect(replay.ok()).toBeTruthy();
    expect(replay.request().postDataJSON()).toEqual(lostRequest);
    expect(await replay.json()).toEqual(acceptedVersion);
    expect(
      await (
        await request.get(`/api/projects/${project.id}/evaluation-protocols`)
      ).json(),
    ).toHaveLength(1);
    expect(
      await (await request.get(`/api/projects/${project.id}/runs`)).json(),
    ).toEqual([]);
  } finally {
    await request.delete(`/api/projects/${project.id}`).catch(() => {});
  }
});
