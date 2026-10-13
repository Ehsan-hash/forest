import { describe, expect, it } from "vitest";
import { reconcileNodeEditorDraft } from "./nodeEditorDraft";
import type { NodeEditorDraft } from "./nodeEditorDraft";

function original(): NodeEditorDraft {
  return {
    title: "Original title",
    instructions: "Original instructions",
    config: JSON.stringify({ unknown_key: { original: true } }, null, 2),
    inputs: "[]",
    overrides: "{}",
    comment: "",
    stopCurrent: false,
    tab: "config",
    base: {
      revision: 3,
      nodeRevision: 1,
      content: {
        title: "Original title",
        instructions: "Original instructions",
        config: { unknown_key: { original: true } },
        inputs: [],
        context_overrides: {},
      },
    },
  };
}
const latest = {
  revision: 7,
  nodeRevision: 2,
  content: {
    title: "Authoritative title",
    instructions: "Authoritative instructions",
    config: { unknown_key: { latest: true } },
    inputs: [{ node_id: "producer-full-id", path: "actual.csv" }],
    context_overrides: { unknown_context: "retained" },
  },
};

describe("authoritative node draft reconciliation", () => {
  it("updates an untouched form and its complete baseline", () => {
    const reconciled = reconcileNodeEditorDraft(original(), latest);
    expect(reconciled.title).toBe(latest.content.title);
    expect(JSON.parse(reconciled.config)).toEqual(latest.content.config);
    expect(JSON.parse(reconciled.inputs)).toEqual(latest.content.inputs);
    expect(JSON.parse(reconciled.overrides)).toEqual(
      latest.content.context_overrides,
    );
    expect(reconciled.tab).toBe("config");
    expect(reconciled.base).toBe(latest);
  });
  it("retains complete dirty text and its original authority, including invalid JSON", () => {
    const draft = {
      ...original(),
      title: "Unsaved title",
      config: "{ invalid JSON",
    };
    expect(reconcileNodeEditorDraft(draft, latest)).toBe(draft);
    expect(draft.base.revision).toBe(3);
    expect(draft.config).toBe("{ invalid JSON");
  });
  it("treats formatting, comments and intervention intent as unsaved edits", () => {
    for (const patch of [
      { config: '{"unknown_key":{"original":true}}' },
      { comment: "Pending comment" },
      { stopCurrent: true },
    ]) {
      const draft = { ...original(), ...patch };
      expect(reconcileNodeEditorDraft(draft, latest)).toBe(draft);
    }
  });
});
