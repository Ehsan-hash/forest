import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import type { Graph, Run } from "../api";
import { Button, ErrorBox, Field, useLoad, useUI } from "../ui";
import { RunPanel } from "./RunPanel";
import { useSelectedRun } from "./useSelectedRun";

export function RunsPage() {
  const { id } = useParams();
  const { t } = useUI();
  const [params, setParams] = useSearchParams();
  const requestedOffset = Number(params.get("offset"));
  const offset =
    Number.isSafeInteger(requestedOffset) && requestedOffset >= 0
      ? requestedOffset
      : 0;
  const records = useLoad<Run[]>(
    `/projects/${id}/runs?limit=100&offset=${offset}`,
    [],
  );
  const graph = useLoad<Graph | null>(`/projects/${id}/graph`, null);
  const selectedId = params.get("run") || "";
  const [filter, setFilter] = useState("");
  const [status, setStatus] = useState("all");
  const runs = useMemo(
    () =>
      records.data.filter(
        (run) =>
          (status === "all" || run.status === status) &&
          (!filter ||
            [
              run.id,
              run.kind,
              graph.data?.nodes.find((node) => node.id === run.node_id)
                ?.title || "",
            ].some((value) =>
              value.toLowerCase().includes(filter.toLowerCase()),
            )),
      ),
    [records.data, graph.data, filter, status],
  );
  const selected = useSelectedRun(id, selectedId || runs[0]?.id || "");
  const reload = async () => {
    await Promise.all([records.reload(), selected.reload()]);
  };
  useEffect(() => {
    const refresh = () => {
      void reload();
    };
    window.addEventListener("forest-refresh", refresh);
    return () => window.removeEventListener("forest-refresh", refresh);
  }, [records.reload, selected.reload]);
  const select = (runId: string) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.set("run", runId);
      return next;
    });
  const statuses = [...new Set(records.data.map((run) => run.status))];
  return (
    <div className="page runs-page">
      <div className="page-heading">
        <div>
          <h1>{t("运行记录", "Runs")}</h1>
          <p className="muted">
            {t(
              "检查实际执行、输出与证据来源。",
              "Inspect execution, output and evidence provenance.",
            )}
          </p>
        </div>
      </div>
      <div className="run-history-filters inline-actions">
        <Field label={t("查找运行", "Find a run")}>
          <input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder={t("完整 ID、节点或类型", "Full ID, node or kind")}
          />
        </Field>
        <Field label={t("运行状态", "Run status")}>
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="all">{t("全部状态", "All statuses")}</option>
            {statuses.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </Field>
        <Button
          disabled={offset === 0}
          onClick={() =>
            setParams((current) => {
              const next = new URLSearchParams(current);
              next.set("offset", String(Math.max(0, offset - 100)));
              return next;
            })
          }
        >
          {t("较新记录", "Newer runs")}
        </Button>
        <Button
          disabled={records.data.length < 100}
          onClick={() =>
            setParams((current) => {
              const next = new URLSearchParams(current);
              next.set("offset", String(offset + 100));
              return next;
            })
          }
        >
          {t("更早记录", "Older runs")}
        </Button>
      </div>
      <ErrorBox error={records.error || graph.error} retry={reload} />
      <RunPanel
        runs={runs}
        nodes={graph.data?.nodes || []}
        branches={graph.data?.branches || []}
        reload={reload}
        selectedRunId={selectedId}
        onSelectRun={select}
        selectedRun={selected.run}
        selectionError={selected.error}
        selectionLoading={selected.loading}
      />
    </div>
  );
}
