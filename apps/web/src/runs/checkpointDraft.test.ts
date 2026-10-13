import { describe, expect, it } from "vitest";
import {
  checkpointDraftIdentity,
  reconcileCheckpointDraft,
} from "./checkpointDraft";

describe("debug checkpoint draft identity", () => {
  const point = {
    stage: "before_tool",
    payload: { tool: "read_file", arguments: { path: "a.txt" } },
    override: null,
  };
  it("starts with the actual pending payload and retains configuration fields", () => {
    const draft = reconcileCheckpointDraft(
      { configuration: '{"unknown_config":true}' },
      point,
    );
    expect(JSON.parse(draft.checkpoint)).toEqual(point.payload);
    expect(draft.configuration).toBe('{"unknown_config":true}');
  });
  it("keeps same-payload invalid text and archives it when the saved checkpoint advances", () => {
    const saved = {
      configuration: "{}",
      checkpoint: '{"unfinished":',
      checkpointIdentity: checkpointDraftIdentity(point),
    };
    expect(reconcileCheckpointDraft(saved, point).checkpoint).toBe(
      saved.checkpoint,
    );
    const next = {
      ...point,
      payload: { ...point.payload, arguments: { path: "b.txt" } },
    };
    const draft = reconcileCheckpointDraft(saved, next);
    expect(JSON.parse(draft.checkpoint)).toEqual(next.payload);
    expect(draft.checkpointArchive).toEqual([
      { identity: saved.checkpointIdentity, text: saved.checkpoint },
    ]);
    expect(reconcileCheckpointDraft(draft, next).checkpointArchive).toEqual(
      draft.checkpointArchive,
    );
  });
  it("retains an older unbound draft without assigning it to a current scientific payload", () => {
    const draft = reconcileCheckpointDraft(
      { checkpoint: '{"legacy":true}' },
      point,
    );
    expect(JSON.parse(draft.checkpoint)).toEqual(point.payload);
    expect(draft.checkpointArchive?.[0].text).toBe('{"legacy":true}');
  });
});
