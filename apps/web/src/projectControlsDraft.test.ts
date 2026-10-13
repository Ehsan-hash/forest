import { describe, expect, it } from "vitest";
import {
  acknowledgeControlsSave,
  controlsFromProject,
  mergeControlsSnapshot,
} from "./projectControlsDraft";
import type { Project } from "./api";
const project = {
  id: "p",
  revision: 2,
  mode: "manual",
  budget: { seconds: 120, future_field: { keep: true } },
} as unknown as Project;
describe("project controls snapshots", () => {
  it("keeps dirty budget and its original revision when an SSE snapshot arrives", () => {
    const draft = {
      ...controlsFromProject(project),
      budget: '{"seconds":999}',
    };
    expect(
      mergeControlsSnapshot(draft, { ...project, revision: 3, mode: "auto" }),
    ).toEqual(draft);
  });
  it("adopts a fresh snapshot only when clean, preserving unknown budget fields", () => {
    const current = {
      ...project,
      revision: 4,
      budget: { ...project.budget, seconds: 300 },
    };
    expect(
      mergeControlsSnapshot(controlsFromProject(project), current),
    ).toEqual(controlsFromProject(current));
    expect(
      JSON.parse(mergeControlsSnapshot(null, current).budget).future_field,
    ).toEqual({ keep: true });
  });
  it("does not apply another project's draft to this project's controls", () => {
    const dirty = { ...controlsFromProject(project), mode: "auto" };
    expect(
      mergeControlsSnapshot(dirty, { ...project, id: "q" }).projectId,
    ).toBe("q");
  });
  it("retains later invalid budget and mode while adopting only the acknowledged baseline", () => {
    const submitted = {
      ...controlsFromProject(project),
      budget: '{"seconds":300}',
      mode: "assisted",
    };
    const later = {
      ...submitted,
      budget: "{ unsaved invalid JSON",
      mode: "auto",
    };
    const saved = {
      ...project,
      revision: 3,
      budget: { seconds: 300 },
      mode: "assisted",
    };
    const acknowledged = acknowledgeControlsSave(later, submitted, saved)!;
    expect(acknowledged.budget).toBe(later.budget);
    expect(acknowledged.mode).toBe("auto");
    expect(acknowledged.savedBudget).toBe(
      JSON.stringify(saved.budget, null, 2),
    );
    expect(acknowledged.savedMode).toBe("assisted");
    expect(acknowledged.revision).toBe(3);
    expect(mergeControlsSnapshot(acknowledged, { ...saved, revision: 8 })).toBe(
      acknowledged,
    );
  });
  it("normalizes unchanged fields and protects a field reverted during submission", () => {
    const submitted = {
      ...controlsFromProject(project),
      budget: '{"seconds":300}',
    };
    const saved = { ...project, revision: 3, budget: { seconds: 300 } };
    expect(acknowledgeControlsSave(submitted, submitted, saved)).toEqual(
      controlsFromProject(saved),
    );
    const reverted = { ...submitted, budget: submitted.savedBudget };
    expect(acknowledgeControlsSave(reverted, submitted, saved)!.budget).toBe(
      submitted.savedBudget,
    );
    expect(
      acknowledgeControlsSave({ ...submitted, mode: "auto" }, submitted, saved)!
        .budget,
    ).toBe(JSON.stringify(saved.budget, null, 2));
  });
  it("never acknowledges one project into another project or a cleared draft", () => {
    const submitted = controlsFromProject(project);
    const other = { ...submitted, projectId: "other" };
    expect(acknowledgeControlsSave(other, submitted, project)).toBe(other);
    expect(
      acknowledgeControlsSave(submitted, submitted, {
        ...project,
        id: "other",
      }),
    ).toBe(submitted);
    expect(acknowledgeControlsSave(null, submitted, project)).toBeNull();
  });
});
