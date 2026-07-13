const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  step: "source",
  profiles: [],
  templates: [],
  nodes: [],
  workspace: null,
  findings: [],
  outputs: { clash: "", surge: "" },
  validationReady: false,
  editingProfile: null,
};

const els = {
  home: $("#profile-home"),
  flow: $("#flow-view"),
  profilesList: $("#profiles-list"),
  profileCount: $("#profile-count"),
  health: $("#health-chip"),
  notice: $("#global-notice"),
  subscriptionUrl: $("#subscription-url"),
  sourceResult: $("#source-result"),
  validateSource: $("#validate-source-button"),
  clashTemplate: $("#clash-template"),
  surgeTemplate: $("#surge-template"),
  claudeEgress: $("#claude-egress"),
  routePreviewEgress: $("#route-preview-egress"),
  confirmRouting: $("#confirm-routing-button"),
  publishButton: $("#publish-profile-button"),
  publishResult: $("#publish-result"),
  inspector: $("#context-inspector"),
  toast: $("#toast"),
};

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = String(value ?? "");
  return div.innerHTML;
}

function shortTemplateName(templateId) {
  const template = state.templates.find((item) => item.id === templateId);
  if (!template) return templateId || "—";
  const label = template.label || template.id;
  return label.replace(/^community_templates\/THEYAMLS\//, "");
}

async function jsonRequest(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(body.detail)
      ? body.detail.map((item) => item.msg || JSON.stringify(item)).join("；")
      : body.detail || `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return body;
}

async function postJson(path, payload) {
  return jsonRequest(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { els.toast.hidden = true; }, 2200);
}

function setNotice(message = "") {
  els.notice.textContent = message;
  els.notice.hidden = !message;
}

function setButtonBusy(button, busy, busyText) {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.innerHTML;
    button.textContent = busyText;
  } else if (button.dataset.label) {
    button.innerHTML = button.dataset.label;
  }
  button.disabled = busy;
}

function getStoredTokens() {
  try { return JSON.parse(localStorage.getItem("subflow.profileTokens") || "{}"); }
  catch (_error) { return {}; }
}

function rememberProfileToken(id, token) {
  const tokens = getStoredTokens();
  tokens[id] = token;
  localStorage.setItem("subflow.profileTokens", JSON.stringify(tokens));
}

async function loadSystemStatus() {
  try {
    const body = await jsonRequest("/system/status");
    const okay = body.app?.status === "ok" && body.profile_db?.status === "ok";
    els.health.classList.toggle("is-ok", okay);
    els.health.classList.toggle("is-error", !okay);
    $("span", els.health).textContent = okay ? "本地服务正常" : "部分服务异常";
    if (!okay) setNotice("部分本地服务不可用，请在发布前检查运行环境。");
  } catch (error) {
    els.health.classList.add("is-error");
    $("span", els.health).textContent = "服务状态未知";
    setNotice(`无法读取系统状态：${error.message}`);
  }
}

function profileSubscribeUrl(profileId, token, target) {
  return new URL(`/subscribe/${profileId}?token=${encodeURIComponent(token)}&target=${target}`, location.origin).toString();
}

function renderProfiles() {
  els.profileCount.textContent = `${state.profiles.length} 个`;
  if (!state.profiles.length) {
    els.profilesList.innerHTML = `
      <article class="empty-profile-card">
        <div><h3>还没有已发布的订阅</h3><p>从一条机场订阅开始，通常只需要两分钟。</p></div>
        <button class="button button-dark" type="button" data-new-profile>开始创建 →</button>
      </article>`;
    return;
  }
  const tokens = getStoredTokens();
  els.profilesList.innerHTML = state.profiles.map((profile) => {
    const hasToken = Boolean(tokens[profile.id]);
    return `
      <article class="profile-card" data-profile-id="${escapeHtml(profile.id)}">
        <div>
          <div class="profile-card-top"><div><span class="kicker">PROFILE</span><h3>${escapeHtml(profile.id)}</h3></div><span class="status-pill">${profile.has_artifact ? "已验证" : "待首次拉取"}</span></div>
          <div class="profile-meta"><span>C · ${escapeHtml(shortTemplateName(profile.clash_template))}</span><span>S · ${escapeHtml(shortTemplateName(profile.surge_template))}</span></div>
        </div>
        <div class="profile-actions">
          <button type="button" data-copy-profile="clash" ${hasToken ? "" : "disabled"}>复制 Clash 链接</button>
          <button type="button" data-copy-profile="surge" ${hasToken ? "" : "disabled"}>复制 Surge 链接</button>
          ${hasToken
            ? '<button type="button" class="muted-action" data-edit-profile>编辑</button>'
            : '<button type="button" class="muted-action" data-clone-profile>基于此新建</button>'}
        </div>
      </article>`;
  }).join("");
}

async function loadProfiles() {
  try {
    const body = await jsonRequest("/profiles");
    state.profiles = body.profiles || [];
    renderProfiles();
    if (!state.profiles.length) startNewProfile();
  } catch (error) {
    els.profilesList.innerHTML = `<div class="empty-profile-card"><div><h3>Profile 列表加载失败</h3><p>${escapeHtml(error.message)}</p></div></div>`;
  }
}

function templateScore(template, target) {
  const id = template.id || "";
  if (target === "surge") return id === "ai-tools" ? 100 : id === "full" ? 90 : 0;
  if (id.endsWith("liandu2024/clash-fallback.yaml")) return 110;
  if (id.includes("liandu2024/clash-fallback")) return 100;
  if (id === "ai-tools") return 80;
  return id.startsWith("local:") ? 60 : 20;
}

function fillTemplateSelect(select, templates, selectedId) {
  select.replaceChildren();
  for (const template of templates) {
    const option = document.createElement("option");
    option.value = template.id;
    option.textContent = shortTemplateName(template.id);
    select.append(option);
  }
  if (templates.some((item) => item.id === selectedId)) select.value = selectedId;
}

function refreshTemplateRecommendation() {
  const clash = state.templates.find((item) => item.id === els.clashTemplate.value);
  const surge = state.templates.find((item) => item.id === els.surgeTemplate.value);
  $("#clash-template-name").textContent = shortTemplateName(clash?.id);
  $("#surge-template-name").textContent = shortTemplateName(surge?.id);
  $("#clash-template-reason").textContent = clash?.claude?.dedicated_group
    ? `保留现有 ${clash.claude.dedicated_group} 策略组`
    : `从 ${clash?.claude?.current_targets?.join(" / ") || "共享策略"} 拆分 Claude`;
  $("#surge-template-reason").textContent = "完整规则图可被 Surge 原样编译";
  $("#aside-templates").textContent = `${shortTemplateName(clash?.id)} / ${shortTemplateName(surge?.id)}`;
}

async function loadTemplates() {
  const body = await jsonRequest("/claude/templates");
  state.templates = body.templates || [];
  const clashTemplates = [...state.templates].sort((a, b) => templateScore(b, "clash") - templateScore(a, "clash"));
  const surgeTemplates = clashTemplates.filter((item) => item.claude?.surge_compatible).sort((a, b) => templateScore(b, "surge") - templateScore(a, "surge"));
  const recommendedClash = clashTemplates[0]?.id || "ai-tools";
  const recommendedSurge = surgeTemplates[0]?.id || "ai-tools";
  fillTemplateSelect(els.clashTemplate, clashTemplates, recommendedClash);
  fillTemplateSelect(els.surgeTemplate, surgeTemplates, recommendedSurge);
  refreshTemplateRecommendation();
  renderProfiles();
}

function resetDraft() {
  state.step = "source";
  state.nodes = [];
  state.workspace = null;
  state.findings = [];
  state.outputs = { clash: "", surge: "" };
  state.validationReady = false;
  state.editingProfile = null;
  els.subscriptionUrl.value = "";
  els.sourceResult.hidden = true;
  els.claudeEgress.innerHTML = '<option value="">请选择一个出口</option>';
  els.routePreviewEgress.textContent = "等待选择";
  els.confirmRouting.disabled = true;
  els.publishButton.disabled = true;
  els.publishButton.hidden = false;
  els.publishResult.hidden = true;
  $$(".step-tab").forEach((tab) => {
    const isSource = tab.dataset.stepTarget === "source";
    tab.disabled = !isSource;
    tab.classList.remove("is-active", "is-complete");
  });
  $("#aside-source").textContent = "未连接";
  $("#aside-egress").textContent = "未选择";
  $("#draft-status").textContent = "新建 Profile";
  updateInspector();
}

function startNewProfile() {
  resetDraft();
  els.home.hidden = true;
  els.flow.hidden = false;
  showStep("source", true);
  requestAnimationFrame(() => els.subscriptionUrl.focus());
}

function showHome() {
  els.flow.hidden = true;
  els.home.hidden = false;
  closeInspector();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

const stepOrder = ["source", "targets", "routing", "publish"];

function showStep(step, force = false) {
  const targetTab = $(`.step-tab[data-step-target="${step}"]`);
  if (!force && targetTab?.disabled) return;
  state.step = step;
  $$(".flow-step").forEach((section) => {
    const active = section.dataset.step === step;
    section.hidden = !active;
    section.classList.toggle("is-active", active);
  });
  $$(".step-tab").forEach((tab) => {
    const index = stepOrder.indexOf(tab.dataset.stepTarget);
    const current = stepOrder.indexOf(step);
    tab.classList.toggle("is-active", index === current);
    tab.classList.toggle("is-complete", index < current && !tab.disabled);
  });
  const subtitles = {
    source: "先连接你的订阅源，后续选择会基于真实节点和客户端能力。",
    targets: "告诉 Subflow 你要发布到哪里，我们会解释每个模板推荐。",
    routing: "为 Claude 选择一个明确出口，其余模板行为保持不变。",
    publish: "确认策略含义与兼容性，然后发布长期订阅地址。",
  };
  $("#flow-subtitle").textContent = subtitles[step];
  if (step === "publish") validateDraft();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function unlockStep(step) {
  const tab = $(`.step-tab[data-step-target="${step}"]`);
  if (tab) tab.disabled = false;
}

function renderSourceSuccess() {
  els.sourceResult.hidden = false;
  els.sourceResult.className = "source-result";
  els.sourceResult.innerHTML = `<strong>订阅连接成功</strong> · 已读取 ${state.nodes.length} 个节点`;
  $("#aside-source").textContent = `${state.nodes.length} 个节点`;
  els.claudeEgress.innerHTML = '<option value="">请选择一个出口</option>';
  for (const node of state.nodes) {
    const option = document.createElement("option");
    option.value = node.name;
    option.textContent = node.name;
    els.claudeEgress.append(option);
  }
  updateInspector();
}

async function validateSource() {
  const subscriptionUrl = els.subscriptionUrl.value.trim();
  if (!subscriptionUrl) {
    els.subscriptionUrl.focus();
    return;
  }
  setButtonBusy(els.validateSource, true, "正在连接…");
  els.sourceResult.hidden = true;
  try {
    const body = await postJson("/preview", { subscription_url: subscriptionUrl, target: "clash" });
    state.nodes = body.nodes || [];
    renderSourceSuccess();
    unlockStep("targets");
    showStep("targets");
  } catch (error) {
    els.sourceResult.hidden = false;
    els.sourceResult.className = "source-result is-error";
    els.sourceResult.innerHTML = `<strong>无法连接订阅</strong> · ${escapeHtml(error.message)}`;
  } finally {
    setButtonBusy(els.validateSource, false);
  }
}

function draftPayload(target = "clash") {
  const template = target === "surge" ? els.surgeTemplate.value : els.clashTemplate.value;
  return {
    subscription_url: els.subscriptionUrl.value.trim(),
    template,
    clash_template: els.clashTemplate.value,
    surge_template: els.surgeTemplate.value,
    target,
    claude_policy: { enabled: true, egress: els.claudeEgress.value },
  };
}

function subscribeOutputUrl(target) {
  const payload = draftPayload(target);
  const url = new URL("/subscribe", location.origin);
  url.searchParams.set("subscription_url", payload.subscription_url);
  url.searchParams.set("template", payload.template);
  url.searchParams.set("target", target);
  url.searchParams.set("claude", JSON.stringify(payload.claude_policy));
  return url;
}

function renderValidationState(kind, title, detail) {
  const box = $("#validation-state");
  box.className = `validation-state is-${kind}`;
  $("strong", box).textContent = title;
  $("p", box).textContent = detail;
}

async function fetchText(url) {
  const response = await fetch(url);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${response.status}`);
  }
  return response.text();
}

async function validateDraft() {
  if (!els.claudeEgress.value || !els.subscriptionUrl.value.trim()) return;
  state.validationReady = false;
  els.publishButton.disabled = true;
  renderValidationState("running", "正在构建并检查策略…", "检查模板引用、Claude 路由和目标兼容性。");
  $("#summary-nodes").textContent = `${state.nodes.length} 个`;
  $("#summary-clash").textContent = shortTemplateName(els.clashTemplate.value);
  $("#summary-surge").textContent = shortTemplateName(els.surgeTemplate.value);
  $("#summary-egress").textContent = els.claudeEgress.value;
  try {
    const [preview, clashOutput, surgeOutput] = await Promise.all([
      postJson("/workspace/preview", draftPayload("clash")),
      fetchText(subscribeOutputUrl("clash")),
      fetchText(subscribeOutputUrl("surge")),
    ]);
    state.workspace = preview.workspace;
    state.findings = preview.findings || [];
    state.outputs = { clash: clashOutput, surge: surgeOutput };
    const errors = state.findings.filter((item) => item.severity === "error");
    if (errors.length) throw new Error(`${errors.length} 个策略错误需要处理`);
    state.validationReady = true;
    els.publishButton.disabled = false;
    renderValidationState("valid", "两个客户端均已验证", `${state.findings.length} 条分析结果，没有阻止发布的问题。`);
  } catch (error) {
    state.validationReady = false;
    renderValidationState("error", "当前配置无法发布", error.message);
    openInspector("findings");
  }
  updateInspector();
}

async function publishProfile() {
  if (!state.validationReady) return;
  setButtonBusy(els.publishButton, true, "正在发布…");
  try {
    let created;
    if (state.editingProfile) {
      const { id, token } = state.editingProfile;
      created = await jsonRequest(`/profiles/${id}?token=${encodeURIComponent(token)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draftPayload("clash")),
      });
      created.token = token;
    } else {
      created = await postJson("/profiles", draftPayload("clash"));
      rememberProfileToken(created.id, created.token);
    }
    $("#published-clash-url").value = new URL(created.subscribe_urls.clash, location.origin).toString();
    $("#published-surge-url").value = new URL(created.subscribe_urls.surge, location.origin).toString();
    els.publishResult.hidden = false;
    els.publishButton.hidden = true;
    $("#draft-status").textContent = `已发布 · ${created.id}`;
    showToast(state.editingProfile ? "Profile 更新成功" : "Profile 发布成功");
    await loadProfiles();
    els.flow.hidden = false;
    els.home.hidden = true;
  } catch (error) {
    renderValidationState("error", "发布失败", error.message);
  } finally {
    setButtonBusy(els.publishButton, false);
  }
}

async function editProfile(profile, token) {
  startNewProfile();
  state.editingProfile = { id: profile.id, token };
  $("#draft-status").textContent = `编辑 · ${profile.id}`;
  setNotice("");
  try {
    const draft = await jsonRequest(
      `/profiles/${profile.id}/draft?token=${encodeURIComponent(token)}`
    );
    const request = draft.request || {};
    els.subscriptionUrl.value = request.subscription_url || "";
    await validateSource();
    const clashTemplate = request.clash_template || request.template;
    const surgeTemplate = request.surge_template || request.template;
    if ($(`#clash-template option[value="${CSS.escape(clashTemplate)}"]`)) {
      els.clashTemplate.value = clashTemplate;
    }
    if ($(`#surge-template option[value="${CSS.escape(surgeTemplate)}"]`)) {
      els.surgeTemplate.value = surgeTemplate;
    }
    refreshTemplateRecommendation();
    els.claudeEgress.value = request.claude_policy?.egress || "";
    els.claudeEgress.dispatchEvent(new Event("change"));
    showToast("已载入 Profile 草稿");
  } catch (error) {
    setNotice(`无法载入 Profile：${error.message}`);
  }
}

function updateFindingBadges() {
  const count = state.findings.length;
  $("#inspector-finding-count").textContent = count;
  $("#finding-badge").textContent = count;
  $("#finding-badge").hidden = count === 0;
}

function updateInspector() {
  const overview = $("#inspector-overview");
  if (!state.nodes.length) {
    overview.className = "inspector-empty";
    overview.textContent = "连接订阅后，这里会显示节点与策略统计。";
  } else {
    const groups = state.workspace?.proxy_groups?.length || 0;
    const providers = state.workspace?.rule_providers?.length || 0;
    overview.className = "";
    overview.innerHTML = `<div class="overview-stats"><div><strong>${state.nodes.length}</strong><span>节点</span></div><div><strong>${groups}</strong><span>策略组</span></div><div><strong>${providers}</strong><span>规则源</span></div></div><div class="node-preview">${state.nodes.slice(0, 8).map((node) => `<div><strong>${escapeHtml(node.name)}</strong><span>${escapeHtml(node.type || node.protocol || "proxy")}</span></div>`).join("")}</div>`;
  }
  const findings = $("#inspector-findings");
  if (!state.findings.length) {
    findings.className = "inspector-empty";
    findings.textContent = state.workspace ? "没有发现需要处理的问题。" : "尚未执行策略验证。";
  } else {
    findings.className = "";
    findings.innerHTML = state.findings.map((item) => `<article class="finding-item"><strong>${escapeHtml(item.code || item.severity || "finding")}</strong><p>${escapeHtml(item.message || item.detail || JSON.stringify(item))}</p></article>`).join("");
  }
  $("#source-output").textContent = state.outputs[$("#source-target").value] || "# 验证后显示最终配置";
  updateFindingBadges();
}

function openInspector(view = "overview") {
  els.inspector.classList.add("is-open");
  els.inspector.setAttribute("aria-hidden", "false");
  showInspectorView(view);
}

function closeInspector() {
  els.inspector.classList.remove("is-open");
  els.inspector.setAttribute("aria-hidden", "true");
}

function showInspectorView(view) {
  $$('[data-inspector-target]').forEach((button) => button.classList.toggle("is-active", button.dataset.inspectorTarget === view));
  $$('[data-inspector-view]').forEach((section) => {
    const active = section.dataset.inspectorView === view;
    section.hidden = !active;
    section.classList.toggle("is-active", active);
  });
}

async function simulate(event) {
  event.preventDefault();
  const destination = $("#simulate-destination").value.trim();
  if (!destination || !state.workspace) return;
  const result = $("#simulate-result");
  result.className = "inspector-empty";
  result.textContent = "正在模拟…";
  try {
    const body = await postJson("/simulate", { workspace: state.workspace, destination });
    const trace = body.trace || {};
    result.className = "";
    result.innerHTML = `<article class="finding-item"><strong>${escapeHtml(destination)} → ${escapeHtml(trace.final_target || trace.target || "未命中")}</strong><p>${escapeHtml(trace.explanation || trace.reason || JSON.stringify(trace))}</p></article>`;
  } catch (error) {
    result.textContent = error.message;
  }
}

async function copyText(value, message = "已复制") {
  if (!value) return;
  await navigator.clipboard.writeText(value);
  showToast(message);
}

function bindEvents() {
  $("#new-profile-button").addEventListener("click", startNewProfile);
  $("#hero-new-profile-button").addEventListener("click", startNewProfile);
  $("#back-home-button").addEventListener("click", showHome);
  els.profilesList.addEventListener("click", async (event) => {
    const newButton = event.target.closest("[data-new-profile]");
    if (newButton) return startNewProfile();
    const card = event.target.closest("[data-profile-id]");
    if (!card) return;
    const profile = state.profiles.find((item) => item.id === card.dataset.profileId);
    const token = getStoredTokens()[card.dataset.profileId];
    const copyButton = event.target.closest("[data-copy-profile]");
    if (copyButton && token) return copyText(profileSubscribeUrl(profile.id, token, copyButton.dataset.copyProfile));
    if (event.target.closest("[data-edit-profile]") && token) return editProfile(profile, token);
    if (event.target.closest("[data-clone-profile]")) {
      startNewProfile();
      if (state.templates.length) {
        if ($(`#clash-template option[value="${CSS.escape(profile.clash_template)}"]`)) els.clashTemplate.value = profile.clash_template;
        if ($(`#surge-template option[value="${CSS.escape(profile.surge_template)}"]`)) els.surgeTemplate.value = profile.surge_template;
        refreshTemplateRecommendation();
      }
      showToast("已继承模板组合，请重新连接订阅源");
    }
  });
  els.validateSource.addEventListener("click", validateSource);
  els.subscriptionUrl.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); validateSource(); }
  });
  $("#toggle-template-picker").addEventListener("click", () => $(".recommendation-block").classList.toggle("is-editing"));
  els.clashTemplate.addEventListener("change", refreshTemplateRecommendation);
  els.surgeTemplate.addEventListener("change", refreshTemplateRecommendation);
  $("#confirm-targets-button").addEventListener("click", () => { unlockStep("routing"); showStep("routing"); });
  els.claudeEgress.addEventListener("change", () => {
    const value = els.claudeEgress.value;
    els.routePreviewEgress.textContent = value || "等待选择";
    $("#aside-egress").textContent = value || "未选择";
    els.confirmRouting.disabled = !value;
    state.validationReady = false;
  });
  els.confirmRouting.addEventListener("click", () => { unlockStep("publish"); showStep("publish"); });
  $$('[data-go-step]').forEach((button) => button.addEventListener("click", () => showStep(button.dataset.goStep)));
  $$(".step-tab").forEach((button) => button.addEventListener("click", () => showStep(button.dataset.stepTarget)));
  els.publishButton.addEventListener("click", publishProfile);
  $("#preview-config-button").addEventListener("click", () => openInspector("source"));
  $("#open-inspector-button").addEventListener("click", () => openInspector());
  $("#summary-inspector-button").addEventListener("click", () => openInspector());
  $$('[data-close-inspector]').forEach((item) => item.addEventListener("click", closeInspector));
  $$('[data-inspector-target]').forEach((button) => button.addEventListener("click", () => showInspectorView(button.dataset.inspectorTarget)));
  $("#source-target").addEventListener("change", updateInspector);
  $("#copy-source-button").addEventListener("click", () => copyText($("#source-output").textContent));
  $("#simulate-form").addEventListener("submit", simulate);
  $("#publish-result").addEventListener("click", (event) => {
    const button = event.target.closest("[data-copy-output]");
    if (!button) return;
    const input = button.dataset.copyOutput === "surge" ? $("#published-surge-url") : $("#published-clash-url");
    copyText(input.value);
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeInspector(); });
}

async function init() {
  bindEvents();
  updateInspector();
  await Promise.allSettled([loadSystemStatus(), loadTemplates(), loadProfiles()]);
}

init();
