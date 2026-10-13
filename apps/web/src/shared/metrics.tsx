import type { Json } from "../api";

export function compactMetrics(metrics: Json) {
  return (
    Object.entries(metrics)
      .filter(
        ([key, value]) =>
          typeof value === "number" ||
          typeof value === "boolean" ||
          (typeof value === "string" && key === "evidence_label"),
      )
      .slice(0, 5)
      .map(
        ([key, value]) =>
          `${key}: ${typeof value === "number" ? Number(value.toPrecision(5)) : value}`,
      )
      .join(" · ") || Object.keys(metrics).join(", ")
  );
}
