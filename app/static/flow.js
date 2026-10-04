const $ = (selector, root = document) => root.querySelector(selector);
const LEO_TEMPLATE = "local:community_templates/leo/leo.yaml";
const state = {
  leoGroups: [], leoSummary: null, leoAudit: null, publicData: [],
  serviceCategories: [{id:"ai",label:"AI 工具"},{id:"developer",label:"开发与系统"},{id:"streaming",label:"影音与通讯"}],
  servicePacks: [], serviceChoices: {}, nodes: [], profile: null, legacy: null,
  upgrade: null, check: null, checkedInput: null, savedInput: null, epoch: 0, busy: false,
  showAll: false, showCustomized: false,
  surgeProtocols: [],
};
const CLIENT_LABELS = {mihomo:"Clash / OpenClash",surge:"Surge",shadowrocket:"Shadowrocket"};
const PRIMARY_SERVICES = ["openai","claude","gemini","github","youtube"];
const SERVICE_SYMBOLS = {openai:"O",claude:"C",gemini:"G",github:"GH",youtube:"Y"};
function escapeHtml(value) { const div=document.createElement("div"); div.textContent=String(value??""); return div.innerHTML.replaceAll('"', "&quot;").replaceAll("'", "&#39;"); }
async function jsonRequest(path, options={}) {
  const response=await fetch(path, {...options, cache:"no-store"});
  const body=await response.json().catch(()=>({}));
  if (!response.ok) {
    const detail=Array.isArray(body.detail) ? body.detail.map(x=>x.msg).join("；") : typeof body.detail==="object" ? body.detail.message : body.detail;
    const error=new Error(detail||`HTTP ${response.status}`); error.checks=body.detail?.checks; throw error;
  }
  return body;
}
const postJson=(path,body)=>jsonRequest(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
function focusControl(element, scroll=false, block="center") {
  if(!element||element.disabled) return;
  if(scroll) element.scrollIntoView?.({behavior:globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches?"auto":"smooth",block});
  element.focus?.({preventScroll:true});
}
function setNotice(message="") {
  const notice=$("#global-notice"); notice.textContent=message; notice.hidden=!message;
  if(message) focusControl(notice,true);
}
function showToast(message) { const el=$("#toast"); el.textContent=message; el.hidden=false; clearTimeout(showToast.timer); showToast.timer=setTimeout(()=>{el.hidden=true;},2400); }
async function copyToClipboard(value,input) {
  try { if(navigator.clipboard?.writeText) { await navigator.clipboard.writeText(value); return; } } catch {}
  input.focus(); input.select(); if(!document.execCommand("copy")) throw new Error("复制失败"); input.blur();
}
async function copyPublishedLink(button) {
  const input=$(`#published-${button.dataset.copyOutput}-url`);
  const label=button.dataset.copyLabel||button.textContent;
  button.dataset.copyLabel=label;
  clearTimeout(button.copyFeedbackTimer);
  const restore=()=>{button.textContent=label;button.classList.remove("is-copied");};
  try {
    await copyToClipboard(input.value,input);
    button.textContent="已复制";button.classList.add("is-copied");
    button.copyFeedbackTimer=setTimeout(restore,1800);
    showToast("链接已复制");
  } catch {restore();showToast("请选中链接后手动复制");}
  finally {focusControl(button);}
}
function selectedTargets() { return [...document.querySelectorAll('input[name="target"]:checked')].map(x=>x.value); }
function updateSurgePreferenceVisibility() {
  const selected=selectedTargets().includes("surge");
  $("#surge-auto-test-row").hidden=!selected;
  $("#surge-auto-test-protocols").disabled=!selected||state.busy;
}
function restoreSurgePreferences(preferences={}) {
  const protocols=Array.isArray(preferences?.auto_test_protocols)?[...preferences.auto_test_protocols]:[];
  const choice=protocols.length===0?"all":protocols.length===1&&protocols[0]==="anytls"?"anytls":"custom";
  state.surgeProtocols=protocols;
  $("#surge-auto-test-custom").hidden=choice!=="custom";
  $("#surge-auto-test-protocols").value=choice;
  updateSurgePreferenceVisibility();
}
function payload() {
  const targets=selectedTargets();
  const surgeChoice=$("#surge-auto-test-protocols").value;
  const surge_preferences={auto_test_protocols:surgeChoice==="custom"?[...state.surgeProtocols]:surgeChoice==="anytls"?["anytls"]:[]};
  const common={subscription_url:$("#subscription-url").value.trim(),template:LEO_TEMPLATE,profile_name:$("#profile-name").value.trim(),target:targets[0]||"mihomo",publication_targets:targets,surge_preferences};
  if(state.legacy) return {...structuredClone(state.legacy),...common};
  return {...common,service_routes:Object.entries(state.serviceChoices).filter(([,r])=>r.mode!=="default").map(([service,r])=>({service,mode:r.mode,egress:r.egress?.trim()||null,...(r.mode==="fallback"?{fallback:r.fallback?.trim()||null}:{})}))};
}
function invalidate() { $("#diagnose-result").hidden=true; state.epoch++; state.check=null; state.checkedInput=null; state.savedInput=null; $("#check-results").hidden=true; $("#publish-result").hidden=true; $("#check-state").textContent=state.nodes.length?"配置已改变，请重新检查":"先读取订阅节点"; updateActions(); }
function currentDraftSaved() { return Boolean(state.check?.can_publish&&state.savedInput&&state.savedInput===state.checkedInput&&state.savedInput===JSON.stringify(payload())); }
function updateWorkspaceSummary() {
  const targets=selectedTargets();
  const sourceReady=state.nodes.length>0&&targets.length>0;
  const checked=state.check&&state.checkedInput===JSON.stringify(payload());
  const saved=currentDraftSaved();
  const current=!sourceReady?"source":checked?"review":"services";
  const steps=["source","services","review"];
  document.querySelectorAll('[data-step]').forEach(link=>{
    const active=link.dataset.step===current;
    link.classList.toggle("is-current",active);
    link.classList.toggle("is-complete",steps.indexOf(link.dataset.step)<steps.indexOf(current));
    if(active) link.setAttribute("aria-current","step"); else link.removeAttribute("aria-current");
  });
  const values={
    "summary-label":saved?"已保存配置":"当前草稿",
    "summary-next-label":saved?"下一步 · 导入 / 刷新":"下一步",
    "summary-nodes":state.nodes.length?`${state.nodes.length} 个节点`:"尚未读取",
    "summary-clients":targets.length?`${targets.length} 个客户端`:"尚未选择",
    "summary-routes":state.legacy?"保留旧策略快照":`${Object.values(state.serviceChoices).filter(r=>r.mode!=="default").length} 项服务偏好`,
    "summary-state":state.busy?"处理中":saved?"已保存，请在客户端导入或刷新":!sourceReady?"待读取来源":!checked?"待检查":state.check.can_publish?"可以保存":"有阻断项",
    "workspace-progress":state.busy?"正在处理，请稍候":saved?"已保存 · 复制订阅链接，在客户端导入或刷新":current==="source"?"第 1 步 · 读取来源与选择客户端":current==="services"?"第 2 步 · 确认服务出口，再检查配置":state.check.can_publish?"第 3 步 · 核对检查结果，保存订阅":"第 3 步 · 查看阻断项，修正后重新检查",
  };
  for(const [id,text] of Object.entries(values)) {const element=$("#"+id);if(element) element.textContent=text;}
  const link=$("#summary-link");
  if(link) {link.href=saved?"#publish-result":"#step-review";link.innerHTML=`${saved?"查看订阅链接":"前往检查与保存"} <span aria-hidden="true">↗</span>`;}
}
function updateActions() {
  const ready=state.nodes.length>0 && selectedTargets().length>0 && !state.busy;
  const saved=currentDraftSaved();
  $("#refresh-publications-button").disabled=!state.profile||state.busy;
  $("#check-button").disabled=!ready;
  $("#diagnose-button").disabled=!ready;
  $("#generate-button").disabled=!ready||saved||!state.check?.can_publish||state.checkedInput!==JSON.stringify(payload());
  $("#generate-button").textContent=saved?"已保存":state.profile?"更新原订阅":"保存并生成链接";
  $("#save-title").textContent=saved?"订阅已保存":state.profile?"保存到原订阅":"生成订阅";
  $("#generate-hint").textContent=saved?"当前配置已保存；请复制下方链接，或在客户端刷新原订阅。":state.profile?"更新保留原链接；客户端刷新后采用新配置。":"检查通过后，生成所选客户端的订阅。";
  if(saved) $("#check-state").textContent="已保存，请在客户端导入或刷新订阅";
  updateWorkspaceSummary();
}
async function busy(button,label,operation) {
  if(state.busy) return;
  const original=button.textContent; state.busy=true;
  const workbench=$(".config-workbench"); workbench?.setAttribute?.("aria-busy","true"); button.classList?.add("is-loading");
  const controls=[...document.querySelectorAll(".config-workbench input, .config-workbench select, .config-workbench button")];
  const disabled=controls.map(el=>el.disabled); controls.forEach(el=>{el.disabled=true;}); button.textContent=label;
  updateWorkspaceSummary();
  let failed=false, errorFocus=null;
  setNotice("");
  try { await operation(); } catch(error) {
    failed=true; errorFocus=error.focusTarget;
    if(error.checks) renderCheck(error.checks);
    if(!errorFocus) setNotice(error.message);
  }
  finally {
    controls.forEach((el,i)=>{el.disabled=disabled[i];}); state.busy=false; button.textContent=original;
    workbench?.setAttribute?.("aria-busy","false"); button.classList?.remove("is-loading");
    renderServices(); updateSurgePreferenceVisibility(); updateActions();
    if(errorFocus) focusControl($(errorFocus),true);
    else if(!failed) {
      const result=button.dataset?.successTarget?$(button.dataset.successTarget):null;
      if(result&&!result.hidden&&result.getClientRects().length) focusControl(result,true,"start");
      else focusControl(button);
    }
  }
}
function defaultServiceTarget(id) { return state.servicePacks.find(s=>s.id===id)?.default_target||"默认代理"; }
function leoEgressGroups() {
  return state.leoGroups.filter(g=>{
    if(!g["include-all"]||!g.filter||!state.nodes.length) return true;
    try { return state.nodes.some(n=>new RegExp(g.filter.replace(/^\(\?i\)/,""),"i").test(n.name)); } catch { return false; }
  }).map(g=>g.name);
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
      <p><span>Surge 5.21+</span><b>核心域名兼容；Mihomo 专属项会跳过并告警</b></p>
    </div>
    <div class="route-order" aria-label="规则命中顺序">
      <span>启动直连</span><i>→</i><span>核心服务</span><i>→</i><span>广告拦截</span><i>→</i><span>国内直连</span><i>→</i><span>默认代理</span>
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
  const coreNames = ["默认代理", "自动选择", "香港自动", "手动选择"];
  const regionNames = ["香港自动", "美国节点"]
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
      <div class="reference-module-body"><p class="reference-note">默认代理首选香港池；其他成员是手动候选，不代表自动故障切换。</p><div class="reference-flow">${coreRows}</div></div>
    </details>
    <details class="reference-module" open>
      <summary>地区节点选择</summary>
      <div class="reference-module-body"><p class="reference-note">仅保留香港低延迟池；AI 服务提供美国和新加坡节点手动选择；连通性测试只更新延迟，不会自动切换节点。</p><div class="reference-chips">${regionNames.map((name) => `<span class="reference-chip">${escapeHtml(name)}</span>`).join("")}</div></div>
    </details>
    <details class="reference-module">
      <summary>服务默认出口</summary>
      <div class="reference-module-body"><p class="reference-note">点击服务可定位出口配置；不修改时沿用下列默认值。</p><div class="reference-service-list">${serviceRows || '<span class="reference-note">正在载入服务映射…</span>'}</div></div>
    </details>
    <details class="reference-module">
      <summary>运行能力</summary>
      <div class="reference-module-body reference-capabilities">
        <div class="reference-capability"><b>DNS</b><span>${state.leoSummary.has_dns ? "已内置" : "未配置"}</span></div>
        <div class="reference-capability"><b>TUN</b><span>${state.leoSummary.has_tun ? "模板可用" : "未配置"}</span></div>
        <div class="reference-capability"><b>Mihomo</b><span>完整输出</span></div>
        <div class="reference-capability"><b>Surge 5.21+</b><span>兼容输出，跳过项会告警</span></div>
        <div class="reference-capability"><b>Shadowrocket</b><span>节点订阅 + Leo 配置，跳过项会告警</span></div>
      </div>
    </details>`;
}

function serviceRouteSummary(id,route) {
  if(route.mode==="default") return `跟随 Leo · ${defaultServiceTarget(id)}`;
  if(route.mode==="fixed") return `固定出口 · ${route.egress||"待选择节点"}`;
  if(route.mode==="manual") return `客户端手选 · ${route.egress||"待选择策略或节点"}`;
  return `主 ${route.egress||"待选择"} → 备 ${route.fallback||"待选择"}`;
}
function renderServices() {
  const root=$("#service-route-list"); if(!state.servicePacks.length) return;
  const active=document.activeElement;
  const focusedService=active?.closest?.('[data-service]')?.dataset.service;
  const focusedField=["mode","egress","fallback"].find(field=>active?.matches?.(`[data-${field}]`));
  const selection=typeof active?.selectionStart==="number"?[active.selectionStart,active.selectionEnd,active.selectionDirection]:null;
  const search=$("#service-search").value.trim().toLowerCase();
  const groups=leoEgressGroups();
  const rank=id=>PRIMARY_SERVICES.includes(id)?PRIMARY_SERVICES.indexOf(id):PRIMARY_SERVICES.length;
  const customized=id=>(state.serviceChoices[id]?.mode||"default")!=="default";
  const services=[...state.servicePacks].sort((a,b)=>rank(a.id)-rank(b.id)).filter(s=>`${s.label} ${s.id}`.toLowerCase().includes(search)).filter(s=>!state.showCustomized||customized(s.id)).filter(s=>search||state.showAll||PRIMARY_SERVICES.includes(s.id)||customized(s.id));
  root.innerHTML=services.map(service=>{
    const r=state.serviceChoices[service.id]||{mode:"default",egress:"",fallback:""};
    const options=[["default","跟随 Leo"],["fixed","固定节点"],["manual","交给客户端选择"],["fallback","主备故障切换"]];
    const locked=state.legacy||state.busy?" disabled":"";
    let fields="";
    if(r.mode==="manual") {
      const choices=[...new Set([...groups,...state.nodes.map(n=>n.name),"DIRECT","REJECT",r.egress])].filter(x=>x&&x!==service.group);
      fields=`<label class="field"><span>委托给哪个策略或节点？</span><select data-egress${locked}><option value="">请选择</option>${choices.map(name=>`<option value="${escapeHtml(name)}"${name===r.egress?" selected":""}>${escapeHtml(name)}</option>`).join("")}</select></label><p class="helper">显示的是配置意图；组内最终节点由客户端的当前选择决定。</p>`;
    } else if(r.mode!=="default") {
      fields=`<div class="egress-fields"><label class="field"><span>${r.mode==="fallback"?"主节点":"固定到"}</span><input data-egress list="node-options" value="${escapeHtml(r.egress)}" placeholder="输入名称查找节点" autocomplete="off"${locked} /></label>${r.mode==="fallback"?`<label class="field"><span>备用节点</span><input data-fallback list="node-options" value="${escapeHtml(r.fallback)}" placeholder="选择另一个节点" autocomplete="off"${locked} /></label>`:""}</div><p class="helper">${r.mode==="fixed"?"仅保留这一目标；节点消失时检查会报错，不会默默改走其他出口。":"主节点通用连通性检查失败才切换备用；不代表目标服务已解锁。"}</p>`;
    }
    const intent=serviceRouteSummary(service.id,r);
    return `<article class="service-card${r.mode!=="default"?" is-customized":""}" data-service="${escapeHtml(service.id)}"><div class="service-card-heading"><div class="service-identity"><span class="service-symbol" data-kind="${escapeHtml(service.id)}" aria-hidden="true">${escapeHtml(SERVICE_SYMBOLS[service.id]||service.label.slice(0,1))}</span><div><strong>${escapeHtml(service.label)}</strong><small>${service.rules.length} 条服务规则</small></div></div><select data-mode aria-label="${escapeHtml(service.label)}出口行为"${locked}>${options.map(([v,l])=>`<option value="${v}"${r.mode===v?" selected":""}>${l}</option>`).join("")}</select></div><p class="service-route-summary">${escapeHtml(intent)}</p>${fields?`<div class="service-card-fields">${fields}</div>`:""}</article>`;
  }).join("")||`<p class="service-empty">${state.showCustomized?"没有匹配的已设置服务。清除筛选可查看其他服务。":"没有匹配的服务。试试服务名称，或清空搜索查看常用服务。"}</p>`;
  $("#node-options").innerHTML=[...state.nodes.map(n=>n.name),"DIRECT","REJECT"].map(n=>`<option value="${escapeHtml(n)}"></option>`).join("");
  const searchStatus=$("#service-search-status");
  if(searchStatus) searchStatus.textContent=search?`找到 ${services.length} 项服务`:`显示 ${services.length} / ${state.servicePacks.length} 项服务`;
  const expand=$("#show-all-services");
  if(expand) {expand.textContent=search?"清除搜索，查看全部":state.showCustomized?"查看全部服务":state.showAll?"收起其他服务":"展开全部服务";expand.setAttribute("aria-expanded",String(state.showAll&&!search&&!state.showCustomized));}
  const filter=$("#filter-customized-services");
  if(filter) filter.setAttribute("aria-pressed",String(state.showCustomized));
  const clear=$("#clear-service-filter");
  if(clear) clear.hidden=!search&&!state.showCustomized;
  const filterStatus=$("#service-filter-status");
  if(filterStatus) filterStatus.textContent=state.showCustomized?"仅显示已设置出口的服务；筛选不会修改配置。":search?"按名称搜索；清除后可查看其他服务。":"筛选只影响显示，不改变服务出口。";
  if(focusedService&&focusedField) {
    const replacement=root.querySelector(`[data-service="${focusedService}"] [data-${focusedField}]`);
    focusControl(replacement);
    if(replacement&&!replacement.disabled&&selection) replacement.setSelectionRange?.(...selection);
    if(!replacement) focusControl(state.showCustomized?filter:$("#service-search"));
  }
}
function restoreChoices(routes=[]) {
  state.serviceChoices=Object.fromEntries(state.servicePacks.map(s=>[s.id,{mode:"default",egress:"",fallback:""}]));
  for(const r of routes) if(r.enabled!==false&&r.mode&&r.mode!=="legacy") state.serviceChoices[r.service]={...r};
}
function resetServiceView() {state.showCustomized=false;state.showAll=false;$("#service-search").value="";}
async function loadNodes() {
  state.nodes=[]; invalidate(); renderServices();
  try {
    if(!$("#subscription-url").value.trim()) throw new Error("请输入原始订阅地址。");
    if(!selectedTargets().length) throw new Error("至少选择一个客户端。");
    const body=await postJson("/preview",{subscription_url:$("#subscription-url").value.trim(),target:selectedTargets()[0]}); state.nodes=body.nodes||[];
    const result=$("#source-result"); result.hidden=false; result.className="inline-status"; result.textContent=`已读取 ${state.nodes.length} 个节点 · 读取成功不代表节点可访问目标服务`;
  } catch(error) {
    const result=$("#source-result"); result.hidden=false; result.className="inline-status is-error"; result.textContent=error.message;
    error.focusTarget=selectedTargets().length?"#subscription-url":'input[name="target"]';
    throw error;
  }
  $("#check-state").textContent=state.nodes.length?"节点已读取，可以检查所选客户端":"来源中没有节点，请核对订阅地址";
  renderServices(); updateActions();
}
function renderDependencySummary(inventory) {
  if(!inventory?.summary) return "";
  const s=inventory.summary;
  return `<small>规则与地理数据：${escapeHtml(s.total)} 项依赖 · ${escapeHtml(s.remote)} 个远程地址 · ${escapeHtml(s.geodata)} 项地理数据${s.unresolved?` · ${escapeHtml(s.unresolved)} 项地址未解析`:""} · 未下载审计</small>`;
}
function renderRuntimeEvidence(actual) {
  if(!actual.identity) return "";
  const identity=actual.identity;
  const kinds={group_selection:"当前选择",rule_match:"规则匹配",probe:"指定节点探测",configuration_identity:"配置一致性"};
  const statuses={observed:"已观测",partial:"证据有限",unavailable:"不可用",not_tested:"未验证",unknown:"未知"};
  const mode={rule:"规则模式",global:"全局代理",proxy:"全局代理",direct:"全局直连",unknown:"模式未知"};
  return `<div class="compatibility-note"><b>${escapeHtml(CLIENT_LABELS[identity.client]||identity.client)} · ${escapeHtml(identity.version||"版本未知")} · ${escapeHtml(!identity.platform||identity.platform==="unknown"?"平台未知":identity.platform)} · ${escapeHtml(mode[identity.mode]||"模式未知")}</b><p>观测范围：服务器配置的实例 · ${escapeHtml(auditTime(identity.observed_at))}</p><p>此观测不代表手机或其他客户端状态。</p></div><div class="table-scroll"><table class="evidence-table"><thead><tr><th>证据</th><th>状态</th><th>说明</th></tr></thead><tbody>${(actual.evidence||[]).map(e=>`<tr><td>${escapeHtml(kinds[e.kind]||e.kind)}</td><td>${escapeHtml(statuses[e.status]||"未知")}</td><td>${escapeHtml(e.reason)}</td></tr>`).join("")}</tbody></table></div>`;
}
function renderStaticEvidence(evidence) {
  if(!evidence) return "";
  const reasons={earlier_rule_requires_runtime:"前置规则需要运行态数据",no_domain_rule_match:"未匹配到可确定的域名规则",client_selection_not_observed:"配置路径按首个候选推断，未读取客户端选择",target_resolution_incomplete:"配置目标未完整解析"};
  return `<p class="helper">配置推断 · ${escapeHtml(auditTime(evidence.observed_at))}${evidence.reasons?.length?` · ${evidence.reasons.map(r=>escapeHtml(reasons[r]||r)).join("；")}`:""}</p>`;
}
function renderCheck(report) {
  const errors=(report.findings||[]).filter(f=>f.severity==="error");
  const notices=(report.findings||[]).filter(f=>f.severity!=="error");
  $("#check-results").hidden=false;
  $("#check-results").innerHTML=`<div class="check-grid"><div class="check-card ${errors.length?"is-error":""}"><b>配置结构</b><strong>${errors.length?`${errors.length} 个错误`:"通过"}</strong><small>${report.node_count} 个节点 · 仅验证生成配置</small></div>${report.clients.map(c=>`<div class="check-card ${c.errors.length?"is-error":c.warnings.length?"is-warning":""}"><b>${escapeHtml(CLIENT_LABELS[c.target]||c.target)}</b><strong>${c.errors.length?"无法发布":c.warnings.length?`${c.warnings.length} 项兼容提示`:"编译通过"}</strong><small>${c.target==="shadowrocket"?"已检查节点与配套配置":"未替代客户端导入验证"}</small>${renderDependencySummary(c.dependencies)}</div>`).join("")}<div class="check-card neutral"><b>实际访问</b><strong>未验证</strong><small>可在下方按需进行客户端实测</small></div></div>${errors.map(e=>`<p class="inline-status is-error">${escapeHtml(e.message)}</p>`).join("")}${report.clients.map(c=>[...c.errors,...c.warnings.map(w=>w.suggestion||w.message||w.code)].map(m=>`<p class="compatibility-note"><b>${escapeHtml(CLIENT_LABELS[c.target]||c.target)}</b> ${escapeHtml(m)}</p>`).join("")).join("")}${notices.length?`<details class="check-details"><summary>${notices.length} 项结构与运行条件提示</summary>${notices.map(n=>`<p>${escapeHtml(n.message)}</p>`).join("")}</details>`:""}<p class="helper">服务规则与节点选择可在下方逐项诊断。远程规则集的运行态匹配不属于静态检查结果。</p>`;
}
async function checkConfig() {
  const key=JSON.stringify(payload()); const epoch=state.epoch;
  const report=await postJson("/check",payload());
  if(epoch!==state.epoch) return;
  state.check=report; state.checkedInput=key; renderCheck(report);
  $("#check-state").textContent=report.can_publish?"检查完成，请查看兼容提示后保存":"存在阻断项，请修正后重新检查";
}
const LOOPBACK_HOSTS=new Set(["127.0.0.1","localhost","::1","[::1]","0.0.0.0"]);
function showReachWarning(hostname) {
  const warning=$("#publish-reach-warning"); warning.hidden=!LOOPBACK_HOSTS.has(hostname);
  warning.textContent=`${hostname} 只在本机有效。路由器或手机请通过本机局域网地址访问 Subflow，或配置 SUBFLOW_PUBLIC_BASE_URL 后重新打开页面。`;
}
function showPublished(body) {
  const urls={clash:body.subscribe_urls.clash,surge:body.subscribe_urls.surge,shadowrocket:body.subscribe_urls.shadowrocket,"shadowrocket-config":body.config_urls.shadowrocket};
  for(const [key,url] of Object.entries(urls)) $(`#published-${key}-url`).value=new URL(url,location.origin).toString();
  document.querySelectorAll('[data-output-target]').forEach(el=>{el.hidden=!selectedTargets().includes(el.dataset.outputTarget);});
  showReachWarning(new URL(body.subscribe_urls.clash,location.origin).hostname);
  $("#publish-result").hidden=false;
}
function renderPublicationStatus(body) {
  $("#publication-status-panel").hidden=false;
  const rows=Object.entries(body.publications||{});
  const labels={...CLIENT_LABELS,"shadowrocket-config":"Shadowrocket 配置"};
  const status=m=>m.last_status==="stale"?"上次刷新失败，使用上次成功配置":m.revision!==body.current_publication_revision?"基础配置已变化，等待刷新":"已生成，设备更新状态需在客户端核对";
  $("#publication-status").innerHTML=`<p>已保存版本 ${escapeHtml(body.generation)} · ${escapeHtml(body.cache_ttl_seconds)} 秒内重复请求可复用服务器缓存</p>${rows.length?`<div class="table-scroll"><table><thead><tr><th>客户端</th><th>配置标识 / 生成时间</th><th>状态</th></tr></thead><tbody>${rows.map(([target,m])=>`<tr><td>${escapeHtml(labels[target]||target)}</td><td>${escapeHtml((m.revision||"").slice(0,12))}<br>${escapeHtml(auditTime(m.generated_at))}</td><td>${escapeHtml(status(m))}</td></tr>`).join("")}</tbody></table></div>`:"<p>此版本尚未生成配置；客户端下次更新时生成。</p>"}`;
}
async function loadPublicationStatus() {
  const p=state.profile;
  if(!p) return null;
  const body=await jsonRequest(`/profiles/${encodeURIComponent(p.id)}/draft?token=${encodeURIComponent(p.token)}`);
  if(state.profile===p) renderPublicationStatus(body);
  return body;
}
async function refreshPublications() {
  const p=state.profile;
  if(!p) throw new Error("请先打开或保存订阅。");
  const saved=await loadPublicationStatus();
  const targets=[...new Set(saved.request.publication_targets||[saved.request.target==="clash"?"mihomo":saved.request.target])];
  if(targets.includes("shadowrocket")) targets.push("shadowrocket-config");
  const outcomes=await Promise.allSettled(targets.map(async target=>{
    const response=await fetch(`/subscribe/${encodeURIComponent(p.id)}?token=${encodeURIComponent(p.token)}&target=${encodeURIComponent(target)}&force_refresh=true`,{cache:"no-store"});
    await response.text();
    if(!response.ok) throw new Error(`${CLIENT_LABELS[target]||target} 刷新失败（HTTP ${response.status}）`);
    return response.headers.get("X-Subflow-Stale")==="true";
  }));
  await loadPublicationStatus();
  const errors=outcomes.filter(r=>r.status==="rejected").map(r=>r.reason.message);
  if(errors.length) throw new Error(errors.join("；"));
  showToast(outcomes.some(r=>r.value)?"上游暂不可用，部分输出使用上次成功配置":"服务器配置已刷新，请在各客户端更新订阅");
}
async function saveProfile() {
  const request=payload(),key=JSON.stringify(request);
  if(!state.check?.can_publish||state.checkedInput!==key) throw new Error("配置已变化，请重新检查。");
  if(state.savedInput===key) return;
  const editing=Boolean(state.profile);
  const body=editing ? await jsonRequest(`/profiles/${encodeURIComponent(state.profile.id)}?token=${encodeURIComponent(state.profile.token)}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:key}) : await postJson("/profiles",request);
  if(!editing) state.profile={id:body.id,token:body.token};
  state.savedInput=key;
  showPublished(body); $("#publish-title").textContent=editing?"已更新，原订阅链接保持不变":"已保存订阅";
  $("#profile-status").textContent=`正在编辑：${$("#profile-name").value.trim()||state.profile.id.slice(0,8)}`;
  try {await loadPublicationStatus();}
  catch(error) {
    $("#publication-status-panel").hidden=false;
    $("#publication-status").textContent=`订阅已保存，刷新状态读取失败：${error.message}。可稍后重试刷新状态。`;
  }
  showToast(editing?"原订阅已更新，请在客户端刷新":"订阅已保存");
}
async function openProfile() {
  let url; try { url=new URL($("#existing-profile-url").value.trim(),location.origin); } catch { throw new Error("请输入有效的 Subflow 订阅链接。"); }
  const match=url.pathname.match(/^\/subscribe\/([a-zA-Z0-9_-]+)$/); const token=url.searchParams.get("token");
  if(!match||!token) throw new Error("请粘贴带 token 的 /subscribe/… 链接。");
  const body=await jsonRequest(`/profiles/${encodeURIComponent(match[1])}/draft?token=${encodeURIComponent(token)}`);
  state.profile={id:match[1],token}; state.savedInput=null; state.legacy=body.mode==="legacy_snapshot"?body.request:null;
  resetServiceView();
  $("#subscription-url").value=body.request.subscription_url; $("#profile-name").value=body.request.profile_name||"";
  const targets=body.request.publication_targets||[body.request.target==="clash"?"mihomo":body.request.target];
  document.querySelectorAll('input[name="target"]').forEach(el=>{el.checked=targets.includes(el.value)||targets.includes(el.value+"-config");});
  restoreSurgePreferences(body.request.surge_preferences); restoreChoices(body.request.service_routes); $("#legacy-panel").hidden=!state.legacy; $("#upgrade-result").hidden=true;
  $("#profile-status").textContent=`正在编辑：${body.request.profile_name||state.profile.id.slice(0,8)} · ${state.legacy?"旧策略快照":body.update_available?"基础规则已更新，出口偏好保留":"跟随当前 Leo 与服务规则"}`;
  $("#existing-profile-url").value="";
  renderPublicationStatus(body);
  await loadNodes();
}
async function previewUpgrade() {
  state.upgrade=await postJson(`/profiles/${encodeURIComponent(state.profile.id)}/upgrade-preview?token=${encodeURIComponent(state.profile.token)}`,{});
  const c=state.upgrade.changes;
  const ruleChanges=c.added_rules.map(x=>"+ "+x).concat(c.removed_rules.map(x=>"− "+x));
  if(c.rule_order_changed) ruleChanges.unshift("↕ 共同规则的相对顺序改变，前置规则可能改变实际出口。");
  $("#upgrade-result").hidden=false;
  $("#upgrade-result").innerHTML=`<p>${escapeHtml(c.message)}</p><p>新增 ${c.added_rules.length} 条 · 移除 ${c.removed_rules.length} 条${c.rule_order_changed?" · 规则顺序改变":""} · 保留 ${state.upgrade.request.service_routes.length} 个服务出口</p><p>将替换的策略组：${escapeHtml(c.removed_groups.join("、")||"无")}</p><p>无法保留的出口：${escapeHtml(c.discarded_preferences.join("、")||"无")}</p><details><summary>查看规则变化</summary><pre>${escapeHtml(ruleChanges.join("\n"))}</pre></details><button id="apply-upgrade-button" class="simple-button secondary" type="button">应用到编辑草稿</button>`;
}
function applyUpgrade() {
  if(!state.upgrade) return;
  state.legacy=null; restoreSurgePreferences(state.upgrade.request.surge_preferences); restoreChoices(state.upgrade.request.service_routes); $("#legacy-panel").hidden=true;
  $("#profile-status").textContent="升级已应用到草稿，尚未保存；请检查后更新原订阅。";
  invalidate(); renderServices();
}
async function diagnose() {
  const service=$("#diagnose-service").value;
  const report=await postJson("/diagnose",{request:payload(),service,runtime:$("#diagnose-runtime").checked,client:$("#diagnose-client").value,samples:$("#diagnose-runtime").checked?3:1});
  const actual=report.runtime;
  const observed=actual.domain_routes?`<details class="check-details" open><summary>客户端规则解释（域名 / TCP 443）</summary><p>${actual.consistent_exit?"已检查域名出口一致":"存在出口差异或未能读取，请逐项核对"}</p><div class="table-scroll"><table><thead><tr><th>域名</th><th>解释得到的节点</th><th>解释规则</th></tr></thead><tbody>${actual.domain_routes.map(d=>`<tr><td>${escapeHtml(d.domain)}</td><td>${escapeHtml(d.actual_node||"未读取")}</td><td>${escapeHtml(d.rule||"未读取")}</td></tr>`).join("")}</tbody></table></div></details>`:"";
  $("#diagnose-result").hidden=false;
  $("#diagnose-result").innerHTML=`<div class="diagnosis-summary"><b>${escapeHtml(report.service.label)}</b><p>配置分流：${report.service.status==="consistent"?"已检查的服务域名使用同一策略":"部分域名需要客户端规则数据，或存在出口差异"}</p><p>客户端选择：${escapeHtml(actual.actual_node||"未验证")}</p><p>${escapeHtml(actual.message)}</p>${actual.service_tested===false?'<p>该服务尚未配置专用探测地址，本次仅检查通用连通性。</p>':""}</div>${(report.warnings||[]).map(w=>`<p class="compatibility-note">${escapeHtml(w.suggestion||w.message)}</p>`).join("")}${renderRuntimeEvidence(actual)}${renderStaticEvidence(report.service.evidence)}<details class="check-details" open><summary>配置路径（不是客户端实时选择）</summary><div class="table-scroll"><table><thead><tr><th>域名</th><th>配置路径</th></tr></thead><tbody>${report.service.domains.map(d=>`<tr><td>${escapeHtml(d.domain)}</td><td>${escapeHtml(d.path.join(" → "))}${d.status!=="matched"?" · 需运行态规则":""}</td></tr>`).join("")}</tbody></table></div></details>${observed}${actual.probes?`<div class="probe-list">${actual.probes.map(p=>`<p><b>${p.status==="reachable"?"探测可达":p.status==="degraded"?"间歇失败":"探测未通过"}</b><span>${escapeHtml(p.url)}</span><small>${p.http_status?`HTTP ${p.http_status} · `:""}${p.latency_ms!=null?`${Math.round(p.latency_ms)} ms`:"未取得成功响应"} · ${p.sample_count||1} 次采样 · 失败 ${Math.round((p.failure_rate||0)*100)}%${p.latency_spread_ms!=null?` · 延迟波动 ${Math.round(p.latency_spread_ms)} ms`:""} · ${escapeHtml(p.node||"未读取节点")}</small></p>`).join("")}</div><p class="helper">此结果只对应本次探测，不代表完整登录、对话或长期可用。</p>`:""}`;
}
function newProfile() {
  state.profile=null;state.savedInput=null;state.legacy=null;state.upgrade=null;state.nodes=[];restoreChoices();
  resetServiceView();
  restoreSurgePreferences();
  for(const id of ["subscription-url","profile-name","existing-profile-url"]) $("#"+id).value="";
  $("#profile-status").textContent="正在新建订阅";$("#legacy-panel").hidden=true;$("#source-result").hidden=true;$("#diagnose-result").hidden=true;
  $("#publication-status-panel").hidden=true;
  invalidate();renderServices();setNotice("");
}
async function loadHealth() {
  try { const body=await jsonRequest("/system/status"); const ok=body.app?.status==="ok"&&body.profile_db?.status==="ok"; $("#health-chip").classList.toggle("is-ok",ok); $("#health-chip span").textContent=ok?"服务正常":"服务异常"; } catch { $("#health-chip span").textContent="服务不可用"; }
}
function bindEvents() {
  const actions={"refresh-publications-button":["刷新中…",refreshPublications],"validate-source-button":["读取中…",loadNodes],"check-button":["检查中…",checkConfig],"generate-button":["保存中…",saveProfile],"open-profile-button":["打开中…",openProfile],"preview-upgrade-button":["比较中…",previewUpgrade],"diagnose-button":["检查中…",diagnose]};
  for(const [id,[label,fn]] of Object.entries(actions)) $("#"+id).addEventListener("click",event=>busy(event.currentTarget,label,fn));
  $("#new-profile-button").addEventListener("click",newProfile);
  $("#subscription-url").addEventListener("input",()=>{state.nodes=[];$("#source-result").hidden=true;invalidate();renderServices();});
  $("#profile-name").addEventListener("input",invalidate);
  document.querySelectorAll('input[name="target"]').forEach(el=>el.addEventListener("change",()=>{updateSurgePreferenceVisibility();invalidate();}));
  $("#surge-auto-test-protocols").addEventListener("change",invalidate);
  for(const id of ["diagnose-service","diagnose-client","diagnose-runtime"]) $("#"+id).addEventListener("change",()=>{$("#diagnose-result").hidden=true;});
  $("#service-search").addEventListener("input",renderServices);
  $("#show-all-services").addEventListener("click",()=>{
    if($("#service-search").value.trim()||state.showCustomized) {$("#service-search").value="";state.showCustomized=false;state.showAll=true;}
    else state.showAll=!state.showAll;
    renderServices();
  });
  $("#filter-customized-services").addEventListener("click",()=>{state.showCustomized=!state.showCustomized;renderServices();});
  $("#clear-service-filter").addEventListener("click",()=>{$("#service-search").value="";state.showCustomized=false;renderServices();focusControl($("#service-search"));});
  $("#service-route-list").addEventListener("change",event=>{
    const card=event.target.closest('[data-service]');if(!card||state.busy) return;
    const r=state.serviceChoices[card.dataset.service]||{mode:"default",egress:"",fallback:""};
    if(event.target.matches('[data-mode]')) {r.mode=event.target.value;r.fallback="";}
    if(event.target.matches('[data-egress]')) r.egress=event.target.value;
    if(event.target.matches('[data-fallback]')) r.fallback=event.target.value;
    state.serviceChoices[card.dataset.service]=r;invalidate();
    if(event.target.matches('[data-mode]')) renderServices();
    else $(".service-route-summary",card).textContent=serviceRouteSummary(card.dataset.service,r);
  });
  $("#service-route-list").addEventListener("input",event=>{
    const card=event.target.closest('[data-service]'); if(!card) return;
    const r=state.serviceChoices[card.dataset.service];
    if(event.target.matches('input[data-egress]')) r.egress=event.target.value;
    else if(event.target.matches('input[data-fallback]')) r.fallback=event.target.value;
    else return;
    $(".service-route-summary",card).textContent=serviceRouteSummary(card.dataset.service,r);
    invalidate();
  });
  $("#upgrade-result").addEventListener("click",event=>{if(event.target.id==="apply-upgrade-button")applyUpgrade();});
  $("#publish-result").addEventListener("click",event=>{const b=event.target.closest('[data-copy-output]');if(b)return copyPublishedLink(b);});
  $("#leo-reference").addEventListener("click",event=>{const b=event.target.closest('[data-reference-service]');if(!b)return;state.showCustomized=false;$("#service-search").value=b.dataset.referenceService;renderServices();focusControl($(`[data-service="${b.dataset.referenceService}"] [data-mode]`),true);});
}
async function init() {
  bindEvents();
  updateSurgePreferenceVisibility();
  const results=await Promise.allSettled([
    jsonRequest(`/templates/detail?template=${encodeURIComponent(LEO_TEMPLATE)}`).then(body=>{state.leoGroups=body.proxy_groups||[];state.leoSummary=body.summary;state.publicData=body.public_data||[];}),
    jsonRequest("/templates/audit").then(body=>{state.leoAudit=body;}),
    jsonRequest("/services").then(body=>{state.servicePacks=body.services;for(const service of body.services) state.serviceChoices[service.id]??={mode:"default",egress:"",fallback:""};$("#diagnose-service").innerHTML=body.services.map(s=>`<option value="${escapeHtml(s.id)}"${s.id==="openai"?" selected":""}>${escapeHtml(s.label)}</option>`).join("");}),
    jsonRequest("/runtime/capabilities").then(body=>{const names=Object.keys(body).filter(k=>body[k]);$("#runtime-state").textContent=names.length?`已配置：${names.join(" / ")} · 尚未发起客户端实测`:"未连接客户端 · 可进行静态分流检查";}),loadHealth(),
  ]);
  if(results.slice(0,3).some(r=>r.status==="rejected")) setNotice("部分策略数据加载失败，请刷新页面后重试。");
  renderServices();renderDataLedger();renderLeoReference();updateActions();
}
init();
