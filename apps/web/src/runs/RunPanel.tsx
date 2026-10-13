import { useEffect, useRef, useState } from "react";
import {
  Pause,
  Play,
  RefreshCw,
  Settings2,
  Square,
  SquareTerminal,
} from "lucide-react";
import { api, formatDate, parseJson, uid } from "../api";
import type { Json, ResearchNode, Run } from "../api";
import { requestApi } from "../apiClient";
import {
  Badge,
  Button,
  ErrorBox,
  Field,
  IconButton,
  JsonView,
  Modal,
  useUI,
} from "../ui";
import { RunEvidence } from "../RunEvidence";
import { canResumeRun } from "./runControls";
import {
  checkpointDraftIdentity,
  reconcileCheckpointDraft,
} from "./checkpointDraft";
import type { RunEditorDraft } from "./checkpointDraft";

type DetailedRun = Run & { resource?: Json; priority?: number };

export function RunPanel({
  runs,
  reload,
  nodes = [],
  branches = [],
  selectedRunId,
  onSelectRun,
  selectedRun,
  selectionError = "",
  selectionLoading = false,
}: {
  runs: Run[];
  reload: () => Promise<void>;
  nodes?: ResearchNode[];
  branches?: Json[];
  selectedRunId?: string;
  onSelectRun?: (id: string) => void;
  selectedRun?: Run | null;
  selectionError?: string;
  selectionLoading?: boolean;
}) {
  const { t } = useUI();
  const [selected, setSelected] = useState("");
  const [output, setOutput] = useState("");
  const [outputError, setOutputError] = useState("");
  const [controlError, setControlError] = useState("");
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [priority, setPriority] = useState("0");
  const [configuration, setConfiguration] = useState("{}");
  const [checkpoint, setCheckpoint] = useState("{}");
  const editorDraft = useRef<RunEditorDraft>({
    configuration: "{}",
    checkpoint: "{}",
  });
  const [checkpointArchive, setCheckpointArchive] = useState<
    RunEditorDraft["checkpointArchive"]
  >([]);
  const [diagnostics, setDiagnostics] = useState<Json | null>(null);
  const [repository, setRepository] = useState<Json | null>(null);
  const retryIntent = useRef<{ runId: string; requestId: string } | null>(null);
  const requestedId = selectedRunId ?? selected;
  const run: DetailedRun | undefined = selectionError
    ? undefined
    : requestedId
      ? (selectedRun?.id === requestedId ? selectedRun : undefined) ||
        runs.find((r) => r.id === requestedId)
      : selectedRun || runs[0];
  const currentRunId = useRef(run?.id);
  currentRunId.current = run?.id;
  const checkpointIdentity = checkpointDraftIdentity(run?.resource?.checkpoint);
  const status = run?.status || "";
  const terminal = [
    "completed",
    "failed",
    "cancelled",
    "interrupted",
    "skipped",
  ].includes(status);
  const active = [
    "queued",
    "running",
    "pausing",
    "paused",
    "waiting_input",
    "waiting",
    "budget_exhausted",
  ].includes(status);
  const allowedActions: Record<string, boolean> = {
    pause: ["queued", "waiting", "budget_exhausted", "running"].includes(
      status,
    ),
    resume: Boolean(run && canResumeRun(run, branches)),
    cancel: [
      "queued",
      "running",
      "pausing",
      "paused",
      "waiting_input",
      "waiting",
      "budget_exhausted",
    ].includes(status),
    retry: terminal,
  };
  useEffect(() => {
    setOutput("");
    setOutputError("");
    setControlError("");
    setBusy(false);
    setAdvanced(false);
    setDiagnostics(null);
    setRepository(null);
    setPriority(String(run?.priority ?? 0));
    let draft: Partial<RunEditorDraft> = {};
    try {
      const stored = JSON.parse(
        sessionStorage.getItem(`forest-run-editor:${run?.id}`) || "{}",
      );
      if (stored && typeof stored === "object" && !Array.isArray(stored))
        draft = stored;
    } catch {
      /* Retain server defaults if storage is unavailable. */
    }
    editorDraft.current = reconcileCheckpointDraft(
      draft,
      run?.resource?.checkpoint,
    );
    setConfiguration(editorDraft.current.configuration);
    setCheckpoint(editorDraft.current.checkpoint);
    setCheckpointArchive(editorDraft.current.checkpointArchive);
    retryIntent.current = null;
    if (!run) return;
    let active = true;
    let offset = 0;
    let pending = false;
    const load = async () => {
      if (pending) return;
      pending = true;
      try {
        const result = await requestApi("/api/runs/{ident}/output", "get", {
          path: { ident: run.id },
          query: { offset },
        });
        if (active) {
          setOutput((o) => (o + result.text).slice(-2_000_000));
          offset = result.offset;
          setOutputError("");
        }
      } catch (e) {
        if (active) setOutputError((e as Error).message);
      } finally {
        pending = false;
      }
    };
    void load();
    const timer = setInterval(load, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [run?.id]);
  useEffect(() => {
    if (!run || editorDraft.current.checkpointIdentity === checkpointIdentity)
      return;
    editorDraft.current = reconcileCheckpointDraft(
      editorDraft.current,
      run.resource?.checkpoint,
    );
    setCheckpoint(editorDraft.current.checkpoint);
    setCheckpointArchive(editorDraft.current.checkpointArchive);
    try {
      sessionStorage.setItem(
        `forest-run-editor:${run.id}`,
        JSON.stringify(editorDraft.current),
      );
    } catch {
      /* The live draft and retained text remain available. */
    }
  }, [run?.id, checkpointIdentity]);
  const editDraft = (field: "configuration" | "checkpoint", value: string) => {
    if (!run) return;
    if (field === "configuration") setConfiguration(value);
    else setCheckpoint(value);
    editorDraft.current = { ...editorDraft.current, [field]: value };
    try {
      sessionStorage.setItem(
        `forest-run-editor:${run.id}`,
        JSON.stringify(editorDraft.current),
      );
    } catch {
      /* Live draft remains editable. */
    }
  };

  const perform = async (operation: () => Promise<unknown>) => {
    if (!run || busy) return;
    const runId = run.id;
    setBusy(true);
    setControlError("");
    try {
      await operation();
      await reload();
    } catch (e) {
      if (currentRunId.current === runId) setControlError((e as Error).message);
    } finally {
      if (currentRunId.current === runId) setBusy(false);
    }
  };
  if (!runs.length && !requestedId)
    return (
      <div className="dock-empty">
        <span className="subtle-icon">
          <SquareTerminal size={22} />
        </span>
        <strong>{t("暂无运行记录", "No runs")}</strong>
      </div>
    );
  return (
    <div className="run-panel">
      <div className="run-list" aria-label={t("运行记录", "Run history")}>
        {(selectedRun && !runs.some((r) => r.id === selectedRun.id)
          ? [selectedRun, ...runs]
          : runs
        ).map((r) => (
          <button
            className={run?.id === r.id ? "active" : ""}
            key={r.id}
            onClick={() =>
              onSelectRun ? onSelectRun(r.id) : setSelected(r.id)
            }
          >
            <span className={`run-indicator status-${r.status}`} />
            <span>
              <span
                className="run-list-title"
                title={nodes.find((n) => n.id === r.node_id)?.title || r.kind}
              >
                {nodes.find((n) => n.id === r.node_id)?.title || r.kind}
              </span>
              <small title={r.id}>
                {r.id.slice(0, 8)} · {formatDate(r.created_at)}
              </small>
            </span>
            <Badge status={r.status} />
          </button>
        ))}
      </div>
      <div className="run-output">
        {selectionLoading && (
          <p role="status">{t("正在加载所选运行…", "Loading selected run…")}</p>
        )}
        <ErrorBox error={selectionError} retry={reload} />
        {!run && !selectionLoading && !selectionError && (
          <p className="run-selection-error" role="status">
            {t(
              "找不到所选运行。请选择其他记录。",
              "Selected run is unavailable. Choose another record.",
            )}
          </p>
        )}
        {run && (
          <>
            <div className="run-output-toolbar">
              <code title={run.id}>{run.id.slice(0, 8)}</code>
              <span>
                PID {run.pid ?? "—"} · exit {run.exit_code ?? "—"}
              </span>
              <span className="toolbar-spacer" />
              {[
                ["pause", Pause],
                ["resume", Play],
                ["cancel", Square],
                ["retry", RefreshCw],
              ].map(([op, Icon]) => (
                <IconButton
                  key={String(op)}
                  label={String(op)}
                  disabled={busy || !allowedActions[String(op)]}
                  onClick={() =>
                    perform(async () => {
                      const body: Json = {};
                      if (op === "retry") {
                        if (retryIntent.current?.runId !== run.id)
                          retryIntent.current = {
                            runId: run.id,
                            requestId: uid(),
                          };
                        body.request_id = retryIntent.current.requestId;
                      }
                      const result = await api<Run>(
                        `/runs/${run.id}/${op}`,
                        "POST",
                        body,
                      );
                      if (
                        op === "retry" &&
                        currentRunId.current === run.id &&
                        retryIntent.current?.requestId === body.request_id
                      ) {
                        retryIntent.current = null;
                        onSelectRun
                          ? onSelectRun(result.id)
                          : setSelected(result.id);
                      }
                    })
                  }
                >
                  {typeof Icon !== "string" && <Icon size={13} />}
                </IconButton>
              ))}
              <IconButton
                label={t("运行高级设置", "Run advanced controls")}
                onClick={() => setAdvanced(true)}
              >
                <Settings2 size={14} />
              </IconButton>
            </div>
            <ErrorBox error={controlError} />
            {Object.keys(run.metrics || {}).length > 0 && (
              <details className="run-recorded-metrics">
                <summary>{t("已记录指标", "Recorded metrics")}</summary>
                <p>
                  <code>{run.id}</code> ·{" "}
                  {t("执行节点版本", "Executed node revision")}{" "}
                  {run.node_revision}
                </p>
                <JsonView value={run.metrics} />
              </details>
            )}
            <RunEvidence runId={run.id} />
            {run.kind === "agent" &&
              terminal &&
              typeof run.metrics?.summary === "string" &&
              run.metrics.summary.trim() && (
                <section
                  className="run-final-summary"
                  aria-label={t("最终摘要", "Final summary")}
                >
                  <strong>{t("最终摘要", "Final summary")}</strong>
                  <p>{run.metrics.summary}</p>
                </section>
              )}
            <ErrorBox error={outputError} />
            <pre>
              {output ||
                run.error ||
                (terminal
                  ? t("没有输出", "No output")
                  : active
                    ? t("等待执行器输出…", "Waiting for executor output…")
                    : t(
                        `此状态暂无输出：${status}`,
                        `No output available for status: ${status}`,
                      ))}
            </pre>
            {advanced && (
              <Modal
                wide
                title={t("运行设置与诊断", "Run controls and diagnostics")}
                onClose={() => setAdvanced(false)}
              >
                <p>
                  <code>{run.id}</code> ·{" "}
                  {t("执行节点版本", "Executed node revision")}{" "}
                  {run.node_revision} · <Badge status={status} />
                </p>
                <ErrorBox error={controlError} />
                {run.resource?.branch_scheduling_hold &&
                  !canResumeRun(run, branches) && (
                    <p className="muted">
                      {t(
                        "恢复研究路线后才能继续此运行。",
                        "Restore the research path before resuming this held run.",
                      )}
                    </p>
                  )}
                <div className="inline-actions">
                  <Button
                    busy={busy}
                    disabled={status !== "queued"}
                    onClick={() =>
                      perform(() => api(`/runs/${run.id}/skip`, "POST", {}))
                    }
                  >
                    {t("跳过排队任务", "Skip queued run")}
                  </Button>
                  <Field label={t("调度优先级", "Scheduling priority")}>
                    <input
                      type="number"
                      step="1"
                      value={priority}
                      onChange={(e) => setPriority(e.target.value)}
                    />
                  </Field>
                  <Button
                    busy={busy}
                    disabled={!/^-?\d+$/.test(priority)}
                    onClick={() =>
                      perform(() =>
                        api(`/runs/${run.id}/priority`, "POST", {
                          priority: Number(priority),
                        }),
                      )
                    }
                  >
                    {t("保存优先级", "Save priority")}
                  </Button>
                  <Button
                    busy={busy}
                    onClick={() =>
                      perform(async () => {
                        const result = await requestApi(
                          "/api/runs/{ident}/diagnostics",
                          "get",
                          { path: { ident: run.id } },
                        );
                        if (currentRunId.current === run.id)
                          setDiagnostics(result);
                      })
                    }
                  >
                    {t("进程诊断", "Process diagnostics")}
                  </Button>
                  <Button
                    busy={busy}
                    onClick={() =>
                      perform(async () => {
                        const result = await requestApi(
                          "/api/runs/{ident}/repository",
                          "get",
                          { path: { ident: run.id } },
                        );
                        if (currentRunId.current === run.id)
                          setRepository(result);
                      })
                    }
                  >
                    {t("代码来源", "Repository provenance")}
                  </Button>
                </div>
                {diagnostics && <JsonView value={diagnostics} />}
                {repository && <JsonView value={repository} />}
                <details>
                  <summary>
                    {t("已保存运行配置", "Saved run configuration")}
                  </summary>
                  <JsonView value={run.config || {}} />
                </details>
                <Field
                  label={t(
                    "配置更新（JSON 补丁）",
                    "Configuration update (JSON patch)",
                  )}
                >
                  <textarea
                    className="code-input"
                    rows={6}
                    value={configuration}
                    onChange={(e) => editDraft("configuration", e.target.value)}
                  />
                </Field>
                <p className="muted">
                  {t(
                    "仅更新填写的键。启动后的提供商身份固定；科学配置变更会取消证据验证状态。",
                    "Only supplied keys are updated. Started runs retain their provider identity; scientific configuration changes mark verification unverified.",
                  )}
                </p>
                <Button
                  busy={busy}
                  onClick={() =>
                    perform(() => {
                      const patch = parseJson(configuration);
                      if (
                        !patch ||
                        typeof patch !== "object" ||
                        Array.isArray(patch)
                      )
                        throw new Error(
                          t(
                            "配置补丁必须是 JSON 对象。",
                            "Configuration patch must be a JSON object.",
                          ),
                        );
                      return requestApi(
                        "/api/runs/{ident}/configuration",
                        "patch",
                        { path: { ident: run.id }, body: patch },
                      ).then((result) => {
                        if (
                          currentRunId.current === run.id &&
                          editorDraft.current.configuration === configuration
                        )
                          editDraft("configuration", "{}");
                        return result;
                      });
                    })
                  }
                >
                  {t("应用配置补丁", "Apply configuration patch")}
                </Button>
                {status === "waiting_input" && run.resource?.checkpoint && (
                  <>
                    <details open>
                      <summary>
                        {t(
                          "当前停止的调试检查点",
                          "Current stopped debug checkpoint",
                        )}
                      </summary>
                      <JsonView value={run.resource.checkpoint} />
                    </details>
                    <Field
                      label={t(
                        "调试检查点更新（JSON）",
                        "Debug checkpoint override (JSON)",
                      )}
                    >
                      <textarea
                        className="code-input"
                        rows={6}
                        value={checkpoint}
                        onChange={(e) =>
                          editDraft("checkpoint", e.target.value)
                        }
                      />
                    </Field>
                    <Button
                      busy={busy}
                      onClick={() =>
                        perform(() =>
                          requestApi("/api/runs/{ident}/checkpoint", "patch", {
                            path: { ident: run.id },
                            body: { payload: parseJson(checkpoint) },
                          }),
                        )
                      }
                    >
                      {t(
                        "保存检查点，不恢复运行",
                        "Save checkpoint without resuming",
                      )}
                    </Button>
                  </>
                )}
                {Boolean(checkpointArchive?.length) && (
                  <details>
                    <summary>
                      {t(
                        "其他检查点的保留草稿",
                        "Retained drafts from other checkpoints",
                      )}
                    </summary>
                    <p className="muted">
                      {t(
                        "这些文本不会应用到当前检查点。",
                        "These texts are retained separately from the current checkpoint.",
                      )}
                    </p>
                    <JsonView value={checkpointArchive} />
                  </details>
                )}
              </Modal>
            )}
          </>
        )}
      </div>
    </div>
  );
}
