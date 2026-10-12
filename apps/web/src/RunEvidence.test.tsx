import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SessionContinuity } from "./RunEvidence";

describe("saved research lifecycle", () => {
  it("shows an automatic boundary as continuing work after legacy cumulative limits", () => {
    const html = renderToStaticMarkup(<SessionContinuity session={{
      status: "waiting", wait_for: { type: "work_round" },
      lifecycle: { state: "checkpointed", round_index: 401,
        checkpoints: [{ checkpoint_id: "saved-round-401", reason: "round_boundary" }] },
    }} />);
    expect(html).toContain("Work round 401");
    expect(html).toContain("next work round continues automatically");
    expect(html).toContain("saved-round-401");
    expect(html).not.toContain("exhausted");
  });

  it("retains explicit blocker diagnostics without describing process waiting as a round", () => {
    const html = renderToStaticMarkup(<SessionContinuity session={{
      wait_for: { type: "process", process_id: "training" },
      lifecycle: { state: "blocked", round_index: 3,
        recovery: { reason: "Validation data authorization requires owner action" } },
    }} />);
    expect(html).toContain("Research lifecycle: blocked");
    expect(html).toContain("Validation data authorization requires owner action");
    expect(html).not.toContain("continues automatically");
  });

  it("accepts legacy checkpoints without lifecycle metadata", () => {
    expect(renderToStaticMarkup(<SessionContinuity session={{ status: "running" }} />)).toBe("");
  });

  it("reports an unreadable ledger without claiming zero consumption", () => {
    const html = renderToStaticMarkup(<SessionContinuity session={{
      usage: null, usage_error: { code: "LEDGER_UNREADABLE", message: "Reconnect the original supervisor" },
    }} />);
    expect(html).toContain("Resource usage unavailable");
    expect(html).toContain("Reconnect the original supervisor");
    expect(html).not.toContain("0 seconds");
  });

  it("shows the preserved reason when restored execution needs reconciliation", () => {
    const html = renderToStaticMarkup(<SessionContinuity session={{
      status: "waiting_input", lifecycle: { state: "blocked", recovery: {} },
      reconciliation_reason: "Completed checkpoint artifacts changed",
    }} />);
    expect(html).toContain("Research lifecycle: blocked");
    expect(html).toContain("Completed checkpoint artifacts changed");
    expect(html).not.toContain("continues automatically");
  });

  it("does not promise continuation from a stale round wait on blocked or cancelled work", () => {
    for (const status of ["waiting_input", "cancelled", "paused"]) {
      const html = renderToStaticMarkup(<SessionContinuity session={{
        status, wait_for: { type: "work_round" },
        lifecycle: { state: "blocked", round_index: 7 },
        reconciliation_reason: "Original supervisory ledger requires reconciliation",
      }} />);
      expect(html).toContain("Work round 7");
      expect(html).toContain("Original supervisory ledger requires reconciliation");
      expect(html).not.toContain("continues automatically");
    }
    const blockedWaiting = renderToStaticMarkup(<SessionContinuity session={{
      status: "waiting", wait_for: { type: "work_round" }, lifecycle: { state: "blocked" },
    }} />);
    expect(blockedWaiting).not.toContain("continues automatically");
  });
});
