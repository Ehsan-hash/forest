import { useRef, useState } from "react";
import { ApiError } from "./api";
import { requestApi } from "./apiClient";
import type { components } from "./apiClient";
import { Button, ErrorBox, JsonView, useUI } from "./ui";

type WritingEdit = components["schemas"]["WritingEdit"];
export function applyWritingEdit(source: string, edit: WritingEdit): string {
  if (edit.requires_evidence_judgment || edit.replacement === null)
    throw new Error("This phrase requires an evidence judgment.");
  // Python review offsets count Unicode code points, unlike JavaScript string offsets.
  const characters = Array.from(source);
  if (
    !Number.isInteger(edit.start) ||
    !Number.isInteger(edit.end) ||
    edit.start < 0 ||
    edit.end < edit.start ||
    characters.slice(edit.start, edit.end).join("") !== edit.original
  )
    throw new Error(
      "The reviewed source span changed. Review the current draft again.",
    );
  return (
    characters.slice(0, edit.start).join("") +
    edit.replacement +
    characters.slice(edit.end).join("")
  );
}

export function PaperWritingReview({
  projectId,
  source,
  onChange,
  disabled = false,
}: {
  projectId: string;
  source: string;
  onChange: (source: string) => void;
  disabled?: boolean;
}) {
  const { t } = useUI();
  const [report, setReport] = useState<{
    projectId: string;
    source: string;
    result: components["schemas"]["WritingReview"];
  } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const currentProject = useRef(projectId);
  currentProject.current = projectId;
  const current = report?.projectId === projectId ? report : null;
  return (
    <details className="paper-layout">
      <summary>{t("逐处措辞检查", "Literal writing review")}</summary>
      <ErrorBox error={error} />
      <Button
        busy={busy}
        disabled={disabled || !source.trim()}
        onClick={async () => {
          if (pending.current) return;
          pending.current = true;
          setBusy(true);
          setError("");
          try {
            const reviewedSource = source;
            const result = await requestApi(
              "/api/projects/{ident}/writing/review",
              "post",
              { path: { ident: projectId }, body: { source: reviewedSource } },
            );
            if (currentProject.current === projectId)
              setReport({ projectId, source: reviewedSource, result });
          } catch (failure) {
            if (currentProject.current === projectId)
              setError(
                failure instanceof ApiError
                  ? `${failure.code}: ${failure.message}`
                  : (failure as Error).message,
              );
          } finally {
            pending.current = false;
            setBusy(false);
          }
        }}
      >
        {t("检查当前草稿措辞", "Review current draft wording")}
      </Button>
      {current && (
        <>
          <p>{current.result.coverage}</p>
          {current.source !== source && (
            <p role="status">
              {t(
                "草稿已变化；重新检查后再应用修改。",
                "The draft changed. Review it again before applying edits.",
              )}
            </p>
          )}
          <ol>
            {current.result.edits.map((edit, index) => (
              <li key={`${edit.start}:${index}`}>
                <strong>
                  {t("行", "Line")} {edit.line}
                </strong>{" "}
                · {edit.reason}
                <p>
                  <code>{edit.original}</code>
                  {edit.replacement !== null && (
                    <>
                      {" "}
                      →{" "}
                      <code>
                        {edit.replacement ||
                          t("删除冗余措辞", "Remove redundant wording")}
                      </code>
                    </>
                  )}
                </p>
                {edit.requires_evidence_judgment ||
                edit.replacement === null ? (
                  <p className="muted">
                    {t(
                      "需要作者结合证据判断。",
                      "Requires the author's evidence judgment.",
                    )}
                  </p>
                ) : (
                  <Button
                    disabled={disabled || busy || current.source !== source}
                    onClick={() => {
                      try {
                        onChange(applyWritingEdit(source, edit));
                      } catch (failure) {
                        setError((failure as Error).message);
                      }
                    }}
                  >
                    {t("应用到草稿", "Apply to draft")}
                  </Button>
                )}
              </li>
            ))}
          </ol>
          {!current.result.edits.length && (
            <p>{t("未找到所检查的措辞。", "No matching phrases found.")}</p>
          )}
          <details>
            <summary>{t("检查策略", "Review policy")}</summary>
            <JsonView value={current.result.profile} />
            <p>{current.result.editing_policy}</p>
          </details>
        </>
      )}
    </details>
  );
}
