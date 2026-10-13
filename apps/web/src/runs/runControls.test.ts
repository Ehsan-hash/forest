import { describe, expect, it } from "vitest";
import type { Run } from "../api";
import { canResumeRun } from "./runControls";

describe("persisted run recovery availability", () => {
  const run = {
    id: "saved-run",
    branch_id: "saved-branch",
    kind: "agent",
    status: "failed",
  } as Run;
  it("allows only the documented legacy Agent context failures to resume under the saved ID", () => {
    for (const error of [
      "Task controls exceed the configured context_char_budget; details",
      "The latest complete native tool exchange exceeds context_char_budget; details",
    ]) {
      expect(canResumeRun({ ...run, error })).toBe(true);
      expect(canResumeRun({ ...run, kind: "command", error })).toBe(false);
    }
    expect(canResumeRun({ ...run, error: "Provider connection failed" })).toBe(
      false,
    );
  });
  it("requires an active matching branch when a persisted scheduling hold exists", () => {
    const held = {
      ...run,
      status: "paused",
      resource: { branch_scheduling_hold: true },
    };
    expect(canResumeRun(held)).toBe(false);
    expect(canResumeRun(held, [{ id: "other-branch", status: "active" }])).toBe(
      false,
    );
    for (const status of ["pruned", "archived", "disabled", "materializing"])
      expect(canResumeRun(held, [{ id: run.branch_id, status }])).toBe(false);
    expect(canResumeRun(held, [{ id: run.branch_id, status: "active" }])).toBe(
      true,
    );
  });
});
