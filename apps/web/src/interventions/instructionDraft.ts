import type { Graph, Run } from "../api";
import type { components } from "../../../../packages/contracts/api.generated";

export type InstructionDraft = components["schemas"]["InstructionRequest"];
type IntentPatch = Partial<
  Pick<InstructionDraft, "text" | "scope" | "target_id" | "boundary">
>;

const activeRunStates = new Set([
  "queued",
  "running",
  "pausing",
  "paused",
  "waiting_input",
  "waiting",
  "budget_exhausted",
]);

export function activeInstructionRuns(runs: Run[], projectId: string) {
  return runs.filter(
    (run) =>
      run.project_id === projectId &&
      run.kind === "agent" &&
      activeRunStates.has(run.status),
  );
}

/** A retry retains its identity; changing the instruction creates another intent. */
export function updateInstructionDraft(
  draft: InstructionDraft,
  patch: IntentPatch,
  createRequestId: () => string = () => crypto.randomUUID(),
): InstructionDraft {
  const next = { ...draft, ...patch };
  if (next.scope !== draft.scope) next.target_id = null;
  if (next.scope === "project") next.target_id = null;
  if (next.scope === "run") next.boundary = "next_request";
  const changed =
    next.text !== draft.text ||
    next.scope !== draft.scope ||
    next.target_id !== draft.target_id ||
    next.boundary !== draft.boundary;
  return changed ? { ...next, request_id: createRequestId() } : draft;
}

export function instructionTargetIssue(
  draft: InstructionDraft,
  projectId: string,
  graph: Graph,
  runs: Run[],
): "missing_branch" | "missing_node" | "missing_run" | "run_boundary" | null {
  if (draft.scope === "branch")
    return graph.branches.some((branch) => branch.id === draft.target_id)
      ? null
      : "missing_branch";
  if (draft.scope === "node")
    return graph.nodes.some(
      (node) => node.id === draft.target_id && node.project_id === projectId,
    )
      ? null
      : "missing_node";
  if (draft.scope === "run") {
    if (draft.boundary !== "next_request") return "run_boundary";
    return activeInstructionRuns(runs, projectId).some(
      (run) => run.id === draft.target_id,
    )
      ? null
      : "missing_run";
  }
  return null;
}
