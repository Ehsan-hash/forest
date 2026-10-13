import { expect, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

function latch() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

async function createProject(request: APIRequestContext) {
  const response = await request.post("/api/projects", {
    data: {
      name: `File pagination freshness ${Date.now()}`,
      mode: "manual",
      goal: "Keep the selected file page current",
    },
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

const firstCursor = JSON.stringify({ is_dir: false, path: "alpha.txt" });
const filePage = (path: string, hasMore = false) => ({
  files: [{ path, size: 1, modified: 0, is_dir: false }],
  has_more: hasMore,
  next_cursor: hasMore ? firstCursor : null,
});

test("background refresh rereads the selected file cursor while explicit Refresh restarts the scan", async ({
  page,
  request,
}) => {
  const project = await createProject(request);
  let changed = false;
  const cursors: Array<string | null> = [];
  try {
    // File pages are response fixtures; project ownership and browser events are real.
    await page.route(`**/api/projects/${project.id}/files*`, (route) => {
      const url = new URL(route.request().url());
      if (url.pathname !== `/api/projects/${project.id}/files`)
        return route.continue();
      const cursor = url.searchParams.get("cursor");
      cursors.push(cursor);
      return route.fulfill({
        json: cursor
          ? filePage(changed ? "zeta-current.txt" : "zeta.txt")
          : filePage("alpha.txt", true),
      });
    });
    await page.goto(`/projects/${project.id}/files`);
    const tree = page.locator(".file-tree");
    await expect(
      tree.getByRole("button", { name: "alpha.txt 1 B" }),
    ).toBeVisible();
    await tree.getByRole("button", { name: "Next", exact: true }).click();
    await expect(tree).toContainText("Page 2");
    await expect(
      tree.getByRole("button", { name: "zeta.txt 1 B" }),
    ).toBeVisible();
    changed = true;
    await page.evaluate(() =>
      window.dispatchEvent(new Event("forest-refresh")),
    );
    await expect(
      tree.getByRole("button", { name: "zeta-current.txt 1 B" }),
    ).toBeVisible();
    await expect(tree).toContainText("Page 2");
    await expect(
      tree.getByRole("button", { name: "Previous", exact: true }),
    ).toBeEnabled();
    expect(cursors.at(-1)).toBe(firstCursor);
    await page.reload();
    await expect(
      tree.getByRole("button", { name: "alpha.txt 1 B" }),
    ).toBeVisible();
    await expect(tree).toContainText("Page 1");
    await expect(
      tree.getByRole("button", { name: "Previous", exact: true }),
    ).toBeDisabled();
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});

test("file events during Next defer to its new cursor and an older refresh cannot override Previous", async ({
  page,
  request,
}) => {
  const project = await createProject(request);
  const nextGate = latch(),
    nextStarted = latch(),
    refreshGate = latch(),
    refreshStarted = latch(),
    oldFinished = latch();
  let cursorReads = 0;
  let phase = "next";
  try {
    await page.route(`**/api/projects/${project.id}/files*`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname !== `/api/projects/${project.id}/files`)
        return route.continue();
      if (!url.searchParams.has("cursor"))
        return route.fulfill({
          json: filePage(
            phase === "older-refresh" ? "alpha-current.txt" : "alpha.txt",
            true,
          ),
        });
      cursorReads++;
      if (cursorReads === 1) {
        nextStarted.release();
        await nextGate.promise;
        return route.fulfill({ json: filePage("zeta-next.txt") });
      }
      if (phase === "older-refresh") {
        refreshStarted.release();
        await refreshGate.promise;
        await route.fulfill({ json: filePage("STALE-PAGE-TWO.txt") });
        oldFinished.release();
        return;
      }
      return route.fulfill({ json: filePage("zeta-current.txt") });
    });
    await page.goto(`/projects/${project.id}/files`);
    const tree = page.locator(".file-tree");
    await expect(
      tree.getByRole("button", { name: "alpha.txt 1 B" }),
    ).toBeVisible();
    await tree.getByRole("button", { name: "Next", exact: true }).click();
    await nextStarted.promise;
    await page.evaluate(() =>
      window.dispatchEvent(new Event("forest-refresh")),
    );
    nextGate.release();
    await expect(
      tree.getByRole("button", { name: "zeta-current.txt 1 B" }),
    ).toBeVisible();
    await expect(tree).toContainText("Page 2");
    expect(cursorReads).toBeGreaterThanOrEqual(2);

    phase = "older-refresh";
    await page.evaluate(() =>
      window.dispatchEvent(new Event("forest-refresh")),
    );
    await refreshStarted.promise;
    await tree.getByRole("button", { name: "Previous", exact: true }).click();
    await expect(
      tree.getByRole("button", { name: "alpha-current.txt 1 B" }),
    ).toBeVisible();
    await expect(tree).toContainText("Page 1");
    const staleResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname ===
          `/api/projects/${project.id}/files` &&
        new URL(response.url()).searchParams.has("cursor"),
    );
    refreshGate.release();
    await oldFinished.promise;
    await (await staleResponse).finished();
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await expect(
      tree.getByRole("button", { name: "alpha-current.txt 1 B" }),
    ).toBeVisible();
    await expect(tree).not.toContainText("STALE-PAGE-TWO.txt");
    await expect(tree).toContainText("Page 1");
  } finally {
    nextGate.release();
    refreshGate.release();
    await request.delete(`/api/projects/${project.id}`);
  }
});
