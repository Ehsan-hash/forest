import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import type { Graph, Json, Run } from "./api";
import { parseJson, uid } from "./api";
import { requestApi } from "./apiClient";
import type { components } from "./apiClient";
import { Button, ErrorBox, Field, useLoad, useUI } from "./ui";

function useToolDraft<T>(key: string, initial: T) {
  const [draft, setDraft] = useState<T>(() => {
    try {
      return JSON.parse(sessionStorage.getItem(key) || "null") || initial;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      sessionStorage.setItem(key, JSON.stringify(draft));
    } catch {
      /* Editing remains available without browser storage. */
    }
  }, [key, draft]);
  return [draft, setDraft] as const;
}

function jsonObject(text: string): Json {
  const value = parseJson(text);
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Enter a JSON object.");
  return value;
}

function Submission({
  projectId,
  operation,
  label,
  payload,
  submit,
}: {
  projectId: string;
  operation:
    | "repository-clone"
    | "paired-inference"
    | "statistics-review"
    | "independent-checks";
  label: string;
  payload: () => Json;
  submit: (body: Json) => Promise<Run>;
}) {
  const { t } = useUI();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [receipt, setReceipt] = useState<Run | null>(null);
  const attemptKey = `forest-tool-attempt:${projectId}:${operation}`;
  const lock = useRef(false),
    intent = useRef<{ serialized: string; requestId: string } | null>(
      (() => {
        try {
          return JSON.parse(sessionStorage.getItem(attemptKey) || "null");
        } catch {
          return null;
        }
      })(),
    );
  return (
    <>
      <ErrorBox error={error} />
      <Button
        className="primary"
        busy={busy}
        onClick={async () => {
          if (lock.current) return;
          lock.current = true;
          setBusy(true);
          setError("");
          try {
            const body = payload(),
              serialized = JSON.stringify(body);
            if (intent.current?.serialized !== serialized)
              intent.current = { serialized, requestId: uid() };
            try {
              sessionStorage.setItem(
                attemptKey,
                JSON.stringify(intent.current),
              );
            } catch {
              /* The active request intent remains available. */
            }
            const result = await submit({
              ...body,
              request_id: intent.current.requestId,
            });
            if (result.project_id !== projectId)
              throw new Error(
                t(
                  "返回的运行不属于当前项目。",
                  "Returned run belongs to another project.",
                ),
              );
            setReceipt(result);
            intent.current = null;
            try {
              sessionStorage.removeItem(attemptKey);
            } catch {
              /* The accepted receipt is available in run history. */
            }
            window.dispatchEvent(new Event("forest-refresh"));
          } catch (failure) {
            setError((failure as Error).message);
          } finally {
            lock.current = false;
            setBusy(false);
          }
        }}
      >
        {label}
      </Button>
      {receipt && (
        <p role="status">
          {t("提交已接收", "Submission accepted")} ·{" "}
          <NavLink
            className="text-link"
            to={`/projects/${projectId}/runs?run=${encodeURIComponent(receipt.id)}`}
          >
            {t("检查实际运行", "Inspect actual run")} {receipt.id}
          </NavLink>
        </p>
      )}
    </>
  );
}

export function RepositoryInput({ projectId }: { projectId: string }) {
  const { t } = useUI();
  const [draft, setDraft] = useToolDraft(
    `forest-repository-input:${projectId}`,
    {
      url: "",
      ref: "HEAD",
      directory: "source",
      transport: "auto",
      credential: "",
    },
  );
  return (
    <section className="page advanced-tools">
      <details className="surface">
        <summary>{t("代码仓库输入", "Repository input")}</summary>
        <p>
          {t(
            "准备 GitHub 源码，并在运行记录中保留实际 commit。",
            "Prepare a GitHub checkout and retain its actual commit in the run record.",
          )}
        </p>
        <Field label={t("GitHub 仓库地址", "GitHub repository URL")}>
          <input
            value={draft.url}
            onChange={(event) =>
              setDraft({ ...draft, url: event.target.value })
            }
            placeholder="https://github.com/owner/repository"
          />
        </Field>
        <div className="form-row">
          <Field label={t("分支、标签或 commit", "Branch, tag or commit")}>
            <input
              value={draft.ref}
              onChange={(event) =>
                setDraft({ ...draft, ref: event.target.value })
              }
            />
          </Field>
          <Field label={t("工作区相对目录", "Workspace relative directory")}>
            <input
              value={draft.directory}
              onChange={(event) =>
                setDraft({ ...draft, directory: event.target.value })
              }
            />
          </Field>
        </div>
        <div className="form-row">
          <Field label={t("传输方式", "Transport")}>
            <select
              value={draft.transport}
              onChange={(event) =>
                setDraft({ ...draft, transport: event.target.value })
              }
            >
              <option value="auto">Auto</option>
              <option value="https">HTTPS</option>
              <option value="ssh">SSH</option>
            </select>
          </Field>
          <Field
            label={t(
              "已有凭据配置名称（可选）",
              "Existing credential profile name (optional)",
            )}
          >
            <input
              value={draft.credential}
              onChange={(event) =>
                setDraft({ ...draft, credential: event.target.value })
              }
            />
          </Field>
        </div>
        <Submission
          projectId={projectId}
          operation="repository-clone"
          label={t("准备仓库", "Prepare repository")}
          payload={() => ({
            repository: {
              url: draft.url,
              ref: draft.ref,
              directory: draft.directory,
              transport: draft.transport,
              ...(draft.credential ? { credential: draft.credential } : {}),
            },
          })}
          submit={(body) =>
            requestApi("/api/projects/{ident}/repositories/clone", "post", {
              path: { ident: projectId },
              body: body as {
                repository: { url: string };
                request_id?: string;
              },
            }) as Promise<Run>
          }
        />
      </details>
    </section>
  );
}

export function StatisticsTools({ projectId }: { projectId: string }) {
  const { t } = useUI();
  const [draft, setDraft] = useToolDraft(
    `forest-paired-statistics:${projectId}`,
    {
      path: "",
      unit_column: "",
      baseline_column: "",
      candidate_column: "",
      direction: "higher",
      confidence: "0.95",
      bootstrap_samples: "2000",
      seed: "0",
      review: "{}",
    },
  );
  return (
    <section className="page advanced-tools">
      <details className="surface">
        <summary>
          {t("配对推断与统计审阅", "Paired inference & statistics review")}
        </summary>
        <p>
          {t(
            "以实际数据表的独立单位聚合配对观测；提交后在运行输出中检查结果。",
            "Aggregate paired observations by independent units in an actual data table. Inspect the result in run output after submission.",
          )}
        </p>
        <Field
          label={t(
            "已保存数据表的项目相对路径",
            "Saved table project-relative path",
          )}
        >
          <input
            value={draft.path}
            onChange={(event) =>
              setDraft({ ...draft, path: event.target.value })
            }
          />
        </Field>
        <div className="form-row">
          {(
            [
              ["unit_column", t("独立单位列", "Independent unit column")],
              ["baseline_column", t("基线列", "Baseline column")],
              ["candidate_column", t("候选列", "Candidate column")],
            ] as const
          ).map(([key, label]) => (
            <Field key={key} label={label}>
              <input
                value={draft[key]}
                onChange={(event) =>
                  setDraft({ ...draft, [key]: event.target.value })
                }
              />
            </Field>
          ))}
        </div>
        <div className="form-row">
          <Field label={t("改进方向", "Improvement direction")}>
            <select
              value={draft.direction}
              onChange={(event) =>
                setDraft({ ...draft, direction: event.target.value })
              }
            >
              <option value="higher">{t("更高", "Higher")}</option>
              <option value="lower">{t("更低", "Lower")}</option>
            </select>
          </Field>
          {(
            [
              ["confidence", t("置信水平", "Confidence level")],
              ["bootstrap_samples", t("Bootstrap 次数", "Bootstrap samples")],
              ["seed", t("重采样种子", "Resampling seed")],
            ] as const
          ).map(([key, label]) => (
            <Field key={key} label={label}>
              <input
                type="number"
                value={draft[key]}
                onChange={(event) =>
                  setDraft({ ...draft, [key]: event.target.value })
                }
              />
            </Field>
          ))}
        </div>
        <Submission
          projectId={projectId}
          operation="paired-inference"
          label={t("运行配对推断", "Run paired inference")}
          payload={() => ({
            path: draft.path,
            unit_column: draft.unit_column,
            baseline_column: draft.baseline_column,
            candidate_column: draft.candidate_column,
            direction: draft.direction,
            confidence: Number(draft.confidence),
            bootstrap_samples: Number(draft.bootstrap_samples),
            seed: Number(draft.seed),
          })}
          submit={(body) =>
            requestApi("/api/projects/{ident}/statistics/paired", "post", {
              path: { ident: projectId },
              body: body as {
                path: string;
                unit_column: string;
                baseline_column: string;
                candidate_column: string;
              },
            }) as Promise<Run>
          }
        />
        <details>
          <summary>
            {t("统计审阅配置", "Statistics review configuration")}
          </summary>
          <Field
            label={t(
              "审阅来源与范围（JSON）",
              "Review sources and scope (JSON)",
            )}
          >
            <textarea
              rows={6}
              value={draft.review}
              onChange={(event) =>
                setDraft({ ...draft, review: event.target.value })
              }
            />
          </Field>
          <Submission
            projectId={projectId}
            operation="statistics-review"
            label={t("提交统计审阅", "Submit statistics review")}
            payload={() => jsonObject(draft.review)}
            submit={(body) =>
              requestApi("/api/projects/{ident}/statistics/review", "post", {
                path: { ident: projectId },
                body,
              }) as Promise<Run>
            }
          />
        </details>
      </details>
    </section>
  );
}

export function VerificationSetup({ projectId }: { projectId: string }) {
  const { t } = useUI();
  const [open, setOpen] = useState(false);
  const graph = useLoad<Graph | null>(
    open ? `/projects/${projectId}/graph` : null,
    null,
  );
  const [draft, setDraft] = useToolDraft(
    `forest-verification-setup:${projectId}`,
    { producer: "", verifier: "", checks: "[]", execution: "{}" },
  );
  return (
    <section className="page advanced-tools">
      <details
        className="surface"
        onToggle={(event) => setOpen(event.currentTarget.open)}
      >
        <summary>{t("独立证据检查", "Independent evidence checks")}</summary>
        <p>
          {t(
            "指定产出节点、实际文件与检查范围。数值或字节比较需要独立执行的重复结果；最终判定来自服务端的来源绑定检查。",
            "Declare the producer, actual files and check scope. Numeric or byte comparisons require independently executed repeat results. The service determines the source-bound verdict.",
          )}
        </p>
        <ErrorBox error={graph.error} retry={graph.reload} />
        <Field label={t("产出节点", "Producer node")}>
          <select
            value={draft.producer}
            onChange={(event) =>
              setDraft({ ...draft, producer: event.target.value })
            }
          >
            <option value="">{t("选择节点", "Choose node")}</option>
            {graph.data?.nodes
              .filter(
                (node) => !node.archived && node.config.kind !== "verification",
              )
              .map((node) => (
                <option key={node.id} value={node.id}>
                  {node.title} · {node.id}
                </option>
              ))}
          </select>
        </Field>
        <Field label={t("检查节点（可选）", "Verification node (optional)")}>
          <select
            value={draft.verifier}
            onChange={(event) =>
              setDraft({ ...draft, verifier: event.target.value })
            }
          >
            <option value="">
              {t("独立运行记录", "Standalone run record")}
            </option>
            {graph.data?.nodes
              .filter(
                (node) => !node.archived && node.config.kind === "verification",
              )
              .map((node) => (
                <option key={node.id} value={node.id}>
                  {node.title} · {node.id}
                </option>
              ))}
          </select>
        </Field>
        <Field
          label={t("检查声明（JSON 数组）", "Declared checks (JSON array)")}
        >
          <textarea
            rows={7}
            value={draft.checks}
            onChange={(event) =>
              setDraft({ ...draft, checks: event.target.value })
            }
          />
        </Field>
        <p className="muted">
          numeric_compare · byte_compare · csv_integrity · csv_coverage ·
          paired_recompute · data_contract
        </p>
        <Field
          label={t(
            "检查执行配置（JSON）",
            "Verification execution configuration (JSON)",
          )}
        >
          <textarea
            rows={6}
            value={draft.execution}
            onChange={(event) =>
              setDraft({ ...draft, execution: event.target.value })
            }
          />
        </Field>
        <Submission
          projectId={projectId}
          operation="independent-checks"
          label={t("提交独立检查", "Submit independent checks")}
          payload={() => {
            const checks = parseJson(draft.checks);
            if (!Array.isArray(checks))
              throw new Error(
                t(
                  "检查声明必须是 JSON 数组。",
                  "Declared checks must be a JSON array.",
                ),
              );
            if (!draft.producer)
              throw new Error(t("请选择产出节点。", "Choose a producer node."));
            return {
              ...jsonObject(draft.execution),
              project_id: projectId,
              ...(draft.verifier ? { node_id: draft.verifier } : {}),
              verification: { producer_node_id: draft.producer, checks },
            };
          }}
          submit={(body) =>
            requestApi("/api/verification/run", "post", {
              body: body as components["schemas"]["VerificationRequest"],
            }) as Promise<Run>
          }
        />
      </details>
    </section>
  );
}
