import {
  acknowledgeSettingsSave,
  applyProviderToSettingsDraft,
  settingsDraftFromSnapshot,
  settingsDraftPatch,
  useSettingsDraft,
} from "../settingsDraft";
import { hostEditorConfig, hostPayload } from "../connectionPayload";
import { useState, useEffect } from "react";
import {
  Plus,
  Trash2,
  Pencil,
  RefreshCw,
  Lightbulb,
  Settings,
  PlugZap,
  Server,
  Save,
} from "lucide-react";
import { api, parseJson } from "../api";
import type { Json } from "../api";
import { ProviderUsage } from "../ProviderUsage";
import {
  useUI,
  useLoad,
  Button,
  IconButton,
  Badge,
  Modal,
  Field,
  Empty,
  ErrorBox,
  JsonView,
  Tabs,
  PageHeading,
} from "../ui";

export function SettingsPage() {
  const { t, action } = useUI();
  const {
    data: providers,
    reload: reloadProviders,
    error,
  } = useLoad<Json[]>("/providers", []);
  const { data: hosts, reload: reloadHosts } = useLoad<Json[]>("/hosts", []);
  const { data: agents, reload: reloadAgents } = useLoad<Json[]>("/agents", []);
  const { data: system, reload: reloadSystem } = useLoad<Json>("/system", {});
  const {
    data: settings,
    reload: reloadSettings,
    error: settingsError,
    loading: settingsLoading,
  } = useLoad<Json>("/settings", {});
  const [tab, setTab] = useState("providers");
  const [modal, setModal] = useState<{ resource: string; item?: Json } | null>(
    null,
  );
  const [test, setTest] = useState<Json | null>(null);
  const [retiring, setRetiring] = useState<{
    provider: Json;
    references: Json[];
    historical_request_count: number;
  } | null>(null);
  const [replacementProviderId, setReplacementProviderId] = useState("");
  const { draft, setDraft, dirty } = useSettingsDraft(
    settings,
    !settingsLoading && !settingsError,
  );
  const settingsText = draft.text;
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);
  const [discard, setDiscard] = useState(false);
  useEffect(() => {
    const refresh = () => {
      void reloadSettings();
    };
    window.addEventListener("forest-settings-changed", refresh);
    return () => window.removeEventListener("forest-settings-changed", refresh);
  }, [reloadSettings]);
  const reload = () => {
    void reloadProviders();
    void reloadHosts();
    void reloadAgents();
    void reloadSystem();
    void reloadSettings();
  };
  return (
    <>
      <header className="topbar">
        <div className="topbar-project">
          <Settings size={16} />
          {t("设置与连接", "Settings & connections")}
        </div>
        <span className="local-label">FOREST / LOCAL</span>
      </header>
      <div className="page settings-page">
        <PageHeading title={t("设置", "Settings")} />
        <Tabs
          panelId="settings-tabs"
          value={tab}
          onChange={setTab}
          items={[
            { id: "providers", label: t("模型 Provider", "Model providers") },
            { id: "hosts", label: t("计算主机", "Compute hosts") },
            { id: "agents", label: t("Agent 角色", "Agent roles") },
            { id: "defaults", label: t("预算与默认参数", "Budget & defaults") },
            { id: "system", label: t("系统资源", "System resources") },
          ]}
        />
        <div
          className="tab-panel"
          id="settings-tabs-panel"
          role="tabpanel"
          aria-labelledby={`settings-tabs-tab-${tab}`}
          tabIndex={0}
        >
          <ErrorBox error={error} retry={reload} />
          {["providers", "hosts", "agents"].includes(tab) && (
            <>
              <div className="section-toolbar">
                <h2>
                  {tab === "providers"
                    ? t("模型连接", "Model connections")
                    : tab === "hosts"
                      ? t("计算主机", "Compute hosts")
                      : t("研究角色", "Research agents")}
                </h2>
                <Button
                  className="primary"
                  onClick={() => setModal({ resource: tab })}
                >
                  <Plus size={15} />
                  {t("添加", "Add")}
                </Button>
              </div>
              <div className="settings-cards">
                {(tab === "providers"
                  ? providers
                  : tab === "hosts"
                    ? hosts
                    : agents
                ).map((item) => (
                  <section className="surface connection-card" key={item.id}>
                    <div className="connection-card-icon">
                      {tab === "providers" ? (
                        <PlugZap size={23} />
                      ) : tab === "hosts" ? (
                        <Server size={23} />
                      ) : (
                        <Lightbulb size={23} />
                      )}
                    </div>
                    <div>
                      <h3>{item.name || item.role}</h3>
                      <p>
                        {item.model ||
                          item.hostname ||
                          item.description ||
                          item.kind}
                      </p>
                      <code>{item.base_url || item.address || item.role}</code>
                    </div>
                    <Badge status={item.status || "idle"} />
                    {tab === "providers" &&
                      item.kind !== "ollama" &&
                      item.status !== "retired" && (
                        <ProviderUsage providerId={item.id} />
                      )}
                    {!(tab === "providers" && item.status === "retired") && (
                      <IconButton
                        label={t("编辑连接", "Edit connection")}
                        onClick={() => setModal({ resource: tab, item })}
                      >
                        <Pencil size={15} />
                      </IconButton>
                    )}
                    {tab !== "agents" &&
                      !(tab === "providers" && item.status === "retired") && (
                        <Button
                          onClick={() =>
                            action(async () => {
                              setTest(
                                await api(
                                  `/${tab}/${item.id}/test`,
                                  "POST",
                                  {},
                                ),
                              );
                              reload();
                            })
                          }
                        >
                          <PlugZap size={14} />
                          {t("测试连接", "Test connection")}
                        </Button>
                      )}
                    {tab === "providers" && item.status !== "retired" && (
                      <Button
                        onClick={() =>
                          action(async () => {
                            const saved = await api<Json>(
                              "/settings",
                              "PATCH",
                              {
                                default_provider_id: item.id,
                              },
                            );
                            setDraft((current) =>
                              applyProviderToSettingsDraft(
                                current,
                                item.id,
                                saved,
                              ),
                            );
                            window.dispatchEvent(
                              new Event("forest-settings-changed"),
                            );
                          }, "Default model updated")
                        }
                      >
                        Use for new projects
                      </Button>
                    )}
                    {tab === "providers" && item.status !== "retired" && (
                      <Button
                        onClick={() =>
                          action(async () => {
                            const result = await api<Json>(
                              `/providers/${item.id}/references`,
                            );
                            setReplacementProviderId("");
                            setRetiring({
                              provider: item,
                              references: result.references || [],
                              historical_request_count:
                                result.historical_request_count || 0,
                            });
                          })
                        }
                      >
                        {t("退役", "Retire")}
                      </Button>
                    )}
                    {tab === "providers" && item.status === "retired" && (
                      <Button
                        onClick={() =>
                          action(
                            async () => {
                              await api(
                                `/providers/${item.id}/restore`,
                                "POST",
                                {},
                              );
                              reload();
                            },
                            t(
                              "提供方已恢复；请重新配置凭据。",
                              "Provider restored; configure credentials again.",
                            ),
                          )
                        }
                      >
                        {t("恢复", "Restore")}
                      </Button>
                    )}
                  </section>
                ))}
              </div>
              {!(
                tab === "providers"
                  ? providers
                  : tab === "hosts"
                    ? hosts
                    : agents
              ).length && (
                <Empty
                  icon={
                    tab === "providers" ? (
                      <PlugZap size={28} />
                    ) : (
                      <Server size={28} />
                    )
                  }
                  title={
                    tab === "providers"
                      ? t("暂无模型提供方", "No model providers")
                      : tab === "hosts"
                        ? t("暂无计算主机", "No compute hosts")
                        : t("暂无 Agent 角色", "No agent roles")
                  }
                  action={
                    <Button onClick={() => setModal({ resource: tab })}>
                      <Plus size={15} />
                      {t("添加配置", "Add configuration")}
                    </Button>
                  }
                />
              )}
            </>
          )}
          {tab === "defaults" && (
            <section className="surface settings-defaults">
              <h3>{t("全局运行设置", "Global execution settings")}</h3>
              <p className="muted">
                {t(
                  "项目设置会覆盖这些默认值。",
                  "Project settings override these defaults.",
                )}
              </p>
              <textarea
                aria-label={t("全局设置 JSON", "Global settings JSON")}
                className="code-input"
                rows={20}
                value={settingsText}
                disabled={!draft.initialized}
                onChange={(e) =>
                  setDraft((current) => ({ ...current, text: e.target.value }))
                }
              />
              <ErrorBox
                error={settingsError || saveError}
                retry={reloadSettings}
              />
              <p role="status">
                {!draft.initialized
                  ? settingsLoading
                    ? t("正在加载全局设置…", "Loading global settings…")
                    : t(
                        "无法读取已保存的全局设置",
                        "Saved global settings unavailable",
                      )
                  : dirty
                    ? t("全局设置草稿未保存", "Unsaved global settings draft")
                    : t("全局设置已保存", "Global settings saved")}
              </p>
              <p className="muted">
                {t(
                  "只保存修改的键；省略的键保留当前服务器值。",
                  "Only changed keys are saved; omitted keys retain their current server values.",
                )}
              </p>
              <Button
                className="primary"
                busy={saving}
                disabled={!draft.initialized || !dirty}
                onClick={() =>
                  action(
                    async () => {
                      setSaving(true);
                      setSaveError("");
                      try {
                        const saved = await api<Json>(
                          "/settings",
                          "PATCH",
                          settingsDraftPatch(draft),
                        );
                        setDraft((current) =>
                          acknowledgeSettingsSave(current, draft, saved),
                        );
                        await reloadSettings();
                        window.dispatchEvent(
                          new Event("forest-settings-changed"),
                        );
                      } catch (error) {
                        setSaveError((error as Error).message);
                        throw error;
                      } finally {
                        setSaving(false);
                      }
                    },
                    t("设置已保存", "Settings saved"),
                  )
                }
              >
                <Save size={15} />
                {t("保存设置", "Save settings")}
              </Button>
              {dirty && (
                <Button onClick={() => setDiscard(true)}>
                  {t("载入当前服务器设置", "Load current server settings")}
                </Button>
              )}
              <StorageCleanup
                retentionDays={
                  Number.isSafeInteger(settings.retention_days) &&
                  settings.retention_days >= 0
                    ? settings.retention_days
                    : null
                }
                settingsLoaded={!settingsLoading && !settingsError}
              />
            </section>
          )}
          {tab === "system" && (
            <section className="surface">
              <div className="section-toolbar">
                <h3>{t("实际运行环境", "Runtime environment")}</h3>
                <Button onClick={reloadSystem}>
                  <RefreshCw size={14} />
                  {t("刷新检测", "Refresh")}
                </Button>
              </div>
              <JsonView value={system} />
            </section>
          )}
        </div>
      </div>
      {modal && (
        <ConnectionEditor
          resource={modal.resource}
          item={modal.item}
          onClose={() => setModal(null)}
          onSaved={reload}
        />
      )}{" "}
      {retiring && (
        <Modal
          title={t("退役模型提供方", "Retire model provider")}
          onClose={() => setRetiring(null)}
        >
          <p>
            {t(
              "退役会停止新请求使用此提供方，清除其保存的凭据，并保留历史请求记录。",
              "Retiring stops new requests from using this provider, removes its stored credential, and preserves request history.",
            )}
          </p>
          <p>
            <strong>{retiring.provider.name}</strong> ·{" "}
            {retiring.references.length} {t("处当前引用", "current references")}{" "}
            · {retiring.historical_request_count}{" "}
            {t("条历史请求", "historical requests")}
          </p>
          {retiring.references.length > 0 && (
            <ul>
              {retiring.references.map((ref) => (
                <li key={`${ref.kind}:${ref.id}`}>
                  {ref.name} — {ref.location}
                </li>
              ))}
            </ul>
          )}
          <Field label={t("如何处理当前引用", "Handle current references")}>
            <select
              value={replacementProviderId}
              onChange={(event) => setReplacementProviderId(event.target.value)}
            >
              <option value="">
                {t("清除这些引用", "Clear these references")}
              </option>
              {providers
                .filter(
                  (provider) =>
                    provider.id !== retiring.provider.id &&
                    provider.status !== "retired",
                )
                .map((provider) => (
                  <option key={provider.id} value={provider.id}>
                    {provider.name} · {provider.model}
                  </option>
                ))}
            </select>
          </Field>
          <div className="modal-actions">
            <Button onClick={() => setRetiring(null)}>
              {t("取消", "Cancel")}
            </Button>
            <Button
              className="primary"
              onClick={() =>
                action(
                  async () => {
                    await api(
                      `/providers/${retiring.provider.id}/retire`,
                      "POST",
                      {
                        replacement_provider_id: replacementProviderId || null,
                      },
                    );
                    setRetiring(null);
                    reload();
                  },
                  t("提供方已退役", "Provider retired"),
                )
              }
            >
              {t("确认退役", "Confirm retirement")}
            </Button>
          </div>
        </Modal>
      )}
      {test && (
        <Modal
          title={t("实际连接测试结果", "Connection test result")}
          onClose={() => setTest(null)}
        >
          <JsonView value={test} />
        </Modal>
      )}
      {discard && (
        <Modal
          title={t("放弃全局设置草稿？", "Discard global settings draft?")}
          onClose={() => setDiscard(false)}
        >
          <p>
            {t(
              "将用当前已保存的服务器设置替换此草稿。",
              "Replace this draft with the current saved server settings.",
            )}
          </p>
          <Button
            className="primary"
            onClick={() =>
              action(async () => {
                const latest = await api<Json>("/settings");
                setDraft(settingsDraftFromSnapshot(latest));
                setSaveError("");
                setDiscard(false);
              })
            }
          >
            {t("载入当前设置", "Load current settings")}
          </Button>
        </Modal>
      )}
    </>
  );
}

function ConnectionEditor({
  resource,
  item,
  onClose,
  onSaved,
}: {
  resource: string;
  item?: Json;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t, action } = useUI();
  const [name, setName] = useState(item?.name || "");
  const [kind, setKind] = useState(
    item?.kind || (resource === "providers" ? "ollama" : "local"),
  );
  const [url, setUrl] = useState(item?.base_url || "http://127.0.0.1:11434");
  const [model, setModel] = useState(item?.model || "");
  const [key, setKey] = useState("");
  const [paid, setPaid] = useState(item?.allow_paid || false);
  const [apiMode, setApiMode] = useState(item?.config?.api || "responses");
  const [limitUsd, setLimitUsd] = useState(
    String(item?.config?.budget_usd ?? ""),
  );
  const [inputRate, setInputRate] = useState(
    String(item?.config?.pricing?.input_per_million ?? ""),
  );
  const [cachedRate, setCachedRate] = useState(
    String(item?.config?.pricing?.cached_input_per_million ?? ""),
  );
  const [outputRate, setOutputRate] = useState(
    String(item?.config?.pricing?.output_per_million ?? ""),
  );
  const [imageModel, setImageModel] = useState(
    item?.config?.image_generation?.model || "",
  );
  const [imageCeiling, setImageCeiling] = useState(
    String(item?.config?.image_generation?.max_request_usd ?? ""),
  );
  const [config, setConfig] = useState(
    JSON.stringify(
      item && resource === "providers"
        ? item.config || {}
        : item && resource === "hosts"
          ? hostEditorConfig(item)
          : item
            ? Object.fromEntries(
                Object.entries(item).filter(
                  ([k]) =>
                    !["id", "name", "created_at", "updated_at"].includes(k),
                ),
              )
            : resource === "hosts"
              ? {
                  kind: "local",
                  hostname: "localhost",
                  workspace: "",
                  allow_shell: true,
                }
              : resource === "agents"
                ? {
                    role: "researcher",
                    model: "",
                    instructions: "",
                    tools: [],
                    budget: {},
                  }
                : {},
      null,
      2,
    ),
  );
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  return (
    <Modal
      title={t("连接与角色配置", "Connection & role configuration")}
      onClose={onClose}
    >
      <ErrorBox error={formError} />
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setFormError("");
          const r = await action(async () => {
            try {
              const parsedConfig = parseJson(config);
              if (
                !parsedConfig ||
                typeof parsedConfig !== "object" ||
                Array.isArray(parsedConfig)
              )
                throw new Error(
                  t(
                    "配置必须是 JSON 对象。",
                    "Configuration must be a JSON object.",
                  ),
                );
              const body =
                resource === "providers"
                  ? {
                      name,
                      kind,
                      base_url: url,
                      model,
                      allow_paid: paid,
                      ...(key ? { api_key: key } : {}),
                      config: {
                        ...parsedConfig,
                        ...(kind !== "ollama" && kind !== "codex_cli"
                          ? {
                              api: apiMode,
                              budget_usd: limitUsd.trim()
                                ? Number(limitUsd)
                                : null,
                              ...(imageModel.trim()
                                ? {
                                    image_generation: {
                                      ...((parsedConfig.image_generation as Json) ||
                                        {}),
                                      model: imageModel.trim(),
                                      max_request_usd: Number(imageCeiling),
                                    },
                                  }
                                : {}),
                              pricing: {
                                ...((parsedConfig.pricing as Json) || {}),
                                input_per_million: inputRate.trim()
                                  ? Number(inputRate)
                                  : null,
                                cached_input_per_million: cachedRate.trim()
                                  ? Number(cachedRate)
                                  : inputRate.trim()
                                    ? Number(inputRate)
                                    : null,
                                output_per_million: outputRate.trim()
                                  ? Number(outputRate)
                                  : null,
                                currency: "USD",
                              },
                            }
                          : {}),
                      },
                    }
                  : resource === "hosts"
                    ? hostPayload(name, parsedConfig, item?.kind)
                    : { name, ...parsedConfig };
              return await api(
                item ? `/${resource}/${item.id}` : `/${resource}`,
                item ? "PATCH" : "POST",
                body,
              );
            } catch (error) {
              setFormError((error as Error).message);
              throw error;
            }
          });
          setBusy(false);
          if (r) {
            onSaved();
            onClose();
          }
        }}
      >
        <Field label={t("名称", "Name")}>
          <input
            required
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        {resource === "providers" && (
          <>
            <Field label="Provider">
              <select
                value={kind}
                onChange={(e) => {
                  setKind(e.target.value);
                  if (e.target.value === "codex_cli") {
                    setModel("gpt-6-luna");
                    setConfig(
                      JSON.stringify(
                        { reasoning_effort: "xhigh", timeout: 120 },
                        null,
                        2,
                      ),
                    );
                    setPaid(false);
                  }
                  setUrl(
                    e.target.value === "codex_cli"
                      ? "http://127.0.0.1"
                      : e.target.value === "ollama"
                        ? "http://127.0.0.1:11434"
                        : "https://api.openai.com/v1",
                  );
                }}
              >
                <option value="ollama">Ollama (local)</option>
                <option value="codex_cli">Codex CLI (local login)</option>
                <option value="openai">OpenAI-compatible API</option>
              </select>
            </Field>
            {kind === "codex_cli" && (
              <p>
                Uses the server account’s existing Codex CLI login. FOREST owns
                tool execution; the inference subprocess has shell, plugins and
                browser tools disabled. Subscription USD cost and a hard token
                ceiling are unavailable; use a reporting request cap.
              </p>
            )}
            <Field label="Base URL">
              <input
                required
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </Field>
            {kind !== "ollama" && kind !== "codex_cli" && (
              <>
                <Field label="API format">
                  <select
                    value={apiMode}
                    onChange={(e) => setApiMode(e.target.value)}
                  >
                    <option value="responses">OpenAI Responses</option>
                    <option value="chat_completions">
                      Chat Completions compatible
                    </option>
                  </select>
                </Field>
                <Field label="Shared API spending limit (USD)">
                  <input
                    type="number"
                    min="0"
                    step="any"
                    required={paid}
                    value={limitUsd}
                    onChange={(e) => setLimitUsd(e.target.value)}
                  />
                </Field>
                <div className="form-row">
                  <Field label="Input price / 1M tokens (USD)">
                    <input
                      type="number"
                      min="0"
                      step="any"
                      required={paid}
                      value={inputRate}
                      onChange={(e) => setInputRate(e.target.value)}
                    />
                  </Field>
                  <Field label="Output price / 1M tokens (USD)">
                    <input
                      type="number"
                      min="0"
                      step="any"
                      required={paid}
                      value={outputRate}
                      onChange={(e) => setOutputRate(e.target.value)}
                    />
                  </Field>
                </div>
                <Field label="Cached input price / 1M tokens (USD, optional)">
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={cachedRate}
                    onChange={(e) => setCachedRate(e.target.value)}
                  />
                </Field>
                <Field
                  label={t("图片模型 ID（可选）", "Image model ID (optional)")}
                >
                  <input
                    value={imageModel}
                    onChange={(e) => setImageModel(e.target.value)}
                  />
                </Field>
                {imageModel.trim() && (
                  <Field
                    label={t(
                      "每次图片请求费用上界（美元）",
                      "Per image request cost ceiling (USD)",
                    )}
                    hint={t(
                      "图片请求与文字请求共用总限额；无法核算费用时保留预留金额。",
                      "Image and text requests share the total limit; unknown image charges retain their reservation.",
                    )}
                  >
                    <input
                      required
                      type="number"
                      min="0.001"
                      step="any"
                      value={imageCeiling}
                      onChange={(e) => setImageCeiling(e.target.value)}
                    />
                  </Field>
                )}
              </>
            )}
            <Field label={t("实际模型 ID", "Model ID")}>
              <input
                required
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={t(
                  "由 Provider 提供的模型名称",
                  "Model ID provided by your provider",
                )}
              />
            </Field>
            <Field label="API key">
              <input
                type="password"
                autoComplete="new-password"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder={
                  item
                    ? t("留空保留现有密钥", "Leave blank to keep existing key")
                    : ""
                }
              />
            </Field>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={paid}
                onChange={(e) => setPaid(e.target.checked)}
              />
              {t("允许使用此已配置的付费 API", "Allow paid API calls")}
            </label>
          </>
        )}
        <Field label={t("配置", "Configuration")}>
          <textarea
            className="code-input"
            rows={resource === "providers" ? 3 : 12}
            value={config}
            onChange={(e) => setConfig(e.target.value)}
          />
        </Field>
        <div className="modal-actions">
          <Button type="button" onClick={onClose}>
            {t("取消", "Cancel")}
          </Button>
          <Button className="primary" type="submit" busy={busy}>
            <Save size={14} />
            {t("保存配置", "Save configuration")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function StorageCleanup({
  retentionDays,
  settingsLoaded,
}: {
  retentionDays: number | null;
  settingsLoaded: boolean;
}) {
  const { t, action } = useUI();
  const [days, setDays] = useState(String(retentionDays ?? ""));
  const [daysTouched, setDaysTouched] = useState(false);
  const [history, setHistory] = useState(false);
  const [projectId, setProjectId] = useState("");
  const [result, setResult] = useState<Json | null>(null);
  const { data: projects } = useLoad<Json[]>("/projects", []);
  useEffect(() => {
    if (!daysTouched) setDays(String(retentionDays ?? ""));
  }, [daysTouched, retentionDays]);
  const parsedDays = Number(days);
  const validDays = /^\d+$/.test(days) && Number.isSafeInteger(parsedDays);
  const canCleanUp =
    validDays && ((settingsLoaded && retentionDays !== null) || daysTouched);
  return (
    <details className="storage-cleanup">
      <summary>{t("存储与历史清理", "Storage & history cleanup")}</summary>
      <p className="muted">
        {t(
          "删除选定保留期之前的已完成运行与对应输出。引用这些材料的位置会显示需要更新。",
          "Delete completed runs and outputs older than the retention period. References to removed material will require an update.",
        )}
      </p>
      <div className="form-row">
        <Field label={t("项目范围", "Project scope")}>
          <select
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
          >
            <option value="">{t("全部项目", "All projects")}</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("保留最近天数", "Days to retain")}>
          <input
            type="number"
            min="0"
            value={days}
            onChange={(e) => {
              setDaysTouched(true);
              setDays(e.target.value);
            }}
          />
        </Field>
      </div>
      {!settingsLoaded && !daysTouched && (
        <p className="muted" role="status">
          {t(
            "设置中的保留天数尚未加载；请先输入并核对天数。",
            "Saved retention settings are unavailable. Enter and verify the number of days before cleanup.",
          )}
        </p>
      )}
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={history}
          onChange={(e) => setHistory(e.target.checked)}
        />
        {t(
          "同时清理所有项目的撤销历史",
          "Also remove undo history from all projects",
        )}
      </label>
      <Button
        disabled={!canCleanUp}
        onClick={() => {
          if (
            confirm(
              t(
                `删除 ${days} 天前的已完成运行输出${history ? "，并清空所有项目撤销历史" : ""}？`,
                `Delete completed run outputs older than ${days} days${history ? " and all project undo history" : ""}?`,
              ),
            )
          )
            void action(async () =>
              setResult(
                await api("/settings/cleanup", "POST", {
                  days: parsedDays,
                  clear_edit_history: history,
                  ...(projectId ? { project_id: projectId } : {}),
                }),
              ),
            );
        }}
      >
        <Trash2 size={14} />
        {t("执行清理", "Clean up")}
      </Button>
      {result && <JsonView value={result} />}
    </details>
  );
}
