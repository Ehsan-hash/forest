import { describe, expect, it } from "vitest";
import {
  acknowledgeAnalysisIntent,
  analysisDraftKey,
  analysisReferenceUrl,
  analysisReportMarkdown,
  retainAnalysisIntent,
} from "./researchAnalysisDraft";
import type { AnalysisDraft, AnalysisPayload } from "./researchAnalysisDraft";

const draft: AnalysisDraft = {
  question: "Inspect the actual failed run.",
  literatureQuery: "",
  literatureSources: ["crossref", "arxiv"],
  baseRevision: 4,
  baseFingerprint: "original-snapshot",
  providerId: "original-provider",
  lastRunId: "",
};
const payload: AnalysisPayload = {
  scope: "node",
  node_id: "full-node-id",
  expected_revision: 4,
  expected_fingerprint: "original-snapshot",
  question: draft.question,
  language: "en",
  provider_id: draft.providerId,
  literature_sources: draft.literatureSources,
  literature_limit: 6,
  timeout: 180,
};
describe("research analysis saved intent", () => {
  it("reuses the exact revision/provider/request identity after uncertain transport", () => {
    const first = retainAnalysisIntent(
      draft,
      payload,
      () => "request-original",
    );
    expect(
      retainAnalysisIntent(first, { ...payload }, () => "incorrect-new-id"),
    ).toBe(first);
    expect(first.intent?.payload.expected_revision).toBe(4);
    expect(first.intent?.payload.expected_fingerprint).toBe(
      "original-snapshot",
    );
    expect(first.intent?.payload.provider_id).toBe("original-provider");
    expect(
      retainAnalysisIntent(
        first,
        { ...payload, expected_revision: 5 },
        () => "reviewed-new-context",
      ).intent?.requestId,
    ).toBe("reviewed-new-context");
  });
  it("keeps later question text and a different pending intent when an earlier acknowledgment arrives", () => {
    const first = retainAnalysisIntent(draft, payload, () => "first");
    const later = retainAnalysisIntent(
      { ...first, question: "Later question" },
      { ...payload, question: "Later question" },
      () => "later",
    );
    const acknowledged = acknowledgeAnalysisIntent(
      later,
      "first",
      "accepted-full-run-id",
    );
    expect(acknowledged.question).toBe("Later question");
    expect(acknowledged.baseRevision).toBe(4);
    expect(acknowledged.intent?.requestId).toBe("later");
    expect(acknowledged.lastRunId).toBe("accepted-full-run-id");
  });
  it("separates full project/node/path identities and rejects executable citation URLs", () => {
    expect(analysisDraftKey("P", "project", "N", "B")).not.toBe(
      analysisDraftKey("P", "node", "N", "B"),
    );
    expect(analysisDraftKey("P", "node", "node-one-full-id", "B")).not.toBe(
      analysisDraftKey("P", "node", "node-two-full-id", "B"),
    );
    expect(analysisReferenceUrl("javascript:alert(1)")).toBeUndefined();
    expect(analysisReferenceUrl("https://doi.org/10.1/actual")).toBe(
      "https://doi.org/10.1/actual",
    );
  });
  it("retains a later explicit historical selection when the earlier request is acknowledged", () => {
    const pending = retainAnalysisIntent(
      { ...draft, lastRunId: "previous-history" },
      payload,
      () => "pending",
    );
    const changed = { ...pending, lastRunId: "explicit-other-history" };
    const acknowledged = acknowledgeAnalysisIntent(
      changed,
      "pending",
      "new-accepted-run",
      "previous-history",
    );
    expect(acknowledged.lastRunId).toBe("explicit-other-history");
    expect(acknowledged.intent).toBeUndefined();
  });
  it("exports full saved source identities, reading scope and judgment without promoting verification", () => {
    const markdown = analysisReportMarkdown("project-full-id", {
      run_id: "analysis-full-id",
      snapshot: { project_revision: 4, fingerprint: "original-snapshot" },
      current: false,
      stale_reasons: ["New results"],
      artifacts: { report: "runs/analysis-full-id/research_analysis.json" },
      report: {
        summary: "Local protocol fixture advice",
        experiments: [
          {
            node_id: "full-node-id",
            run_ids: ["actual-run-id"],
            what_was_done: "Command",
            result: "Recorded output",
            problems: "No scientific verification",
          },
        ],
        findings: [],
        recommendations: [],
        limitations: ["Abstract only"],
        coverage: { node_ids: ["full-node-id"] },
      },
      citations: [
        {
          id: "citation-full-id",
          title: "Source title",
          source: "crossref",
          read_scope: "metadata/abstract",
          url: "javascript:alert(1)",
        },
      ],
      literature_searches: [],
    });
    expect(markdown).toContain("Context fingerprint: original-snapshot");
    expect(markdown).toContain("Run: actual-run-id");
    expect(markdown).toContain("Read scope: metadata/abstract");
    expect(markdown).toContain(
      "scientific verification retains its own source evidence",
    );
    expect(markdown).not.toContain("javascript:");
  });
});
