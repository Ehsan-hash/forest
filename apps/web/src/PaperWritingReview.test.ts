import { describe, expect, it } from "vitest";
import { applyWritingEdit } from "./PaperWritingReview";
import { publicationChanges } from "./ProjectRequirements";

describe("literal manuscript edits", () => {
  it("uses Python code-point offsets after a non-BMP character", () => {
    const source = "😀 We may potentially observe an effect.";
    expect(
      applyWritingEdit(source, {
        start: 5,
        end: 20,
        line: 1,
        original: "may potentially",
        replacement: "may",
        reason: "Redundant possibility markers",
        requires_evidence_judgment: false,
      }),
    ).toBe("😀 We may observe an effect.");
  });
  it("retains phrases that require scientific judgment and rejects a changed span", () => {
    const edit = {
      start: 0,
      end: 19,
      line: 1,
      original: "limited improvement",
      replacement: null,
      reason: "Name the measured effect",
      requires_evidence_judgment: true,
    };
    expect(() => applyWritingEdit("limited improvement", edit)).toThrow(
      "evidence judgment",
    );
    expect(() =>
      applyWritingEdit("different text", {
        ...edit,
        replacement: "meaningful effect",
        requires_evidence_judgment: false,
      }),
    ).toThrow("span changed");
  });
});

describe("publication merge intent", () => {
  it("patches only edited fields and retains unknown saved and concurrently added requirements", () => {
    expect(
      publicationChanges(
        { target: "Original", custom: { dimension: 7 } },
        { target: "Edited", custom: { dimension: 7 } },
        { target: "Original", custom: { dimension: 7 }, future_field: true },
      ),
    ).toEqual({ target: "Edited" });
    expect(
      publicationChanges(
        { custom: { threshold: 7, condition: "paired" } },
        { custom: { condition: "paired", threshold: 7 } },
        { custom: { threshold: 7, condition: "paired" } },
      ),
    ).toEqual({});
  });
  it("retains the local draft on a conflicting field and does not pretend merge can remove keys", () => {
    expect(() =>
      publicationChanges(
        { target: "Original" },
        { target: "Mine" },
        { target: "Other editor" },
      ),
    ).toThrow("changed in: target");
    expect(() =>
      publicationChanges(
        { target: "Original", custom: 2 },
        { target: "Original" },
        { target: "Original", custom: 2 },
      ),
    ).toThrow("cannot be removed");
  });
});
