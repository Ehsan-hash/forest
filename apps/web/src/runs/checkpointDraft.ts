import type { Json } from "../api";

export type RunEditorDraft = {
  configuration: string;
  checkpoint: string;
  checkpointIdentity?: string;
  checkpointArchive?: { identity: string; text: string }[];
};

export function checkpointDraftIdentity(point: Json | null | undefined) {
  return point
    ? JSON.stringify({ stage: point.stage, payload: point.payload })
    : "";
}

/** A same-run checkpoint can advance. Retain old text without applying it to a new payload. */
export function reconcileCheckpointDraft(
  saved: Partial<RunEditorDraft>,
  point: Json | null | undefined,
): RunEditorDraft {
  const identity = checkpointDraftIdentity(point);
  const archive = (
    Array.isArray(saved.checkpointArchive) ? saved.checkpointArchive : []
  ).filter(
    (entry) =>
      typeof entry.identity === "string" && typeof entry.text === "string",
  );
  if (
    typeof saved.checkpoint === "string" &&
    saved.checkpointIdentity !== identity
  ) {
    const entry = {
      identity: saved.checkpointIdentity || "legacy checkpoint draft",
      text: saved.checkpoint,
    };
    if (
      !archive.some(
        (item) => item.identity === entry.identity && item.text === entry.text,
      )
    )
      archive.push(entry);
  }
  return {
    configuration:
      typeof saved.configuration === "string" ? saved.configuration : "{}",
    checkpoint:
      saved.checkpointIdentity === identity &&
      typeof saved.checkpoint === "string"
        ? saved.checkpoint
        : JSON.stringify(point?.override ?? point?.payload ?? {}, null, 2),
    checkpointIdentity: identity,
    checkpointArchive: archive,
  };
}
