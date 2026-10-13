import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Check, ChevronDown, Folder, Search } from "lucide-react";
import type { Project } from "../api";
import { ErrorBox, Modal, useLoad, useUI } from "../ui";
import { demoLink } from "../DemoGuide";

export function ProjectSwitcher({ projectId, onNavigate }: { projectId?: string; onNavigate: () => void }) {
  const { t } = useUI();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const location = useLocation();
  const navigate = useNavigate();
  const { data: current } = useLoad<Project | null>(projectId ? `/projects/${projectId}` : null, null);
  const projects = useLoad<Project[]>(open ? "/projects?limit=500" : null, []);
  const go = (path: string) => { navigate(demoLink(path, location.search)); setOpen(false); onNavigate(); };
  return <>
    <button className="project-switch" aria-label={t("切换项目", "Switch project")} aria-expanded={open} onClick={() => { setQuery(""); setOpen(true); }}>
      <span className="workspace-avatar"><Folder size={16} /></span>
      <span>{current?.name || t("全部项目", "All projects")}<small>{t("切换研究项目", "Switch research project")}</small></span>
      <ChevronDown size={15} />
    </button>
    {open && <Modal title={t("切换项目", "Switch project")} onClose={() => setOpen(false)}>
      <label className="search-input"><Search size={16} /><input autoFocus aria-label={t("搜索项目", "Search projects")} value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <ErrorBox error={projects.error} retry={projects.reload} />
      <div className="command-list project-options">
        <button onClick={() => go("/projects")}><Folder size={16} />{t("全部项目与新建项目", "All projects & new project")}</button>
        {projects.data.filter((project) => project.name.toLowerCase().includes(query.toLowerCase())).map((project) => <button key={project.id} onClick={() => go(`/projects/${project.id}/workspace`)}>
          <span><strong>{project.name}</strong><small>{project.archived ? t("已归档", "Archived") : project.goal || project.id}</small></span>{project.id === projectId && <Check size={16} />}
        </button>)}
        {!projects.loading && !projects.data.some((project) => project.name.toLowerCase().includes(query.toLowerCase())) && <p className="muted">{t("没有匹配的项目", "No matching projects")}</p>}
      </div>
    </Modal>}
  </>;
}
