import { useEffect, useState } from "react";
import type { Json } from "./api";

export type SettingsDraft = { initialized: boolean; text: string; base: Json };
const storageKey = "forest-settings-draft";
export function settingsDraftFromSnapshot(settings: Json): SettingsDraft {
  return {
    initialized: true,
    text: JSON.stringify(settings, null, 2),
    base: settings,
  };
}
export function settingsDraftDirty(draft: SettingsDraft) {
  return (
    draft.initialized && draft.text !== JSON.stringify(draft.base, null, 2)
  );
}
export function reconcileSettingsDraft(
  draft: SettingsDraft,
  settings: Json,
): SettingsDraft {
  return settingsDraftDirty(draft)
    ? draft
    : settingsDraftFromSnapshot(settings);
}
/** The settings endpoint shallow-merges supplied keys; untouched keys must stay server-owned. */
export function settingsDraftPatch(draft: SettingsDraft): Json {
  const value = JSON.parse(draft.text);
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Settings must be a JSON object.");
  return Object.fromEntries(
    Object.entries(value).filter(
      ([key, value]) =>
        JSON.stringify(value) !== JSON.stringify(draft.base[key]),
    ),
  );
}
export function applyProviderToSettingsDraft(
  draft: SettingsDraft,
  providerId: string,
  saved: Json,
): SettingsDraft {
  if (!settingsDraftDirty(draft)) return settingsDraftFromSnapshot(saved);
  return {
    ...draft,
    text: updateDefaultProviderDraft(draft.text, providerId),
    base: { ...draft.base, default_provider_id: providerId },
  };
}
export function acknowledgeSettingsSave(
  current: SettingsDraft,
  submitted: SettingsDraft,
  saved: Json,
): SettingsDraft {
  if (current.text === submitted.text) return settingsDraftFromSnapshot(saved);
  try {
    const edits = settingsDraftPatch({
      initialized: true,
      text: current.text,
      base: JSON.parse(submitted.text),
    });
    return {
      initialized: true,
      text: JSON.stringify({ ...saved, ...edits }, null, 2),
      base: saved,
    };
  } catch {
    return {
      ...current,
      base: { ...current.base, ...settingsDraftPatch(submitted) },
    };
  }
}
export function useSettingsDraft(settings: Json, loaded: boolean) {
  const [draft, setDraft] = useState<SettingsDraft>(() => {
    try {
      const stored = JSON.parse(sessionStorage.getItem(storageKey) || "null");
      if (
        stored?.initialized === true &&
        typeof stored.text === "string" &&
        stored.base &&
        typeof stored.base === "object" &&
        !Array.isArray(stored.base)
      )
        return stored;
    } catch {
      /* Saved settings remain accessible when draft storage is unavailable. */
    }
    return { initialized: false, text: "{}", base: {} };
  });
  useEffect(() => {
    if (loaded)
      setDraft((current) => reconcileSettingsDraft(current, settings));
  }, [loaded, settings]);
  useEffect(() => {
    try {
      if (settingsDraftDirty(draft))
        sessionStorage.setItem(storageKey, JSON.stringify(draft));
      else sessionStorage.removeItem(storageKey);
    } catch {
      /* Keep the live draft editable. */
    }
  }, [draft]);
  return { draft, setDraft, dirty: settingsDraftDirty(draft) };
}

export function updateDefaultProviderDraft(text: string, providerId: string) {
  try {
    const current = JSON.parse(text);
    if (!current || typeof current !== "object" || Array.isArray(current))
      return text;
    return JSON.stringify(
      { ...current, default_provider_id: providerId },
      null,
      2,
    );
  } catch {
    return text;
  }
}
