import type { Json, Run } from "../api";

/** Matches the persisted lifecycle service; ordinary failures require a new retry. */
export function canResumeRun(
  run: Run & { resource?: Json },
  branches: Json[] = [],
) {
  if (
    run.resource?.branch_scheduling_hold &&
    !branches.some(
      (branch) => branch.id === run.branch_id && branch.status === "active",
    )
  )
    return false;
  return (
    ["paused", "waiting_input", "waiting", "budget_exhausted"].includes(
      run.status,
    ) ||
    (run.kind === "agent" &&
      run.status === "failed" &&
      [
        "Task controls exceed the configured context_char_budget;",
        "The latest complete native tool exchange exceeds context_char_budget;",
      ].some((prefix) => (run.error || "").startsWith(prefix)))
  );
}
