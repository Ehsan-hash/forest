import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { formatDate, uid } from "../api";
import type { Graph, Run } from "../api";
import { requestApi } from "../apiClient";
import type { components } from "../../../../packages/contracts/api.generated";
import {
  Badge,
  Button,
  ErrorBox,
  Field,
  JsonView,
  Modal,
  useLoad,
  useUI,
} from "../ui";
import {
  activeInstructionRuns,
  instructionTargetIssue,
  updateInstructionDraft,
} from "./instructionDraft";

type Intervention = components["schemas"]["InterventionView"];
type Decision = components["schemas"]["DecisionView"];
type Instruction = components["schemas"]["InstructionRequest"];
type HistoryPage = {
  before: { created_at: string; id: string };
  rows: Intervention[];
  hasMore: boolean;
};

export function InterventionPanel({ projectId }: { projectId: string }) {
  const { t } = useUI();
  const receipts = useLoad<Intervention[]>(
    `/projects/${projectId}/interventions?limit=21`,
    [],
  );
  const decisions = useLoad<Decision[]>(
    `/projects/${projectId}/decisions?limit=50`,
    [],
  );
  const scope = useRef({ projectId, generation: 0 });
  if (scope.current.projectId !== projectId) {
    scope.current = { projectId, generation: scope.current.generation + 1 };
  }
  const isCurrent = (generation: number) =>
    scope.current.projectId === projectId &&
    scope.current.generation === generation;
  const [draft, setDraft] = useState<Instruction | null>(null);
  const runs = useLoad<Run[]>(
    draft?.scope === "run" ? `/projects/${projectId}/runs?limit=500` : null,
    [],
  );
  const [graph, setGraph] = useState<Graph | null>(null);
  const [opening, setOpening] = useState(false);
  const openingRef = useRef(false);
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const [instructionError, setInstructionError] = useState("");
  const [needsReview, setNeedsReview] = useState(false);
  const [currentGraph, setCurrentGraph] = useState<Graph | null>(null);
  const [loadingReview, setLoadingReview] = useState(false);
  const [review, setReview] = useState<Decision | null>(null);
  const [answering, setAnswering] = useState(false);
  const answeringRef = useRef(false);
  const [decisionError, setDecisionError] = useState("");
  const [choice, setChoice] = useState<"accept" | "edit" | "reject">("accept");
  const [payload, setPayload] = useState("");
  const [reason, setReason] = useState("");
  const [resumeError, setResumeError] = useState("");
  const [resuming, setResuming] = useState(() => new Set<string>());
  const resumingRef = useRef(new Set<string>());
  const [olderPages, setOlderPages] = useState<HistoryPage[]>([]);
  const olderPagesRef = useRef<HistoryPage[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(false);
  const loadingHistoryRef = useRef(false);
  const historyGeneration = useRef(0);
  const visibleReceipts = [
    ...receipts.data.slice(0, 20),
    ...olderPages.flatMap((page) => page.rows),
  ].filter((row) => row.project_id === projectId);
  const latestPage = olderPages[olderPages.length - 1];
  const hasOlder = latestPage ? latestPage.hasMore : receipts.data.length > 20;
  const loadHistoryPage = useCallback(
    async (before: HistoryPage["before"]) => {
      const rows = await requestApi(
        "/api/projects/{ident}/interventions",
        "get",
        {
          path: { ident: projectId },
          query: { before: before.created_at, before_id: before.id, limit: 21 },
        },
      );
      if (rows.some((row) => row.project_id !== projectId))
        throw new Error("Received intervention history for another project.");
      return rows;
    },
    [projectId],
  );
  useEffect(
    () => () => {
      scope.current.generation += 1;
    },
    [],
  );
  useEffect(() => {
    historyGeneration.current += 1;
    olderPagesRef.current = [];
    setOlderPages([]);
    setHistoryError("");
    setDraft(null);
    setGraph(null);
    setReview(null);
    setCurrentGraph(null);
    setNeedsReview(false);
    setInstructionError("");
    setDecisionError("");
    setResumeError("");
    openingRef.current =
      sendingRef.current =
      answeringRef.current =
      loadingHistoryRef.current =
        false;
    resumingRef.current = new Set();
    setResuming(new Set());
    setOpening(false);
    setSending(false);
    setAnswering(false);
    setLoadingHistory(false);
    setLoadingReview(false);
  }, [projectId]);
  useEffect(() => {
    let live = true;
    let historyPending = false;
    const generation = scope.current.generation;
    const refresh = () => {
      void receipts.reload();
      void decisions.reload();
      void runs.reload();
      const loaded = olderPagesRef.current;
      if (loaded.length && !historyPending && !loadingHistoryRef.current) {
        historyPending = true;
        const historyVersion = historyGeneration.current;
        void Promise.all(
          loaded.map(async (page) => {
            const rows = await loadHistoryPage(page.before);
            return {
              ...page,
              rows: rows.slice(0, 20),
              hasMore: rows.length > 20,
            };
          }),
        )
          .then((pages) => {
            if (
              !live ||
              !isCurrent(generation) ||
              historyGeneration.current !== historyVersion
            )
              return;
            olderPagesRef.current = pages;
            setOlderPages(pages);
            setHistoryError("");
          })
          .catch((error) => {
            if (
              live &&
              isCurrent(generation) &&
              historyGeneration.current === historyVersion
            )
              setHistoryError((error as Error).message);
          })
          .finally(() => {
            historyPending = false;
          });
      }
    };
    const timer = setInterval(refresh, 2000);
    window.addEventListener("forest-refresh", refresh);
    return () => {
      live = false;
      clearInterval(timer);
      window.removeEventListener("forest-refresh", refresh);
    };
  }, [receipts.reload, decisions.reload, runs.reload, loadHistoryPage]);

  const availableRuns = activeInstructionRuns(runs.data, projectId);
  const targetIssue =
    draft && graph
      ? instructionTargetIssue(draft, projectId, graph, runs.data)
      : null;
  const targetMessage =
    targetIssue === "missing_branch"
      ? t(
          "请选择当前项目中存在的分支。",
          "Choose an existing branch in this project.",
        )
      : targetIssue === "missing_node"
        ? t(
            "请选择当前项目中存在的节点。",
            "Choose an existing node in this project.",
          )
        : targetIssue === "run_boundary"
          ? t(
              "运行指令只支持下一模型请求边界。",
              "Run instructions require the next model request boundary.",
            )
          : targetIssue === "missing_run"
            ? t(
                "请选择当前项目中仍在活动状态的 Agent 运行；已停止的运行不能采用新指令。",
                "Choose an active Agent run in this project. Stopped runs cannot consume a new instruction.",
              )
            : "";
  const instructionDisabled =
    !draft?.text.trim() ||
    draft.text.length > 100000 ||
    !!targetIssue ||
    needsReview ||
    sending ||
    (draft?.scope === "run" && (runs.loading || !!runs.error));

  const openInstruction = async () => {
    if (openingRef.current) return;
    openingRef.current = true;
    setOpening(true);
    const generation = scope.current.generation;
    try {
      const value = await requestApi("/api/projects/{ident}/graph", "get", {
        path: { ident: projectId },
      });
      if (!isCurrent(generation)) return;
      if (value.project_id !== projectId)
        throw new Error("Received a graph for another project.");
      setGraph(value as Graph);
      setDraft({
        request_id: uid(),
        expected_revision: value.revision,
        text: "",
        scope: "project",
        target_id: null,
        boundary: "next_request",
      });
      setInstructionError("");
      setNeedsReview(false);
      setCurrentGraph(null);
      void runs.reload();
    } catch (error) {
      if (isCurrent(generation)) setInstructionError((error as Error).message);
    } finally {
      if (isCurrent(generation)) {
        openingRef.current = false;
        setOpening(false);
      }
    }
  };
  const sendInstruction = async () => {
    if (!draft || instructionDisabled || sendingRef.current) return;
    const submitted = { ...draft };
    const generation = scope.current.generation;
    sendingRef.current = true;
    setSending(true);
    setInstructionError("");
    try {
      const accepted = await requestApi(
        "/api/projects/{ident}/instructions",
        "post",
        { path: { ident: projectId }, body: submitted },
      );
      if (!isCurrent(generation)) return;
      if (
        accepted.project_id !== projectId ||
        accepted.request_id !== submitted.request_id
      )
        throw new Error(
          "The instruction receipt does not match the submitted project and request.",
        );
      receipts.setData((current) =>
        [accepted, ...current.filter((item) => item.id !== accepted.id)].slice(
          0,
          21,
        ),
      );
      setDraft(null);
      setGraph(null);
      void receipts.reload();
    } catch (error) {
      if (!isCurrent(generation)) return;
      setInstructionError((error as Error).message);
      if ((error as { code?: string }).code === "REVISION_CONFLICT") {
        setNeedsReview(true);
        setCurrentGraph(null);
      }
      void runs.reload();
    } finally {
      if (isCurrent(generation)) {
        sendingRef.current = false;
        setSending(false);
      }
    }
  };
  const loadCurrentReview = async () => {
    if (loadingReview || !draft) return;
    const generation = scope.current.generation;
    setLoadingReview(true);
    try {
      const value = await requestApi("/api/projects/{ident}/graph", "get", {
        path: { ident: projectId },
      });
      if (!isCurrent(generation)) return;
      if (value.project_id !== projectId)
        throw new Error("Received a graph for another project.");
      setCurrentGraph(value as Graph);
    } catch (error) {
      if (isCurrent(generation)) setInstructionError((error as Error).message);
    } finally {
      if (isCurrent(generation)) setLoadingReview(false);
    }
  };
  const loadOlder = async () => {
    if (loadingHistoryRef.current) return;
    const pages = olderPagesRef.current;
    const lastRows = pages.length ? pages[pages.length - 1].rows : [];
    const lastVisible = pages.length
      ? lastRows[lastRows.length - 1]
      : receipts.data[19];
    if (!lastVisible || lastVisible.project_id !== projectId) return;
    loadingHistoryRef.current = true;
    setLoadingHistory(true);
    const generation = scope.current.generation;
    const historyVersion = ++historyGeneration.current;
    try {
      const before = { created_at: lastVisible.created_at, id: lastVisible.id };
      const rows = await loadHistoryPage(before);
      if (
        !isCurrent(generation) ||
        historyGeneration.current !== historyVersion
      )
        return;
      const next = [
        ...olderPagesRef.current,
        { before, rows: rows.slice(0, 20), hasMore: rows.length > 20 },
      ];
      olderPagesRef.current = next;
      setOlderPages(next);
      setHistoryError("");
    } catch (error) {
      if (isCurrent(generation) && historyGeneration.current === historyVersion)
        setHistoryError((error as Error).message);
    } finally {
      if (isCurrent(generation)) {
        loadingHistoryRef.current = false;
        setLoadingHistory(false);
      }
    }
  };
  return (
    <section
      className="surface progress-panel"
      aria-label="Human instructions and decisions"
    >
      <div className="section-toolbar">
        <h2>{t("人工指令与生效记录", "Human instructions and effects")}</h2>
        <Button
          busy={opening}
          disabled={opening}
          onClick={() => void openInstruction()}
        >
          {t("作为指令发送", "Send as instruction")}
        </Button>
      </div>
      <p className="muted">
        {t(
          "指令在所选边界采用；已保存不代表模型已消费。普通评论保留为批注。",
          "Instructions apply at the selected boundary. Saved does not mean consumed. Ordinary comments remain annotations.",
        )}
      </p>
      <ErrorBox
        error={
          receipts.error ||
          decisions.error ||
          resumeError ||
          historyError ||
          (!draft ? instructionError : "")
        }
      />
      {decisions.data
        .filter(
          (item) =>
            item.project_id === projectId &&
            (item.status === "pending" ||
              (!item.consumed_at &&
                ["accepted", "rejected"].includes(item.status))),
        )
        .map((item) => (
          <div className="progress-fact" key={item.id}>
            <strong>
              {item.status === "pending"
                ? t("等待人工决定", "Waiting for human decision")
                : item.can_resume
                  ? t(
                      "决定已保存，等待继续",
                      "Decision saved; awaiting continuation",
                    )
                  : t(
                      `决定已保存；运行状态：${item.run_status || "未知"}`,
                      `Decision saved; run status: ${item.run_status || "unknown"}`,
                    )}
            </strong>
            <NavLink to={`/projects/${projectId}/runs?run=${item.run_id}`}>
              {item.run_id.slice(0, 8)}
            </NavLink>
            <span>
              {" "}
              · {String(item.proposed.tool)} · {formatDate(item.created_at)}
            </span>
            {item.status === "pending" ? (
              <Button
                onClick={() => {
                  setReview(item);
                  setPayload(JSON.stringify(item.proposed, null, 2));
                  setChoice("accept");
                  setReason("");
                  setDecisionError("");
                }}
              >
                {t("审阅动作", "Review action")}
              </Button>
            ) : item.can_resume ? (
              <Button
                busy={resuming.has(item.run_id)}
                disabled={resuming.has(item.run_id)}
                onClick={() => {
                  if (resumingRef.current.has(item.run_id)) return;
                  const generation = scope.current.generation;
                  resumingRef.current.add(item.run_id);
                  setResuming(new Set(resumingRef.current));
                  void requestApi("/api/runs/{ident}/{action}", "post", {
                    path: { ident: item.run_id, action: "resume" },
                    body: {},
                  })
                    .then(() => {
                      if (!isCurrent(generation)) return;
                      setResumeError("");
                      void decisions.reload();
                    })
                    .catch((error) => {
                      if (isCurrent(generation))
                        setResumeError((error as Error).message);
                    })
                    .finally(() => {
                      if (!isCurrent(generation)) return;
                      resumingRef.current.delete(item.run_id);
                      setResuming(new Set(resumingRef.current));
                    });
                }}
              >
                {t("继续已保存的决定", "Continue saved decision")}
              </Button>
            ) : null}
          </div>
        ))}
      {!visibleReceipts.length && (
        <p>{t("还没有正式干预记录。", "No formal intervention recorded.")}</p>
      )}
      <ul>
        {visibleReceipts.map((row) => (
          <li className="progress-fact" key={row.id}>
            <div>
              <strong>
                {row.kind === "instruction"
                  ? t("正式指令", "Owner instruction")
                  : row.kind === "configuration"
                    ? t("项目控制", "Project controls")
                    : row.kind === "run_control"
                      ? t("运行控制", "Run control")
                      : row.kind === "goal_applicability"
                        ? t("证据用途决定", "Evidence-use decision")
                        : t("图修改", "Graph edit")}
              </strong>{" "}
              <Badge status={row.status} /> · {formatDate(row.accepted_at)}
            </div>
            {row.kind === "instruction" && (
              <p>{String(row.intent.text || "")}</p>
            )}
            <small>
              {row.status === "accepted"
                ? t(
                    "已保存，等待对应请求或运行效果确认。",
                    "Saved; waiting for request delivery or execution confirmation.",
                  )
                : row.applied_at
                  ? `${t("已确认生效", "Confirmed applied")} · ${formatDate(row.applied_at)}`
                  : row.status}
            </small>
            {row.effects.map((effect) => (
              <div key={effect.id}>
                <NavLink
                  to={
                    effect.action === "materialize_workspace"
                      ? `/projects/${projectId}/workspace?branch=${effect.run_id}`
                      : `/projects/${projectId}/runs?run=${effect.run_id}`
                  }
                >
                  {effect.run_id.slice(0, 8)}
                </NavLink>{" "}
                · {effect.action} · {effect.status}
                {effect.error && <p role="status">{effect.error}</p>}
              </div>
            ))}
          </li>
        ))}
      </ul>
      {hasOlder && (
        <Button
          busy={loadingHistory}
          disabled={loadingHistory}
          onClick={() => void loadOlder()}
        >
          {t("加载更早记录", "Load older records")}
        </Button>
      )}
      {draft && graph?.project_id === projectId && (
        <Modal
          title={t("发送正式指令", "Send owner instruction")}
          onClose={() => {
            if (!sendingRef.current) setDraft(null);
          }}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void sendInstruction();
            }}
          >
            <ErrorBox error={instructionError} />
            {needsReview && (
              <section role="status">
                <p>
                  {t(
                    "项目在审阅后发生了变化。指令仍保留；请审阅当前版本后再提交。",
                    "The project changed after review. Your instruction is preserved. Review the current version before submitting again.",
                  )}
                </p>
                <Button
                  type="button"
                  busy={loadingReview}
                  disabled={loadingReview}
                  onClick={() => void loadCurrentReview()}
                >
                  {t("审阅当前项目", "Review current project")}
                </Button>
                {currentGraph && (
                  <>
                    <details open>
                      <summary>
                        {t(
                          `当前项目版本 r${currentGraph.revision}`,
                          `Current project revision r${currentGraph.revision}`,
                        )}
                      </summary>
                      <JsonView
                        value={{
                          revision: currentGraph.revision,
                          branches: currentGraph.branches,
                          nodes:
                            draft.scope === "node"
                              ? currentGraph.nodes.filter(
                                  (node) => node.id === draft.target_id,
                                )
                              : currentGraph.nodes.map((node) => ({
                                  id: node.id,
                                  title: node.title,
                                  revision: node.revision,
                                  branch_id: node.branch_id,
                                })),
                        }}
                      />
                    </details>
                    <Button
                      type="button"
                      disabled={sending}
                      onClick={() => {
                        setGraph(currentGraph);
                        setDraft({
                          ...draft,
                          expected_revision: currentGraph.revision,
                          request_id: uid(),
                        });
                        setNeedsReview(false);
                        setCurrentGraph(null);
                        setInstructionError("");
                      }}
                    >
                      {t(
                        "保留指令并采用已审阅版本",
                        "Keep instruction and use reviewed revision",
                      )}
                    </Button>
                  </>
                )}
              </section>
            )}
            <fieldset
              disabled={sending}
              style={{ border: 0, padding: 0, margin: 0 }}
            >
              <Field label={t("指令", "Instruction")}>
                <textarea
                  aria-label="Instruction"
                  required
                  maxLength={100000}
                  value={draft.text}
                  onChange={(event) =>
                    setDraft(
                      updateInstructionDraft(draft, {
                        text: event.target.value,
                      }),
                    )
                  }
                />
              </Field>
              <Field label={t("作用范围", "Instruction scope")}>
                <select
                  aria-label="Instruction scope"
                  value={draft.scope}
                  onChange={(event) =>
                    setDraft(
                      updateInstructionDraft(draft, {
                        scope: event.target.value as Instruction["scope"],
                      }),
                    )
                  }
                >
                  <option value="project">{t("整个项目", "Project")}</option>
                  <option value="branch">{t("指定分支", "Branch")}</option>
                  <option value="node">{t("指定任务", "Node")}</option>
                  <option value="run">{t("指定运行", "Run")}</option>
                </select>
              </Field>
              {draft.scope === "branch" || draft.scope === "node" ? (
                <Field label={t("对象", "Instruction target")}>
                  <select
                    aria-label="Instruction target"
                    required
                    value={draft.target_id || ""}
                    onChange={(event) =>
                      setDraft(
                        updateInstructionDraft(draft, {
                          target_id: event.target.value,
                        }),
                      )
                    }
                  >
                    <option value="">{t("选择对象", "Choose target")}</option>
                    {(draft.scope === "branch"
                      ? graph.branches.map((b) => ({ id: b.id, title: b.name }))
                      : graph.nodes
                    ).map((n) => (
                      <option key={n.id} value={n.id}>
                        {n.title}
                      </option>
                    ))}
                  </select>
                </Field>
              ) : (
                draft.scope === "run" && (
                  <Field label={t("活动 Agent 运行", "Active Agent run")}>
                    <select
                      aria-label="Instruction run"
                      required
                      value={draft.target_id || ""}
                      onChange={(event) =>
                        setDraft(
                          updateInstructionDraft(draft, {
                            target_id: event.target.value,
                          }),
                        )
                      }
                    >
                      <option value="">
                        {t("选择活动 Agent 运行", "Choose an active Agent run")}
                      </option>
                      {draft.target_id &&
                        !availableRuns.some(
                          (run) => run.id === draft.target_id,
                        ) && (
                          <option value={draft.target_id} disabled>
                            {t("不可用的运行", "Unavailable run")} ·{" "}
                            {draft.target_id}
                          </option>
                        )}
                      {availableRuns.map((run) => (
                        <option key={run.id} value={run.id}>
                          {run.id} · {run.status}
                        </option>
                      ))}
                    </select>
                    <ErrorBox
                      error={runs.error}
                      retry={() => void runs.reload()}
                    />
                    {!runs.loading && !runs.error && !availableRuns.length && (
                      <p role="status">
                        {t(
                          "已载入的运行记录中没有活动 Agent 运行。请选择节点、分支或项目范围。",
                          "No active Agent runs in the loaded history. Choose node, branch or project scope.",
                        )}
                      </p>
                    )}
                  </Field>
                )
              )}
              <Field label={t("何时采用", "Instruction boundary")}>
                <select
                  aria-label="Instruction boundary"
                  value={draft.boundary}
                  onChange={(event) =>
                    setDraft(
                      updateInstructionDraft(draft, {
                        boundary: event.target.value as Instruction["boundary"],
                      }),
                    )
                  }
                >
                  <option value="next_request">
                    {t("下一模型请求", "Next model request")}
                  </option>
                  <option value="next_run" disabled={draft.scope === "run"}>
                    {t("之后新建的运行", "New runs created afterward")}
                  </option>
                </select>
              </Field>
              {draft.scope === "run" && (
                <p className="muted">
                  {t(
                    "运行范围只在下一模型请求采用；之后的新运行请使用节点、分支或项目范围。",
                    "Run scope applies at the next model request. Use node, branch or project scope for future runs.",
                  )}
                </p>
              )}
              {targetMessage && <p role="status">{targetMessage}</p>}
              <p>
                {t(
                  "已发出的请求和实验保留原条件。若审阅期间项目改变，需要重新审阅。",
                  "Requests and experiments already sent retain their original conditions. Project changes during review require another review.",
                )}
              </p>
            </fieldset>
            <Button
              type="submit"
              className="primary"
              busy={sending}
              disabled={instructionDisabled}
            >
              {t("发送指令", "Send instruction")}
            </Button>
          </form>
        </Modal>
      )}
      {review?.project_id === projectId && (
        <Modal
          wide
          title={t("审阅待执行动作", "Review pending action")}
          onClose={() => {
            if (!answeringRef.current) setReview(null);
          }}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (answeringRef.current || review.status !== "pending") return;
              const generation = scope.current.generation;
              answeringRef.current = true;
              setAnswering(true);
              setDecisionError("");
              void (async () => {
                try {
                  const answered = await requestApi(
                    "/api/decisions/{ident}/answer",
                    "post",
                    {
                      path: { ident: review.id },
                      body: {
                        expected_revision: review.observed_revision,
                        choice,
                        reason,
                        action: choice === "edit" ? JSON.parse(payload) : null,
                        resume: true,
                      },
                    },
                  );
                  if (!isCurrent(generation)) return;
                  if (
                    answered.id !== review.id ||
                    answered.project_id !== projectId ||
                    answered.run_id !== review.run_id
                  )
                    throw new Error(
                      "The decision receipt does not match the reviewed action and run.",
                    );
                  decisions.setData((current) =>
                    current.map((item) =>
                      item.id === answered.id ? answered : item,
                    ),
                  );
                  setResumeError(answered.resume_error || "");
                  if (answered.status === "stale") {
                    setReview(answered);
                    setDecisionError(
                      t(
                        "项目已改变，此动作已失效。你的编辑仍保留；请返回对应运行检查新的待审动作。",
                        "The project changed and this action is stale. Your edits are preserved. Return to the run to inspect a new pending action.",
                      ),
                    );
                  } else if (
                    ["accepted", "rejected", "consumed"].includes(
                      answered.status,
                    )
                  ) {
                    setReview(null);
                  } else {
                    throw new Error(
                      `Decision was not saved: ${answered.status}`,
                    );
                  }
                  void decisions.reload();
                } catch (error) {
                  if (isCurrent(generation))
                    setDecisionError((error as Error).message);
                } finally {
                  if (isCurrent(generation)) {
                    answeringRef.current = false;
                    setAnswering(false);
                  }
                }
              })();
            }}
          >
            <ErrorBox error={decisionError} />
            {review.status !== "pending" && (
              <NavLink
                className="button"
                to={`/projects/${projectId}/runs?run=${encodeURIComponent(review.run_id)}`}
              >
                {t("打开对应运行", "Open this run")}
              </NavLink>
            )}
            <fieldset
              disabled={answering || review.status !== "pending"}
              style={{ border: 0, padding: 0, margin: 0 }}
            >
              <pre>{JSON.stringify(review.proposed, null, 2)}</pre>
              <Field label={t("决定", "Action decision")}>
                <select
                  value={choice}
                  onChange={(event) =>
                    setChoice(event.target.value as typeof choice)
                  }
                >
                  <option value="accept">
                    {t("执行这一动作", "Execute this action")}
                  </option>
                  <option value="edit">
                    {t("修改后执行", "Edit and execute")}
                  </option>
                  <option value="reject">
                    {t("拒绝这一动作", "Reject this action")}
                  </option>
                </select>
              </Field>
              {choice === "edit" && (
                <Field label={t("修改后的动作", "Edited action")}>
                  <textarea
                    value={payload}
                    onChange={(event) => setPayload(event.target.value)}
                  />
                </Field>
              )}
              <Field label={t("意见或理由", "Decision reason")}>
                <textarea
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                />
              </Field>
              <p>
                {t(
                  "决定绑定这一个动作和审阅时的项目状态，跨重启保留。恢复仍检查剩余预算。",
                  "The decision binds this action and reviewed project state and survives restart. Resume still checks remaining budget.",
                )}
              </p>
            </fieldset>
            <Button
              type="submit"
              className="primary"
              busy={answering}
              disabled={answering || review.status !== "pending"}
            >
              {t("保存决定并继续", "Save decision and continue")}
            </Button>
          </form>
        </Modal>
      )}
    </section>
  );
}
