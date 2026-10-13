import { describe, expect, it } from "vitest";
import {
  acknowledgeSettingsSave,
  applyProviderToSettingsDraft,
  reconcileSettingsDraft,
  settingsDraftFromSnapshot,
  settingsDraftPatch,
} from "./settingsDraft";

describe("global settings editing session", () => {
  it("preserves dirty JSON and original baseline during an external settings update", () => {
    const draft = settingsDraftFromSnapshot({
      retention_days: 30,
      unknown: { keep: 7 },
    });
    draft.text = '{"retention_days":14,"unknown":{"keep":7}}';
    expect(
      reconcileSettingsDraft(draft, {
        retention_days: 90,
        unknown: { keep: 9 },
        foreign: true,
      }),
    ).toBe(draft);
    expect(settingsDraftPatch(draft)).toEqual({ retention_days: 14 });
  });
  it("changes the provider without turning untouched unknown keys into a patch", () => {
    const draft = settingsDraftFromSnapshot({
      default_provider_id: "old",
      unknown: { keep: true },
      timeout: 30,
    });
    draft.text =
      '{"default_provider_id":"old","unknown":{"keep":true},"timeout":60}';
    const changed = applyProviderToSettingsDraft(draft, "new", {
      default_provider_id: "new",
      unknown: { keep: true },
      timeout: 30,
    });
    expect(JSON.parse(changed.text).unknown).toEqual({ keep: true });
    expect(settingsDraftPatch(changed)).toEqual({ timeout: 60 });
  });
  it("keeps invalid JSON through provider change and rejects a nonobject save", () => {
    const draft = settingsDraftFromSnapshot({ default_provider_id: "old" });
    draft.text = '{"timeout":';
    expect(
      applyProviderToSettingsDraft(draft, "new", { default_provider_id: "new" })
        .text,
    ).toBe(draft.text);
    expect(() => settingsDraftPatch({ ...draft, text: "[]" })).toThrow(
      "JSON object",
    );
  });
  it("retains edits made while a save is pending and adopts untouched server keys", () => {
    const submitted = settingsDraftFromSnapshot({
      timeout: 30,
      server_key: "before",
    });
    submitted.text = '{"timeout":60,"server_key":"before"}';
    const current = {
      ...submitted,
      text: '{"timeout":90,"server_key":"before","late_edit":true}',
    };
    const acknowledged = acknowledgeSettingsSave(current, submitted, {
      timeout: 60,
      server_key: "after",
      foreign_key: true,
    });
    expect(JSON.parse(acknowledged.text)).toEqual({
      timeout: 90,
      server_key: "after",
      foreign_key: true,
      late_edit: true,
    });
    expect(settingsDraftPatch(acknowledged)).toEqual({
      timeout: 90,
      late_edit: true,
    });
  });
});
