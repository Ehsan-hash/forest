import {
  useRef,
  useState,
  useEffect,
  useCallback,
  lazy,
  Suspense,
} from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { Maximize2, Pin, X } from "lucide-react";
import { Workspace } from "./Workspace";
import { ResearchAnalysisPanel } from "./ResearchAnalysisPanel";
import type { AnalysisScope } from "./researchAnalysisDraft";
const PaperPage = lazy(() =>
  import("./pages/PaperPage").then((module) => ({ default: module.PaperPage })),
);
import { Loading, IconButton, Tabs, useUI, focusTrapTarget } from "./ui";
import { demoLink } from "./DemoGuide";

export function WorkspacePage() {
  const { id } = useParams();
  const { t } = useUI();
  const location = useLocation();
  const navigate = useNavigate();
  const query = new URLSearchParams(location.search);
  const [pinnedView, setPinnedView] = useState<"analysis" | "paper">(() =>
    localStorage.getItem(`forest-reading-view:${id}`) === "analysis"
      ? "analysis"
      : "paper",
  );
  const [pinned, setPinned] = useState(
    () =>
      localStorage.getItem(`forest-reading-pinned:${id}`) === "true" ||
      localStorage.getItem(`forest-paper-pinned:${id}`) === "true",
  );
  const [width, setWidth] = useState(
    () => Number(localStorage.getItem("forest-paper-width")) || 440,
  );
  const panel =
    query.get("analysis") === "1"
      ? "analysis"
      : query.get("paper") === "1"
        ? "paper"
        : pinnedView;
  const open =
    query.get("analysis") === "1" || query.get("paper") === "1" || pinned;
  const scope: AnalysisScope = ["node", "branch"].includes(
    query.get("analysis_scope") || "",
  )
    ? (query.get("analysis_scope") as AnalysisScope)
    : "project";
  const nodeId = query.get("node") || "";
  const branchId =
    query.get("branch") === "all" ? "" : query.get("branch") || "";
  const [inspectorRequest, requestInspector] = useState(0);
  const resize = useRef<{ x: number; width: number } | null>(null);
  const panelRef = useRef<HTMLElement>(null);
  const [compact, setCompact] = useState(
    () => matchMedia("(max-width: 820px)").matches,
  );
  useEffect(() => {
    const media = matchMedia("(max-width: 820px)");
    const update = () => setCompact(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const updateQuery = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(location.search);
      for (const [key, value] of Object.entries(patch)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      navigate({ pathname: location.pathname, search: next.toString() });
    },
    [location.pathname, location.search, navigate],
  );
  const closePanel = () => {
    setPinned(false);
    localStorage.setItem(`forest-reading-pinned:${id}`, "false");
    localStorage.setItem(`forest-paper-pinned:${id}`, "false");
    updateQuery({ analysis: null, paper: null });
  };
  const selectPanel = (next: string) => {
    const value = next === "analysis" ? "analysis" : "paper";
    setPinnedView(value);
    localStorage.setItem(`forest-reading-view:${id}`, value);
    updateQuery({
      analysis: value === "analysis" ? "1" : null,
      paper: value === "paper" ? "1" : null,
      view: null,
    });
  };
  const selectNode = useCallback(
    (selected: string) => {
      updateQuery({
        node: selected || null,
        ...(selected && open && panel === "analysis"
          ? { analysis_scope: "node" }
          : {}),
      });
    },
    [updateQuery, open, panel],
  );
  const saveWidth = (value: number) => {
    const next = Math.max(360, Math.min(720, value));
    setWidth(next);
    localStorage.setItem("forest-paper-width", String(next));
  };
  const closeRef = useRef(closePanel);
  closeRef.current = closePanel;
  useEffect(() => {
    if (!open || !compact || !panelRef.current) return;
    const panel = panelRef.current;
    const previous = document.activeElement as HTMLElement | null;
    const focusable = () =>
      Array.from(
        panel.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => element.getClientRects().length > 0);
    focusable()[0]?.focus();
    const keyboard = (event: KeyboardEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest(".modal")) return;
      if (
        event.key === "Escape" &&
        !target?.closest(
          'textarea, input, [contenteditable="true"], .monaco-editor',
        )
      ) {
        event.preventDefault();
        event.stopImmediatePropagation();
        closeRef.current();
      } else if (event.key === "Tab") {
        const next = focusTrapTarget(
          focusable(),
          document.activeElement,
          event.shiftKey,
        );
        if (next) {
          event.preventDefault();
          next.focus();
        }
      }
    };
    document.addEventListener("keydown", keyboard, true);
    return () => {
      document.removeEventListener("keydown", keyboard, true);
      if (previous?.isConnected) previous.focus();
    };
  }, [open, compact]);
  return (
    <div className={`workspace-layout ${open ? "with-manuscript" : ""}`}>
      <div className="workspace-primary" inert={open && compact}>
        <Workspace
          readingPanelOpen={open}
          onOpenAnalysis={() => selectPanel("analysis")}
          onCloseReadingPanel={closePanel}
          inspectorRequest={inspectorRequest}
          onSelectedNode={selectNode}
          onViewChange={(next) => {
            if (next === "focus") {
              setPinned(false);
              localStorage.setItem(`forest-reading-pinned:${id}`, "false");
              localStorage.setItem(`forest-paper-pinned:${id}`, "false");
              updateQuery({ view: "focus", analysis: null, paper: null });
            } else updateQuery({ view: null });
          }}
        />
      </div>
      {open && (
        <>
          <div
            className="manuscript-resizer"
            role="separator"
            aria-label={
              panel === "paper"
                ? t("调整论文侧栏宽度", "Resize manuscript panel")
                : t("调整分析侧栏宽度", "Resize analysis panel")
            }
            aria-orientation="vertical"
            aria-valuemin={360}
            aria-valuemax={720}
            aria-valuenow={width}
            tabIndex={0}
            onKeyDown={(event) => {
              if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
                event.preventDefault();
                saveWidth(width + (event.key === "ArrowLeft" ? 24 : -24));
              }
            }}
            onPointerDown={(event) => {
              resize.current = { x: event.clientX, width };
              event.currentTarget.setPointerCapture(event.pointerId);
            }}
            onPointerMove={(event) => {
              if (resize.current)
                saveWidth(
                  resize.current.width + resize.current.x - event.clientX,
                );
            }}
            onPointerUp={() => {
              resize.current = null;
            }}
          />
          <aside
            ref={panelRef}
            className="manuscript-panel workspace-reading-panel"
            style={{ width }}
            role={compact ? "dialog" : undefined}
            aria-modal={compact ? true : undefined}
            aria-label={
              panel === "paper"
                ? t("论文侧栏", "Manuscript panel")
                : t("研究分析侧栏", "Research analysis panel")
            }
          >
            <header>
              <div className="reading-panel-tabs">
                <Tabs
                  panelId="workspace-reading-tabs"
                  value={panel}
                  onChange={selectPanel}
                  items={[
                    { id: "analysis", label: t("分析", "Analysis") },
                    { id: "paper", label: t("论文", "Paper") },
                  ]}
                />
              </div>
              <span />
              <IconButton
                label={
                  panel === "paper"
                    ? t("固定论文侧栏", "Pin manuscript panel")
                    : t("固定分析侧栏", "Pin analysis panel")
                }
                aria-pressed={pinned}
                onClick={() => {
                  setPinned(!pinned);
                  localStorage.setItem(
                    `forest-paper-pinned:${id}`,
                    String(!pinned && panel === "paper"),
                  );
                  localStorage.setItem(
                    `forest-reading-pinned:${id}`,
                    String(!pinned),
                  );
                  localStorage.setItem(`forest-reading-view:${id}`, panel);
                  setPinnedView(panel);
                }}
              >
                <Pin size={16} />
              </IconButton>
              {panel === "paper" && (
                <IconButton
                  label={t("打开完整论文编辑器", "Open full manuscript editor")}
                  onClick={() =>
                    navigate(demoLink(`/projects/${id}/paper`, location.search))
                  }
                >
                  <Maximize2 size={16} />
                </IconButton>
              )}
              <IconButton
                label={
                  panel === "paper"
                    ? t("关闭论文侧栏", "Close manuscript panel")
                    : t("关闭分析侧栏", "Close analysis panel")
                }
                onClick={closePanel}
              >
                <X size={16} />
              </IconButton>
            </header>
            <div
              id="workspace-reading-tabs-panel"
              role="tabpanel"
              aria-labelledby={`workspace-reading-tabs-tab-${panel}`}
            >
              {panel === "analysis" ? (
                <ResearchAnalysisPanel
                  projectId={id!}
                  scope={scope}
                  nodeId={nodeId}
                  branchId={branchId}
                  onScopeChange={(next) =>
                    updateQuery({ analysis_scope: next })
                  }
                  onNodeChange={(next) =>
                    updateQuery({
                      node: next || null,
                      branch: null,
                      analysis_scope: "node",
                    })
                  }
                  onBranchChange={(next) =>
                    updateQuery({
                      branch: next || null,
                      analysis_scope: "branch",
                    })
                  }
                  onEditNode={() => {
                    closePanel();
                    requestInspector((value) => value + 1);
                  }}
                />
              ) : (
                <Suspense fallback={<Loading />}>
                  <PaperPage embedded />
                </Suspense>
              )}
            </div>
          </aside>
        </>
      )}
    </div>
  );
}
