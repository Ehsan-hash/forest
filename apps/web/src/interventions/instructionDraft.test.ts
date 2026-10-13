import { describe, expect, it } from "vitest";
import type { Graph, Run } from "../api";
import {
  activeInstructionRuns,
  instructionTargetIssue,
  updateInstructionDraft,
} from "./instructionDraft";
import type { InstructionDraft } from "./instructionDraft";

const draft: InstructionDraft = {
  request_id: "original-intent",
  expected_revision: 4,
  text: "Inspect the saved measurements",
  scope: "project",
  target_id: null,
  boundary: "next_run",
};
const graph: Graph = {
  project_id: "project-a",
  revision: 4,
  nodes: [
    { id: "node-a", project_id: "project-a" },
    { id: "foreign-node", project_id: "project-b" },
  ].map((node) => ({
    ...node,
    branch_id: "branch-a",
    type: "experiment",
    title: node.id,
    instructions: "",
    revision: 1,
    config: {},
    position: { x: 0, y: 0 },
    execution_status: "idle",
    research_status: "not_evaluated",
    deliverable_status: "draft",
    archived: false,
    inputs: [],
    outputs: [],
    comments: [],
    context_overrides: {},
  })),
  branches: [{ id: "branch-a" }],
  edges: [],
};
const run = (
  id: string,
  status: string,
  kind = "agent",
  projectId = "project-a",
): Run => ({
  id,
  status,
  kind,
  project_id: projectId,
  node_id: null,
  branch_id: null,
  config: {},
  node_revision: 0,
  created_at: "2026-10-13T00:00:00Z",
  started_at: null,
  finished_at: null,
  exit_code: null,
  pid: null,
  error: null,
  metrics: {},
  output_path: "",
});

describe("formal instruction scope and request identity", () => {
  it("only offers this project's active Agent runs, including yielded and budget states", () => {
    const active = [
      "queued",
      "running",
      "pausing",
      "paused",
      "waiting_input",
      "waiting",
      "budget_exhausted",
    ];
    const stopped = [
      "completed",
      "failed",
      "cancelled",
      "interrupted",
      "skipped",
      "unknown",
    ];
    const runs = [
      ...active.map((status) => run(status, status)),
      ...stopped.map((status) => run(status, status)),
      run("command", "running", "command"),
      run("foreign", "running", "agent", "project-b"),
    ];
    expect(
      activeInstructionRuns(runs, "project-a").map((item) => item.id),
    ).toEqual(active);
  });

  it("forces a new run-scoped instruction to next_request and clears the old target", () => {
    const next = updateInstructionDraft(
      { ...draft, scope: "node", target_id: "node-a" },
      { scope: "run" },
      () => "new-run-intent",
    );
    expect(next).toMatchObject({
      request_id: "new-run-intent",
      scope: "run",
      target_id: null,
      boundary: "next_request",
      expected_revision: 4,
    });
    expect(
      instructionTargetIssue(next, "project-a", graph, [
        run("agent-a", "running"),
      ]),
    ).toBe("missing_run");
  });

  it("retains the exact identity and reviewed revision for an unchanged lost-response retry", () => {
    let allocations = 0;
    const retry = updateInstructionDraft(draft, { text: draft.text }, () => {
      allocations++;
      return "replacement";
    });
    expect(retry).toBe(draft);
    expect(retry.request_id).toBe("original-intent");
    expect(retry.expected_revision).toBe(4);
    expect(allocations).toBe(0);
  });

  it("allocates a new identity for changed text, target or delivery boundary without rebasing", () => {
    const text = updateInstructionDraft(
      draft,
      { text: "Inspect another artifact" },
      () => "text-intent",
    );
    const boundary = updateInstructionDraft(
      draft,
      { boundary: "next_request" },
      () => "boundary-intent",
    );
    const targeted = {
      ...draft,
      scope: "node",
      target_id: "node-a",
    } as InstructionDraft;
    const target = updateInstructionDraft(
      targeted,
      { target_id: "node-b" },
      () => "target-intent",
    );
    expect([text.request_id, boundary.request_id, target.request_id]).toEqual([
      "text-intent",
      "boundary-intent",
      "target-intent",
    ]);
    expect([
      text.expected_revision,
      boundary.expected_revision,
      target.expected_revision,
    ]).toEqual([4, 4, 4]);
  });

  it("rejects a stopped or wrong-project run and a foreign node rather than selecting another target", () => {
    const selected = {
      ...draft,
      scope: "run",
      target_id: "selected",
      boundary: "next_request",
    } as InstructionDraft;
    expect(
      instructionTargetIssue(selected, "project-a", graph, [
        run("selected", "completed"),
        run("other", "running"),
      ]),
    ).toBe("missing_run");
    expect(
      instructionTargetIssue(selected, "project-a", graph, [
        run("selected", "running", "agent", "project-b"),
      ]),
    ).toBe("missing_run");
    expect(
      instructionTargetIssue(
        { ...draft, scope: "node", target_id: "foreign-node" },
        "project-a",
        graph,
        [],
      ),
    ).toBe("missing_node");
    expect(
      instructionTargetIssue(
        { ...selected, boundary: "next_run" },
        "project-a",
        graph,
        [run("selected", "running")],
      ),
    ).toBe("run_boundary");
  });
});
