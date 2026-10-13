import { useEffect, useState } from "react";
import type { Project } from "./api";

export type ControlsDraft = {
  projectId: string;
  revision: number;
  budget: string;
  mode: string;
  savedBudget: string;
  savedMode: string;
};
export function controlsFromProject(project: Project): ControlsDraft {
  const budget = JSON.stringify(project.budget, null, 2),
    mode = project.mode || "assisted";
  return {
    projectId: project.id,
    revision: project.revision,
    budget,
    mode,
    savedBudget: budget,
    savedMode: mode,
  };
}
export function controlsDirty(draft: ControlsDraft) {
  return draft.budget !== draft.savedBudget || draft.mode !== draft.savedMode;
}
export function mergeControlsSnapshot(
  draft: ControlsDraft | null,
  project: Project,
) {
  return draft?.projectId === project.id && controlsDirty(draft)
    ? draft
    : controlsFromProject(project);
}
export function acknowledgeControlsSave(
  current: ControlsDraft | null,
  submitted: ControlsDraft,
  saved: Project,
): ControlsDraft | null {
  if (
    current?.projectId !== submitted.projectId ||
    saved.id !== submitted.projectId
  )
    return current;
  const baseline = controlsFromProject(saved);
  return {
    ...baseline,
    budget:
      current.budget === submitted.budget ? baseline.budget : current.budget,
    mode: current.mode === submitted.mode ? baseline.mode : current.mode,
  };
}
export function useControlsDraft(
  projectId: string,
  project: Project | null,
  saving = false,
) {
  const key = `forest-controls-draft:${projectId}`;
  const [draft, setDraft] = useState<ControlsDraft | null>(() => {
    try {
      const value = JSON.parse(sessionStorage.getItem(key) || "null");
      return value?.projectId === projectId &&
        typeof value.revision === "number" &&
        typeof value.budget === "string" &&
        typeof value.savedBudget === "string" &&
        typeof value.mode === "string" &&
        typeof value.savedMode === "string"
        ? value
        : null;
    } catch {
      return null;
    }
  });
  useEffect(() => {
    if (!saving && project?.id === projectId)
      setDraft((value) => mergeControlsSnapshot(value, project));
  }, [projectId, project, saving]);
  useEffect(() => {
    if (draft?.projectId !== projectId) return;
    try {
      if (controlsDirty(draft))
        sessionStorage.setItem(key, JSON.stringify(draft));
      else sessionStorage.removeItem(key);
    } catch {
      /* Keep the live draft when browser storage is unavailable. */
    }
  }, [key, projectId, draft]);
  const dirty = !!draft && controlsDirty(draft);
  useEffect(() => {
    if (!dirty) return;
    const protect = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty]);
  return {
    draft: draft?.projectId === projectId ? draft : null,
    setDraft,
    dirty,
  };
}
