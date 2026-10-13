import { useState, useEffect, useRef } from "react";
import { useParams, NavLink } from "react-router-dom";
import {
  ArrowUpRight,
  Download,
  Play,
  ChartNoAxesCombined,
  Table2,
} from "lucide-react";
import { api, download, downloadBlob, formatDate } from "../api";
import { requestApi } from "../apiClient";
import type { Json, RecordItem, Run } from "../api";
import {
  useUI,
  useLoad,
  Button,
  Badge,
  Modal,
  Field,
  Empty,
  ErrorBox,
  JsonView,
  Tabs,
  PageHeading,
} from "../ui";
import { demoLink } from "../DemoGuide";

export function DataPage() {
  const { id } = useParams();
  const { t, action } = useUI();
  const {
    data: recordedRuns,
    error,
    reload,
  } = useLoad<Run[]>(`/projects/${id}/runs`, []);
  const runs = recordedRuns.filter((run) => run.kind !== "research_analysis");
  useEffect(() => {
    const refresh = () => void reload();
    window.addEventListener("forest-refresh", refresh);
    return () => window.removeEventListener("forest-refresh", refresh);
  }, [reload]);
  const { data: analyses, reload: reloadAnalyses } = useLoad<RecordItem[]>(
    `/analyses?project_id=${id}`,
    [],
  );
  const [recomputing, setRecomputing] = useState(false);
  const recomputingRef = useRef(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [metric, setMetric] = useState("accuracy");
  const [baseline, setBaseline] = useState("");
  const [status, setStatus] = useState("all");
  const [detail, setDetail] = useState<RecordItem | null>(null);
  const metrics = [
    ...new Set(
      runs.flatMap((r) =>
        Object.entries(r.metrics || {})
          .filter(([, value]) => typeof value === "number")
          .map(([key]) => key),
      ),
    ),
  ];
  const visible = runs.filter(
    (r) =>
      status === "all" ||
      (status === "succeeded"
        ? r.status === "completed" || r.status === "succeeded"
        : r.status === status),
  );
  const exportCSV = () => {
    const fields = ["run_id", "kind", "status", ...metrics];
    const cell = (v: any) => `"${String(v ?? "").replaceAll('"', '""')}"`;
    const text = [
      fields.map(cell).join(","),
      ...visible.map((r) =>
        [r.id, r.kind, r.status, ...metrics.map((k) => r.metrics?.[k])]
          .map(cell)
          .join(","),
      ),
    ].join("\n");
    downloadBlob(
      new Blob([text], { type: "text/csv;charset=utf-8" }),
      "measured-runs.csv",
    );
  };
  return (
    <div className="page">
      <PageHeading
        title={t("数据分析", "Data & analysis")}
        actions={
          <Button
            disabled={!runs.length}
            onClick={exportCSV}
            aria-label={t("导出 CSV", "Export CSV")}
          >
            <Download size={15} />
            CSV
          </Button>
        }
      />
      <ErrorBox error={error} retry={reload} />
      <MeasuredResults runs={runs} />
      <RawDataBrowser key={id} projectId={id!} runs={runs} />
      <section className="surface analysis-controls">
        <Field label={t("主指标", "Primary metric")}>
          <input
            list="metrics"
            value={metric}
            onChange={(e) => setMetric(e.target.value)}
          />
          <datalist id="metrics">
            {metrics.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </datalist>
        </Field>
        <Field label={t("基线运行或方法", "Baseline run or method")}>
          <input
            value={baseline}
            onChange={(e) => setBaseline(e.target.value)}
            placeholder={t("运行 ID 或方法名", "Run ID or method name")}
          />
        </Field>
        <Field label={t("筛选状态", "Filter status")}>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="all">
              {t("全部（含缺失/失败）", "All (including failed)")}
            </option>
            <option value="succeeded">{t("成功", "Succeeded")}</option>
            {["failed", "running", "queued"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </Field>
        <Button
          className="primary"
          busy={recomputing}
          disabled={!runs.length || recomputing}
          onClick={() => {
            if (recomputingRef.current) return;
            recomputingRef.current = true;
            setRecomputing(true);
            void action(
              async () => {
                try {
                  await api("/analysis/run", "POST", {
                    project_id: id,
                    run_ids: selected.length
                      ? selected
                      : runs
                          .filter((r) => Object.keys(r.metrics || {}).length)
                          .map((r) => r.id),
                    baseline,
                    metric,
                  });
                  await reloadAnalyses();
                } finally {
                  recomputingRef.current = false;
                  setRecomputing(false);
                }
              },
              t("统计分析已排队", "Analysis queued"),
            );
          }}
        >
          <Play size={14} />
          {t("重新计算统计", "Recompute statistics")}
        </Button>
      </section>
      {runs.length ? (
        <section className="surface table-surface">
          <table>
            <thead>
              <tr>
                <th />
                <th>{t("运行 / 方法", "Run / method")}</th>
                <th>{t("状态", "Status")}</th>
                {metrics.map((m) => (
                  <th key={m}>{m}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => (
                <tr key={r.id}>
                  <td>
                    <input
                      aria-label={`Select ${r.id}`}
                      type="checkbox"
                      checked={selected.includes(r.id)}
                      onChange={(e) =>
                        setSelected((s) =>
                          e.target.checked
                            ? [...s, r.id]
                            : s.filter((x) => x !== r.id),
                        )
                      }
                    />
                  </td>
                  <td>
                    {r.kind}
                    <small>{r.id.slice(0, 8)}</small>
                  </td>
                  <td>
                    <Badge status={r.status} />
                  </td>
                  {metrics.map((m) => (
                    <td className="numeric" key={m}>
                      {r.metrics?.[m] === undefined ? (
                        <span className="missing">{t("缺失", "Missing")}</span>
                      ) : typeof r.metrics[m] === "number" ? (
                        r.metrics[m].toPrecision(5)
                      ) : (
                        JSON.stringify(r.metrics[m])
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : (
        <Empty
          icon={<Table2 size={28} />}
          title={t("暂无数据", "No data")}
          action={
            <NavLink
              className="button"
              to={demoLink(`/projects/${id}/experiments`)}
            >
              {t("前往实验", "Open experiments")}
              <ArrowUpRight size={14} />
            </NavLink>
          }
        />
      )}
      <div className="section-toolbar">
        <h2>{t("已保存分析", "Saved analyses")}</h2>
        <NavLink className="button" to={demoLink(`/projects/${id}/figures`)}>
          <ChartNoAxesCombined size={15} />
          {t("打开图表工作室", "Open Figure studio")}
        </NavLink>
      </div>
      <div className="record-list">
        {analyses.map((a) => (
          <button
            className="surface analysis-row"
            key={a.id}
            onClick={() => setDetail(a)}
          >
            <ChartNoAxesCombined size={20} />
            <div>
              <strong>{a.title}</strong>
              <p>{formatDate(a.updated_at)}</p>
            </div>
            <Badge status={a.status} />
            <ArrowUpRight size={17} />
          </button>
        ))}
      </div>
      {detail && (
        <Modal wide title={detail.title} onClose={() => setDetail(null)}>
          <JsonView value={detail.data} />
        </Modal>
      )}
    </div>
  );
}

function MeasuredResults({ runs }: { runs: Run[] }) {
  const { t } = useUI();
  const available = runs.filter(
    (r) => Array.isArray(r.metrics?.summary) && r.metrics.summary.length,
  );
  const [runId, setRunId] = useState("");
  const [dataset, setDataset] = useState("all");
  const [metric, setMetric] = useState("brier");
  const [mode, setMode] = useState("summary");
  const run = available.find((r) => r.id === runId) || available[0];
  if (!run) return null;
  const rows: Json[] =
    mode === "summary" ? run.metrics.summary : run.metrics.per_seed || [];
  const datasets = [...new Set(rows.map((r) => r.dataset))];
  const visible = rows.filter(
    (r) => dataset === "all" || r.dataset === dataset,
  );
  const metricOptions = ["brier", "log_loss", "auc", "accuracy", "ece"].filter(
    (k) => rows.some((r) => typeof r[k] === "number"),
  );
  const max = Math.max(...visible.map((r) => Number(r[metric] || 0)), 0.001);
  return (
    <section className="surface measured-results">
      <div className="section-toolbar">
        <div>
          <span className="muted">MEASURED</span>
          <h2>{t("方法与数据集比较", "Methods across datasets")}</h2>
        </div>
        <div className="toolbar-actions">
          <select
            aria-label="Measured run"
            value={run.id}
            onChange={(e) => setRunId(e.target.value)}
          >
            {available.map((r) => (
              <option key={r.id} value={r.id}>
                {r.kind} · {r.id.slice(0, 8)}
              </option>
            ))}
          </select>
          <select
            aria-label="Dataset"
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
          >
            <option value="all">{t("全部数据集", "All datasets")}</option>
            {datasets.map((d) => (
              <option key={d}>{d}</option>
            ))}
          </select>
          <select
            aria-label="Measured metric"
            value={metric}
            onChange={(e) => setMetric(e.target.value)}
          >
            {metricOptions.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </div>
      </div>
      <Tabs
        panelId="experiment-results-tabs"
        value={mode}
        onChange={setMode}
        items={[
          {
            id: "summary",
            label: t("均值与标准差", "Mean & standard deviation"),
          },
          { id: "seeds", label: t("每次重复", "Per repetition") },
        ]}
      />
      <div
        id="experiment-results-tabs-panel"
        role="tabpanel"
        aria-labelledby={`experiment-results-tabs-tab-${mode}`}
        tabIndex={0}
      >
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t("数据集", "Dataset")}</th>
                <th>{t("方法", "Method")}</th>
                <th>
                  {mode === "summary" ? t("重复数", "Repetitions") : "Seed"}
                </th>
                <th>{metric}</th>
                <th>{t("数值比较", "Value comparison")}</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row, index) => (
                <tr key={`${row.dataset}-${row.method}-${index}`}>
                  <td>{row.dataset}</td>
                  <td>
                    <strong>{row.method}</strong>
                  </td>
                  <td>{mode === "summary" ? row.seeds : row.seed}</td>
                  <td className="numeric">
                    {typeof row[metric] === "number"
                      ? row[metric].toFixed(5)
                      : "—"}
                    {mode === "summary" &&
                      typeof row[`${metric}_std`] === "number" && (
                        <small>± {row[`${metric}_std`].toFixed(5)}</small>
                      )}
                  </td>
                  <td>
                    <div className="value-track">
                      <div
                        style={{
                          width: `${(Number(row[metric] || 0) / max) * 100}%`,
                        }}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="statistical-scope">
          {run.metrics.statistical_scope ||
            t(
              "保存的重复实验结果：均值 ± 标准差。",
              "Mean ± SD across saved repetitions.",
            )}
        </p>
      </div>
    </section>
  );
}

function RawDataBrowser({
  projectId,
  runs,
}: {
  projectId: string;
  runs: Run[];
}) {
  const { t, action } = useUI();
  const available = runs.filter(
    (r) =>
      r.project_id === projectId &&
      r.kind === "experiment" &&
      r.status === "completed",
  );
  const [runId, setRunId] = useState("");
  const [path, setPath] = useState("");
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState("");
  const [descending, setDescending] = useState(false);
  const [offset, setOffset] = useState(0);
  const [loaded, setLoaded] = useState<{
    generation: number;
    value: Json;
  } | null>(null);
  const [pendingGeneration, setPendingGeneration] = useState<number | null>(
    null,
  );
  const pendingRef = useRef<number | null>(null);
  const [failure, setFailure] = useState<{
    generation: number;
    message: string;
  } | null>(null);
  const selected = runId || available[0]?.id;
  const selectedRun = available.find((run) => run.id === selected);
  const scopeKey = JSON.stringify([
    projectId,
    selected,
    path.trim(),
    !!selectedRun,
  ]);
  const scope = useRef({ key: scopeKey, generation: 0, request: 0 });
  if (scope.current.key !== scopeKey) {
    scope.current = {
      key: scopeKey,
      generation: scope.current.generation + 1,
      request: 0,
    };
  }
  useEffect(
    () => () => {
      scope.current.generation += 1;
    },
    [],
  );
  const data =
    loaded?.generation === scope.current.generation ? loaded.value : null;
  const busy = pendingGeneration === scope.current.generation;
  const error =
    failure?.generation === scope.current.generation ? failure.message : "";
  const load = async (next = 0) => {
    if (!selectedRun || pendingRef.current === scope.current.generation) return;
    const generation = scope.current.generation;
    const request = ++scope.current.request;
    const current = () =>
      scope.current.generation === generation &&
      scope.current.request === request;
    pendingRef.current = generation;
    setPendingGeneration(generation);
    setFailure(null);
    try {
      const value = await requestApi("/api/data/{ident}/rows", "get", {
        path: { ident: selectedRun.id },
        query: {
          offset: next,
          limit: 50,
          filter,
          sort,
          descending,
          ...(path.trim() ? { path: path.trim() } : {}),
        },
      });
      if (!current()) return;
      setLoaded({ generation, value });
      setOffset(next);
    } catch (failure) {
      if (current())
        setFailure({ generation, message: (failure as Error).message });
    } finally {
      if (current()) {
        pendingRef.current = null;
        setPendingGeneration(null);
      }
    }
  };
  if (!available.length) return null;
  return (
    <details className="surface raw-data-browser">
      <summary>
        <Table2 size={16} />
        {t("查看原始预测与数据表", "Raw predictions & tables")}
      </summary>
      <div className="raw-data-controls">
        <Field label={t("来源运行", "Source run")}>
          <select
            value={selected}
            onChange={(e) => {
              setRunId(e.target.value);
              setLoaded(null);
            }}
          >
            {runId && !selectedRun && (
              <option value={runId} disabled>
                {runId} · {t("当前不可用", "Currently unavailable")}
              </option>
            )}
            {available.map((r) => (
              <option key={r.id} value={r.id}>
                {r.id.slice(0, 8)} · {r.kind}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label={t(
            "文件路径（留空读取预测）",
            "File path (blank for predictions)",
          )}
        >
          <input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="predictions.csv / dataset.parquet"
          />
        </Field>
        <Field label={t("过滤表格内容", "Filter table values")}>
          <input value={filter} onChange={(e) => setFilter(e.target.value)} />
        </Field>
        <Button
          className="primary"
          busy={busy}
          disabled={!selectedRun}
          onClick={() => load(0)}
        >
          <Table2 size={14} />
          {t("读取实际数据", "Load data")}
        </Button>
      </div>
      <ErrorBox error={error} retry={() => void load(0)} />
      {data && (
        <>
          <div className="section-toolbar">
            <span className="muted">
              {data.total.toLocaleString()} {t("行", "rows")} · {data.origin}
            </span>
            <div className="inline-actions">
              <select
                aria-label="Sort raw table"
                value={sort}
                onChange={(e) => setSort(e.target.value)}
              >
                <option value="">{t("原始顺序", "Original order")}</option>
                {data.columns.map((column: string) => (
                  <option key={column}>{column}</option>
                ))}
              </select>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={descending}
                  onChange={(e) => setDescending(e.target.checked)}
                />
                {t("降序", "Descending")}
              </label>
              <Button onClick={() => load(0)}>
                {t("应用排序", "Apply sort")}
              </Button>
            </div>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {data.columns.map((column: string) => (
                    <th key={column}>{column}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row: Json, index: number) => (
                  <tr key={index}>
                    {data.columns.map((column: string) => (
                      <td key={column}>
                        {row[column] === null ? (
                          <span className="missing">null</span>
                        ) : typeof row[column] === "object" ? (
                          JSON.stringify(row[column])
                        ) : (
                          String(row[column] ?? "")
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="raw-pagination">
            <Button
              disabled={offset === 0 || busy}
              onClick={() => load(Math.max(0, offset - 50))}
            >
              {t("上一页", "Previous")}
            </Button>
            <span>
              {offset + 1}–{Math.min(offset + 50, data.total)} / {data.total}
            </span>
            <Button
              disabled={offset + 50 >= data.total || busy}
              onClick={() => load(offset + 50)}
            >
              {t("下一页", "Next")}
            </Button>
            <Button
              onClick={() =>
                action(() =>
                  download(
                    `/projects/${selectedRun!.project_id}/download?path=${encodeURIComponent(path.trim() || `${selectedRun!.output_path}/predictions.csv`)}`,
                  ),
                )
              }
            >
              <Download size={14} />
              {t("下载原始数据", "Download source")}
            </Button>
          </div>
        </>
      )}
    </details>
  );
}
