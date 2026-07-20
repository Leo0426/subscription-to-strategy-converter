const $ = (selector, root = document) => root.querySelector(selector);
const LEO_TEMPLATE = "local:community_templates/leo/leo.yaml";

const state = {
  leoGroups: [],
  leoSummary: null,
  leoAudit: null,
  publicData: [],
  serviceCategories: [],
  servicePacks: [],
  serviceChoices: {},
  nodes: [],
};

const SERVICE_DEFAULTS = {
  claude: "AI 服务",
  openai: "AI 服务",
  gemini: "AI 服务",
  perplexity: "AI 服务",
  cursor: "AI 服务",
  "github-copilot": "AI 服务",
  github: "开发服务",
  developer: "开发服务",
  microsoft: "Microsoft",
  apple: "Apple",
  netflix: "流媒体",
  youtube: "流媒体",
  disney: "流媒体",
  spotify: "流媒体",
  telegram: "社交通讯",
};

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = String(value ?? "");
  return div.innerHTML;
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

function setBusy(button, busy, busyText) {
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = busyText;
  } else if (button.dataset.label) {
    button.textContent = button.dataset.label;
  }
  button.disabled = busy;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.hidden = true; }, 2000);
}

function setNotice(message = "") {
  const notice = $("#global-notice");
  notice.textContent = message;
  notice.hidden = !message;
}

function leoEgressGroups() {
  return [...new Set([
    ...state.leoGroups.filter((group) => group.type === "select").map((group) => group.name),
    ...state.leoGroups.flatMap((group) => Array.isArray(group.proxies) ? group.proxies : []),
    "DIRECT",
  ])].filter(Boolean);
}

function defaultServiceTarget(serviceId) {
  return SERVICE_DEFAULTS[serviceId] || "默认代理";
}

function auditTime(value) {
  if (!value) return "暂无时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function renderDataLedger() {
  const root = $("#data-ledger");
  if (!state.leoSummary || !state.leoAudit) {
    root.innerHTML = '<div class="simple-loading">正在核对公开数据…</div>';
    return;
  }

  const audit = state.leoAudit;
  const score = audit.quality_score || {};
  const summary = audit.summary || {};
  const mrsCount = (audit.sources || []).filter((source) => source.declared_format === "mrs").length;
  const observed = Number(summary.invalid || 0) + Number(summary.failed || 0);
  const dataLinks = state.publicData.length ? state.publicData : [
    { label: "完整模板 YAML", href: "/templates/source" },
    { label: "全部规则与来源", href: "/community/rules" },
    { label: "完整质量审计", href: "/templates/audit" },
  ];

  root.innerHTML = `
    <section class="ledger-score" aria-label="Leo 结构质量">
      <div><span>结构质量</span><strong>${escapeHtml(score.total ?? "—")}</strong><small>/ 100 · ${escapeHtml(score.grade || "未评级")}</small></div>
      <i title="结构质量为审计快照，不代表长期语义准确率"></i>
    </section>
    <div class="ledger-metrics" aria-label="模板实时统计">
      <div><strong>${escapeHtml(state.leoSummary.proxy_group_count)}</strong><span>策略组</span></div>
      <div><strong>${escapeHtml(state.leoSummary.rule_count)}</strong><span>路由规则</span></div>
      <div><strong>${escapeHtml(state.leoSummary.rule_provider_count)}</strong><span>远程来源</span></div>
      <div><strong>${escapeHtml(summary.valid ?? "—")}</strong><span>本轮可用</span></div>
    </div>
    <div class="ledger-facts">
      <p><span>审计时间</span><b>${escapeHtml(auditTime(audit.generated_at))}</b></p>
      <p><span>观察项</span><b>${escapeHtml(observed)} 个失败或异常，未静默删除</b></p>
      <p><span>Mihomo</span><b>完整读取 Leo 规则格式</b></p>
      <p><span>Surge</span><b>${escapeHtml(mrsCount)} 个 MRS 会降级跳过并告警</b></p>
    </div>
    <div class="route-order" aria-label="规则命中顺序">
      <span>REJECT</span><i>→</i><span>DIRECT</span><i>→</i><span>专用服务</span><i>→</i><span>默认代理</span>
    </div>
    <nav class="public-data-links" aria-label="公开数据接口">
      ${dataLinks.map((item, index) => `<a href="${escapeHtml(item.href)}" target="_blank" rel="noopener"><small>0${index + 1}</small><span>${escapeHtml(item.label)}</span><b>↗</b></a>`).join("")}
    </nav>
    <p class="privacy-boundary">公开的是模板与审计元数据；你的订阅地址、节点密码和第三方规则正文不会写入这些接口。</p>`;
}

function renderLeoReference() {
  const root = $("#leo-reference");
  if (!state.leoSummary || !state.leoGroups.length) {
    root.innerHTML = '<div class="simple-loading">正在读取 Leo 模板…</div>';
    return;
  }

  const groupByName = Object.fromEntries(state.leoGroups.map((group) => [group.name, group]));
  const coreNames = ["默认代理", "自动选择", "故障转移", "手动选择"];
  const regionNames = ["香港自动", "台湾自动", "日本自动", "新加坡自动", "美国自动", "韩国自动", "欧洲自动"]
    .filter((name) => groupByName[name]);
  const coreRows = coreNames.filter((name) => groupByName[name]).map((name) => {
    const group = groupByName[name];
    const members = Array.isArray(group.proxies) && group.proxies.length
      ? group.proxies.slice(0, 3).join(" / ")
      : group.type;
    return `<div class="reference-flow-row"><b>${escapeHtml(name)}</b><span>${escapeHtml(members)}</span></div>`;
  }).join("");
  const serviceRows = state.serviceCategories.map((category) => {
    const packs = state.servicePacks.filter((pack) => pack.category === category.id);
    if (!packs.length) return "";
    return `<p class="reference-note">${escapeHtml(category.label)}</p>${packs.map((pack) => `
      <button class="reference-service" type="button" data-reference-service="${escapeHtml(pack.id)}">
        <span>${escapeHtml(pack.label)}</span><small>${escapeHtml(defaultServiceTarget(pack.id))}</small>
      </button>`).join("")}`;
  }).join("");

  root.innerHTML = `
    <details class="reference-module" open>
      <summary>核心出口骨架</summary>
      <div class="reference-module-body"><p class="reference-note">默认代理按自动选择、故障转移和地区组逐级组织。</p><div class="reference-flow">${coreRows}</div></div>
    </details>
    <details class="reference-module" open>
      <summary>地区自动选择</summary>
      <div class="reference-module-body"><p class="reference-note">地区组只收录名称匹配的节点，并独立测速。</p><div class="reference-chips">${regionNames.map((name) => `<span class="reference-chip">${escapeHtml(name)}</span>`).join("")}</div></div>
    </details>
    <details class="reference-module">
      <summary>服务默认出口</summary>
      <div class="reference-module-body"><p class="reference-note">点击服务可定位右侧配置；不修改时沿用下列默认值。</p><div class="reference-service-list">${serviceRows || '<span class="reference-note">正在载入服务映射…</span>'}</div></div>
    </details>
    <details class="reference-module">
      <summary>运行能力</summary>
      <div class="reference-module-body reference-capabilities">
        <div class="reference-capability"><b>DNS</b><span>${state.leoSummary.has_dns ? "已内置" : "未配置"}</span></div>
        <div class="reference-capability"><b>TUN</b><span>${state.leoSummary.has_tun ? "模板可用" : "未配置"}</span></div>
        <div class="reference-capability"><b>Mihomo</b><span>完整输出</span></div>
        <div class="reference-capability"><b>Surge</b><span>自动适配</span></div>
      </div>
    </details>`;
}

function renderServices() {
  const root = $("#service-route-list");
  if (!state.servicePacks.length || !state.leoGroups.length) {
    root.innerHTML = '<div class="simple-loading">正在载入服务列表…</div>';
    return;
  }
  const groupOptions = leoEgressGroups();
  const nodeOptions = state.nodes.map((node) => node.name).filter(Boolean);
  root.innerHTML = state.serviceCategories.map((category) => {
    const packs = state.servicePacks.filter((pack) => pack.category === category.id);
    if (!packs.length) return "";
    return `<section class="service-category" data-service-category="${escapeHtml(category.id)}">
      <h3>${escapeHtml(category.label)}</h3>
      ${packs.map((pack) => {
        const selected = state.serviceChoices[pack.id] || "";
        const defaultTarget = defaultServiceTarget(pack.id);
        return `<label class="service-row${selected ? " is-customized" : ""}" data-service="${escapeHtml(pack.id)}">
          <span class="service-name"><strong>${escapeHtml(pack.label)}</strong><small>${escapeHtml(pack.description)}</small></span>
          <select data-service-choice aria-label="${escapeHtml(pack.label)}服务出口">
            <option value=""${selected ? "" : " selected"}>跟随 Leo · ${escapeHtml(defaultTarget)}</option>
            <optgroup label="Leo 策略组">${groupOptions.map((target) => `<option value="${escapeHtml(target)}"${target === selected ? " selected" : ""}>${escapeHtml(target)}</option>`).join("")}</optgroup>
            ${nodeOptions.length ? `<optgroup label="具体节点">${nodeOptions.map((node) => `<option value="${escapeHtml(node)}"${node === selected ? " selected" : ""}>${escapeHtml(node)}</option>`).join("")}</optgroup>` : ""}
          </select>
        </label>`;
      }).join("")}
    </section>`;
  }).join("");
}

async function loadLeoTemplate() {
  try {
    const body = await jsonRequest(`/templates/detail?template=${encodeURIComponent(LEO_TEMPLATE)}`);
    state.leoGroups = body.proxy_groups || [];
    state.leoSummary = body.summary || null;
    state.publicData = body.public_data || [];
    renderDataLedger();
    renderLeoReference();
    renderServices();
  } catch (error) {
    $("#leo-reference").innerHTML = `<div class="simple-loading">Leo 模板加载失败：${escapeHtml(error.message)}</div>`;
    $("#service-route-list").innerHTML = `<div class="simple-loading">Leo 配置加载失败：${escapeHtml(error.message)}</div>`;
  }
}

async function loadLeoAudit() {
  try {
    state.leoAudit = await jsonRequest("/templates/audit");
    renderDataLedger();
  } catch (error) {
    $("#data-ledger").innerHTML = `<div class="ledger-error">审计数据暂不可用：${escapeHtml(error.message)}</div>`;
  }
}

async function loadServices() {
  try {
    const body = await jsonRequest("/rule-packs");
    state.serviceCategories = body.categories || [];
    state.servicePacks = body.packs || [];
    state.serviceChoices = Object.fromEntries(state.servicePacks.map((pack) => [pack.id, ""]));
    renderLeoReference();
    renderServices();
  } catch (error) {
    $("#service-route-list").innerHTML = `<div class="simple-loading">服务列表加载失败：${escapeHtml(error.message)}</div>`;
  }
}

function selectedPolicy() {
  const overridden = state.servicePacks.filter((pack) => state.serviceChoices[pack.id]);
  const proxyGroups = [];
  const rules = [];
  for (const pack of overridden) {
    const selected = state.serviceChoices[pack.id];
    const defaultTarget = defaultServiceTarget(pack.id);
    const fallback = pack.group.name === defaultTarget ? "默认代理" : defaultTarget;
    proxyGroups.push({
      name: pack.group.name,
      type: "select",
      proxies: [...new Set([selected, fallback, "默认代理", "DIRECT"])],
    });
    rules.push(...pack.rules);
  }
  return {
    mode: "merge",
    node_selectors: [],
    proxy_groups: proxyGroups,
    rule_providers: {},
    rules,
  };
}

function payload() {
  return {
    subscription_url: $("#subscription-url").value.trim(),
    template: LEO_TEMPLATE,
    target: "clash",
    selected_policy: selectedPolicy(),
  };
}

function overrideCount() {
  return Object.values(state.serviceChoices).filter(Boolean).length;
}

function refreshGenerateHint() {
  if (!state.nodes.length) return;
  const count = overrideCount();
  $("#generate-hint").textContent = count
    ? `已读取 ${state.nodes.length} 个节点，${count} 个服务使用独立出口。`
    : `已读取 ${state.nodes.length} 个节点，全部服务跟随 Leo。`;
}

async function loadNodes() {
  const input = $("#subscription-url");
  const result = $("#source-result");
  const button = $("#validate-source-button");
  if (!input.value.trim()) return input.focus();
  setBusy(button, true, "读取中…");
  result.hidden = true;
  $("#generate-button").disabled = true;
  $("#publish-result").hidden = true;
  try {
    const body = await postJson("/preview", payload());
    state.nodes = body.nodes || [];
    const validTargets = new Set([...leoEgressGroups(), ...state.nodes.map((node) => node.name)]);
    for (const [service, choice] of Object.entries(state.serviceChoices)) {
      if (choice && !validTargets.has(choice)) state.serviceChoices[service] = "";
    }
    renderServices();
    result.className = "inline-status";
    result.textContent = `已读取 ${state.nodes.length} 个节点`;
    result.hidden = false;
    $("#generate-button").disabled = false;
    refreshGenerateHint();
  } catch (error) {
    state.nodes = [];
    result.className = "inline-status is-error";
    result.textContent = error.message;
    result.hidden = false;
  } finally {
    setBusy(button, false);
  }
}

async function generateSubscription() {
  const button = $("#generate-button");
  if (!state.nodes.length) return loadNodes();
  setBusy(button, true, "生成中…");
  setNotice("");
  $("#publish-result").hidden = true;
  try {
    const preview = await postJson("/workspace/preview", payload());
    const errors = (preview.findings || []).filter((item) => item.severity === "error");
    if (errors.length) throw new Error(`${errors.length} 个配置错误，请检查服务出口。`);
    const created = await postJson("/profiles", payload());
    $("#published-clash-url").value = new URL(created.subscribe_urls.clash, location.origin).toString();
    $("#published-surge-url").value = new URL(created.subscribe_urls.surge, location.origin).toString();
    $("#publish-result").hidden = false;
    $("#generate-hint").textContent = "订阅已生成。";
  } catch (error) {
    setNotice(`生成失败：${error.message}`);
  } finally {
    setBusy(button, false);
  }
}

async function loadHealth() {
  const health = $("#health-chip");
  try {
    const body = await jsonRequest("/system/status");
    const okay = body.app?.status === "ok" && body.profile_db?.status === "ok";
    health.classList.toggle("is-ok", okay);
    health.classList.toggle("is-error", !okay);
    $("span", health).textContent = okay ? "服务正常" : "服务异常";
  } catch (_error) {
    health.classList.add("is-error");
    $("span", health).textContent = "服务不可用";
  }
}

function bindEvents() {
  $("#validate-source-button").addEventListener("click", loadNodes);
  $("#subscription-url").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); loadNodes(); }
  });
  $("#service-route-list").addEventListener("change", (event) => {
    const select = event.target.closest("[data-service-choice]");
    if (!select) return;
    const row = select.closest("[data-service]");
    state.serviceChoices[row.dataset.service] = select.value;
    renderServices();
    $("#publish-result").hidden = true;
    refreshGenerateHint();
  });
  $("#leo-reference").addEventListener("click", (event) => {
    const reference = event.target.closest("[data-reference-service]");
    if (!reference) return;
    const row = [...document.querySelectorAll("[data-service]")]
      .find((candidate) => candidate.dataset.service === reference.dataset.referenceService);
    if (!row) return;
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.remove("is-referenced");
    requestAnimationFrame(() => row.classList.add("is-referenced"));
    $("select", row)?.focus({ preventScroll: true });
  });
  $("#generate-button").addEventListener("click", generateSubscription);
  $("#publish-result").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy-output]");
    if (!button) return;
    const value = $(`#published-${button.dataset.copyOutput}-url`).value;
    if (!value) return;
    await navigator.clipboard.writeText(value);
    showToast("链接已复制");
  });
}

async function init() {
  bindEvents();
  await Promise.allSettled([loadHealth(), loadLeoTemplate(), loadLeoAudit(), loadServices()]);
}

init();
