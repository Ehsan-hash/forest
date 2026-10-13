import { readFile, writeFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

const initialSource =
  "\\documentclass{article}\n\\begin{document}\nManuscript compilation smoke.\n\\end{document}\n";

test.beforeEach(async ({ page }) => {
  page.on("pageerror", (error) =>
    console.log(`Manuscript browser error: ${error.message}`),
  );
  page.on("console", (message) => {
    if (message.type() === "error")
      console.log(`Manuscript console: ${message.text()}`);
  });
});

test("manuscript draft keeps source, references, layout, instruction and revision through panel/full-page/reload", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  const created = await request.post("/api/projects", {
    data: {
      name: `Manuscript shared draft ${Date.now()}`,
      goal: "Preserve exact editing intent across research surfaces",
      mode: "manual",
      budget: { max_runs: 2, seconds: 120, allow_paid: false },
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  try {
    const saved = await (await request.get(`/api/papers/${project.id}`)).json();
    await page.goto(`/projects/${project.id}/workspace?paper=1`);
    const sourceEditor = page.locator(".paper-source .monaco-editor");
    await expect(sourceEditor).toBeVisible();
    await sourceEditor.getByRole("textbox", { name: "Editor content" }).focus();
    await page.keyboard.press("ControlOrMeta+A");
    await page.keyboard.insertText(`${initialSource}% retained source draft\n`);
    await page.getByRole("tab", { name: "references.bib" }).click();
    await sourceEditor.getByRole("textbox", { name: "Editor content" }).focus();
    await page.keyboard.press("ControlOrMeta+A");
    await page.keyboard.insertText(
      "@article{retained, title={Retained reference draft}}\n",
    );
    await page.getByText("Layout and numbers", { exact: true }).click();
    await page.getByLabel("Paper columns").selectOption("double");
    await page.getByLabel("Significant digits").fill("3");
    await page
      .getByLabel("Manuscript revision instruction")
      .fill("Keep this request across every view");
    await page.getByRole("button", { name: "Close manuscript panel" }).click();
    await expect(page.locator(".react-flow")).toBeVisible();
    await page
      .locator(".workspace-toolbar")
      .getByRole("button", { name: "Paper", exact: true })
      .click();
    await expect(
      page.getByRole("tab", { name: "references.bib" }),
    ).toHaveAttribute("aria-selected", "true");
    await expect(
      sourceEditor.getByRole("textbox", { name: "Editor content" }),
    ).toHaveValue(/Retained reference draft/);
    await expect(
      page.getByLabel("Manuscript revision instruction"),
    ).toHaveValue("Keep this request across every view");
    await page
      .getByRole("button", { name: "Open full manuscript editor" })
      .click();
    await expect(page).toHaveURL(new RegExp(`/projects/${project.id}/paper`));
    await page.getByText("Layout and numbers", { exact: true }).click();
    await expect(page.getByLabel("Paper columns")).toHaveValue("double");
    await expect(page.getByLabel("Significant digits")).toHaveValue("3");
    await expect(
      sourceEditor.getByRole("textbox", { name: "Editor content" }),
    ).toHaveValue(/Retained reference draft/);

    const external = await request.patch(`/api/papers/${project.id}`, {
      data: {
        expected_revision: saved.revision,
        data: { source: `${initialSource}% external saved change\n` },
      },
    });
    expect(external.ok()).toBeTruthy();
    await expect(page.locator(".paper-draft-notice")).toContainText(
      `r${saved.revision}`,
    );
    await expect(
      sourceEditor.getByRole("textbox", { name: "Editor content" }),
    ).toHaveValue(/Retained reference draft/);
    await page.reload();
    await expect(
      sourceEditor.getByRole("textbox", { name: "Editor content" }),
    ).toHaveValue(/Retained reference draft/);
    await expect(
      page.getByLabel("Manuscript revision instruction"),
    ).toHaveValue("Keep this request across every view");
    await page.getByText("Layout and numbers", { exact: true }).click();
    await expect(page.getByLabel("Paper columns")).toHaveValue("double");
    await expect(page.getByLabel("Significant digits")).toHaveValue("3");
    await page.getByRole("tab", { name: "LaTeX", exact: true }).click();
    await sourceEditor
      .getByRole("textbox", { name: "Editor content" })
      .press("ControlOrMeta+End");
    await expect(sourceEditor).toContainText("retained source draft");
    let guardedRequests = 0;
    const observeGuard = (request: import("@playwright/test").Request) => {
      if (
        (request.url().endsWith(`/api/papers/${project.id}`) &&
          request.method() === "PATCH") ||
        request.url().endsWith(`/api/papers/${project.id}/export`)
      )
        guardedRequests++;
    };
    page.on("request", observeGuard);
    await page
      .getByRole("button", { name: "Current PDF", exact: true })
      .click();
    await expect(page.locator(".paper-page .error-box")).toContainText(
      "Apply layout and compile before exporting the current PDF",
    );
    expect(guardedRequests).toBe(0);
    page.off("request", observeGuard);
    expect(
      (await (await request.get(`/api/papers/${project.id}`)).json()).data
        .source,
    ).toBe(`${initialSource}% external saved change\n`);
    const failedSave = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/papers/${project.id}`) &&
        response.request().method() === "PATCH",
    );
    await page
      .locator(".paper-actions")
      .getByRole("button", { name: "Save", exact: true })
      .click();
    expect((await failedSave).status()).toBe(409);
    await expect(page.locator(".paper-page .error-box")).toContainText(
      "REVISION_CONFLICT",
    );
    await expect(sourceEditor).toContainText("retained source draft");
    const draft = await page.evaluate(
      (id) => JSON.parse(sessionStorage.getItem(`forest-paper-draft:${id}`)!),
      project.id,
    );
    expect(draft).toMatchObject({
      editingRevision: saved.revision,
      dirty: true,
      layoutDirty: true,
      instruction: "Keep this request across every view",
      layout: { columns: "double", significant_digits: 3 },
    });
  } finally {
    await request.delete(`/api/projects/${project.id}`).catch(() => {});
  }
});

test("an accepted save from the hidden panel keeps newer typing in the full editor", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: {
      name: `Manuscript pending save ${Date.now()}`,
      goal: "Preserve typing when a real save response arrives after expansion",
      mode: "manual",
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  let releaseSave = () => {};
  let saveAccepted = () => {};
  const release = new Promise<void>((resolve) => {
    releaseSave = resolve;
  });
  const accepted = new Promise<void>((resolve) => {
    saveAccepted = resolve;
  });
  let gated = false;
  await page.route(`**/api/papers/${project.id}`, async (route) => {
    if (route.request().method() !== "PATCH" || gated) return route.continue();
    gated = true;
    const response = await route.fetch();
    saveAccepted();
    await release;
    await route.fulfill({ response });
  });
  try {
    await page.goto(`/projects/${project.id}/workspace?paper=1`);
    const sourceEditor = page.locator(".paper-source .monaco-editor");
    await expect(sourceEditor).toBeVisible();
    await sourceEditor.getByRole("textbox", { name: "Editor content" }).focus();
    await page.keyboard.press("ControlOrMeta+A");
    const firstSource = `${initialSource}% first saved draft\n`;
    await page.keyboard.insertText(firstSource);
    const saveResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/papers/${project.id}`) &&
        response.request().method() === "PATCH",
    );
    await page
      .locator(".paper-actions")
      .getByRole("button", { name: "Save", exact: true })
      .click();
    await accepted;
    await page
      .getByRole("button", { name: "Open full manuscript editor" })
      .click();
    await expect(
      page
        .locator(".paper-actions")
        .getByRole("button", { name: "Save", exact: true }),
    ).toBeDisabled();
    await expect(sourceEditor).toBeVisible();
    await sourceEditor.getByRole("textbox", { name: "Editor content" }).focus();
    await page.keyboard.press("ControlOrMeta+A");
    const newerSource = `${initialSource}% newer unsaved typing\n`;
    await page.keyboard.insertText(newerSource);
    releaseSave();
    const saved = await (await saveResponse).json();
    await expect
      .poll(() =>
        page.evaluate(
          (id) =>
            JSON.parse(sessionStorage.getItem(`forest-paper-draft:${id}`)!),
          project.id,
        ),
      )
      .toMatchObject({
        source: newerSource,
        editingRevision: saved.revision,
        dirty: true,
      });
    expect(
      (await (await request.get(`/api/papers/${project.id}`)).json()).data
        .source,
    ).toBe(firstSource);
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
      .toBe(newerSource);
  } finally {
    releaseSave();
    await request.delete(`/api/projects/${project.id}`).catch(() => {});
  }
});

test("paper saves real source, compiles through the worker, downloads current PDF and rejects the actual stale PDF", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(180_000);
  const pdfPageErrors: string[] = [];
  page.on("pageerror", (error) => pdfPageErrors.push(error.message));
  const created = await request.post("/api/projects", {
    data: {
      name: `Manuscript actual compilation ${Date.now()}`,
      goal: "Compile and export the exact saved local draft",
      mode: "manual",
      config: { publication_profile: "operational" },
      budget: { max_runs: 3, seconds: 300, allow_paid: false },
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  try {
    await page.goto(`/projects/${project.id}/paper`);
    await expect(page.locator(".paper-source .monaco-editor")).toBeVisible();
    await expect(
      page.getByText("No compiled PDF yet.", { exact: true }),
    ).toBeVisible();
    const unavailable = page.waitForResponse((response) =>
      response.url().endsWith(`/api/papers/${project.id}/export`),
    );
    await page
      .getByRole("button", { name: "Current PDF", exact: true })
      .click();
    expect((await unavailable).status()).toBe(409);
    await expect(page.locator(".paper-page .error-box")).toContainText(
      "PDF_UNAVAILABLE",
    );

    const sourceEditor = page.locator(".paper-source .monaco-editor");
    await sourceEditor.getByRole("textbox", { name: "Editor content" }).focus();
    await page.keyboard.press("ControlOrMeta+A");
    await page.keyboard.insertText(initialSource);
    await page
      .locator(".paper-actions")
      .getByRole("button", { name: "Save", exact: true })
      .click();
    await expect(page.getByText("Paper saved", { exact: true })).toBeVisible();
    const saved = await (await request.get(`/api/papers/${project.id}`)).json();
    expect(saved.data.source).toBe(initialSource);
    const compileResponse = page.waitForResponse((response) =>
      response.url().endsWith(`/api/papers/${project.id}/compile`),
    );
    await page
      .getByRole("button", { name: "Compile PDF", exact: true })
      .click();
    const submitted = await (await compileResponse).json();
    expect(submitted).toMatchObject({
      project_id: project.id,
      kind: "paper_compile",
    });
    await expect
      .poll(
        async () => {
          const run = await (
            await request.get(`/api/runs/${submitted.id}`)
          ).json();
          return run.status;
        },
        { timeout: 120_000, intervals: [250, 500, 1000] },
      )
      .toBe("completed");
    await expect(page.locator(".paper-compilation-state")).toContainText(
      `PDF compiled for r${saved.revision}`,
    );
    const compiled = await (
      await request.get(`/api/papers/${project.id}`)
    ).json();
    expect(compiled.data).toMatchObject({
      compiled_revision: compiled.revision,
    });
    expect(compiled.data.pdf_path).toBeTruthy();
    const downloaded = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Current PDF", exact: true })
      .click();
    const pdf = await downloaded;
    await pdf.saveAs(testInfo.outputPath("current-manuscript.pdf"));
    const path = await pdf.path();
    expect(path).toBeTruthy();
    const bytes = await readFile(path!);
    expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");
    expect(bytes.length).toBeGreaterThan(1000);
    console.log(
      JSON.stringify({
        observed: "real worker PDF",
        project_id: project.id,
        run_id: submitted.id,
        paper_revision: compiled.revision,
        compiled_revision: compiled.data.compiled_revision,
        pdf_path: compiled.data.pdf_path,
        downloaded_bytes: bytes.length,
      }),
    );

    // Delay browser GET delivery of the PDF just compiled by the real worker.
    // Hiding its preview must abort the read without terminating a pending
    // PDF.js URL loader; a subsequent preview must still render those bytes.
    await page.getByRole("tab", { name: "Compile log", exact: true }).click();
    let pdfFetches = 0;
    await page.route(
      `**/api/projects/${project.id}/download?*`,
      async (route) => {
        pdfFetches++;
        await new Promise((resolve) => setTimeout(resolve, 50));
        try {
          await route.fulfill({ body: bytes, contentType: "application/pdf" });
        } catch {
          // The browser aborts this delayed response when its preview closes.
        }
      },
    );
    for (let cycle = 0; cycle < 12; cycle++) {
      const before = pdfFetches;
      await page.getByRole("tab", { name: "PDF preview", exact: true }).click();
      await expect.poll(() => pdfFetches).toBeGreaterThan(before);
      await page.getByRole("tab", { name: "Compile log", exact: true }).click();
      await expect(page.locator(".pdf-viewer")).toHaveCount(0);
    }
    await page.unroute(`**/api/projects/${project.id}/download?*`);
    await page.getByRole("tab", { name: "PDF preview", exact: true }).click();
    await expect(page.locator(".pdf-toolbar")).toContainText("1 / 1");
    await expect
      .poll(() =>
        page.locator(".pdf-pages canvas").evaluate((canvas) => {
          const element = canvas as HTMLCanvasElement;
          if (element.height <= 150) return false;
          const pixels = element
            .getContext("2d")!
            .getImageData(0, 0, element.width, element.height).data;
          let ink = 0;
          for (let index = 0; index < pixels.length; index += 4)
            if (
              pixels[index + 3] &&
              pixels[index] < 180 &&
              pixels[index + 1] < 180 &&
              pixels[index + 2] < 180
            )
              ink++;
          return ink > 100;
        }),
      )
      .toBe(true);
    expect(pdfPageErrors).toEqual([]);
    const lifecyclePath = testInfo.outputPath("pdf-preview-lifecycle.json");
    await writeFile(
      lifecyclePath,
      JSON.stringify(
        {
          cycles: 12,
          delayed_gets: pdfFetches,
          actual_pdf_bytes: bytes.length,
          rendered_ink: true,
          page_errors: pdfPageErrors,
        },
        null,
        2,
      ),
    );
    await testInfo.attach("pdf-preview-lifecycle.json", {
      path: lifecyclePath,
      contentType: "application/json",
    });

    await page.getByText("Layout and numbers", { exact: true }).click();
    await page.getByLabel("Paper columns").selectOption("double");
    let layoutExportRequests = 0;
    const observeExport = (request: import("@playwright/test").Request) => {
      if (request.url().endsWith(`/api/papers/${project.id}/export`))
        layoutExportRequests++;
    };
    page.on("request", observeExport);
    await page
      .getByRole("button", { name: "Current PDF", exact: true })
      .click();
    await expect(page.locator(".paper-page .error-box")).toContainText(
      "Apply layout and compile before exporting the current PDF",
    );
    expect(layoutExportRequests).toBe(0);
    expect(
      (await (await request.get(`/api/papers/${project.id}`)).json()).revision,
    ).toBe(compiled.revision);
    await page
      .getByRole("button", { name: "View saved PDF", exact: true })
      .click();
    await expect(
      page.locator(".paper-preview .paper-draft-notice"),
    ).toContainText(`Saved manuscript PDF · r${compiled.revision}`);
    page.off("request", observeExport);
    // Returning to the saved layout is recognized on the authoritative read.
    await page.getByLabel("Paper columns").selectOption("single");
    await page.reload();
    await expect(page.locator(".paper-compilation-state")).toContainText(
      `PDF compiled for r${compiled.revision}`,
    );
    await expect
      .poll(() =>
        page.evaluate(
          (id) =>
            JSON.parse(sessionStorage.getItem(`forest-paper-draft:${id}`)!)
              .layoutDirty,
          project.id,
        ),
      )
      .toBe(false);

    await sourceEditor.getByRole("textbox", { name: "Editor content" }).focus();
    await page.keyboard.press("ControlOrMeta+A");
    await page.keyboard.insertText(
      `${initialSource}% edited after compilation\n`,
    );
    const saveResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/papers/${project.id}`) &&
        response.request().method() === "PATCH",
    );
    await page
      .locator(".paper-actions")
      .getByRole("button", { name: "Save", exact: true })
      .click();
    expect((await saveResponse).ok()).toBeTruthy();
    await expect(page.locator(".paper-compilation-state")).toContainText(
      "Compile to update it",
    );
    await expect(
      page.getByRole("button", { name: "View previous PDF" }),
    ).toBeVisible();
    const staleResponse = page.waitForResponse((response) =>
      response.url().endsWith(`/api/papers/${project.id}/export`),
    );
    await page
      .getByRole("button", { name: "Current PDF", exact: true })
      .click();
    expect((await staleResponse).status()).toBe(409);
    await expect(page.locator(".paper-page .error-box")).toContainText(
      "STALE_PDF",
    );
    const sourceDownload = page.waitForEvent("download");
    await page.getByRole("button", { name: "Source ZIP", exact: true }).click();
    const zip = await sourceDownload;
    await zip.saveAs(testInfo.outputPath("manuscript-source.zip"));
    const zipBytes = await readFile((await zip.path())!);
    expect(zipBytes.subarray(0, 2).toString()).toBe("PK");
    expect(pdfPageErrors).toEqual([]);
  } finally {
    await request.delete(`/api/projects/${project.id}`).catch(() => {});
  }
});
