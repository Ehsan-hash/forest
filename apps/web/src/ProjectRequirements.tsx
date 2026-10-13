import { useEffect, useRef, useState } from "react";
import { ApiError, uid } from "./api";
import type { Json } from "./api";
import { requestApi } from "./apiClient";
import type { components } from "./apiClient";
import { Badge, Button, ErrorBox, Field, JsonView, useLoad, useUI } from "./ui";

type EvaluationVersion = components["schemas"]["EvaluationProtocolVersion"];
type EvaluationRequest =
  components["schemas"]["EvaluationProtocolRevisionRequest"];
type ProfileDraft = { text: string; base: Json | null; dirty: boolean };
type ProtocolDraft = {
  text: string;
  scope: string;
  projectRevision: number;
  protocolRevision: number;
  dirty: boolean;
};
const json = (value: unknown) => JSON.stringify(value, null, 2);
const canonical = (value: unknown): string => {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, field]) => `${JSON.stringify(key)}:${canonical(field)}`)
      .join(",")}}`;
  return JSON.stringify(value) ?? "undefined";
};
function object(text: string): Json {
  const value = JSON.parse(text);
  if (!value || Array.isArray(value) || typeof value !== "object")
    throw new Error("Enter a JSON object.");
  return value;
}
function stored<T>(key: string): T | null {
  try {
    return JSON.parse(sessionStorage.getItem(key) || "null");
  } catch {
    return null;
  }
}
function keep(key: string, value: unknown) {
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* The active draft remains editable. */
  }
}

/** Publication PATCH merges fields; omitted fields cannot be removed by that operation. */
export function publicationChanges(
  base: Json,
  edited: Json,
  current: Json,
): Json {
  const removed = Object.keys(base).filter((key) => !(key in edited));
  if (removed.length)
    throw new Error(
      `Publication fields cannot be removed by a merge: ${removed.join(", ")}.`,
    );
  const patch = Object.fromEntries(
    Object.entries(edited).filter(
      ([key, value]) => canonical(value) !== canonical(base[key]),
    ),
  );
  const conflicting = Object.keys(patch).filter(
    (key) =>
      canonical(current[key]) !== canonical(base[key]) &&
      canonical(current[key]) !== canonical(edited[key]),
  );
  if (conflicting.length)
    throw new Error(
      `Saved publication requirements changed in: ${conflicting.join(", ")}. Reload and review your draft.`,
    );
  return Object.fromEntries(
    Object.entries(patch).filter(
      ([key, value]) => canonical(current[key]) !== canonical(value),
    ),
  );
}

export function ProjectRequirements(props: {
  projectId: string;
  projectRevision: number;
  onChanged?: () => Promise<void>;
}) {
  const { t } = useUI();
  const [opened, setOpened] = useState(false);
  return (
    <details
      className="surface project-requirements"
      onToggle={(event) => {
        if (event.currentTarget.open) setOpened(true);
      }}
    >
      <summary>
        {t(
          "投稿要求与实验协议",
          "Publication requirements & evaluation protocols",
        )}
      </summary>
      {opened && <RequirementsForm key={props.projectId} {...props} />}
    </details>
  );
}

function RequirementsForm({
  projectId,
  projectRevision,
  onChanged,
}: {
  projectId: string;
  projectRevision: number;
  onChanged?: () => Promise<void>;
}) {
  const { t } = useUI();
  const {
    data: audit,
    error: auditError,
    reload: reloadAudit,
  } = useLoad<components["schemas"]["PublicationAudit"] | null>(
    `/projects/${projectId}/publication`,
    null,
  );
  const { data: defaults, error: defaultError } = useLoad<Json | null>(
    "/publication-profile",
    null,
  );
  const { data: writingPolicy, error: policyError } = useLoad<Json | null>(
    "/writing-policy",
    null,
  );
  const {
    data: versions,
    error: versionError,
    reload: reloadVersions,
  } = useLoad<EvaluationVersion[]>(
    `/projects/${projectId}/evaluation-protocols`,
    [],
  );
  const profileKey = `forest-publication-draft:${projectId}`;
  const protocolKey = `forest-protocol-draft:${projectId}`;
  const attemptKey = `forest-protocol-attempt:${projectId}`;
  const [profile, setProfile] = useState<ProfileDraft>(
    () =>
      stored<ProfileDraft>(profileKey) || {
        text: "",
        base: null,
        dirty: false,
      },
  );
  const [protocol, setProtocol] = useState<ProtocolDraft>(
    () =>
      stored<ProtocolDraft>(protocolKey) || {
        text: json({
          name: "",
          definition: {},
          reason: "",
          classification: "exploratory",
          feedback_run_ids: [],
        }),
        scope: "",
        projectRevision,
        protocolRevision: 0,
        dirty: false,
      },
  );
  const profileRef = useRef(profile);
  profileRef.current = profile;
  const protocolRef = useRef(protocol);
  protocolRef.current = protocol;
  const knownRevision = useRef(projectRevision);
  knownRevision.current = Math.max(knownRevision.current, projectRevision);
  const busyRef = useRef(false);
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [validation, setValidation] = useState<{
    text: string;
    result: components["schemas"]["ProtocolResult"];
  } | null>(null);
  const attempt = useRef<{
    fingerprint: string;
    body: EvaluationRequest;
  } | null>(stored(attemptKey));
  const updateProfile = (next: ProfileDraft) => {
    profileRef.current = next;
    setProfile(next);
    keep(profileKey, next);
  };
  const updateProtocol = (next: ProtocolDraft) => {
    protocolRef.current = next;
    setProtocol(next);
    keep(protocolKey, next);
  };
  const refresh = async () => {
    await Promise.all([reloadAudit(), reloadVersions(), onChanged?.()]);
  };
  useEffect(() => {
    const listener = () => {
      void refresh();
    };
    window.addEventListener("forest-refresh", listener);
    return () => window.removeEventListener("forest-refresh", listener);
  }, [reloadAudit, reloadVersions, onChanged]);
  useEffect(() => {
    if (audit && !profileRef.current.dirty)
      updateProfile({
        text: json(audit.profile),
        base: audit.profile,
        dirty: false,
      });
  }, [audit]);
  const latest = [
    ...new Map(
      versions.map((version) => [version.protocol_id, version]),
    ).values(),
  ];
  const loadProtocol = (
    scope: string,
    revision = knownRevision.current,
    rows = versions,
  ) => {
    const version = [...rows]
      .reverse()
      .find((row) => row.protocol_id === scope);
    // A clean restored scope must wait for its authoritative history read.
    if (scope && !version) return;
    updateProtocol({
      scope,
      projectRevision: revision,
      protocolRevision: version?.revision || 0,
      dirty: false,
      text: json(
        version
          ? {
              name: version.name || "",
              definition: version.definition,
              reason: "",
              classification: version.classification,
              feedback_run_ids: version.feedback_run_ids,
            }
          : {
              name: "",
              definition: {},
              reason: "",
              classification: "exploratory",
              feedback_run_ids: [],
            },
      ),
    });
    attempt.current = null;
    keep(attemptKey, null);
    setValidation(null);
  };
  useEffect(() => {
    if (!protocolRef.current.dirty) loadProtocol(protocolRef.current.scope);
  }, [versions, projectRevision]);
  const perform = async (kind: string, work: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setPending(kind);
    setError("");
    setMessage("");
    try {
      await work();
    } catch (failure) {
      setError(
        failure instanceof ApiError
          ? `${failure.code}: ${failure.message}`
          : (failure as Error).message,
      );
    } finally {
      busyRef.current = false;
      setPending("");
    }
  };
  const reloadGuard = async () => {
    const project = await requestApi("/api/projects/{ident}", "get", {
      path: { ident: projectId },
    });
    knownRevision.current = project.revision;
    return project.revision;
  };

  return (
    <div className="project-requirements-content">
      <ErrorBox error={auditError} retry={reloadAudit} />
      <ErrorBox error={error} />
      {message && <p role="status">{message}</p>}
      {audit && (
        <section aria-label={t("当前交付检查", "Current delivery audit")}>
          <div className="inline-actions">
            <strong>{t("已保存交付状态", "Saved delivery status")}</strong>
            <Badge status={audit.status} />
            <Button disabled={!!pending} onClick={() => void refresh()}>
              {t("刷新检查", "Refresh audit")}
            </Button>
          </div>
          <p>
            {audit.verification_scope ||
              t(
                "当前已保存材料的服务端交付检查。",
                "Service audit of current saved delivery material.",
              )}
          </p>
          <p className="muted">
            {t(
              "交付检查结果不会证明论文获会议或期刊录用。",
              "Delivery readiness does not establish journal or conference acceptance.",
            )}
          </p>
          <ul>
            {audit.gaps.map((gap, index) => (
              <li key={index}>
                <strong>{String(gap.code || "gap")}</strong> ·{" "}
                {String(gap.finding || gap.message || json(gap))}
                {gap.next_role ? ` · ${gap.next_role}` : ""}
              </li>
            ))}
          </ul>
          <details>
            <summary>
              {t("查看完整检查与来源", "Full audit and sources")}
            </summary>
            <JsonView value={audit} />
          </details>
        </section>
      )}
      <details>
        <summary>{t("编辑投稿要求", "Edit publication requirements")}</summary>
        <Field
          label={t("投稿要求 JSON", "Publication requirements JSON")}
          hint={t(
            "仅合并修改的字段；省略字段不会删除已保存值。",
            "Only edited fields are merged. Omitting a field cannot delete its saved value.",
          )}
        >
          <textarea
            className="code-input"
            rows={12}
            value={profile.text}
            onChange={(event) =>
              updateProfile({
                ...profile,
                text: event.target.value,
                dirty: true,
              })
            }
          />
        </Field>
        <div className="inline-actions">
          <Button
            disabled={!!pending || !profile.base || !profile.dirty}
            onClick={() =>
              void perform("publication", async () => {
                const submitted = profileRef.current;
                const edited = object(submitted.text);
                const current = await requestApi(
                  "/api/projects/{ident}/publication",
                  "get",
                  { path: { ident: projectId } },
                );
                const patch = publicationChanges(
                  submitted.base!,
                  edited,
                  current.profile,
                );
                const saved = Object.keys(patch).length
                  ? await requestApi(
                      "/api/projects/{ident}/publication",
                      "patch",
                      { path: { ident: projectId }, body: patch },
                    )
                  : { profile: current.profile };
                const typedLater = profileRef.current.text !== submitted.text;
                updateProfile({
                  text: typedLater
                    ? profileRef.current.text
                    : json(saved.profile),
                  base: saved.profile,
                  dirty: typedLater,
                });
                await reloadGuard();
                await refresh();
                setMessage(
                  t(
                    "投稿要求已保存；请查看服务端规范化后的配置。",
                    "Publication requirements saved; inspect the normalized saved profile.",
                  ),
                );
              })
            }
          >
            {t("保存投稿要求", "Save publication requirements")}
          </Button>
          <Button
            disabled={!!pending || !audit}
            onClick={() =>
              void perform("reload-publication", async () => {
                if (
                  profile.dirty &&
                  !window.confirm(
                    t(
                      "放弃未保存的投稿要求修改？",
                      "Discard unsaved publication requirement edits?",
                    ),
                  )
                )
                  return;
                const current = await requestApi(
                  "/api/projects/{ident}/publication",
                  "get",
                  { path: { ident: projectId } },
                );
                updateProfile({
                  text: json(current.profile),
                  base: current.profile,
                  dirty: false,
                });
                await reloadAudit();
              })
            }
          >
            {t("重新读取已保存要求", "Reload saved requirements")}
          </Button>
        </div>
      </details>
      <details>
        <summary>
          {t(
            "实验与评估协议版本",
            "Experiment and evaluation protocol versions",
          )}
        </summary>
        <ErrorBox error={versionError} retry={reloadVersions} />
        <Field label={t("评估协议", "Evaluation protocol")}>
          <select
            value={protocol.scope}
            onChange={(event) => {
              if (
                !protocol.dirty ||
                window.confirm(
                  t(
                    "放弃未保存的协议修改？",
                    "Discard unsaved protocol edits?",
                  ),
                )
              )
                loadProtocol(event.target.value);
            }}
          >
            <option value="">{t("新建协议", "New protocol")}</option>
            {latest.map((row) => (
              <option value={row.protocol_id} key={row.protocol_id}>
                {row.name || row.protocol_id} · r{row.revision} ·{" "}
                {row.classification}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label={t("评估协议 JSON", "Evaluation protocol JSON")}
          hint={t(
            "definition 保留完整定义；reason 说明本次修改；feedback_run_ids 引用实际反馈运行。",
            "Keep the complete definition, state this revision's reason, and reference actual feedback run IDs.",
          )}
        >
          <textarea
            className="code-input"
            rows={13}
            value={protocol.text}
            onChange={(event) =>
              updateProtocol({
                ...protocol,
                text: event.target.value,
                dirty: true,
              })
            }
          />
        </Field>
        <p className="muted">
          {t("编辑基于项目版本", "Editing project revision")}: r
          {protocol.projectRevision} · {t("协议版本", "Protocol revision")}: r
          {protocol.protocolRevision}.{" "}
          {t(
            "独立确认分类是声明的评估范围，不是执行或验证结果。",
            "Independent confirmation declares an evaluation scope; it does not establish execution or verification.",
          )}
        </p>
        <div className="inline-actions">
          <Button
            disabled={!!pending}
            onClick={() =>
              void perform("validate", async () => {
                const text = protocolRef.current.text;
                const draft = object(text);
                const definition = object(json(draft.definition));
                const result = await requestApi(
                  "/api/projects/{ident}/protocol/validate",
                  "post",
                  {
                    path: { ident: projectId },
                    body: definition as components["schemas"]["ProtocolRequest"],
                  },
                );
                setValidation({ text, result });
              })
            }
          >
            {t("检查实验协议结构", "Validate experiment protocol structure")}
          </Button>
          <Button
            disabled={!!pending || !protocol.dirty}
            onClick={() =>
              void perform("protocol", async () => {
                const submitted = protocolRef.current;
                const parsed = object(submitted.text);
                if (typeof parsed.reason !== "string" || !parsed.reason.trim())
                  throw new Error(
                    t(
                      "请说明协议修改的原因。",
                      "State the reason for this protocol revision.",
                    ),
                  );
                const definition = object(json(parsed.definition));
                const intent = {
                  ...parsed,
                  definition,
                  ...(submitted.scope ? { protocol_id: submitted.scope } : {}),
                  expected_revision: submitted.projectRevision,
                  expected_protocol_revision: submitted.protocolRevision,
                } as EvaluationRequest;
                const fingerprint = json(intent);
                if (attempt.current?.fingerprint !== fingerprint) {
                  attempt.current = {
                    fingerprint,
                    body: { ...intent, request_id: uid() },
                  };
                  keep(attemptKey, attempt.current);
                }
                const saved = await requestApi(
                  "/api/projects/{project_id}/evaluation-protocols",
                  "post",
                  {
                    path: { project_id: projectId },
                    body: attempt.current!.body,
                  },
                );
                const revision = await reloadGuard();
                if (protocolRef.current.text === submitted.text)
                  updateProtocol({
                    text: json({
                      name: saved.name || "",
                      definition: saved.definition,
                      reason: "",
                      classification: saved.classification,
                      feedback_run_ids: saved.feedback_run_ids,
                    }),
                    scope: saved.protocol_id,
                    projectRevision: revision,
                    protocolRevision: saved.revision,
                    dirty: false,
                  });
                else
                  updateProtocol({
                    ...protocolRef.current,
                    scope: saved.protocol_id,
                    projectRevision: revision,
                    protocolRevision: saved.revision,
                    dirty: true,
                  });
                attempt.current = null;
                keep(attemptKey, null);
                await refresh();
                setMessage(t("协议版本已记录。", "Protocol version recorded."));
              })
            }
          >
            {t("记录协议版本", "Record protocol version")}
          </Button>
          <Button
            disabled={!!pending}
            onClick={() =>
              void perform("reload-protocol", async () => {
                if (
                  protocol.dirty &&
                  !window.confirm(
                    t(
                      "放弃未保存的协议修改？",
                      "Discard unsaved protocol edits?",
                    ),
                  )
                )
                  return;
                const [revision, current] = await Promise.all([
                  reloadGuard(),
                  requestApi(
                    "/api/projects/{project_id}/evaluation-protocols",
                    "get",
                    { path: { project_id: projectId } },
                  ),
                ]);
                loadProtocol(protocol.scope, revision, current);
                await reloadVersions();
              })
            }
          >
            {t("重新读取最新版本", "Reload latest protocol")}
          </Button>
        </div>
        {validation && (
          <section aria-label={t("协议结构检查", "Protocol structure result")}>
            <Badge status={validation.result.status} />
            <p>{validation.result.verification_scope}</p>
            {(validation.text !== protocol.text ||
              canonical(validation.result.publication_profile) !==
                canonical(audit?.profile)) && (
              <p role="status">
                {t(
                  "草稿或投稿要求已改变，请重新检查。",
                  "The draft or publication requirements changed. Validate again.",
                )}
              </p>
            )}
            <JsonView value={validation.result} />
          </section>
        )}
        <details>
          <summary>
            {t("完整协议版本历史", "Complete protocol version history")} (
            {versions.length})
          </summary>
          <JsonView value={versions} />
        </details>
      </details>
      <details>
        <summary>
          {t("默认投稿与写作政策", "Default publication and writing policies")}
        </summary>
        <ErrorBox error={defaultError} />
        <ErrorBox error={policyError} />
        {defaults && <JsonView value={defaults} />}
        {writingPolicy && <JsonView value={writingPolicy} />}
      </details>
    </div>
  );
}
