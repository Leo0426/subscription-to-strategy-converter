const els = {
  form: document.querySelector("#convert-form"),
  previewButton: document.querySelector("#preview-button"),
  copyButton: document.querySelector("#copy-button"),
  copyUrlButton: document.querySelector("#copy-url-button"),
  downloadButton: document.querySelector("#download-button"),
  nodeFilter: document.querySelector("#node-filter"),
  addGroupButton: document.querySelector("#add-group-button"),
  status: document.querySelector("#status"),
  nodeCount: document.querySelector("#node-count"),
  nodesBody: document.querySelector("#nodes-body"),
  configOutput: document.querySelector("#config-output"),
  convertedUrl: document.querySelector("#converted-url"),
  groupsList: document.querySelector("#groups-list"),
  templateSelect: document.querySelector("#template"),
  targetSelect: document.querySelector("#target"),
  subscriptionUrl: document.querySelector("#subscription-url"),
  activePresetLabel: document.querySelector("#active-preset-label"),
  templateDescription: document.querySelector("#template-description"),
  powerfullzPanel: document.querySelector("#powerfullz-panel"),
  subconverterPanel: document.querySelector("#subconverter-panel"),
  subconverterConfig: document.querySelector("#subconverter-config"),
  subconverterConfigCustomField: document.querySelector("#subconverter-config-custom-field"),
  subconverterConfigSelect: document.querySelector("#subconverter-config-select"),
  previewSubconverterConfigButton: document.querySelector("#preview-subconverter-config"),
  subconverterConfigPreview: document.querySelector("#subconverter-config-preview"),
  subconverterConfigPreviewMeta: document.querySelector("#subconverter-config-preview-meta"),
  subconverterConfigPreviewContent: document.querySelector("#subconverter-config-preview-content"),
  closeSubconverterConfigPreviewButton: document.querySelector("#close-subconverter-config-preview"),
  copySubconverterConfigButton: document.querySelector("#copy-subconverter-config"),
  subconverterInclude: document.querySelector("#subconverter-include"),
  subconverterExclude: document.querySelector("#subconverter-exclude"),
  subconverterRename: document.querySelector("#subconverter-rename"),
  templateMeta: document.querySelector("#template-meta"),
  templateSummary: document.querySelector("#template-summary"),
  policyTable: document.querySelector("#policy-table"),
  policyLocalQuery: document.querySelector("#policy-local-query"),
  selectedPackageCount: document.querySelector("#selected-package-count"),
  configOutputTitle: document.querySelector("#config-output-title"),
  openCommunityBrowserButton: document.querySelector("#open-community-browser"),
  closeCommunityBrowserButton: document.querySelector("#close-community-browser"),
  communityBrowser: document.querySelector("#community-browser"),
  communitySearch: document.querySelector("#community-search"),
  communityFormatFilter: document.querySelector("#community-format-filter"),
  communityList: document.querySelector("#community-list"),
  communityPreview: document.querySelector("#community-preview"),
  enhanceAi: document.querySelector("#enhance-ai"),
  enhanceDev: document.querySelector("#enhance-dev"),
  enhanceStreaming: document.querySelector("#enhance-streaming"),
  enhanceAdblock: document.querySelector("#enhance-adblock"),
  enhanceTun: document.querySelector("#enhance-tun"),
  enhanceFakeip: document.querySelector("#enhance-fakeip"),
  statNodes: document.querySelector("#stat-nodes"),
  statGroups: document.querySelector("#stat-groups"),
  statProviders: document.querySelector("#stat-providers"),
  statRules: document.querySelector("#stat-rules"),
  graphContainer: document.querySelector("#policy-graph-container"),
  findingsContainer: document.querySelector("#findings-container"),
  simulateForm: document.querySelector("#simulate-form"),
  simulateDestination: document.querySelector("#simulate-destination"),
  simulateTrace: document.querySelector("#simulate-trace"),
};

const state = {
  templates: [],
  subconverterTargets: [],
  subconverterTemplates: [],
  communityTemplates: [],
  communityMeta: new Map(),
  customGroups: [],
  yamlView: "full",
  policyYaml: "",
  generatedYaml: "",
  compiledYaml: "",
  templateYaml: "",
  allNodes: [],
  workspace: null,
  graph: null,
  findings: [],
  lastPayload: null,
};

const CUSTOM_SUBCONVERTER_CONFIG = "__custom_subconverter_config__";

function isAdvancedMode() {
  return true;
}

function setStatus(message, type = "") {
  els.status.textContent = message;
  els.status.className = `status ${type}`.trim();
}

function setBusy(isBusy) {
  els.previewButton.disabled = isBusy;
  els.form.querySelector("button[type='submit']").disabled = isBusy;
  if (els.simulateForm) els.simulateForm.querySelector("button[type='submit']").disabled = isBusy;
  els.status.classList.toggle("loading", isBusy);
}

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(body.detail)
      ? body.detail.map((item) => item.msg || JSON.stringify(item)).join("; ")
      : body.detail || `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return body;
}

async function postText(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  if (!response.ok) {
    let detail = text || `HTTP ${response.status}`;
    try {
      const body = JSON.parse(text);
      detail = body.detail || detail;
    } catch (_error) {}
    throw new Error(detail);
  }
  return text;
}

function unique(items) {
  return [...new Set(items)];
}

function getPowerfullzOptions() {
  return {
    loadbalance: document.querySelector("#powerfullz-loadbalance").checked,
    landing: document.querySelector("#powerfullz-landing").checked,
    ipv6: document.querySelector("#powerfullz-ipv6").checked,
    full: document.querySelector("#powerfullz-full").checked,
    keepalive: document.querySelector("#powerfullz-keepalive").checked,
    fakeip: els.enhanceFakeip?.checked ?? document.querySelector("#powerfullz-fakeip").checked,
    quic: document.querySelector("#powerfullz-quic").checked,
    tun: els.enhanceTun?.checked ?? document.querySelector("#powerfullz-tun").checked,
  };
}

function getSubconverterOptions() {
  const selectedConfig = els.subconverterConfigSelect?.value || "";
  const config =
    selectedConfig === CUSTOM_SUBCONVERTER_CONFIG
      ? els.subconverterConfig?.value.trim()
      : selectedConfig;
  const options = {
    config: config || undefined,
    include: els.subconverterInclude?.value.trim() || undefined,
    exclude: els.subconverterExclude?.value.trim() || undefined,
    rename: els.subconverterRename?.value.trim() || undefined,
    emoji: document.querySelector("#subconverter-emoji")?.checked || undefined,
    udp: document.querySelector("#subconverter-udp")?.checked || undefined,
    tfo: document.querySelector("#subconverter-tfo")?.checked || undefined,
    sort: document.querySelector("#subconverter-sort")?.checked || undefined,
    append_type: document.querySelector("#subconverter-append-type")?.checked || undefined,
    scv: document.querySelector("#subconverter-scv")?.checked || undefined,
  };
  return Object.fromEntries(Object.entries(options).filter(([, value]) => value !== undefined && value !== ""));
}

function getCustomStrategy() {
  return {
    proxy_groups: state.customGroups
      .filter((group) => group.source === "custom")
      .map((group) => {
        const base = {
          name: group.name.trim(),
          type: group.type,
          url: group.url.trim() || undefined,
          interval: Number.parseInt(group.interval, 10) || undefined,
        };
        if (group.includeAll) {
          base["include-all"] = true;
          if (group.filter.trim()) base.filter = group.filter.trim();
          if (group.excludeFilter.trim()) base["exclude-filter"] = group.excludeFilter.trim();
        } else {
          base.proxies = group.members
            .split(/\r?\n/)
            .map((item) => item.trim())
            .filter(Boolean);
        }
        return base;
      })
      .filter((group) => group.name),
  };
}

function groupMembers(group) {
  return group.members
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function policyGroupFromEditorGroup(group) {
  const raw = group.raw && typeof group.raw === "object" ? { ...group.raw } : {};
  raw.name = group.name.trim();
  raw.type = group.type;

  if (group.type in {"url-test": true, "fallback": true, "load-balance": true}) {
    raw.url = group.url.trim() || "http://www.gstatic.com/generate_204";
    raw.interval = Number.parseInt(group.interval, 10) || 300;
  } else {
    delete raw.url;
    delete raw.interval;
  }

  if (group.includeAll) {
    raw["include-all"] = true;
    delete raw.proxies;
    if (group.filter.trim()) raw.filter = group.filter.trim();
    else delete raw.filter;
    if (group.excludeFilter.trim()) raw["exclude-filter"] = group.excludeFilter.trim();
    else delete raw["exclude-filter"];
    return raw;
  }

  delete raw["include-all"];
  if (group.filter.trim()) raw.filter = group.filter.trim();
  else delete raw.filter;
  if (group.excludeFilter.trim()) raw["exclude-filter"] = group.excludeFilter.trim();
  else delete raw["exclude-filter"];

  const members = groupMembers(group);
  if (members.length) {
    raw.proxies = members;
    delete raw.use;
  } else if (Array.isArray(group.raw?.use) && group.raw.use.length) {
    raw.use = [...group.raw.use];
    delete raw.proxies;
  } else {
    raw.proxies = ["__ALL_NODES__"];
  }
  return raw;
}

function getSelectedPolicy() {
  if (!isAdvancedMode()) return null;
  const proxyGroups = state.customGroups
    .filter((group) => group.source === "template")
    .map(policyGroupFromEditorGroup)
    .filter((group) => group.name);
  return proxyGroups.length ? { proxy_groups: proxyGroups } : null;
}

function getPayload() {
  const payload = {
    subscription_url: els.subscriptionUrl.value.trim(),
    template: els.templateSelect.value,
    target: els.targetSelect.value,
  };
  if (isAdvancedMode()) {
    const strategy = getCustomStrategy();
    if (strategy.proxy_groups.length) {
      payload.custom_strategy = strategy;
    }
    const selectedPolicy = getSelectedPolicy();
    if (selectedPolicy) {
      payload.selected_policy = selectedPolicy;
    }
    if (payload.template === "powerfullz") {
      payload.powerfullz = getPowerfullzOptions();
    }
  }
  const subconverter = getSubconverterOptions();
  if (Object.keys(subconverter).length) {
    payload.subconverter = subconverter;
  }
  return payload;
}

function targetActualName(target) {
  return target?.startsWith("subconverter:") ? target.replace(/^subconverter:/, "") : target;
}

async function loadSubconverterTargets() {
  try {
    const response = await fetch("/subconverter/targets");
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    state.subconverterTargets = body.targets || [];
  } catch (_error) {
    state.subconverterTargets = [
      { id: "surge", label: "Surge", kind: "app" },
      { id: "mihomo", label: "Mihomo", kind: "app" },
      { id: "clash", label: "Clash", kind: "app" },
      { id: "openclash", label: "OpenClash", kind: "app" },
      { id: "singbox", label: "sing-box", kind: "app" },
    ];
  }
  renderTargetOptions();
}

function renderTargetOptions() {
  const previous = els.targetSelect.value || "mihomo";
  const groups = [["app", "目标客户端"]];
  els.targetSelect.replaceChildren();
  for (const [kind, label] of groups) {
    const items = state.subconverterTargets.filter((item) => item.kind === kind);
    if (!items.length) continue;
    const optgroup = document.createElement("optgroup");
    optgroup.label = label;
    for (const item of items) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent =
        item.id === "mihomo"
          ? `${item.label}（推荐）`
          : ["surge", "singbox"].includes(item.id)
            ? `${item.label}（实验）`
            : item.label;
      optgroup.append(option);
    }
    els.targetSelect.append(optgroup);
  }
  const hasPrevious = [...els.targetSelect.options].some((option) => option.value === previous);
  els.targetSelect.value = hasPrevious ? previous : "mihomo";
  updateConfigOutputTitle();
  refreshSubscribeUrl();
}

function buildSubscribeUrl(payload) {
  const url = new URL("/subscribe", window.location.origin);
  url.searchParams.set("subscription_url", payload.subscription_url);
  url.searchParams.set("template", payload.template);
  url.searchParams.set("target", "mihomo");
  if (payload.template === "powerfullz") {
    url.searchParams.set("powerfullz", JSON.stringify(payload.powerfullz));
  }
  if (payload.subconverter) {
    url.searchParams.set("subconverter", JSON.stringify(payload.subconverter));
  }
  return url.toString();
}

async function createSessionUrl(payload) {
  const sessionData = {};
  if ((payload.custom_strategy?.proxy_groups?.length || 0) > 0) {
    sessionData.strategy = JSON.stringify(payload.custom_strategy);
  }
  if ((payload.selected_policy?.proxy_groups?.length || 0) > 0) {
    sessionData.policy = JSON.stringify(payload.selected_policy);
  }
  if (payload.template === "powerfullz") {
    sessionData.powerfullz = JSON.stringify(payload.powerfullz);
  }
  if (payload.subconverter) {
    sessionData.subconverter = JSON.stringify(payload.subconverter);
  }
  const resp = await fetch("/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sessionData),
  });
  const body = await resp.json();
  const url = new URL("/subscribe", window.location.origin);
  url.searchParams.set("subscription_url", payload.subscription_url);
  url.searchParams.set("template", payload.template);
  url.searchParams.set("target", "mihomo");
  url.searchParams.set("session_id", body.session_id);
  els.convertedUrl.value = url.toString();
}

let _sessionRefreshTimer = null;

function refreshSubscribeUrl() {
  clearTimeout(_sessionRefreshTimer);
  const payload = getPayload();
  if (!payload.subscription_url) {
    els.convertedUrl.value = "";
    return;
  }
  const needsSession =
    isAdvancedMode() &&
    ((payload.custom_strategy?.proxy_groups?.length || 0) > 0 ||
      (payload.selected_policy?.proxy_groups?.length || 0) > 0);
  if (!needsSession) {
    els.convertedUrl.value = buildSubscribeUrl(payload);
    return;
  }
  els.convertedUrl.value = "";
  _sessionRefreshTimer = setTimeout(() => {
    createSessionUrl(payload).catch(() => { els.convertedUrl.value = ""; });
  }, 400);
}

// ── YAML utilities (used by refreshLivePreview) ───────────────────────────

function yamlListItem(value, indent) {
  if (isScalar(value)) {
    return [`${" ".repeat(indent)}- ${quoteYaml(value)}`];
  }
  if (Array.isArray(value)) {
    if (!value.length) return [`${" ".repeat(indent)}- []`];
    const lines = [`${" ".repeat(indent)}-`];
    for (const item of value) lines.push(...yamlListItem(item, indent + 2));
    return lines;
  }
  const entries = Object.entries(value || {});
  if (!entries.length) return [`${" ".repeat(indent)}- {}`];
  const lines = [];
  entries.forEach(([key, val], index) => {
    const marker = index === 0 ? "-" : " ";
    if (isScalar(val)) {
      lines.push(`${" ".repeat(indent)}${marker} ${key}: ${quoteYaml(val)}`);
    } else {
      lines.push(`${" ".repeat(indent)}${marker} ${key}:`);
      lines.push(...yamlValue(val, indent + 4));
    }
  });
  return lines;
}

function yamlValue(value, indent) {
  if (Array.isArray(value)) {
    if (!value.length) return [`${" ".repeat(indent)}[]`];
    return value.flatMap((item) => yamlListItem(item, indent));
  }
  if (typeof value === "object" && value !== null) {
    const lines = [];
    for (const [key, val] of Object.entries(value)) {
      if (isScalar(val)) {
        lines.push(`${" ".repeat(indent)}${key}: ${quoteYaml(val)}`);
      } else {
        lines.push(`${" ".repeat(indent)}${key}:`);
        lines.push(...yamlValue(val, indent + 2));
      }
    }
    return lines.length ? lines : [`${" ".repeat(indent)}{}`];
  }
  return [`${" ".repeat(indent)}${quoteYaml(value)}`];
}

function isScalar(value) {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

function quoteYaml(value) {
  if (value === null) return "null";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  const text = String(value);
  if (!text) return "''";
  if (/^[A-Za-z0-9_./:@+-]+$/.test(text) && !["true", "false", "null"].includes(text.toLowerCase())) {
    return text;
  }
  return JSON.stringify(text);
}

// ── Template loading ──────────────────────────────────────────────────────

function templateDetailUrlFor(templateId) {
  const url = new URL("/templates/detail", window.location.origin);
  url.searchParams.set("template", templateId);
  if (templateId === "powerfullz") {
    url.searchParams.set("powerfullz", JSON.stringify(getPowerfullzOptions()));
  }
  return url.toString();
}

function templateDetailUrl() {
  return templateDetailUrlFor(els.templateSelect.value);
}

function renderTemplateSummary(summary) {
  els.templateSummary.replaceChildren();
  const items = [
    ["预设分组", summary.proxy_group_count],
    ["预设规则", summary.rule_count],
    ["规则集", summary.rule_provider_count],
    ["代理源", summary.proxy_provider_count],
    ["DNS", summary.has_dns ? "是" : "否"],
    ["TUN", summary.has_tun ? "是" : "否"],
  ];
  for (const [label, value] of items) {
    const item = document.createElement("div");
    item.className = "summary-item";
    item.innerHTML = `<span>${label}</span><strong>${value}</strong>`;
    els.templateSummary.append(item);
  }
  if (els.statGroups) els.statGroups.textContent = String(summary.proxy_group_count ?? "-");
  if (els.statProviders) els.statProviders.textContent = String(summary.rule_provider_count ?? "-");
  if (els.statRules) els.statRules.textContent = String(summary.rule_count ?? "-");
}

async function loadTemplateDetail() {
  if (!els.templateSelect.value) return;
  els.templateMeta.textContent = "加载中...";
  els.templateSummary.replaceChildren();
  try {
    const response = await fetch(templateDetailUrl());
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    const source = body.template.path ? `${body.template.source} / ${body.template.path}` : body.template.source;
    els.templateMeta.textContent = `${body.template.label} · ${source}`;
    state.templateYaml = body.yaml;
    if (state.yamlView === "template") updateYamlDisplay();
    renderTemplateSummary(body.summary);
  } catch (error) {
    els.templateMeta.textContent = error.message;
  }
}

function refreshTemplateOptions() {
  const option = els.templateSelect.options[els.templateSelect.selectedIndex];
  const desc = option?.dataset.description || "";
  els.templateDescription.textContent = desc;
  els.templateDescription.hidden = !isAdvancedMode() || !desc;
  if (els.activePresetLabel) {
    els.activePresetLabel.textContent = option?.textContent || els.templateSelect.value || "-";
  }
  els.powerfullzPanel.hidden = !isAdvancedMode() || els.templateSelect.value !== "powerfullz";
  loadTemplateDetail();
  updateSelectionSummary();
  refreshSubscribeUrl();
}

let _currentTemplateId = null;

async function loadTemplates() {
  try {
    const response = await fetch("/templates");
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);

    state.templates = body.templates || [];
    const primaryTemplates = state.templates.filter((template) => template.source === "preset" || template.id === "powerfullz");
    els.templateSelect.replaceChildren();
    for (const template of primaryTemplates) {
      const option = document.createElement("option");
      option.value = template.id;
      option.textContent = template.label;
      option.dataset.description = template.description || "";
      option.selected = false;
      els.templateSelect.append(option);
    }
    if (!els.templateSelect.value) {
      els.templateSelect.value = primaryTemplates.some((template) => template.id === "developer") ? "developer" : primaryTemplates[0]?.id || "minimal";
    }
    _currentTemplateId = els.templateSelect.value;
    renderPolicyTable();
    refreshTemplateOptions();
    loadTemplateGroupsForTemplate(els.templateSelect.value);
    applyEnhancementPreset();
  } catch (error) {
    setStatus(`模板列表加载失败：${error.message}`, "error");
  }
}

// ── Community meta ────────────────────────────────────────────────────────

async function loadCommunityMeta() {
  try {
    const response = await fetch("/community/templates");
    if (!response.ok) return;
    const items = await response.json();
    state.communityTemplates = items;
    state.subconverterTemplates = items.filter((item) => item.format === "conf");
    state.communityMeta = new Map(
      items.map((item) => [communityIdToLocalId(item.id), item])
    );
    renderSubconverterConfigOptions();
    renderPolicyTable();
  } catch (_e) {}
}

function renderSubconverterConfigOptions() {
  if (els.subconverterConfigSelect) {
    const previous = els.subconverterConfigSelect.value;
    els.subconverterConfigSelect.replaceChildren();
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "不额外指定配置文件";
    els.subconverterConfigSelect.append(empty);
    const custom = document.createElement("option");
    custom.value = CUSTOM_SUBCONVERTER_CONFIG;
    custom.textContent = "自定义远程配置 URL";
    els.subconverterConfigSelect.append(custom);
    for (const item of state.subconverterTemplates) {
      const option = document.createElement("option");
      option.value = item.config_value || item.id;
      option.textContent = `${item.name} · ${item.source_path.replace(/^community_templates\//, "")}`;
      els.subconverterConfigSelect.append(option);
    }
    if ([...els.subconverterConfigSelect.options].some((option) => option.value === previous)) {
      els.subconverterConfigSelect.value = previous;
    }
    syncSubconverterConfigField();
  }
}

function syncSubconverterConfigField() {
  const isCustom = els.subconverterConfigSelect?.value === CUSTOM_SUBCONVERTER_CONFIG;
  if (els.subconverterConfigCustomField) els.subconverterConfigCustomField.hidden = !isCustom;
  if (!isCustom && els.subconverterConfig) els.subconverterConfig.value = "";
}

function selectedSubconverterConfigMeta() {
  const value = els.subconverterConfigSelect?.value || "";
  if (!value || value === CUSTOM_SUBCONVERTER_CONFIG) return null;
  return state.subconverterTemplates.find((item) => (item.config_value || item.id) === value) || null;
}

function openSubconverterConfigPreview() {
  if (!els.subconverterConfigPreview) return;
  if (typeof els.subconverterConfigPreview.showModal === "function") {
    els.subconverterConfigPreview.showModal();
  } else {
    els.subconverterConfigPreview.setAttribute("open", "");
  }
}

function closeSubconverterConfigPreview() {
  if (!els.subconverterConfigPreview) return;
  if (typeof els.subconverterConfigPreview.close === "function") {
    els.subconverterConfigPreview.close();
  } else {
    els.subconverterConfigPreview.removeAttribute("open");
  }
}

async function previewSubconverterConfig() {
  if (!els.subconverterConfigPreviewContent || !els.subconverterConfigPreviewMeta) return;
  const selectedValue = els.subconverterConfigSelect?.value || "";
  const customUrl = els.subconverterConfig?.value.trim() || "";
  openSubconverterConfigPreview();

  if (!selectedValue) {
    els.subconverterConfigPreviewMeta.textContent = "当前未额外指定 Subconverter 配置文件";
    els.subconverterConfigPreviewContent.textContent = "使用 subconverter 默认配置生成转换链接。";
    return;
  }

  if (selectedValue === CUSTOM_SUBCONVERTER_CONFIG) {
    els.subconverterConfigPreviewMeta.textContent = customUrl ? "自定义远程配置 URL" : "自定义远程配置 URL 未填写";
    els.subconverterConfigPreviewContent.textContent = customUrl || "请先填写一个可公开访问的 profile.ini / pref.ini 地址。";
    return;
  }

  const meta = selectedSubconverterConfigMeta();
  els.subconverterConfigPreviewMeta.textContent = meta
    ? `${meta.name} · ${meta.source_path}`
    : selectedValue;
  els.subconverterConfigPreviewContent.textContent = "加载中...";

  try {
    const response = await fetch(`/community/templates/raw?id=${encodeURIComponent(selectedValue)}`);
    const text = await response.text();
    if (!response.ok) throw new Error(text || `HTTP ${response.status}`);
    els.subconverterConfigPreviewContent.textContent = text || "配置文件为空";
  } catch (error) {
    els.subconverterConfigPreviewContent.textContent = `加载失败：${error.message}`;
  }
}

function communityIdToLocalId(communityId) {
  return "local:community_templates/" + communityId.replace(/^community:/, "");
}

function groupFromTemplate(g) {
  return {
    id: crypto.randomUUID(),
    source: "template",
    raw: structuredClone(g),
    name: String(g.name || ""),
    type: g.type || "select",
    members: Array.isArray(g.proxies) ? g.proxies.join("\n") : "",
    url: String(g.url || "http://www.gstatic.com/generate_204"),
    interval: String(g.interval || 300),
    includeAll: !!g["include-all"],
    filter: String(g.filter || ""),
    excludeFilter: String(g["exclude-filter"] || ""),
  };
}

async function loadTemplateGroupsForTemplate(templateId) {
  if (!templateId) return;
  try {
    const response = await fetch(templateDetailUrlFor(templateId));
    if (!response.ok) return;
    const body = await response.json();
    if (Array.isArray(body.proxy_groups) && body.proxy_groups.length > 0) {
      state.customGroups = body.proxy_groups.map(groupFromTemplate);
      renderGroups();
    }
  } catch (_e) {}
}

// ── Template library ──────────────────────────────────────────────────────

function templateSourceGroup(template) {
  if (template.source === "built-in") return "内置模板";
  if (template.path) return template.path.split("/")[0] || "本地模板";
  return template.source || "";
}

function templateAuthorGroup(template) {
  if (!template.path) return template.source || "";
  const parts = template.path.split("/");
  return parts.length > 1 ? parts[1] : template.source || "";
}

function templateLabel(templateId) {
  return state.templates.find((item) => item.id === templateId)?.label || templateId;
}

function templateFormatBadge(item) {
  const cm = state.communityMeta.get(item.id);
  if (!cm) return "";
  const fmt = cm.format || "yaml";
  const label = fmt === "conf" ? "Subconverter" : fmt === "openclash" ? "OpenClash" : "YAML";
  return `<span class="format-pill fmt-${escapeAttr(fmt)}">${escapeHtml(label)}</span>`;
}

function templateSurgeWarningBadge(item) {
  if (targetActualName(els.targetSelect.value) !== "surge") return "";
  const cm = state.communityMeta.get(item.id);
  if (!cm || cm.surge_compatible !== false) return "";
  return `<span class="surge-warning-badge" title="此模板含 MRS 格式规则集，Surge 模式下可能不兼容">Surge ⚠</span>`;
}

function passesPolicyFilters(item) {
  const query = (els.policyLocalQuery?.value || "").trim().toLowerCase();
  if (!query) return true;
  return JSON.stringify(item).toLowerCase().includes(query);
}

function visiblePolicyItems() {
  return [...state.templates]
    .filter(passesPolicyFilters)
    .sort((a, b) =>
      templateSourceGroup(a).localeCompare(templateSourceGroup(b), "zh-Hans-CN") ||
      templateAuthorGroup(a).localeCompare(templateAuthorGroup(b), "zh-Hans-CN") ||
      String(a.label || a.id).localeCompare(String(b.label || b.id), "zh-Hans-CN")
    )
    .slice(0, 300);
}

function renderPolicyTable() {
  if (!state.templates.length) {
    els.policyTable.innerHTML = `<div class="empty">模板文件加载中</div>`;
    return;
  }
  const items = visiblePolicyItems();
  if (!items.length) {
    els.policyTable.innerHTML = `<div class="empty">没有匹配项</div>`;
    return;
  }
  renderTemplateLibrary(items);
  updateSelectionSummary();
}

function renderTemplateLibrary(items) {
  els.policyTable.innerHTML = `<table><thead><tr><th></th><th>模板文件</th><th>来源</th><th>说明</th></tr></thead><tbody>${items
    .map(
      (item) => `<tr data-kind="template" data-id="${escapeAttr(item.id)}" class="${item.id === els.templateSelect.value ? "selected-row" : ""}">
        <td><input type="radio" name="template-file" data-id="${escapeAttr(item.id)}" ${item.id === els.templateSelect.value ? "checked" : ""} /></td>
        <td>
          <strong>${escapeHtml(item.label)}</strong>
          <div class="path">${escapeHtml(item.path || item.id)}</div>
          <div class="template-badges">
            ${templateFormatBadge(item)}${templateSurgeWarningBadge(item)}
            ${item.proxy_group_count > 0 ? `<span class="count-pill">${item.proxy_group_count} 组</span>` : ""}
          </div>
        </td>
        <td><span class="pill">${escapeHtml(templateSourceGroup(item) || "-")}</span> <span class="muted">${escapeHtml(templateAuthorGroup(item) || "")}</span></td>
        <td class="muted">${escapeHtml(item.description || "社区模板文件")}</td>
      </tr>`
    )
    .join("")}</tbody></table>`;
  wireTemplateRows();
}

function wireTemplateRows() {
  els.policyTable.querySelectorAll("tr[data-kind='template']").forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.target.matches("button")) return;
      selectTemplateFile(row.dataset.id);
    });
  });
  els.policyTable.querySelectorAll("input[name='template-file']").forEach((input) => {
    input.addEventListener("change", () => selectTemplateFile(input.dataset.id));
  });
}

function selectTemplateFile(templateId) {
  if (!templateId || els.templateSelect.value === templateId) return;
  els.templateSelect.value = templateId;
  refreshTemplateOptions();
  renderPolicyTable();
  switchOutputPane("groups");
  setStatus(`已选择模板：${templateLabel(templateId)}`, "ok");
  _currentTemplateId = templateId;
  loadTemplateGroupsForTemplate(templateId);
}

function updateSelectionSummary() {
  if (!els.selectedPackageCount) return;
  const currentTemplate = state.templates.find((item) => item.id === els.templateSelect.value);
  els.selectedPackageCount.textContent = currentTemplate ? currentTemplate.label : els.templateSelect.value || "—";
}

function applyEnhancementPreset() {
  if (!els.templateSelect?.options.length) return;
  const ai = !!els.enhanceAi?.checked;
  const dev = !!els.enhanceDev?.checked;
  const streaming = !!els.enhanceStreaming?.checked;
  let template = "minimal";
  const enabledCount = [ai, dev, streaming].filter(Boolean).length;
  if (enabledCount > 1) template = "full";
  else if (ai) template = "ai-tools";
  else if (dev) template = "developer";
  else if (streaming) template = "streaming";
  if ([...els.templateSelect.options].some((option) => option.value === template)) {
    els.templateSelect.value = template;
    refreshTemplateOptions();
    renderPolicyTable();
    loadTemplateGroupsForTemplate(template);
  }
}

function scrollToCurrentTemplate() {
  els.policyLocalQuery.value = "";
  renderPolicyTable();
  const current = els.policyTable.querySelector(".selected-row");
  if (current) current.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ── Group editor ──────────────────────────────────────────────────────────

function renderGroups() {
  els.groupsList.replaceChildren();
  if (!state.customGroups.length) {
    const empty = document.createElement("div");
    empty.className = "empty-strategy";
    empty.textContent = "暂无策略组，选择模板后自动加载";
    els.groupsList.append(empty);
    refreshSubscribeUrl();
    refreshLivePreview();
    return;
  }

  for (const group of state.customGroups) {
    const card = document.createElement("article");
    card.className = "group-card";
    card.dataset.groupId = group.id;
    card.innerHTML = `
      <label class="field"><span>名称</span><input data-key="name" value="${escapeAttr(group.name)}" placeholder="策略组" /></label>
      <label class="field"><span>类型</span><select data-key="type">
        ${["select", "url-test", "fallback", "load-balance"]
          .map((type) => `<option value="${type}" ${group.type === type ? "selected" : ""}>${type}</option>`)
          .join("")}
      </select></label>
      <label class="field"><span>测试 URL</span><input data-key="url" value="${escapeAttr(group.url)}" /></label>
      <label class="field"><span>间隔秒</span><input data-key="interval" type="number" min="30" max="86400" value="${escapeAttr(group.interval)}" /></label>
      <label class="field"><span>筛选 filter</span><input data-key="filter" value="${escapeAttr(group.filter || "")}" placeholder="香港|日本|美国" /></label>
      <label class="field"><span>排除 exclude-filter</span><input data-key="excludeFilter" value="${escapeAttr(group.excludeFilter || "")}" placeholder="官网|流量|套餐|剩余" /></label>
      <div class="field members-field">
        <div class="members-label-row">
          <span>成员</span>
          <span class="include-all-toggle"><input type="checkbox" data-key="includeAll" ${group.includeAll ? "checked" : ""} /> 包含所有节点</span>
        </div>
        <textarea data-key="members" placeholder="留空 = 全部节点" ${group.includeAll ? "disabled" : ""}>${escapeHtml(group.members)}</textarea>
      </div>
      <button type="button" class="danger-button" data-action="remove">删除</button>
    `;
    card.querySelectorAll("[data-key]").forEach((input) => {
      const getValue = () => (input.type === "checkbox" ? input.checked : input.value);
      input.addEventListener("input", () => updateGroup(group.id, { [input.dataset.key]: getValue() }));
      input.addEventListener("change", () => {
        const val = getValue();
        updateGroup(group.id, { [input.dataset.key]: val });
        if (input.dataset.key === "includeAll") {
          const ta = card.querySelector("textarea[data-key='members']");
          if (ta) ta.disabled = val;
        }
      });
    });
    card.querySelector("[data-action='remove']").addEventListener("click", () => removeGroup(group.id));
    els.groupsList.append(card);
  }
  refreshSubscribeUrl();
  refreshLivePreview();
}

function addGroup() {
  const groupNumber = state.customGroups.length + 1;
  state.customGroups = [
    ...state.customGroups,
    {
      id: crypto.randomUUID(),
      source: "custom",
      raw: {},
      name: `Custom-${groupNumber}`,
      type: "select",
      members: "",
      url: "http://www.gstatic.com/generate_204",
      interval: "300",
      includeAll: false,
      filter: "",
      excludeFilter: "",
    },
  ];
  renderGroups();
}

function updateGroup(id, patch) {
  state.customGroups = state.customGroups.map((group) => (group.id === id ? { ...group, ...patch } : group));
  refreshSubscribeUrl();
  refreshLivePreview();
}

function removeGroup(id) {
  state.customGroups = state.customGroups.filter((group) => group.id !== id);
  renderGroups();
}

// ── Config output ─────────────────────────────────────────────────────────

function updateConfigOutputTitle() {
  if (!els.configOutputTitle) return;
  const target = els.targetSelect.value;
  const actualTarget = targetActualName(target);
  if (target?.startsWith("subconverter:")) els.configOutputTitle.textContent = `${actualTarget} 订阅预览`;
  else els.configOutputTitle.textContent = "Mihomo YAML 预览";
}

function applyConversionMode() {
  document.body.classList.remove("quick-mode");
  refreshTemplateOptions();
  if (document.querySelector("#output-pane-config")?.hidden === false && state.yamlView === "full") {
    updateYamlDisplay();
  }
  const descEl = document.querySelector("#subconverter-panel-desc");
  if (descEl) {
    descEl.textContent = "按目标客户端生成策略组、规则和节点清洗参数。";
  }
  renderTargetOptions();
  updateConfigOutputTitle();
  refreshSubscribeUrl();
}

function switchYamlView(view) {
  if (!isAdvancedMode() && view !== "full") {
    view = "full";
  }
  state.yamlView = view;
  document.querySelectorAll(".yaml-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.yamlTab === view);
  });
  updateYamlDisplay();
}

function switchOutputPane(name) {
  if (!isAdvancedMode() && ["groups", "graph"].includes(name)) {
    name = "config";
  }
  document.querySelectorAll(".output-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.outputTab === name);
  });
  document.querySelectorAll(".output-pane").forEach((pane) => {
    pane.hidden = pane.id !== `output-pane-${name}`;
  });
  if (name === "graph") renderWorkspaceGraph();
  if (name === "analysis") renderWorkspaceFindings();
}

function updateYamlDisplay() {
  if (!isAdvancedMode() && state.yamlView !== "full") {
    state.yamlView = "full";
  }
  if (state.yamlView === "policy") {
    els.configOutput.value = state.policyYaml;
  } else if (state.yamlView === "full") {
    els.configOutput.value = state.generatedYaml || '# 还未生成配置：粘贴订阅地址后点击「生成配置」\n';
  } else {
    els.configOutput.value = state.templateYaml || "# 等待模板加载\n";
  }
}

function refreshLivePreview() {
  if (!isAdvancedMode()) {
    state.policyYaml = "";
    if (state.yamlView === "policy") switchYamlView("full");
    return;
  }
  const customGroups = getCustomStrategy().proxy_groups;
  const lines = [
    "# 策略预览：以下策略组会注入到模板中",
    "# 点击「生成配置」后会用机场节点填充完整配置",
    "",
    "proxy-groups:",
  ];
  if (customGroups.length) {
    for (const group of customGroups) {
      lines.push(...yamlListItem(group, 2));
    }
  } else {
    lines.push("  []");
  }
  state.policyYaml = `${lines.join("\n")}\n`;
  updateYamlDisplay();
}

function outputFilename() {
  const target = els.targetSelect.value;
  if (state.yamlView === "full") {
    if (target?.startsWith("subconverter:")) {
      const actual = targetActualName(target);
      if (["clash", "clashr"].includes(actual)) return `${actual}.yaml`;
      return `${actual}.conf`;
    }
    if (target === "surge") return "surge.conf";
    if (target === "singbox") return "singbox.json";
    return "mihomo.yaml";
  }
  if (state.yamlView === "template") return "template.yaml";
  return "policy-preview.yaml";
}

function downloadYaml() {
  const content = els.configOutput.value;
  const isEmpty = !content.trim() || content.startsWith("# 还未") || content.startsWith("# 等待");
  if (isEmpty) {
    setStatus("没有可下载的内容", "");
    return;
  }
  const name = outputFilename();
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
  setStatus(`已下载 ${name}`, "ok");
}

// ── Nodes ─────────────────────────────────────────────────────────────────

function renderNodes(nodes = []) {
  state.allNodes = nodes;
  if (els.statNodes) els.statNodes.textContent = String(nodes.length);
  if (els.nodeFilter) els.nodeFilter.value = "";
  renderFilteredNodes();
}

function renderFilteredNodes() {
  const query = (els.nodeFilter?.value || "").trim().toLowerCase();
  const filtered = query
    ? state.allNodes.filter(
        (n) =>
          (n.name || "").toLowerCase().includes(query) ||
          (n.type || n.protocol || "").toLowerCase().includes(query) ||
          (n.server || "").toLowerCase().includes(query)
      )
    : state.allNodes;
  els.nodeCount.textContent = query
    ? `${filtered.length} / ${state.allNodes.length}`
    : String(state.allNodes.length);
  els.nodesBody.replaceChildren();
  if (!filtered.length) {
    const row = document.createElement("tr");
    row.innerHTML = `<td colspan="4" class="empty">${query ? "无匹配节点" : "暂无节点"}</td>`;
    els.nodesBody.append(row);
    return;
  }
  for (const node of filtered) {
    const row = document.createElement("tr");
    for (const key of ["name", "type", "server", "port"]) {
      const cell = document.createElement("td");
      cell.textContent = key === "type" ? (node.type || node.protocol || "") : (node[key] ?? "");
      row.append(cell);
    }
    els.nodesBody.append(row);
  }
}

function workspaceNodes(workspace) {
  return Array.isArray(workspace?.proxies) ? workspace.proxies : [];
}

function renderWorkspaceStats(workspace, nodeCount) {
  if (els.statNodes) els.statNodes.textContent = String(nodeCount ?? workspaceNodes(workspace).length ?? "-");
  if (els.statGroups) els.statGroups.textContent = String(workspace?.proxy_groups?.length ?? 0);
  if (els.statProviders) els.statProviders.textContent = String(workspace?.rule_providers?.length ?? 0);
  if (els.statRules) els.statRules.textContent = String(workspace?.rules?.length ?? 0);
}

function renderWorkspaceGraph() {
  const container = els.graphContainer;
  if (!container) return;
  const raw = state.graph;
  if (!raw?.nodes?.length) {
    container.innerHTML = '<div class="empty">生成配置后查看策略依赖关系</div>';
    return;
  }
  const agg = buildAggregatedGraph(raw);
  const layout = layoutGraphCols(agg.cols);
  renderGraphSVG(container, agg, layout);
}

function renderWorkspaceFindings() {
  if (!els.findingsContainer) return;
  renderFindings(els.findingsContainer, state.findings);
}

function renderWorkspacePreview(body) {
  state.workspace = body.workspace || null;
  state.graph = body.graph || null;
  state.findings = Array.isArray(body.findings) ? body.findings : [];
  renderNodes(workspaceNodes(state.workspace));
  renderWorkspaceStats(state.workspace, body.node_count);
  renderWorkspaceFindings();
  renderWorkspaceGraph();
}

async function compileWorkspaceYaml() {
  if (!state.workspace) return;
  try {
    const yaml = await postText("/compile/mihomo", { workspace: state.workspace });
    state.compiledYaml = yaml;
    state.generatedYaml = yaml;
    updateConfigOutputTitle();
    switchYamlView("full");
  } catch (error) {
    state.compiledYaml = "";
    state.generatedYaml = `# Mihomo 编译失败\n# ${error.message}\n`;
    switchYamlView("full");
    throw error;
  }
}

// ── Preview / Convert ─────────────────────────────────────────────────────

async function previewNodes() {
  const payload = getPayload();
  if (!payload.subscription_url) {
    els.form.reportValidity();
    return;
  }
  setBusy(true);
  setStatus("预览中...");
  refreshSubscribeUrl();
  try {
    const body = await postJson("/preview", payload);
    renderNodes(body.nodes);
    setStatus(`已加载 ${body.node_count} 个节点`, "ok");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function convertConfig(event) {
  event.preventDefault();
  const payload = getPayload();
  if (!payload.subscription_url) {
    els.form.reportValidity();
    return;
  }
  setBusy(true);
  setStatus("生成配置中...");
  refreshSubscribeUrl();
  try {
    state.lastPayload = payload;
    const body = await postJson("/workspace/preview", payload);
    renderWorkspacePreview(body);
    switchOutputPane("config");
    try {
      await compileWorkspaceYaml();
      const errors = state.findings.filter((finding) => finding.severity === "error").length;
      setStatus(
        errors
          ? `配置已生成 · ${body.node_count} 个节点 · ${errors} 个错误`
          : `配置已生成 · ${body.node_count} 个节点 · 订阅链接可复制`,
        errors ? "error" : "ok"
      );
      flashSubscribeBox();
    } catch (compileError) {
      setStatus(`配置已生成，但 Mihomo 编译失败：${compileError.message}`, "error");
    }
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function copyText(text, emptyMessage, okMessage, fallbackElement, button) {
  if (!text) {
    setStatus(emptyMessage);
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    setStatus(okMessage, "ok");
    if (button) flashButton(button, "已复制 ✓");
  } catch {
    if (fallbackElement && typeof fallbackElement.select === "function") fallbackElement.select();
    setStatus("已选中，请手动复制");
  }
}

function flashButton(button, text) {
  if (button.dataset.flashing) return;
  button.dataset.flashing = "1";
  const original = button.textContent;
  button.textContent = text;
  setTimeout(() => {
    button.textContent = original;
    delete button.dataset.flashing;
  }, 1500);
}

function flashSubscribeBox() {
  const box = document.querySelector("#subscribe-box");
  if (!box || !els.convertedUrl.value) return;
  box.classList.remove("flash");
  void box.offsetWidth;
  box.classList.add("flash");
}

// ── Community browser ─────────────────────────────────────────────────────

function filteredCommunityTemplates() {
  const query = (els.communitySearch?.value || "").trim().toLowerCase();
  const fmt = els.communityFormatFilter?.value || "";
  return state.communityTemplates.filter((item) => {
    if (fmt && item.format !== fmt) return false;
    if (!query) return true;
    return (item.name + item.source_path).toLowerCase().includes(query);
  });
}

function communityFormatLabel(fmt) {
  if (fmt === "conf") return "Subconverter";
  if (fmt === "openclash") return "OpenClash";
  return "YAML";
}

function openCommunityBrowser() {
  renderCommunityList();
  els.communityBrowser?.showModal();
}

function closeCommunityBrowser() {
  els.communityBrowser?.close();
}

function renderCommunityList() {
  if (!els.communityList) return;
  const items = filteredCommunityTemplates();
  if (!items.length) {
    els.communityList.innerHTML = `<div class="empty">${state.communityTemplates.length ? "没有匹配项" : "社区模板目录为空"}</div>`;
    return;
  }
  els.communityList.replaceChildren();
  const isSurge = targetActualName(els.targetSelect.value) === "surge";
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "cb-list-item";
    row.dataset.id = item.id;
    const surgeNote = isSurge && item.surge_compatible === false ? `<span class="surge-warning-badge">Surge ⚠</span>` : "";
    row.innerHTML = `
      <div class="cb-list-item-main">
        <strong>${escapeHtml(item.name)}</strong>
        <span class="format-pill fmt-${escapeAttr(item.format)}">${escapeHtml(communityFormatLabel(item.format))}</span>
        ${surgeNote}
      </div>
      <div class="cb-list-item-path path">${escapeHtml(item.source_path)}</div>
      <div class="cb-list-item-meta">
        ${item.proxy_group_count ? `<span>${item.proxy_group_count} 策略组</span>` : ""}
        ${item.rule_count ? `<span>${item.rule_count} 规则</span>` : ""}
      </div>
    `;
    row.addEventListener("click", () => {
      els.communityList.querySelectorAll(".cb-list-item").forEach((r) => r.classList.remove("active"));
      row.classList.add("active");
      renderCommunityPreview(item);
    });
    els.communityList.append(row);
  }
}

async function renderCommunityPreview(item) {
  if (!els.communityPreview) return;
  const isSurge = targetActualName(els.targetSelect.value) === "surge";
  const localId = communityIdToLocalId(item.id);
  const canApply = item.format === "yaml" && state.templates.some((t) => t.id === localId);

  let compatNote = "";
  if (item.format === "conf") {
    compatNote = `<div class="cb-compat-note">Subconverter INI 格式，可作为上方“Subconverter 模板”使用。</div>`;
  } else if (item.format === "openclash") {
    compatNote = `<div class="cb-compat-note">OpenClash 覆写片段，无法直接作为当前 YAML 预设使用。</div>`;
  } else if (isSurge && item.surge_compatible === false) {
    compatNote = `<div class="cb-compat-note cb-compat-warn">该模板含 MRS 格式规则集，Surge 模式下部分规则可能无法直接使用。可以应用并手动替换规则集 URL。</div>`;
  }

  els.communityPreview.innerHTML = `
    <div class="cb-preview-head">
      <div>
        <h3>${escapeHtml(item.name)}</h3>
        <div class="path">${escapeHtml(item.source_path)}</div>
      </div>
      <div class="cb-preview-badges">
        <span class="format-pill fmt-${escapeAttr(item.format)}">${escapeHtml(communityFormatLabel(item.format))}</span>
        ${item.proxy_group_count ? `<span class="count-pill">${item.proxy_group_count} 策略组</span>` : ""}
        ${item.rule_count ? `<span class="count-pill">${item.rule_count} 规则</span>` : ""}
      </div>
    </div>
    ${compatNote}
    <div id="cb-groups-area" class="cb-groups-area"><div class="empty">加载策略组中…</div></div>
    <div class="cb-actions">
      <button type="button" id="cb-apply-button" ${canApply || item.format === "conf" ? "" : "disabled"} title="${canApply || item.format === "conf" ? "" : "只有 YAML 模板或 Subconverter INI 可以应用"}">${item.format === "conf" ? "用作 Subconverter 配置" : "应用到工作区"}</button>
      <button type="button" id="cb-cancel-button">取消</button>
    </div>
  `;

  document.querySelector("#cb-cancel-button")?.addEventListener("click", closeCommunityBrowser);
  document.querySelector("#cb-apply-button")?.addEventListener("click", () => applyCommunityTemplate(item, localId));

  if (item.format !== "yaml") return;

  try {
    const url = `/community/templates/preview?id=${encodeURIComponent(item.id)}`;
    const response = await fetch(url);
    const body = await response.json();
    const area = document.querySelector("#cb-groups-area");
    if (!area) return;
    if (!response.ok || !Array.isArray(body.proxy_groups) || !body.proxy_groups.length) {
      area.innerHTML = `<div class="empty">无策略组数据</div>`;
      return;
    }
    area.replaceChildren();
    for (const g of body.proxy_groups) {
      const row = document.createElement("div");
      row.className = "cb-group-row";
      row.innerHTML = `
        <span class="pill">${escapeHtml(g.type || "select")}</span>
        <strong>${escapeHtml(g.name)}</strong>
        <span class="muted">${g.members.length} 成员</span>
      `;
      area.append(row);
    }
  } catch (_e) {
    const area = document.querySelector("#cb-groups-area");
    if (area) area.innerHTML = `<div class="empty">策略组加载失败</div>`;
  }
}

function applyCommunityTemplate(item, localId) {
  closeCommunityBrowser();
  if (item.format === "conf") {
    if (els.subconverterConfigSelect) els.subconverterConfigSelect.value = item.config_value || item.id;
    syncSubconverterConfigField();
    refreshSubscribeUrl();
    setStatus(`已设置 Subconverter 模板：${item.name}`, "ok");
    return;
  }
  selectTemplateFile(localId);
  setStatus(`已应用社区模板：${item.name}`, "ok");
}

function bindCommunityBrowserEvents() {
  els.openCommunityBrowserButton?.addEventListener("click", openCommunityBrowser);
  els.closeCommunityBrowserButton?.addEventListener("click", closeCommunityBrowser);
  els.communityBrowser?.addEventListener("click", (e) => {
    if (e.target === els.communityBrowser) closeCommunityBrowser();
  });
  els.communitySearch?.addEventListener("input", renderCommunityList);
  els.communityFormatFilter?.addEventListener("change", renderCommunityList);
}

// ── Escape helpers ────────────────────────────────────────────────────────

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  })[char]);
}

function escapeAttr(value) {
  return escapeHtml(value);
}

// ── Policy Graph ─────────────────────────────────────────────────────────

const G = { NW: 160, NH: 44, CGAP: 180, RGAP: 14, PX: 40, PY: 52 };

function buildAggregatedGraph(rawGraph) {
  const groups = rawGraph.nodes.filter((n) => n.type === "group");
  const providers = rawGraph.nodes.filter((n) => n.type === "provider");
  const builtins = rawGraph.nodes.filter((n) => n.type === "builtin");

  const gids = new Set(groups.map((n) => n.id));
  const pids = new Set(providers.map((n) => n.id));
  const bids = new Set(builtins.map((n) => n.id));

  const r2p = new Map();
  const r2t = new Map();
  const usedBids = new Set();

  for (const e of rawGraph.edges) {
    if (e.type === "rule-provider" && pids.has(e.target)) r2p.set(e.source, e.target);
    if (e.type === "rule-target" && (gids.has(e.target) || bids.has(e.target))) {
      r2t.set(e.source, e.target);
      if (bids.has(e.target)) usedBids.add(e.target);
    }
    if (e.type === "group-member" && bids.has(e.target)) usedBids.add(e.target);
  }

  const tc = new Map();
  for (const t of r2t.values()) tc.set(t, (tc.get(t) || 0) + 1);

  const ptc = new Map();
  for (const [rid, pid] of r2p) {
    const tid = r2t.get(rid);
    if (!tid) continue;
    const k = `${pid}\x00${tid}`;
    ptc.set(k, (ptc.get(k) || 0) + 1);
  }

  const derivedEdges = [...ptc].map(([k, count]) => {
    const i = k.indexOf("\x00");
    return { id: `pt:${k}`, source: k.slice(0, i), target: k.slice(i + 1), type: "provider-target", count };
  });

  const directEdges = rawGraph.edges.filter(
    (e) => e.type === "group-member" && gids.has(e.source) && bids.has(e.target)
  );

  const enrichedGroups = groups
    .map((n) => ({ ...n, ruleCount: tc.get(n.id) || 0 }))
    .sort((a, b) => b.ruleCount - a.ruleCount);

  const enrichedBuiltins = builtins
    .filter((n) => usedBids.has(n.id))
    .map((n) => ({ ...n, ruleCount: tc.get(n.id) || 0 }));

  return {
    nodes: [...providers, ...enrichedGroups, ...enrichedBuiltins],
    cols: { provider: providers, group: enrichedGroups, builtin: enrichedBuiltins },
    edges: [...directEdges, ...derivedEdges],
  };
}

function layoutGraphCols(cols) {
  const { NW, NH, CGAP, RGAP, PX, PY } = G;
  const order = ["provider", "group", "builtin"].filter((t) => cols[t].length);
  const pos = new Map();
  const cx = {};

  order.forEach((type, i) => {
    const x = PX + i * (NW + CGAP);
    cx[type] = x;
    cols[type].forEach((n, j) => pos.set(n.id, { x, y: PY + j * (NH + RGAP) }));
  });

  const maxRows = Math.max(...order.map((t) => cols[t].length), 1);
  return {
    pos,
    cx,
    order,
    W: order.length ? PX + order.length * (NW + CGAP) - CGAP + PX : 300,
    H: PY + maxRows * (NH + RGAP) + 24,
  };
}

const SVG_NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs = {}) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  return e;
}
function svgTxt(s, attrs) {
  const e = svgEl("text", attrs);
  e.textContent = s;
  return e;
}
function trunc(s, n) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

const GCOL = {
  group:    { bg: "#e9efff", stroke: "#5b8af6", text: "#1a3a8f" },
  provider: { bg: "#fff4e6", stroke: "#e8933a", text: "#7c3b00" },
  builtin:  { bg: "#eafaf1", stroke: "#4caf7d", text: "#1a5c35" },
};

function drawGNode(node, x, y) {
  const { NW, NH } = G;
  const c = GCOL[node.type] || GCOL.group;
  const g = svgEl("g", { transform: `translate(${x},${y})`, class: `gnode type-${node.type}` });

  g.append(svgEl("rect", { width: NW, height: NH, rx: 6, fill: c.bg, stroke: c.stroke, "stroke-width": 1.5 }));

  const hasSub = node.type !== "builtin";
  g.append(svgTxt(trunc(node.label, 18), {
    x: NW / 2, y: hasSub ? 16 : 26,
    "text-anchor": "middle", "font-size": 13, "font-weight": 600, fill: c.text,
  }));

  if (hasSub) {
    let sub = "";
    if (node.type === "group") {
      sub = node.meta?.group_type || "";
      if (node.ruleCount) sub += (sub ? " \xb7 " : "") + `${node.ruleCount} 条规则`;
    } else if (node.type === "provider") {
      sub = [node.meta?.behavior, node.meta?.format !== "yaml" && node.meta?.format]
        .filter(Boolean).join(" \xb7 ");
    }
    if (sub) {
      g.append(svgTxt(trunc(sub, 26), {
        x: NW / 2, y: 32,
        "text-anchor": "middle", "font-size": 10, fill: c.text, opacity: 0.65,
      }));
    }
  }

  return g;
}

function drawGEdge(svg, x1, y1, x2, y2, color, markerUrl, label) {
  const cp = x1 + (x2 - x1) * 0.55;
  svg.append(svgEl("path", {
    d: `M${x1} ${y1} C${cp} ${y1},${cp} ${y2},${x2} ${y2}`,
    stroke: color, "stroke-width": 1.5, fill: "none", "marker-end": markerUrl, opacity: 0.75,
  }));
  if (label) {
    svg.append(svgTxt(label, {
      x: (x1 + x2) / 2, y: Math.min(y1, y2) - 4,
      "text-anchor": "middle", "font-size": 10, fill: color, opacity: 0.9,
    }));
  }
}

function renderGraphSVG(container, agg, layout) {
  const { NW, NH, PX } = G;
  const { pos, cx, order, W, H } = layout;

  const svg = svgEl("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "policy-graph-svg" });

  const defs = svgEl("defs");
  for (const [id, color] of [["gam", "#4caf7d"], ["gap", "#e8933a"]]) {
    const m = svgEl("marker", { id, markerWidth: 8, markerHeight: 6, refX: 8, refY: 3, orient: "auto" });
    m.append(svgEl("polygon", { points: "0 0,8 3,0 6", fill: color }));
    defs.append(m);
  }
  svg.append(defs);

  const COL_LABELS = { provider: "规则集", group: "策略组", builtin: "目标" };
  for (const type of order) {
    const x = cx[type];
    const c = GCOL[type];
    svg.append(svgEl("rect", { x, y: 5, width: NW, height: 22, rx: 4, fill: c.bg, stroke: c.stroke, "stroke-width": 1 }));
    svg.append(svgTxt(COL_LABELS[type], {
      x: x + NW / 2, y: 20,
      "text-anchor": "middle", "font-size": 12, "font-weight": 600, fill: c.text,
    }));
  }

  for (const e of agg.edges) {
    const sp = pos.get(e.source);
    const tp = pos.get(e.target);
    if (!sp || !tp) continue;

    const sx = sp.x + NW, sy = sp.y + NH / 2;
    const tx = tp.x, ty = tp.y + NH / 2;

    if (e.type === "group-member") {
      drawGEdge(svg, sx, sy, tx, ty, "#4caf7d", "url(#gam)", "");
    } else if (e.type === "provider-target") {
      drawGEdge(svg, sx, sy, tx, ty, "#e8933a", "url(#gap)", `${e.count} 条`);
    }
  }

  for (const n of agg.nodes) {
    const p = pos.get(n.id);
    if (p) svg.append(drawGNode(n, p.x, p.y));
  }

  container.replaceChildren(svg);
}

function renderFindings(container, findings) {
  container.replaceChildren();
  if (!Array.isArray(findings) || !findings.length) {
    const ok = document.createElement("div");
    ok.className = "finding-item finding-ok";
    ok.innerHTML = "<p>✓ 未发现规则问题</p>";
    container.append(ok);
    return;
  }

  const errors = findings.filter((f) => f.severity === "error").length;
  const warnings = findings.filter((f) => f.severity === "warning").length;
  const infos = findings.filter((f) => f.severity === "info").length;

  const header = document.createElement("div");
  header.className = "findings-header";
  const parts = [];
  if (errors) parts.push(`<span class="sev-badge sev-error">${errors} 个错误</span>`);
  if (warnings) parts.push(`<span class="sev-badge sev-warning">${warnings} 个警告</span>`);
  if (infos) parts.push(`<span class="sev-badge sev-info">${infos} 个提示</span>`);
  header.innerHTML = `<h3>规则分析</h3><div class="findings-badges">${parts.join("")}</div>`;
  container.append(header);

  const list = document.createElement("div");
  list.className = "finding-list";
  for (const f of findings) {
    const item = document.createElement("div");
    item.className = `finding-item severity-${f.severity}`;
    item.innerHTML = `
      <div>
        <strong>${escapeHtml(f.code)}</strong>
        <code>${escapeHtml(f.path)}</code>
      </div>
      <p>${escapeHtml(f.message)}</p>
    `;
    list.append(item);
  }
  container.append(list);
}

function renderSimulationTrace(trace) {
  const container = els.simulateTrace;
  if (!container) return;
  container.replaceChildren();
  if (!trace) {
    container.innerHTML = '<div class="empty">输入目的地进行模拟</div>';
    return;
  }

  const summary = document.createElement("div");
  summary.className = "trace-summary";
  const rule = trace.matched_rule;
  const ruleLabel = rule ? `${rule.type}${rule.match ? `, ${rule.match}` : ""}` : "未命中";
  summary.innerHTML = `
    <strong>${escapeHtml(trace.destination || "")}</strong>
    <span>${escapeHtml(ruleLabel)} → ${escapeHtml(trace.resolved || trace.target || "-")}</span>
  `;
  container.append(summary);

  for (const warning of trace.warnings || []) {
    const item = document.createElement("div");
    item.className = "trace-warning";
    item.textContent = warning;
    container.append(item);
  }

  for (const step of trace.steps || []) {
    const item = document.createElement("div");
    item.className = `trace-step ${step.matched === true ? "matched" : step.matched === false ? "missed" : ""}`;
    item.innerHTML = `
      <span>${escapeHtml(step.type || "")}</span>
      <p>${escapeHtml(step.message || step.ref || "")}</p>
    `;
    container.append(item);
  }
}

async function simulateWorkspace(event) {
  event.preventDefault();
  const destination = els.simulateDestination?.value.trim() || "";
  if (!destination) {
    els.simulateDestination?.reportValidity();
    return;
  }
  if (!state.workspace) {
    setStatus("请先生成配置", "error");
    switchOutputPane("config");
    return;
  }

  setBusy(true);
  setStatus("模拟流量中...");
  if (els.simulateTrace) els.simulateTrace.innerHTML = '<div class="empty">模拟中…</div>';
  try {
    const body = await postJson("/simulate", { workspace: state.workspace, destination });
    renderSimulationTrace(body.trace);
    const resolved = body.trace?.resolved || body.trace?.target || "-";
    setStatus(`模拟完成 · ${destination} → ${resolved}`, "ok");
  } catch (error) {
    if (els.simulateTrace) els.simulateTrace.innerHTML = `<div class="empty">模拟失败：${escapeHtml(error.message)}</div>`;
    setStatus(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function loadPolicyGraph() {
  const payload = getPayload();
  if (!payload.subscription_url) {
    els.form.reportValidity();
    return;
  }
  const container = els.graphContainer;
  const btn = document.getElementById("load-graph-button");
  if (!container) return;

  btn.disabled = true;
  container.innerHTML = '<div class="empty">刷新中…</div>';
  setStatus("刷新策略图中…");

  try {
    state.lastPayload = payload;
    const body = await postJson("/workspace/preview", payload);
    renderWorkspacePreview(body);
    const fc = state.findings.filter((f) => f.severity === "error").length;
    const groups = state.workspace?.proxy_groups?.length ?? 0;
    const providers = state.workspace?.rule_providers?.length ?? 0;
    const status = fc
      ? `策略图 · ${groups} 策略组 · ${fc} 个错误`
      : `策略图 · ${groups} 策略组 · ${providers} 规则集`;
    setStatus(status, fc ? "error" : "ok");
  } catch (err) {
    container.innerHTML = `<div class="empty">加载失败：${escapeHtml(err.message)}</div>`;
    setStatus(err.message, "error");
  } finally {
    btn.disabled = false;
  }
}

// ── Event binding ─────────────────────────────────────────────────────────

function bindEvents() {
  els.previewButton.addEventListener("click", previewNodes);
  els.form.addEventListener("submit", convertConfig);
  els.form.addEventListener("input", refreshSubscribeUrl);
  els.form.addEventListener("change", refreshSubscribeUrl);
  els.subconverterPanel?.addEventListener("input", refreshSubscribeUrl);
  els.subconverterPanel?.addEventListener("change", refreshSubscribeUrl);
  els.subconverterConfigSelect?.addEventListener("change", () => {
    syncSubconverterConfigField();
    refreshSubscribeUrl();
  });
  els.previewSubconverterConfigButton?.addEventListener("click", previewSubconverterConfig);
  els.closeSubconverterConfigPreviewButton?.addEventListener("click", closeSubconverterConfigPreview);
  els.subconverterConfigPreview?.addEventListener("click", (event) => {
    if (event.target === els.subconverterConfigPreview) closeSubconverterConfigPreview();
  });
  els.copySubconverterConfigButton?.addEventListener("click", () =>
    copyText(
      els.subconverterConfigPreviewContent?.textContent || "",
      "没有可复制的配置内容",
      "配置内容已复制",
      els.subconverterConfigPreviewContent
    )
  );
  els.templateSelect.addEventListener("change", refreshTemplateOptions);
  els.targetSelect.addEventListener("change", () => { renderPolicyTable(); updateConfigOutputTitle(); refreshSubscribeUrl(); });
  [els.enhanceAi, els.enhanceDev, els.enhanceStreaming].forEach((input) => {
    input?.addEventListener("change", applyEnhancementPreset);
  });
  [els.enhanceAdblock, els.enhanceTun, els.enhanceFakeip].forEach((input) => {
    input?.addEventListener("change", refreshSubscribeUrl);
  });
  els.powerfullzPanel.addEventListener("change", () => {
    refreshSubscribeUrl();
    loadTemplateDetail();
  });
  els.copyButton.addEventListener("click", () =>
    copyText(els.configOutput.value, "没有可复制的内容", "已复制", els.configOutput, els.copyButton)
  );
  els.copyUrlButton.addEventListener("click", async () => {
    clearTimeout(_sessionRefreshTimer);
    const payload = getPayload();
    const needsPolicySession =
      payload.subscription_url &&
      ((payload.custom_strategy?.proxy_groups?.length || 0) > 0 ||
        (payload.selected_policy?.proxy_groups?.length || 0) > 0);
    if (needsPolicySession) {
      await createSessionUrl(payload).catch(() => {});
    } else {
      refreshSubscribeUrl();
    }
    copyText(els.convertedUrl.value, "还没有订阅地址，请先填写订阅 URL", "订阅地址已复制", els.convertedUrl, els.copyUrlButton);
  });
  els.downloadButton?.addEventListener("click", downloadYaml);
  els.addGroupButton.addEventListener("click", addGroup);
  document.querySelectorAll(".yaml-tab").forEach((btn) => {
    btn.addEventListener("click", () => switchYamlView(btn.dataset.yamlTab));
  });
  document.querySelectorAll(".output-tab").forEach((btn) => {
    btn.addEventListener("click", () => switchOutputPane(btn.dataset.outputTab));
  });
  els.nodeFilter?.addEventListener("input", renderFilteredNodes);
  els.subscriptionUrl.addEventListener("blur", () => {
    if (els.subscriptionUrl.value.trim() && !state.allNodes.length) {
      previewNodes();
    }
  });
  els.subscriptionUrl.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      els.form.requestSubmit();
    }
  });
  document.querySelectorAll(".sim-example").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (els.simulateDestination) els.simulateDestination.value = btn.dataset.destination || "";
      els.simulateForm?.requestSubmit();
    });
  });
  els.policyLocalQuery?.addEventListener("input", renderPolicyTable);
  els.policyLocalQuery?.addEventListener("change", renderPolicyTable);
  document.querySelector("#clear-visible-policy")?.addEventListener("click", scrollToCurrentTemplate);
  document.getElementById("load-graph-button")?.addEventListener("click", loadPolicyGraph);
  els.simulateForm?.addEventListener("submit", simulateWorkspace);
}

// ── Init ──────────────────────────────────────────────────────────────────

bindEvents();
bindCommunityBrowserEvents();
renderGroups();
updateSelectionSummary();
loadSubconverterTargets();
loadTemplates();
loadCommunityMeta();
applyConversionMode();
refreshSubscribeUrl();
refreshLivePreview();
