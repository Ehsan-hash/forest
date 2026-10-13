import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { RunPanel } from "./Workspace";
import { UIContext } from "./ui";
import type { Run } from "./api";

function renderRun(status: string, override: Partial<Run> = {}) {
  const run = {
    id: "run-123",
    status,
    kind: "command",
    created_at: "2026-09-29T00:00:00Z",
    ...override,
  } as Run;
  return renderToStaticMarkup(
    <UIContext.Provider
      value={{
        lang: "en",
        theme: "light",
        t: (_zh, en) => en,
        notify: () => {},
        action: async (f) => f(),
      }}
    >
      <RunPanel runs={[run]} reload={async () => {}} />
    </UIContext.Provider>,
  );
}

describe("run controls reflect allowed transitions", () => {
  it("exposes saved metric values with their full run identity even without stdout", () => {
    const html = renderRun("completed", {
      id: "run-full-metric-identity",
      node_revision: 7,
      metrics: {
        mean_difference: 0.33333334,
        unknown_metric: { retained: true },
      },
    });
    expect(html).toContain("Recorded metrics");
    expect(html).toContain("run-full-metric-identity");
    expect(html).toContain("Executed node revision");
    expect(html).toContain("0.33333334");
    expect(html).toContain("unknown_metric");
    expect(html).toContain("No output");
  });
  it.each([
    "Task controls exceed the configured context_char_budget; increase the allowance",
    "The latest complete native tool exchange exceeds context_char_budget; increase the allowance",
  ])(
    "retains same-run Resume for source-supported legacy Agent failure: %s",
    (error) => {
      const html = renderRun("failed", { kind: "agent", error });
      expect(
        html.match(/<button[^>]*aria-label="resume"[^>]*>/)?.[0],
      ).not.toContain('disabled=""');
      expect(
        html.match(/<button[^>]*aria-label="retry"[^>]*>/)?.[0],
      ).not.toContain('disabled=""');
    },
  );
  it("keeps an unfamiliar server status explicit without implying active execution", () => {
    const html = renderRun("custom_scheduler_state");
    expect(html).toContain("custom_scheduler_state");
    expect(html).toContain("No output available for status");
    expect(html).not.toContain("Waiting for executor output");
    for (const action of ["pause", "resume", "cancel", "retry"]) {
      expect(
        html.match(new RegExp(`<button[^>]*aria-label="${action}"[^>]*>`))?.[0],
      ).toContain('disabled=""');
    }
  });
  it.each(["completed", "failed", "cancelled", "interrupted", "skipped"])(
    "disables process controls for %s while retaining retry",
    (status) => {
      const html = renderRun(status);
      for (const action of ["pause", "resume", "cancel"]) {
        expect(
          html.match(
            new RegExp(`<button[^>]*aria-label="${action}"[^>]*>`),
          )?.[0],
        ).toContain('disabled=""');
      }
      expect(
        html.match(/<button[^>]*aria-label="retry"[^>]*>/)?.[0],
      ).not.toContain('disabled=""');
      expect(html).toContain("No output");
      expect(html).not.toContain("Waiting for executor output");
    },
  );

  it.each([
    ["queued", ["pause", "cancel"]],
    ["running", ["pause", "cancel"]],
    ["pausing", ["cancel"]],
    ["paused", ["resume", "cancel"]],
    ["waiting_input", ["resume", "cancel"]],
    ["waiting", ["pause", "resume", "cancel"]],
    ["budget_exhausted", ["pause", "resume", "cancel"]],
  ])("keeps valid controls available for %s", (status, enabled) => {
    const html = renderRun(status as string);
    for (const action of ["pause", "resume", "cancel", "retry"]) {
      const button = html.match(
        new RegExp(`<button[^>]*aria-label="${action}"[^>]*>`),
      )?.[0];
      expect(button).toBeDefined();
      expect(button?.includes('disabled=""')).toBe(!enabled.includes(action));
    }
    expect(html).toContain("Waiting for executor output");
  });
});
