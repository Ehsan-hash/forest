import { describe, expect, it } from "vitest";
import type { RecordItem } from "./api";
import { createPaperDraftSession } from "./paperDraft";

const paper = (
  revision: number,
  source = "saved source",
  data = {},
): RecordItem => ({
  id: "paper-a",
  project_id: "project-a",
  revision,
  title: "Manuscript",
  status: "draft",
  data: { source, bibtex: "saved references", ...data },
  created_at: "",
  updated_at: "",
});
const storage = () => {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) || null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  };
};

describe("shared manuscript editing session", () => {
  it("retains the entire dirty session and reviewed base across hide, expand and background refresh", () => {
    const browser = storage();
    const panel = createPaperDraftSession("project-a", browser);
    panel.sync(paper(3));
    panel.update({
      source: "draft source",
      bibtex: "draft references",
      dirty: true,
      template: "iclr2027",
      layout: { ...panel.getSnapshot().layout, figure_span: "page" },
      layoutDirty: true,
      instruction: "Keep this revision request",
      sourceTab: "bibtex",
      previewTab: "checks",
    });
    const expanded = createPaperDraftSession("project-a", browser);
    expanded.sync(paper(4, "external change"));
    expect(expanded.getSnapshot()).toMatchObject({
      source: "draft source",
      bibtex: "draft references",
      editingRevision: 3,
      serverRevision: 4,
      template: "iclr2027",
      layoutDirty: true,
      dirty: true,
      instruction: "Keep this revision request",
      sourceTab: "bibtex",
      previewTab: "checks",
      layout: { figure_span: "page" },
    });
  });

  it("protects a layout-only draft and advances clean documents without clearing revision instructions", () => {
    const session = createPaperDraftSession("project-a");
    session.sync(paper(1));
    session.update({
      instruction: "Review this",
      layoutDirty: true,
      layout: { ...session.getSnapshot().layout, columns: "double" },
    });
    session.sync(paper(2, "other editor"));
    expect(session.getSnapshot()).toMatchObject({
      source: "saved source",
      editingRevision: 1,
      serverRevision: 2,
      layoutDirty: true,
    });
    session.update({ layoutDirty: false });
    session.sync(paper(2, "other editor"));
    expect(session.getSnapshot()).toMatchObject({
      source: "other editor",
      editingRevision: 2,
      instruction: "Review this",
    });
  });

  it("keeps edits made during saving and bases the next save on the accepted revision", () => {
    const session = createPaperDraftSession("project-a");
    session.sync(paper(5));
    session.update({ source: "first draft", dirty: true });
    const submitted = session.getSnapshot();
    session.update({
      source: "newer typing",
      instruction: "Unsubmitted instruction",
    });
    session.sync(paper(7, "externally saved after the accepted request"));
    session.saved(paper(6, "first draft"), submitted);
    expect(session.getSnapshot()).toMatchObject({
      source: "newer typing",
      editingRevision: 6,
      serverRevision: 7,
      dirty: true,
      instruction: "Unsubmitted instruction",
    });
    session.sync(paper(5, "out-of-order reply"));
    expect(session.getSnapshot().editingRevision).toBe(6);
  });

  it("restores the earlier source/BibTeX browser draft format without discarding saved layout or unknown fields", () => {
    const browser = storage();
    browser.setItem(
      "forest-paper-draft:project-a",
      JSON.stringify({
        source: "legacy draft",
        bibtex: "legacy refs",
        revision: 2,
      }),
    );
    const session = createPaperDraftSession("project-a", browser);
    session.sync(
      paper(3, "saved", {
        template: "iclr2027",
        layout: { figure_span: "page", future_layout_option: "preserve" },
      }),
    );
    expect(session.getSnapshot()).toMatchObject({
      source: "legacy draft",
      editingRevision: 2,
      dirty: true,
      template: "iclr2027",
      layout: { figure_span: "page", future_layout_option: "preserve" },
    });
  });

  it("shares pending and failed save state while preserving the draft and isolates projects", () => {
    const browser = storage();
    const a = createPaperDraftSession("project-a", browser);
    a.sync(paper(1));
    a.update({ source: "unsaved", dirty: true });
    const operation = a.begin("save")!;
    expect(a.begin("save")).toBeNull();
    a.finish(operation, "REVISION_CONFLICT: Manuscript changed");
    expect(a.getSnapshot()).toMatchObject({
      pending: null,
      source: "unsaved",
      dirty: true,
      editingRevision: 1,
      error: "REVISION_CONFLICT: Manuscript changed",
    });
    const b = createPaperDraftSession("project-b", browser);
    expect(b.getSnapshot().source).toBe("");
    const remount = createPaperDraftSession("project-a", browser);
    expect(remount.getSnapshot()).toMatchObject({
      source: "unsaved",
      dirty: true,
      pending: null,
    });
  });
});
