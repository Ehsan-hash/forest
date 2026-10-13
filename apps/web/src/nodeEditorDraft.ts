import type { Json, ResearchNode } from "./api";

export type NodeEditorDraft = {
  title: string;
  instructions: string;
  config: string;
  inputs: string;
  overrides: string;
  comment: string;
  stopCurrent: boolean;
  tab: string;
  base: { revision: number; nodeRevision: number; content: Json };
};

export function reconcileNodeEditorDraft(
  draft: NodeEditorDraft,
  latest: NodeEditorDraft["base"],
): NodeEditorDraft {
  const original = draft.base.content;
  const clean =
    draft.title === (original.title || "") &&
    draft.instructions === (original.instructions || "") &&
    draft.config === JSON.stringify(original.config || {}, null, 2) &&
    draft.inputs === JSON.stringify(original.inputs || [], null, 2) &&
    draft.overrides ===
      JSON.stringify(original.context_overrides || {}, null, 2) &&
    !draft.comment &&
    !draft.stopCurrent;
  if (!clean) return draft;
  return {
    ...draft,
    title: latest.content.title || "",
    instructions: latest.content.instructions || "",
    config: JSON.stringify(latest.content.config || {}, null, 2),
    inputs: JSON.stringify(latest.content.inputs || [], null, 2),
    overrides: JSON.stringify(latest.content.context_overrides || {}, null, 2),
    base: latest,
  };
}

export function readNodeEditorDraft(
  projectId: string,
  node: ResearchNode,
): NodeEditorDraft | null {
  try {
    const raw = sessionStorage.getItem(
      `forest-node-editor:${projectId}:${node.id}`,
    );
    if (!raw) return null;
    const draft = JSON.parse(raw);
    if (
      ![
        "title",
        "instructions",
        "config",
        "inputs",
        "overrides",
        "comment",
        "tab",
      ].every((key) => typeof draft[key] === "string") ||
      typeof draft.stopCurrent !== "boolean" ||
      typeof draft.base?.revision !== "number" ||
      typeof draft.base?.nodeRevision !== "number" ||
      !draft.base?.content
    )
      return null;
    return draft;
  } catch {
    return null;
  }
}

export function persistNodeEditorDraft(
  projectId: string,
  nodeId: string,
  draft: NodeEditorDraft,
) {
  try {
    sessionStorage.setItem(
      `forest-node-editor:${projectId}:${nodeId}`,
      JSON.stringify(draft),
    );
  } catch {
    /* Keep the live editing session if browser storage is unavailable. */
  }
}
