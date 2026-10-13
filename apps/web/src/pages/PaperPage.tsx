import { DependencyImpact } from "../interventions/DependencyImpact";
import { useState, useEffect } from "react";
import { useParams, NavLink } from "react-router-dom";
import { Download, Play, FileText, Check, Save, Send } from "lucide-react";
import { api, ApiError, download, uid } from "../api";
import { requestApi } from "../apiClient";
import { usePaperDraft } from "../paperDraft";
import { PaperWritingReview } from "../PaperWritingReview";
import type { Json, RecordItem, Run } from "../api";
import { PaperLayoutSettings, PaperPreflight } from "../PaperLayout";
import type { PaperTemplate, PaperLayoutConfig } from "../PaperLayout";
import {
  useUI,
  useLoad,
  Button,
  Badge,
  Field,
  Empty,
  ErrorBox,
  JsonView,
  Tabs,
  PageHeading,
} from "../ui";
import { CodeEditor, PDFViewer } from "../editors";
import { demoLink } from "../DemoGuide";

export function PaperPage({ embedded = false }: { embedded?: boolean }) {
  const { id } = useParams();
  const { t, action } = useUI();
  const {
    data: paper,
    error,
    reload,
  } = useLoad<RecordItem>(`/papers/${id}`, null!);
  const { draft, session } = usePaperDraft(id!, paper);
  const {
    source,
    bibtex,
    instruction,
    dirty,
    editingRevision,
    template,
    layout,
    layoutDirty,
  } = draft;
  const tab = draft.sourceTab;
  const rightTab = draft.previewTab;
  const setTab = (sourceTab: string) => session.update({ sourceTab });
  const setRightTab = (previewTab: string) => session.update({ previewTab });
  const [issueSnapshot, setIssueSnapshot] = useState<{
    projectId: string;
    revision: number;
    result: Json;
  } | null>(null);
  const issues =
    issueSnapshot &&
    issueSnapshot.projectId === id &&
    issueSnapshot.revision === paper?.revision
      ? issueSnapshot.result
      : null;
  const [previousPdfScope, setPreviousPdfScope] = useState<string | null>(null);
  const pdfScope = `${id}:${paper?.data.pdf_path || ""}:${paper?.data.compiled_revision ?? ""}`;
  const showPreviousPdf = previousPdfScope === pdfScope;
  const {
    data: compileRun,
    error: compileError,
    reload: reloadCompile,
  } = useLoad<Run | null>(
    draft.compileRunId ? `/runs/${draft.compileRunId}` : null,
    null,
  );
  const busy = draft.pending !== null;
  const revising = draft.pending === "revise";
  useEffect(() => {
    const f = () => {
      void reload();
      void reloadCompile();
    };
    window.addEventListener("forest-refresh", f);
    return () => window.removeEventListener("forest-refresh", f);
  }, [reload, reloadCompile]);
  const save = async () => {
    const current = session.getSnapshot();
    if (!current.dirty && current.editingRevision !== undefined)
      return current.editingRevision;
    const submitted = {
      source: current.source,
      bibtex: current.bibtex,
      editingRevision: current.editingRevision,
    };
    const saved = await requestApi("/api/papers/{ident}", "patch", {
      path: { ident: id! },
      body: {
        expected_revision: submitted.editingRevision,
        data: { source: submitted.source, bibtex: submitted.bibtex },
      },
    });
    session.saved(saved as RecordItem, submitted);
    await reload();
    return saved.revision;
  };
  const perform = async (
    kind: string,
    work: () => Promise<unknown>,
    message?: string,
  ) => {
    const token = session.begin(kind);
    if (token === null) return;
    await action(async () => {
      let failure = "";
      try {
        return await work();
      } catch (error) {
        failure =
          error instanceof ApiError
            ? `${error.code}: ${error.message}`
            : (error as Error).message;
        throw error;
      } finally {
        session.finish(token, failure);
      }
    }, message);
  };
  const applyLayout = async () => {
    await perform(
      "layout",
      async () => {
        const revision = await save();
        const current = session.getSnapshot();
        const run = await requestApi("/api/papers/{ident}/layout", "post", {
          path: { ident: id! },
          body: {
            request_id: uid(),
            expected_revision: revision,
            template: current.template,
            layout: current.layout,
          },
        });
        session.update({ compileRunId: run.id });
        setRightTab("checks");
        await reload();
      },
      t("版面编译已排队", "Layout compilation queued"),
    );
  };
  const compile = async () => {
    if (session.getSnapshot().layoutDirty) return applyLayout();
    await perform(
      "compile",
      async () => {
        const revision = await save();
        const run = await requestApi("/api/papers/{ident}/compile", "post", {
          path: { ident: id! },
          body: { request_id: uid(), expected_revision: revision },
        });
        session.update({ compileRunId: run.id });
        await reload();
      },
      t("论文编译已加入队列", "Paper compilation queued"),
    );
  };
  const currentPdf =
    !!paper?.data.pdf_path && paper.data.compiled_revision === paper.revision;
  const manuscriptReady = !!paper && draft.initialized;
  return (
    <div className={embedded ? "paper-page embedded" : "page paper-page"}>
      {!embedded && <PageHeading title={t("论文", "Paper")} />}
      <ErrorBox error={error} retry={reload} />
      <ErrorBox error={draft.error} />
      <ErrorBox error={compileError} retry={reloadCompile} />
      {paper &&
        editingRevision !== paper.revision &&
        (dirty || layoutDirty) && (
          <p className="paper-draft-notice" role="status">
            {t(
              "已保存论文有新版本；当前草稿仍基于",
              "The saved manuscript changed; this draft remains based on",
            )}{" "}
            r{editingRevision}.{" "}
            {t("保存前请检查差异。", "Review the changes before saving.")}
          </p>
        )}
      <div className="paper-actions">
        <span className="paper-filename">
          <FileText size={16} />
          manuscript.tex{" "}
          <span>
            {dirty || layoutDirty
              ? t("有未保存修改", "Unsaved changes")
              : `r${paper?.revision || 0}`}
          </span>
        </span>
        <div className="toolbar-spacer" />
        <Button
          disabled={busy || !manuscriptReady}
          onClick={() =>
            perform("check", async () => {
              const revision = await save();
              const result = await requestApi(
                "/api/papers/{ident}/check",
                "post",
                { path: { ident: id! } },
              );
              setIssueSnapshot({ projectId: id!, revision, result });
              setRightTab("checks");
            })
          }
        >
          <Check size={14} />
          {t("检查引用与主张", "Check evidence")}
        </Button>
        <Button
          disabled={busy || !manuscriptReady}
          onClick={() => perform("save", save, t("论文已保存", "Paper saved"))}
        >
          <Save size={14} />
          {t("保存", "Save")}
        </Button>
        <Button
          className="primary"
          busy={busy}
          disabled={!manuscriptReady}
          onClick={compile}
        >
          <Play size={13} />
          {t("编译 PDF", "Compile PDF")}
        </Button>
        <Button
          disabled={busy || !manuscriptReady}
          onClick={() =>
            perform("source-export", () =>
              download(
                `/papers/${id}/export`,
                {
                  request_id: uid(),
                  expected_revision: editingRevision,
                  source,
                  bibtex,
                },
                "paper-source.zip",
              ),
            )
          }
        >
          <Download size={14} />
          {t("源码 ZIP", "Source ZIP")}
        </Button>
        <Button
          disabled={busy || !manuscriptReady}
          onClick={() =>
            perform("pdf-export", async () => {
              if (session.getSnapshot().layoutDirty)
                throw new Error(
                  t(
                    "版面尚未应用。请先应用版面并编译，再导出当前 PDF。",
                    "Layout changes are unsaved. Apply layout and compile before exporting the current PDF.",
                  ),
                );
              const revision = await save();
              await download(
                `/papers/${id}/export`,
                { format: "pdf", expected_revision: revision },
                "forest-paper.pdf",
              );
            })
          }
        >
          <Download size={14} />
          {t("当前 PDF", "Current PDF")}
        </Button>
      </div>
      {paper && (
        <p className="paper-compilation-state" role="status">
          {currentPdf
            ? t(
                `PDF 已编译至 r${paper.revision}。`,
                `PDF compiled for r${paper.revision}.`,
              )
            : paper.data.pdf_path
              ? t(
                  `PDF 来自 r${paper.data.compiled_revision ?? "?"}；当前已保存版本为 r${paper.revision}，请重新编译。`,
                  `PDF is from r${paper.data.compiled_revision ?? "?"}; saved revision is r${paper.revision}. Compile to update it.`,
                )
              : t("尚无编译 PDF。", "No compiled PDF yet.")}
          {(dirty || layoutDirty) &&
            ` ${t("预览对应已保存内容，当前编辑尚未保存。", "The preview represents saved content; current edits are unsaved.")}`}
        </p>
      )}
      {compileRun && compileRun.project_id === id && (
        <div className="paper-compile-run">
          <span>
            {t("编译任务", "Compilation run")} · {compileRun.id.slice(0, 8)}
          </span>
          <Badge status={compileRun.status} />
          <NavLink
            to={demoLink(
              `/projects/${id}/runs?run=${encodeURIComponent(compileRun.id)}`,
            )}
          >
            {t("查看运行", "View run")}
          </NavLink>
          {compileRun.error && <p role="alert">{compileRun.error}</p>}
        </div>
      )}
      {paper && id && <DependencyImpact projectId={id} data={paper.data} />}
      <PaperLayoutSettings
        template={template}
        layout={layout}
        onTemplateChange={(value) => {
          session.update({
            template: value,
            layout:
              value === "iclr2027" ? { ...layout, columns: "single" } : layout,
            layoutDirty: true,
          });
        }}
        onChange={(value) => {
          session.update({ layout: value, layoutDirty: true });
        }}
        onApply={applyLayout}
        busy={busy}
        disabled={!manuscriptReady}
      />
      <PaperWritingReview
        key={id}
        projectId={id!}
        source={source}
        disabled={busy || !manuscriptReady}
        onChange={(value) => session.update({ source: value, dirty: true })}
      />
      {paper && (
        <PaperGeneration
          projectId={id!}
          template={template}
          layout={layout}
          dirty={dirty}
          save={save}
          reload={reload}
          perform={perform}
          disabled={busy || !manuscriptReady}
        />
      )}
      {paper && (
        <PaperFigureInsertion
          projectId={id!}
          source={source}
          save={save}
          reload={reload}
          perform={perform}
          disabled={busy || !manuscriptReady}
          onInserted={(saved, submitted) => session.saved(saved, submitted)}
        />
      )}
      <div className="paper-editors">
        <div className="paper-source">
          <Tabs
            panelId="paper-source-tabs"
            value={tab}
            onChange={setTab}
            items={[
              { id: "source", label: "LaTeX" },
              { id: "bibtex", label: "references.bib" },
              { id: "bindings", label: t("图表与指标绑定", "Bindings") },
            ]}
          />
          <div
            className="tab-panel"
            id="paper-source-tabs-panel"
            role="tabpanel"
            aria-labelledby={`paper-source-tabs-tab-${tab}`}
            tabIndex={0}
          >
            {tab === "source" ? (
              <CodeEditor
                value={source}
                language="latex"
                sessionKey={`paper:${id}:source`}
                onChange={(v) => {
                  session.update({ source: v, dirty: true });
                }}
              />
            ) : tab === "bibtex" ? (
              <CodeEditor
                value={bibtex}
                language="bibtex"
                sessionKey={`paper:${id}:bibtex`}
                onChange={(v) => {
                  session.update({ bibtex: v, dirty: true });
                }}
              />
            ) : (
              <JsonView value={paper?.data.bindings || []} />
            )}
          </div>
        </div>
        <div className="paper-preview">
          <Tabs
            panelId="paper-preview-tabs"
            value={rightTab}
            onChange={setRightTab}
            items={[
              { id: "pdf", label: t("论文预览", "PDF preview") },
              { id: "log", label: t("编译日志", "Compile log") },
              { id: "checks", label: t("检查结果", "Checks") },
            ]}
          />
          <div
            className="tab-panel"
            id="paper-preview-tabs-panel"
            role="tabpanel"
            aria-labelledby={`paper-preview-tabs-tab-${rightTab}`}
            tabIndex={0}
          >
            {rightTab === "pdf" &&
              (paper?.data.pdf_path &&
              ((currentPdf && !layoutDirty) || showPreviousPdf) ? (
                <>
                  {(!currentPdf || layoutDirty) && (
                    <p className="paper-draft-notice" role="status">
                      {currentPdf
                        ? t("已保存版本的 PDF", "Saved manuscript PDF")
                        : t("历史编译 PDF", "Previously compiled PDF")}{" "}
                      · r{paper.data.compiled_revision ?? "?"}
                    </p>
                  )}
                  <PDFViewer
                    url={`/api/projects/${id}/download?path=${encodeURIComponent(paper.data.pdf_path)}&revision=${paper.revision}`}
                  />
                </>
              ) : (
                <Empty
                  icon={<FileText size={28} />}
                  title={
                    layoutDirty
                      ? t("版面尚未应用", "Layout changes are unsaved")
                      : paper?.data.pdf_path
                        ? t("PDF 需要更新", "PDF needs recompilation")
                        : t("暂无 PDF", "No PDF")
                  }
                  description={
                    layoutDirty
                      ? t(
                          "请先应用版面并编译，再导出当前 PDF。",
                          "Apply layout and compile before exporting the current PDF.",
                        )
                      : paper?.data.pdf_path
                        ? t(
                            "重新编译当前已保存版本后可导出当前 PDF。",
                            "Compile the current saved revision to export its PDF.",
                          )
                        : undefined
                  }
                  action={
                    paper?.data.pdf_path ? (
                      <Button onClick={() => setPreviousPdfScope(pdfScope)}>
                        {currentPdf
                          ? t("查看已保存版本 PDF", "View saved PDF")
                          : t("查看上次编译 PDF", "View previous PDF")}
                      </Button>
                    ) : undefined
                  }
                />
              ))}
            {rightTab === "log" && (
              <pre className="compile-log">
                {paper?.data.log || t("尚无编译日志。", "No compilation log")}
              </pre>
            )}
            {rightTab === "checks" && (
              <div className="paper-checks">
                {draft.compileRunId && (
                  <p className="muted">
                    {t("已请求编译任务", "Requested compilation run")}:{" "}
                    {draft.compileRunId.slice(0, 8)}
                  </p>
                )}
                <PaperPreflight
                  report={paper?.data.layout_preflight}
                  plan={paper?.data.layout_plan}
                />
                {issues ? (
                  <details open>
                    <summary>Evidence checks</summary>
                    <JsonView value={issues} />
                  </details>
                ) : null}
              </div>
            )}
          </div>
        </div>
      </div>
      <div className="paper-instruction">
        <input
          value={instruction}
          onChange={(e) => session.update({ instruction: e.target.value })}
          aria-label={t("论文修订要求", "Manuscript revision instruction")}
          placeholder={t("描述需要修改的内容…", "Describe the revision…")}
        />
        <Button
          busy={revising}
          disabled={!instruction.trim() || busy || revising || !manuscriptReady}
          onClick={() => {
            void perform(
              "revise",
              async () => {
                const submittedInstruction = session.getSnapshot().instruction;
                const revision = await save();
                await requestApi("/api/papers/{ident}/revise", "post", {
                  path: { ident: id! },
                  body: {
                    instruction: submittedInstruction,
                    expected_revision: revision,
                  },
                });
                if (session.getSnapshot().instruction === submittedInstruction)
                  session.update({ instruction: "" });
                await reload();
              },
              t("论文修订任务已排队", "Paper revision queued"),
            );
          }}
        >
          <Send size={14} />
          {t("修订", "Revise")}
        </Button>
      </div>
      <PaperRevisions
        projectId={id!}
        revision={paper?.revision}
        reload={reload}
        draftDirty={dirty || layoutDirty}
      />
    </div>
  );
}

function PaperGeneration({
  projectId,
  template,
  layout,
  dirty,
  save,
  reload,
  perform,
  disabled,
}: {
  projectId: string;
  template: PaperTemplate;
  layout: PaperLayoutConfig;
  dirty: boolean;
  save: () => Promise<number>;
  reload: () => Promise<void>;
  perform: (
    kind: string,
    work: () => Promise<unknown>,
    message?: string,
  ) => Promise<void>;
  disabled: boolean;
}) {
  const { t } = useUI();
  const { data: runs } = useLoad<Run[]>(
    `/projects/${projectId}/runs?include_manuscript_evidence=true`,
    [],
  );
  const { data: figures } = useLoad<RecordItem[]>(
    `/figures?project_id=${projectId}`,
    [],
  );
  const [runIds, setRunIds] = useState<string[]>([]);
  const [figureIds, setFigureIds] = useState<string[]>([]);
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const completed = runs.filter(
    (run) =>
      run.status === "completed" &&
      ["experiment", "command", "agent"].includes(run.kind) &&
      run.manuscript_evidence?.ready === true,
  );
  const reviewed = figures.filter(
    (figure) =>
      figure.status === "ready_for_review" &&
      (figure.data.outputs?.pdf || figure.data.outputs?.png),
  );
  const toggle = (values: string[], id: string) =>
    values.includes(id)
      ? values.filter((value) => value !== id)
      : [...values, id];
  return (
    <details className="paper-layout">
      <summary>
        {t(
          "从真实实验生成完整论文",
          "Generate a full manuscript from actual experiments",
        )}
      </summary>
      <p className="muted">
        {t(
          "默认完整投稿篇幅；图片由独立 agent 评选和定位，编译后检查实际位置。实验或文献缺项会保留待补工作。",
          "Uses the full submission profile. Independent agents select and place visuals; compiled locations are checked. Evidence gaps remain explicit work to complete.",
        )}
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setBusy(true);
          void perform(
            "generate",
            async () => {
              if (dirty) await save();
              await api(`/papers/${projectId}/generate`, "POST", {
                request_id: uid(),
                run_ids: runIds,
                figure_ids: figureIds,
                manuscript_type: "full_paper",
                template,
                layout,
                ...(instructions.trim() ? { instructions } : {}),
              });
              await reload();
            },
            t("完整论文生成任务已排队", "Full manuscript generation queued"),
          ).finally(() => setBusy(false));
        }}
      >
        <Field
          label={t(
            "已完成的实验与测量",
            "Completed experiments and measurements",
          )}
        >
          {completed.map((run) => (
            <label key={run.id}>
              <input
                type="checkbox"
                checked={runIds.includes(run.id)}
                onChange={() => setRunIds((current) => toggle(current, run.id))}
              />
              {run.kind} · {run.id.slice(0, 8)}
            </label>
          ))}
          {!completed.length && (
            <p>
              {t(
                "先完成能生成可读数值指标的实验。",
                "Complete an experiment that produces readable numeric metrics.",
              )}
            </p>
          )}
        </Field>
        <Field label={t("必须插入的已评审图片", "Reviewed figures to include")}>
          {reviewed.map((figure) => (
            <label key={figure.id}>
              <input
                type="checkbox"
                checked={figureIds.includes(figure.id)}
                onChange={() =>
                  setFigureIds((current) => toggle(current, figure.id))
                }
              />
              {figure.title}
            </label>
          ))}
        </Field>
        <Field
          label={t(
            "研究问题与写作要求",
            "Research question and writing requirements",
          )}
        >
          <textarea
            rows={3}
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
          />
        </Field>
        <Button
          className="primary"
          busy={busy}
          disabled={disabled || busy || !runIds.length}
        >
          {t("生成完整论文", "Generate full manuscript")}
        </Button>
      </form>
    </details>
  );
}

function PaperFigureInsertion({
  projectId,
  source,
  save,
  reload,
  perform,
  disabled,
  onInserted,
}: {
  projectId: string;
  source: string;
  save: () => Promise<number>;
  reload: () => Promise<void>;
  perform: (
    kind: string,
    work: () => Promise<unknown>,
    message?: string,
  ) => Promise<void>;
  disabled: boolean;
  onInserted: (
    saved: RecordItem,
    submitted: { source: string; bibtex: string; editingRevision?: number },
  ) => void;
}) {
  const { t } = useUI();
  const { data: figures } = useLoad<RecordItem[]>(
    `/figures?project_id=${projectId}`,
    [],
  );
  const [figureId, setFigureId] = useState("");
  const [anchor, setAnchor] = useState("");
  const ready = figures.filter(
    (figure) =>
      figure.status === "ready_for_review" &&
      (figure.data.outputs?.pdf || figure.data.outputs?.png),
  );
  return (
    <details className="paper-layout">
      <summary>
        {t(
          "在文章中插入已评审图片",
          "Insert a reviewed figure in the manuscript",
        )}
      </summary>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void perform(
            "insert-figure",
            async () => {
              const currentRevision = await save();
              const saved = await api<RecordItem>(
                `/papers/${projectId}/figures`,
                "POST",
                {
                  expected_revision: currentRevision,
                  figure_id: figureId,
                  anchor_text: anchor,
                },
              );
              onInserted(saved, {
                source,
                bibtex: saved.data.bibtex || "",
                editingRevision: currentRevision,
              });
              setAnchor("");
              await reload();
            },
            t(
              "图片已插入，请编译查看位置",
              "Figure inserted; compile to inspect placement",
            ),
          );
        }}
      >
        <Field label={t("选择图片", "Figure")}>
          <select
            required
            value={figureId}
            onChange={(event) => setFigureId(event.target.value)}
          >
            <option value="">
              {t(
                "选择已生成和评审的图片",
                "Select a rendered and reviewed figure",
              )}
            </option>
            {ready.map((figure) => (
              <option key={figure.id} value={figure.id}>
                {figure.title}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label={t("图片前的文章段落", "Preceding manuscript paragraph")}
          hint={t(
            "粘贴当前正文中唯一的一段文字；图片和交叉引用将紧接其后。",
            "Paste a unique paragraph from the current source; the figure and reference follow it.",
          )}
        >
          <textarea
            required
            rows={3}
            value={anchor}
            onChange={(event) => setAnchor(event.target.value)}
          />
        </Field>
        <Button
          className="primary"
          disabled={
            disabled || !figureId || !anchor.trim() || !source.includes(anchor)
          }
        >
          {t("插入图片", "Insert figure")}
        </Button>
      </form>
    </details>
  );
}

function PaperRevisions({
  projectId,
  revision,
  reload,
  draftDirty,
}: {
  projectId: string;
  revision?: number;
  reload: () => Promise<void>;
  draftDirty: boolean;
}) {
  const { t } = useUI();
  const { data: reviews, reload: reloadReviews } = useLoad<RecordItem[]>(
    `/reviews?project_id=${projectId}`,
    [],
  );
  useEffect(() => {
    const refresh = () => void reloadReviews();
    window.addEventListener("forest-refresh", refresh);
    return () => window.removeEventListener("forest-refresh", refresh);
  }, [reloadReviews]);
  const proposals = reviews.filter(
    (r) =>
      Array.isArray(r.data.edits) &&
      r.data.edits.length &&
      r.status !== "applied",
  );
  if (!proposals.length) return null;
  return (
    <details className="paper-revision-list" open>
      <summary>
        {t("逐处检查论文修订建议", "Suggested revisions")} ({proposals.length})
      </summary>
      {draftDirty && (
        <p className="muted">
          {t(
            "保存或完成当前版面修改后，再应用修订建议。",
            "Save or finish the current layout edits before applying a proposal.",
          )}
        </p>
      )}
      {proposals.map((r) => (
        <RevisionProposal
          key={r.id}
          record={r}
          revision={revision}
          draftDirty={draftDirty}
          onApplied={async () => {
            await reload();
            await reloadReviews();
          }}
        />
      ))}
    </details>
  );
}

function RevisionProposal({
  record,
  revision,
  onApplied,
  draftDirty,
}: {
  record: RecordItem;
  revision?: number;
  onApplied: () => Promise<void>;
  draftDirty: boolean;
}) {
  const { t, action } = useUI();
  const [selected, setSelected] = useState<number[]>([]);
  const edits: Json[] = record.data.edits;
  return (
    <section className="revision-proposal">
      <h3>{record.title}</h3>
      {edits.map((edit, index) => (
        <div key={index}>
          <label>
            <input
              type="checkbox"
              checked={selected.includes(index)}
              onChange={(e) =>
                setSelected((s) =>
                  e.target.checked
                    ? [...s, index]
                    : s.filter((i) => i !== index),
                )
              }
            />
            {edit.reason || edit.issue || t("措辞修订", "Wording revision")}
          </label>
          <p className="revision-original">{edit.original}</p>
          <p className="revision-replacement">{edit.replacement}</p>
        </div>
      ))}
      <div className="inline-actions">
        <Badge status={record.status} />
        <span className="muted">
          {t("来源论文版本", "Source manuscript revision")}:{" "}
          {record.data.paper_revision}
        </span>
        <Button
          className="primary"
          disabled={
            draftDirty ||
            !selected.length ||
            record.data.paper_revision !== revision
          }
          onClick={() =>
            action(async () => {
              await api(`/reviews/${record.id}/apply`, "POST", {
                expected_revision: revision,
                indices: selected,
              });
              await onApplied();
            })
          }
        >
          <Check size={14} />
          {t("应用选中的最小修改", "Apply selected edits")}
        </Button>
      </div>
    </section>
  );
}
