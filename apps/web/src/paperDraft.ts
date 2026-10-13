import { useEffect, useMemo, useSyncExternalStore } from "react";
import type { RecordItem } from "./api";
import { paperLayoutDefaults, paperTemplate } from "./PaperLayout";
import type { PaperLayoutConfig, PaperTemplate } from "./PaperLayout";

export type PaperDraft = {
  initialized: boolean;
  source: string;
  bibtex: string;
  editingRevision?: number;
  documentId?: string;
  serverRevision?: number;
  template: PaperTemplate;
  layout: PaperLayoutConfig;
  dirty: boolean;
  layoutDirty: boolean;
  instruction: string;
  sourceTab: string;
  previewTab: string;
  compileRunId?: string;
  pending: string | null;
  error: string;
};
type DraftStorage = Pick<Storage, "getItem" | "setItem">;
export type PaperSaveSnapshot = Pick<
  PaperDraft,
  "source" | "bibtex" | "editingRevision"
>;

/** Editing state only. Saved document/compilation state remains owned by the API. */
export function createPaperDraftSession(
  projectId: string,
  storage?: DraftStorage,
) {
  const key = `forest-paper-draft:${projectId}`;
  const listeners = new Set<() => void>();
  let stored: Partial<PaperDraft> & { revision?: number } = {};
  try {
    const value = JSON.parse(storage?.getItem(key) || "null");
    if (
      value &&
      typeof value.source === "string" &&
      typeof value.bibtex === "string" &&
      typeof (value.editingRevision ?? value.revision) === "number"
    )
      stored = value;
  } catch {
    // A damaged draft must not stop the saved document from loading.
  }
  const hasStoredLayout = !!stored.layout && typeof stored.layout === "object";
  let state: PaperDraft = {
    initialized: false,
    source: stored.source ?? "",
    bibtex: stored.bibtex ?? "",
    editingRevision: stored.editingRevision ?? stored.revision,
    template: stored.template === "iclr2027" ? "iclr2027" : "article",
    layout: {
      ...stored.layout,
      ...paperLayoutDefaults({
        layout: stored.layout,
        template: stored.template,
      }),
    },
    dirty:
      typeof stored.dirty === "boolean"
        ? stored.dirty
        : typeof stored.source === "string",
    layoutDirty: stored.layoutDirty === true,
    instruction:
      typeof stored.instruction === "string" ? stored.instruction : "",
    sourceTab: ["source", "bibtex", "bindings"].includes(stored.sourceTab || "")
      ? stored.sourceTab!
      : "source",
    previewTab: ["pdf", "log", "checks"].includes(stored.previewTab || "")
      ? stored.previewTab!
      : "pdf",
    compileRunId:
      typeof stored.compileRunId === "string" ? stored.compileRunId : undefined,
    pending: null,
    error: "",
  };
  let operation = 0;
  const canonical = (value: unknown): string => {
    if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
    if (value && typeof value === "object")
      return `{${Object.entries(value)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`)
        .join(",")}}`;
    return JSON.stringify(value) ?? "undefined";
  };
  const replace = (next: PaperDraft) => {
    if (JSON.stringify(next) === JSON.stringify(state)) return;
    state = next;
    try {
      if (state.editingRevision !== undefined) {
        const { pending: _pending, error: _error, ...draft } = state;
        storage?.setItem(key, JSON.stringify(draft));
      }
    } catch {
      // The live shared draft also works when browser storage is full/disabled.
    }
    listeners.forEach((listener) => listener());
  };
  return {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    update: (patch: Partial<PaperDraft>) => replace({ ...state, ...patch }),
    sync: (paper: RecordItem) => {
      if (
        state.serverRevision !== undefined &&
        paper.revision < state.serverRevision
      )
        return;
      const template = paperTemplate(paper.data);
      const layout = {
        ...(paper.data.layout || {}),
        ...paperLayoutDefaults(paper.data),
      };
      const layoutApplied =
        state.layoutDirty &&
        !state.dirty &&
        template === state.template &&
        canonical(layout) === canonical(state.layout);
      const retainDocument =
        state.dirty || (state.layoutDirty && !layoutApplied);
      replace({
        ...state,
        initialized: true,
        documentId: paper.id,
        serverRevision: Math.max(state.serverRevision ?? 0, paper.revision),
        source: retainDocument ? state.source : String(paper.data.source || ""),
        bibtex: retainDocument ? state.bibtex : String(paper.data.bibtex || ""),
        editingRevision: retainDocument
          ? (state.editingRevision ?? paper.revision)
          : paper.revision,
        template:
          retainDocument && (state.initialized || hasStoredLayout)
            ? state.template
            : template,
        layout:
          retainDocument && (state.initialized || hasStoredLayout)
            ? state.layout
            : layout,
        layoutDirty: layoutApplied ? false : state.layoutDirty,
      });
    },
    saved: (paper: RecordItem, submitted: PaperSaveSnapshot) => {
      if (
        state.editingRevision !== undefined &&
        paper.revision < state.editingRevision
      )
        return;
      const source =
        state.source === submitted.source
          ? String(paper.data.source || "")
          : state.source;
      const bibtex =
        state.bibtex === submitted.bibtex
          ? String(paper.data.bibtex || "")
          : state.bibtex;
      replace({
        ...state,
        source,
        bibtex,
        editingRevision: paper.revision,
        serverRevision: Math.max(state.serverRevision ?? 0, paper.revision),
        dirty:
          source !== paper.data.source || bibtex !== (paper.data.bibtex || ""),
      });
    },
    begin: (kind: string) => {
      if (state.pending) return null;
      const token = ++operation;
      replace({ ...state, pending: kind, error: "" });
      return token;
    },
    finish: (token: number, error = "") => {
      if (token === operation) replace({ ...state, pending: null, error });
    },
  };
}

const sessions = new Map<string, ReturnType<typeof createPaperDraftSession>>();
function projectSession(projectId: string) {
  let session = sessions.get(projectId);
  if (!session) {
    let storage: Storage | undefined;
    try {
      storage =
        typeof window === "undefined" ? undefined : window.sessionStorage;
    } catch {
      /* disabled */
    }
    session = createPaperDraftSession(projectId, storage);
    sessions.set(projectId, session);
  }
  return session;
}

export function usePaperDraft(projectId: string, paper: RecordItem | null) {
  const session = useMemo(() => projectSession(projectId), [projectId]);
  const draft = useSyncExternalStore(
    session.subscribe,
    session.getSnapshot,
    session.getSnapshot,
  );
  useEffect(() => {
    if (paper) session.sync(paper);
  }, [paper, session]);
  return { draft, session };
}
