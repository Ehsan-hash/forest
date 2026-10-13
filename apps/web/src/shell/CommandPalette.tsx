import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Command, ArrowRight } from "lucide-react";
import type { Graph, Project, Run } from "../api";
import { ErrorBox, Modal, useLoad, useUI } from "../ui";
import { demoLink } from "../DemoGuide";
import { navigation } from "./navigation";

type Entry = { id: string; name: string; detail: string; path?: string; execute?: () => void };
export function CommandPalette({ projectId, onClose, toggleTheme }: { projectId?: string; onClose: () => void; toggleTheme: () => void }) {
  const { t } = useUI();
  const navigate = useNavigate();
  const location = useLocation();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const [recent, setRecent] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem("forest-recent-commands") || "[]"); } catch { return []; } });
  const projects = useLoad<Project[]>("/projects?limit=500", []);
  const graph = useLoad<Graph | null>(projectId ? `/projects/${projectId}/graph` : null, null);
  const runs = useLoad<Run[]>(projectId ? `/projects/${projectId}/runs?limit=500` : null, []);
  const list = useRef<HTMLDivElement>(null);
  const entries = useMemo<Entry[]>(() => [
    { id: "projects", name: t("全部项目与新建项目", "All projects & new project"), detail: t("页面", "Page"), path: "/projects" },
    { id: "settings", name: t("设置与连接", "Settings & connections"), detail: t("页面", "Page"), path: "/settings" },
    { id: "theme", name: t("切换明暗主题", "Toggle light / dark theme"), detail: t("命令", "Command"), execute: toggleTheme },
    ...(projectId ? navigation.map(([path, zh, en]) => ({ id: `page:${projectId}:${path}`, name: t(zh, en), detail: t("页面", "Page"), path: `/projects/${projectId}/${path}` })) : []),
    ...projects.data.map((project) => ({ id: `project:${project.id}`, name: project.name, detail: t("项目", "Project"), path: `/projects/${project.id}/workspace` })),
    ...(graph.data?.nodes || []).map((node) => ({ id: `node:${node.id}`, name: node.title, detail: `${t("节点", "Node")} · ${node.type} · ${node.id}`, path: `/projects/${projectId}/workspace?node=${encodeURIComponent(node.id)}` })),
    ...runs.data.map((run) => ({ id: `run:${run.id}`, name: `${run.kind} · ${run.status}`, detail: `${t("运行", "Run")} · ${run.id}`, path: `/projects/${projectId}/runs?run=${encodeURIComponent(run.id)}` })),
  ], [projectId, projects.data, graph.data, runs.data, t, toggleTheme]);
  const visible = useMemo(() => entries.filter((entry) => `${entry.name} ${entry.detail}`.toLowerCase().includes(query.toLowerCase())).sort((left, right) => {
    if (query) return 0;
    const a = recent.indexOf(left.id), b = recent.indexOf(right.id);
    return (a < 0 ? 999 : a) - (b < 0 ? 999 : b);
  }).slice(0, 60), [entries, query, recent]);
  useEffect(() => setSelected(0), [query]);
  useEffect(() => { list.current?.querySelector(`[data-selected="true"]`)?.scrollIntoView({ block: "nearest" }); }, [selected]);
  const choose = (entry?: Entry) => {
    if (!entry) return;
    const next = [entry.id, ...recent.filter((id) => id !== entry.id)].slice(0, 12);
    setRecent(next); localStorage.setItem("forest-recent-commands", JSON.stringify(next));
    if (entry.path) navigate(demoLink(entry.path, location.search)); else entry.execute?.();
    onClose();
  };
  return <Modal title={t("搜索与命令", "Search & commands")} onClose={onClose}>
    <input autoFocus role="combobox" aria-label={t("搜索页面、项目、节点、运行或命令", "Search pages, projects, nodes, runs or commands")} aria-expanded="true" aria-controls="command-results" aria-autocomplete="list" aria-activedescendant={visible[selected] ? `command-option-${selected}` : undefined} placeholder={t("搜索页面、项目、节点、运行…", "Search pages, projects, nodes, runs…")} value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setSelected((value) => Math.max(0, Math.min(visible.length - 1, value + (event.key === "ArrowDown" ? 1 : -1)))); }
      if (event.key === "Enter") { event.preventDefault(); choose(visible[selected]); }
    }} />
    <ErrorBox error={projects.error || graph.error || runs.error} retry={() => { void projects.reload(); void graph.reload(); void runs.reload(); }} />
    <div ref={list} className="command-list" id="command-results" role="listbox" aria-label={t("搜索结果", "Search results")}>
      {visible.map((entry, index) => <button role="option" aria-selected={index === selected} id={`command-option-${index}`} data-selected={index === selected} key={entry.id} onMouseEnter={() => setSelected(index)} onClick={() => choose(entry)}>
        <Command size={16} /><span><strong>{entry.name}</strong><small>{entry.detail}</small></span><ArrowRight size={14} />
      </button>)}
      {!visible.length && <p className="muted">{t("没有匹配结果", "No matching results")}</p>}
    </div>
    <p className="palette-hint">↑ ↓ {t("选择", "Select")} · Enter {t("打开", "Open")} · Esc {t("关闭", "Close")}</p>
  </Modal>;
}
