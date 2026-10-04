import json
from pathlib import Path
import subprocess
import tomllib

from fastapi.testclient import TestClient

from app.main import app


def _run_flow_runtime(assertions: str) -> None:
    flow_path = Path(__file__).resolve().parents[1] / "app" / "static" / "flow.js"
    program = f"""
const fs = require("node:fs");
const vm = require("node:vm");
const targets = [
  {{value: "mihomo", checked: true}},
  {{value: "surge", checked: false}},
  {{value: "shadowrocket", checked: false}},
];
const elements = {{
  "#global-notice": {{textContent: "", hidden: true}},
  "#profile-name": {{value: ""}},
  "#subscription-url": {{value: "https://example.com/sub"}},
  "#surge-auto-test-row": {{hidden: true}},
  "#surge-auto-test-protocols": {{value: "all", disabled: true}},
  "#surge-auto-test-custom": {{hidden: true}},
}};
const controls = [elements["#surge-auto-test-protocols"]];
const document = {{
  querySelector: selector => elements[selector] || null,
  querySelectorAll: selector => {{
    if (selector === 'input[name="target"]:checked') return targets.filter(item => item.checked);
    if (selector === ".config-workbench input, .config-workbench select, .config-workbench button") return controls;
    return [];
  }},
  createElement: () => ({{textContent: "", get innerHTML() {{ return this.textContent.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;"); }} }}),
}};
const context = vm.createContext({{
  document,
  elements,
  targets,
  console,
  structuredClone,
  setTimeout,
  clearTimeout,
  URL,
  location: {{origin: "https://subflow.example"}},
}});
let source = fs.readFileSync({json.dumps(str(flow_path))}, "utf8");
source = source.replace("\\ninit();\\n", "\\n");
vm.runInContext(source, context);
const result = vm.runInContext({json.dumps(assertions)}, context);
Promise.resolve(result).catch(error => {{ console.error(error); process.exitCode = 1; }});
"""
    completed = subprocess.run(
        ["node", "-e", program],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_root_and_legacy_advanced_route_serve_the_same_simple_page() -> None:
    client = TestClient(app)

    root = client.get("/")
    advanced = client.get("/advanced")

    assert root.status_code == 200
    assert advanced.status_code == 200
    assert root.text == advanced.text
    assert "/static/flow.js?v=58" in root.text
    assert "/static/flow.css?v=57" in root.text
    assert "/static/assets/subflow-logo.png" in root.text


def test_release_metadata_agrees_between_app_package_and_image() -> None:
    root = Path(__file__).resolve().parents[1]
    page = TestClient(app).get("/").text
    schema = TestClient(app).get("/openapi.json").json()
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    version = project["project"]["version"]
    assert schema["info"]["version"] == version
    assert f"Subflow {version}" in page
    assert f'<span class="version-badge">{version}</span>' in page
    assert f'org.opencontainers.image.version="{version}"' in (
        root / "Dockerfile"
    ).read_text(encoding="utf-8")


def test_page_exposes_the_current_workbench_contract() -> None:
    response = TestClient(app).get("/")
    assert response.status_code == 200
    page = response.text
    controls = (
        "leo-reference", "data-ledger", "subscription-url", "validate-source-button",
        "service-route-list", "generate-button", "existing-profile-url", "open-profile-button",
        "check-button", "check-results", "preview-upgrade-button", "diagnose-service",
        "diagnose-runtime", "diagnose-result", "surge-auto-test-protocols",
        "filter-customized-services", "clear-service-filter", "summary-label",
        "summary-next-label", "summary-link",
        "source-next", "services-next",
    )
    assert all(f'id="{control}"' in page for control in controls)
    assert 'class="config-workbench"' in page
    for client in ("mihomo", "surge", "shadowrocket"):
        assert f'name="target" value="{client}"' in page
    for output in ("clash", "surge", "shadowrocket", "shadowrocket-config"):
        assert f'id="published-{output}-url"' in page
        assert f'data-copy-output="{output}"' in page
    assert all(text in page for text in ('value="all"', 'value="anytls"', "仅 AnyTLS", "配置 → 添加配置", "完整登录"))
    obsolete = ("policy-workbench", "context-inspector", "profiles-list", "policy-preset",
                "rule-pack-catalog", "community-rule-library", "advanced-routing")
    assert not any(f'id="{control}"' in page for control in obsolete)
    assert "专家编排" not in page and "模板策略矩阵" not in page


def test_workbench_preserves_custom_protocol_lists_until_user_changes_control() -> None:
    _run_flow_runtime(
        """
        (() => {
          targets[0].checked = false;
          targets[1].checked = true;
          restoreSurgePreferences({auto_test_protocols: ["ss", "future-protocol"]});
          if (elements["#surge-auto-test-protocols"].value !== "custom") {
            throw new Error("custom protocol list was not represented as custom");
          }
          const preserved = payload().surge_preferences.auto_test_protocols;
          if (JSON.stringify(preserved) !== JSON.stringify(["ss", "future-protocol"])) {
            throw new Error(`custom protocols changed: ${JSON.stringify(preserved)}`);
          }
          elements["#surge-auto-test-protocols"].value = "anytls";
          const changed = payload().surge_preferences.auto_test_protocols;
          if (JSON.stringify(changed) !== JSON.stringify(["anytls"])) {
            throw new Error(`explicit AnyTLS choice was not applied: ${JSON.stringify(changed)}`);
          }
        })()
        """
    )


def test_opening_surge_profile_reenables_preference_after_busy_cleanup() -> None:
    _run_flow_runtime(
        """
        (async () => {
          updateSurgePreferenceVisibility();
          if (!elements["#surge-auto-test-protocols"].disabled) {
            throw new Error("precondition: selector should start disabled");
          }
          renderServices = () => {};
          updateActions = () => {};
          setNotice = () => {};
          const button = {textContent: "打开"};
          await busy(button, "打开中…", async () => {
            targets[1].checked = true;
            restoreSurgePreferences({auto_test_protocols: ["anytls"]});
          });
          if (elements["#surge-auto-test-row"].hidden) {
            throw new Error("Surge preference row stayed hidden");
          }
          if (elements["#surge-auto-test-protocols"].disabled) {
            throw new Error("Surge preference selector stayed disabled");
          }
        })()
        """
    )


def test_opening_profile_keeps_surge_preference_locked_until_busy_finishes() -> None:
    _run_flow_runtime("""
    (async () => {
      renderServices = () => {}; updateActions = () => {}; setNotice = () => {};
      let focused = false, unlockedWhileBusy = false;
      const button = {textContent: "打开", classList: {add() {}, remove() {}}, focus() { focused = true; }};
      await busy(button, "打开中…", async () => {
        targets[1].checked = true;
        restoreSurgePreferences({auto_test_protocols: ["anytls"]});
        unlockedWhileBusy = !elements["#surge-auto-test-protocols"].disabled;
      });
      if (unlockedWhileBusy) throw Error("busy selector was reenabled");
      if (elements["#surge-auto-test-protocols"].disabled) throw Error("selector did not unlock");
      if (!focused) throw Error("action keyboard focus was lost");
    })()
    """)


def test_failed_source_read_clears_node_readiness_and_exposes_local_error() -> None:
    _run_flow_runtime("""
    (async () => {
      for (const id of ["#diagnose-result", "#check-results", "#publish-result", "#check-state", "#source-result"])
        elements[id] = {textContent: "", hidden: false};
      let focused = false;
      elements["#subscription-url"].focus = () => { focused = true; };
      renderServices = () => {}; updateActions = () => {};
      state.nodes = [{name: "Previous source node"}];
      globalThis.fetch = async () => ({ok: false, status: 500, json: async () => ({detail: "来源暂不可用"})});
      await busy({textContent: "读取节点"}, "读取中…", loadNodes);
      if (state.nodes.length) throw Error("old nodes survived failed read");
      if (elements["#check-state"].textContent !== "先读取订阅节点") throw Error("stale readiness message");
      if (elements["#source-result"].hidden || !elements["#source-result"].textContent.includes("来源暂不可用"))
        throw Error("source error was not shown beside source");
      if (!focused) throw Error("source correction target was not focused");
    })()
    """)


def test_successful_action_focuses_visible_result_but_errors_keep_error_focus() -> None:
    _run_flow_runtime("""
    (async () => {
      renderServices = () => {}; updateActions = () => {};
      let focused, scroll;
      globalThis.matchMedia = () => ({matches:true});
      const result = {hidden:false, getClientRects:()=>[{}], focus(){focused='result';},
        scrollIntoView(options){scroll=options;}};
      elements['#check-results'] = result;
      elements['#global-notice'].focus = () => {focused='error';};
      const button = {textContent:'检查', dataset:{successTarget:'#check-results'}, focus(){focused='button';}};
      await busy(button, '检查中…', async () => {});
      if(focused!=='result'||scroll.block!=='start'||scroll.behavior!=='auto') throw Error('visible result was not located');
      result.hidden=true;
      await busy(button, '检查中…', async () => {});
      if(focused!=='button') throw Error('hidden result received focus');
      result.hidden=false; scroll=null;
      await busy(button, '检查中…', async () => {throw Error('检查失败');});
      if(focused!=='error'||scroll) throw Error('failure jumped to old success result');
    })()
    """)


def test_workspace_summary_tracks_current_check_and_invalidated_draft() -> None:
    _run_flow_runtime("""
    (async () => {
      const makeElement = () => ({textContent:"", hidden:false, disabled:false});
      for(const id of ["summary-nodes","summary-clients","summary-routes","summary-state","workspace-progress",
        "refresh-publications-button","check-button","diagnose-button","generate-button","save-title","generate-hint",
        "diagnose-result","check-results","publish-result","check-state","source-result","summary-link","source-next","services-next"]) elements[`#${id}`]=makeElement();
      let isSaved=false, requests=0;
      elements['#workspace-summary']={classList:{toggle(name,on){if(name==='is-saved') isSaved=on;}}};
      const assertNext=(href,text,visible,saved=false)=>{
        const link=elements['#summary-link'];
        if(link.href!==href||!link.innerHTML.includes(text)) throw Error(`wrong next step: ${link.href}`);
        if(elements['#source-next'].hidden===visible||elements['#services-next'].hidden===visible) throw Error('section navigation bypassed readiness or busy state');
        if(isSaved!==saved) throw Error('saved styling disagrees with current draft');
      };
      const steps=["source","services","review"].map(step=>({dataset:{step}, classes:{}, attributes:{},
        classList:{toggle(name,on){this.owner.classes[name]=on;}},
        setAttribute(name,value){this.attributes[name]=value;}, removeAttribute(name){delete this.attributes[name];}}));
      steps.forEach(step=>step.classList.owner=step);
      const originalQuery=document.querySelectorAll;
      document.querySelectorAll=selector=>selector==='[data-step]'?steps:originalQuery(selector);
      updateActions();
      if(steps[0].attributes['aria-current']!=='step') throw Error('missing source step');
      assertNext('#step-source','前往读取来源',false);
      globalThis.fetch=async path=>{requests++;if(path!=='/preview') throw Error('navigation triggered a check or save');return {ok:true,json:async()=>({nodes:[{name:'US01'},{name:'JP01'}]})};};
      await loadNodes();
      state.serviceChoices={openai:{mode:'fixed',egress:'US01'},claude:{mode:'default'}};
      updateActions();
      if(!elements['#summary-nodes'].textContent.includes('2') || !elements['#summary-routes'].textContent.includes('1')) throw Error('summary not derived from draft');
      if(steps[1].attributes['aria-current']!=='step' || !steps[0].classes['is-complete']) throw Error('source readiness not reflected');
      assertNext('#step-services','继续设置出口',true);
      targets[0].checked=false; invalidate();
      assertNext('#step-source','前往读取来源',false);
      targets[0].checked=true; invalidate();
      state.busy=true; updateActions();
      assertNext('#step-services','继续设置出口',false);
      state.busy=false;
      state.check={can_publish:false}; state.checkedInput=JSON.stringify(payload()); updateActions();
      assertNext('#step-review','查看检查结果',true);
      if(!elements['#generate-button'].disabled) throw Error('blocking check enabled saving');
      state.check={can_publish:true}; state.checkedInput=JSON.stringify(payload()); updateActions();
      if(elements['#generate-button'].disabled || steps[2].attributes['aria-current']!=='step') throw Error('current passed check not ready');
      assertNext('#step-review','查看检查结果',true);
      state.savedInput=state.checkedInput; updateActions();
      assertNext('#publish-result','查看订阅链接',true,true);
      elements['#profile-name'].value='changed'; invalidate();
      if(!elements['#generate-button'].disabled || !elements['#check-results'].hidden || steps[1].attributes['aria-current']!=='step') throw Error('stale check remained current');
      assertNext('#step-services','继续设置出口',true);
      if(requests!==1) throw Error('state navigation started a request');
    })()
    """)


def test_saved_profile_disables_duplicate_writes_until_edited_and_checked_again() -> None:
    _run_flow_runtime("""
    (async () => {
      document.querySelector=selector=>elements[selector]||={value:'',textContent:'',hidden:false,disabled:false};
      showPublished=()=>{}; loadPublicationStatus=async()=>{}; showToast=()=>{}; renderCheck=()=>{}; renderServices=()=>{};
      state.nodes=[{name:'US01'}]; state.check={can_publish:true}; state.checkedInput=JSON.stringify(payload());
      const writes=[];
      globalThis.fetch=async (path,options)=>{
        if(path==='/check') return {ok:true,json:async()=>({can_publish:true})};
        writes.push({path,method:options.method});
        return {ok:true,json:async()=>({id:'saved',token:'session'})};
      };
      await saveProfile(); updateActions();
      if(elements['#generate-button'].textContent!=='已保存'||!elements['#generate-button'].disabled) throw Error('saved draft still invites a duplicate write');
      if(elements['#summary-label'].textContent!=='已保存配置'||elements['#summary-link'].href!=='#publish-result') throw Error('saved summary does not lead to output');
      if(!elements['#save-title'].textContent.includes('已保存')||!elements['#check-state'].textContent.includes('已保存')) throw Error('save section disagrees with saved summary');
      await saveProfile();
      if(writes.length!==1) throw Error('unchanged saved draft was written twice');
      elements['#profile-name'].value='Edited'; invalidate();
      let refused=false; try {await saveProfile();} catch {refused=true;}
      if(!refused||!elements['#generate-button'].disabled) throw Error('edit skipped recheck gate');
      await checkConfig(); updateActions();
      if(elements['#generate-button'].disabled) throw Error('checked edit cannot be saved');
      await saveProfile();
      if(writes.length!==2||writes[1].method!=='PUT') throw Error('checked edit did not update the existing profile');
      state.showCustomized=true; state.showAll=true; elements['#service-search']={value:'old search'};
      newProfile();
      if(state.savedInput||state.check) throw Error('new draft inherited saved or check state');
      if(state.showCustomized||state.showAll||elements['#service-search'].value) throw Error('new draft inherited a hidden service view');
    })()
    """)


def test_save_failure_is_not_confirmed_but_later_status_failure_keeps_save_confirmation() -> None:
    _run_flow_runtime("""
    (async () => {
      document.querySelector=selector=>elements[selector]||={value:'',textContent:'',hidden:false,disabled:false};
      showPublished=()=>{}; showToast=()=>{};
      state.nodes=[{name:'US01'}]; state.check={can_publish:true}; state.checkedInput=JSON.stringify(payload());
      globalThis.fetch=async()=>({ok:false,status:503,json:async()=>({detail:'保存不可用'})});
      let rejected=false; try {await saveProfile();} catch {rejected=true;}
      updateActions();
      if(!rejected||state.savedInput||elements['#generate-button'].disabled) throw Error('failed save was marked confirmed');
      globalThis.fetch=async()=>({ok:true,json:async()=>({id:'saved',token:'session'})});
      loadPublicationStatus=async()=>{throw Error('状态暂不可用');};
      await saveProfile(); updateActions();
      if(!elements['#generate-button'].disabled||elements['#generate-button'].textContent!=='已保存') throw Error('confirmed write was lost with status read failure');
      if(!elements['#publication-status'].textContent.includes('已保存')||!elements['#publication-status'].textContent.includes('暂不可用')) throw Error('partial success was not explained');
    })()
    """)


def test_view_filters_preserve_checked_payload_and_return_focus_when_card_disappears() -> None:
    _run_flow_runtime("""
    (() => {
      const makeElement=()=>({value:'',textContent:'',innerHTML:'',hidden:false,listeners:{},
        addEventListener(name,listener){this.listeners[name]=listener;},setAttribute(name,value){this[name]=value;},
        focus(){document.activeElement=this;},querySelector(){return null;}});
      for(const element of Object.values(elements)) {element.listeners={};element.addEventListener=function(name,listener){this.listeners[name]=listener;};}
      document.querySelector=selector=>elements[selector]||=makeElement();
      state.servicePacks=['openai','gemini','netflix'].map(id=>({id,label:id,group:id,default_target:'默认代理',rules:[]}));
      restoreChoices([{service:'netflix',mode:'fixed',egress:'US01'}]);
      state.check={can_publish:true}; state.checkedInput=JSON.stringify(payload());
      const key=state.checkedInput,epoch=state.epoch;
      updateActions=()=>{};
      bindEvents(); renderServices();
      const list=elements['#service-route-list'];
      elements['#filter-customized-services'].listeners.click();
      if(!list.innerHTML.includes('data-service="netflix"')||list.innerHTML.includes('data-service="openai"')) throw Error('customized filter included defaults');
      elements['#service-search'].value='missing'; elements['#service-search'].listeners.input();
      if(!elements['#show-all-services'].textContent.includes('清除搜索')) throw Error('show all concealed active search');
      elements['#show-all-services'].listeners.click();
      if(elements['#service-search'].value||!list.innerHTML.includes('data-service="openai"')||!list.innerHTML.includes('data-service="netflix"')) throw Error('show all failed to clear view filters');
      elements['#filter-customized-services'].listeners.click();
      elements['#clear-service-filter'].listeners.click();
      if(document.activeElement!==elements['#service-search']||elements['#filter-customized-services']['aria-pressed']!=='false') throw Error('clear filters lost focus or kept customization filter');
      if(JSON.stringify(payload())!==key||state.checkedInput!==key||state.epoch!==epoch) throw Error('view-only filtering invalidated configuration');
      elements['#filter-customized-services'].listeners.click();
      const card={dataset:{service:'netflix'}};
      const mode={value:'default',closest:()=>card,matches:selector=>selector==='[data-mode]'};
      document.activeElement=mode;
      list.listeners.change({target:mode});
      if(list.innerHTML.includes('data-service="netflix"')||document.activeElement!==elements['#filter-customized-services']) throw Error('hidden default card stranded keyboard focus');
    })()
    """)


def test_copy_button_confirms_success_locally_and_keeps_failure_honest() -> None:
    _run_flow_runtime("""
    (async () => {
      const makeElement=()=>({value:'',textContent:'',listeners:{},addEventListener(name,listener){this.listeners[name]=listener;}});
      for(const element of Object.values(elements)) {element.listeners={};element.addEventListener=function(name,listener){this.listeners[name]=listener;};}
      document.querySelector=selector=>elements[selector]||=makeElement();
      let restore, copied, focused, copiedClass=false;
      globalThis.setTimeout=callback=>{restore=callback;return 1;}; globalThis.clearTimeout=()=>{};
      globalThis.navigator={clipboard:{writeText:async value=>{copied=value;}}};
      showToast=()=>{};
      const button={dataset:{copyOutput:'clash'},textContent:'复制',focus(){focused=this;},
        classList:{add(){copiedClass=true;},remove(){copiedClass=false;}}};
      const input={value:'https://subflow.example/subscribe/synthetic',focus(){},select(){},blur(){}};
      elements['#published-clash-url']=input;
      bindEvents();
      const event={target:{closest:()=>button}};
      await elements['#publish-result'].listeners.click(event);
      if(copied!==input.value||button.textContent!=='已复制'||!copiedClass||focused!==button) throw Error('copy success was not visible beside the link');
      restore();
      if(button.textContent!=='复制'||copiedClass) throw Error('temporary feedback did not restore the button');
      globalThis.navigator.clipboard.writeText=async()=>{throw Error('clipboard unavailable');};
      document.execCommand=()=>false;
      await elements['#publish-result'].listeners.click(event);
      if(button.textContent==='已复制'||copiedClass||focused!==button) throw Error('copy failure claimed success or lost button focus');
    })()
    """)


def test_service_cards_prioritize_ai_and_keep_field_focus_selection_and_search_count() -> None:
    _run_flow_runtime("""
    (() => {
      const replacement={focus(){document.activeElement=this;},setSelectionRange(...range){this.range=range;}};
      const card={dataset:{service:'openai'}};
      document.activeElement={closest:()=>card,matches:selector=>selector==='[data-egress]',selectionStart:1,selectionEnd:3,selectionDirection:'forward'};
      const root={html:'',get innerHTML(){return this.html;},set innerHTML(html){this.html=html;document.activeElement=null;},
        querySelector:selector=>selector.includes('openai')&&selector.includes('data-egress')?replacement:null};
      elements['#service-route-list']=root;
      elements['#service-search']={value:''}; elements['#node-options']={innerHTML:''};
      elements['#service-search-status']={textContent:''};
      elements['#show-all-services']={textContent:'',setAttribute(name,value){this[name]=value;}};
      state.nodes=[{name:'US01'}];
      state.servicePacks=['netflix','youtube','claude','gemini','openai','github'].map(id=>({id,label:id,group:id,default_target:'默认代理',rules:[]}));
      state.servicePacks.find(s=>s.id==='openai').category='ai';
      state.servicePacks.find(s=>s.id==='claude').category='future-category';
      state.serviceCategories.find(c=>c.id==='ai').label='AI <工具>';
      restoreChoices([{service:'openai',mode:'fixed',egress:'US01'}]);
      renderServices();
      const expected=['openai','claude','gemini','github','youtube'];
      const positions=expected.map(id=>root.innerHTML.indexOf(`data-service="${id}"`));
      if(positions.some((position,index)=>position<0||(index&&position<positions[index-1]))) throw Error('primary service order changed');
      if(root.innerHTML.includes('data-service="netflix"')) throw Error('collapsed view showed unrelated default');
      if(!root.innerHTML.includes('class="service-category-label">AI &lt;工具&gt;</span>')||root.innerHTML.includes('AI <工具>')) throw Error('category label missing or unescaped');
      if((root.innerHTML.match(/class="service-category-label">其他服务/g)||[]).length!==4) throw Error('unknown or missing category did not fall back');
      if(!root.innerHTML.startsWith('<article ')||root.innerHTML.includes('</article><section')) throw Error('category introduced grouping instead of flat cards');
      if(document.activeElement!==replacement || JSON.stringify(replacement.range)!=='[1,3,"forward"]') throw Error('service field focus/selection lost');
      if(!elements['#service-search-status'].textContent.includes('5')) throw Error('visible service count missing');
      state.showAll=true; renderServices();
      if(elements['#show-all-services']['aria-expanded']!=='true'||!root.innerHTML.includes('data-service="netflix"')) throw Error('expanded state missing');
      elements['#service-search'].value='not-a-service'; renderServices();
      if(!root.innerHTML.includes('没有匹配')||!elements['#service-search-status'].textContent.includes('0')) throw Error('search empty state missing');
    })()
    """)


def test_notice_focus_and_scroll_respect_reduced_motion_without_empty_notice_jumps() -> None:
    _run_flow_runtime("""
    (() => {
      const notice=elements['#global-notice']; let focused=0, behavior;
      notice.focus=()=>{focused++;}; notice.scrollIntoView=options=>{behavior=options.behavior;};
      globalThis.matchMedia=()=>({matches:true});
      setNotice(''); if(focused) throw Error('empty notice moved focus');
      setNotice('请修正配置');
      if(focused!==1||behavior!=='auto'||notice.hidden) throw Error('error not accessible with reduced motion');
    })()
    """)


def test_committing_primary_node_keeps_backup_input_available_for_next_click() -> None:
    _run_flow_runtime("""
    (() => {
      const makeElement=()=>({value:'',textContent:'',listeners:{},addEventListener(name,listener){this.listeners[name]=listener;}});
      for(const element of Object.values(elements)) {element.listeners={};element.addEventListener=function(name,listener){this.listeners[name]=listener;};}
      document.querySelector=selector=>elements[selector]||=makeElement();
      const summary={textContent:''};
      const card={dataset:{service:'gemini'},querySelector:()=>summary};
      const field=(name,value)=>({value,connected:true,closest:()=>card,matches:selector=>selector===`[data-${name}]`||selector===`input[data-${name}]`});
      const primary=field('egress','美国 US02'), backup=field('fallback','');
      // A blur change can run while activeElement is BODY, before the next input receives focus.
      document.activeElement={};
      renderServices=()=>{primary.connected=false;backup.connected=false;};
      updateActions=()=>{};
      state.servicePacks=[{id:'gemini',default_target:'AI 服务'}];
      restoreChoices([{service:'gemini',mode:'fallback',egress:'',fallback:''}]);
      bindEvents();
      const list=elements['#service-route-list'];
      list.listeners.input({target:primary});
      list.listeners.change({target:primary});
      if(!backup.connected) throw Error('primary blur removed the pending backup click target');
      backup.value='日本 JP01';
      list.listeners.input({target:backup});
      list.listeners.change({target:backup});
      const route=payload().service_routes[0];
      if(route.egress!=='美国 US02'||route.fallback!=='日本 JP01') throw Error('consecutive node edits lost an exit');
      if(!summary.textContent.includes('美国 US02')||!summary.textContent.includes('日本 JP01')) throw Error('route summary did not follow editing');
    })()
    """)


def test_late_service_catalog_keeps_profile_routes_opened_during_startup() -> None:
    _run_flow_runtime(
        """
        (async () => {
          const makeElement = () => ({
            value: "", textContent: "", innerHTML: "", hidden: false, disabled: false,
            listeners: {}, classList: {toggle() {}, add() {}, remove() {}},
            setAttribute(name, value) { this[name] = value; },
            addEventListener(name, listener) { this.listeners[name] = listener; },
          });
          for (const element of Object.values(elements)) {
            element.listeners = {};
            element.addEventListener = function (name, listener) { this.listeners[name] = listener; };
          }
          document.querySelector = selector => elements[selector] ||= makeElement();
          document.querySelectorAll = selector => {
            if (selector === 'input[name="target"]') return targets;
            if (selector === 'input[name="target"]:checked') return targets.filter(item => item.checked);
            if (selector === ".config-workbench input, .config-workbench select, .config-workbench button") return [elements["#surge-auto-test-protocols"]];
            return [];
          };
          targets.forEach(target => target.addEventListener = () => {});
          let finishServices;
          const services = new Promise(resolve => { finishServices = resolve; });
          const savedRoute = {service: "openai", mode: "fixed", egress: "US01"};
          globalThis.fetch = async path => {
            let body;
            if (path === "/services") body = services;
            else if (path.startsWith("/profiles/p/draft")) body = {
              mode: "service_routes", generation: 1, publications: {},
              request: {
                subscription_url: "https://example.com/sub", profile_name: "Saved",
                publication_targets: ["mihomo"], service_routes: [savedRoute],
              },
            };
            else if (path === "/preview") body = {nodes: [{name: "US01"}]};
            else if (path.startsWith("/templates/detail")) body = {proxy_groups: [], summary: {}, public_data: []};
            else if (path === "/templates/audit") body = {summary: {}};
            else if (path === "/system/status") body = {app: {status: "ok"}, profile_db: {status: "ok"}};
            else body = {};
            return {ok: true, json: async () => body};
          };
          elements["#existing-profile-url"] = makeElement();
          elements["#existing-profile-url"].value = "https://subflow.example/subscribe/p?token=t";
          const boot = init();
          await Promise.resolve();
          state.savedInput = 'previously saved'; state.check = {can_publish: true};
          state.checkedInput = 'previously saved'; state.showCustomized = true; state.showAll = true;
          elements['#service-search'].value = 'old search';
          const button = elements["#open-profile-button"];
          await button.listeners.click({currentTarget: button});
          finishServices({services: [{id: "openai", label: "OpenAI", group: "OpenAI", default_target: "默认代理", rules: []}]});
          await boot;
          const routes = payload().service_routes;
          if (routes.length !== 1 || routes[0].service !== "openai" || routes[0].egress !== "US01") {
            throw new Error(`opened Profile route was lost after catalog load: ${JSON.stringify(routes)}`);
          }
          if (state.savedInput || state.check || state.checkedInput) throw Error('opened Profile inherited compile or save confirmation');
          if (state.showCustomized || state.showAll || elements['#service-search'].value) throw Error('opened Profile inherited a hidden service view');
        })()
        """
    )


def test_system_status_reports_ready_dependencies(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    client = TestClient(app)

    response = client.get("/system/status")

    assert response.status_code == 200
    assert response.json()["app"]["status"] == "ok"
    assert response.json()["profile_db"]["status"] == "ok"
    assert "subconverter" not in response.json()


def test_diagnosis_evidence_keeps_unknown_identity_and_escapes_client_text():
    _run_flow_runtime('''
const html = renderRuntimeEvidence({identity: {client: "surge", version: "<script>bad</script>", platform: "macos", mode: "rule", observed_at: "2026-10-03T00:00:00Z"}, evidence: [
  {kind: "rule_match", status: "partial", reason: "<img onerror=bad>"},
  {kind: "configuration_identity", status: "unknown", reason: "无法核对"},
]});
if (!html.includes("配置一致性") || !html.includes("未知") || !html.includes("服务器配置的实例")) throw Error(html);
if (html.includes("<script>") || html.includes("<img")) throw Error("unescaped client response");
if (!html.includes("&lt;script&gt;") || !html.includes("&lt;img")) throw Error("missing evidence");
if (renderRuntimeEvidence({status: "not_tested"}).includes("已观测")) throw Error("unrequested evidence must stay absent");
''')


def test_diagnose_collapses_only_details_and_keeps_uncertainty_and_failures_visible():
    _run_flow_runtime(r'''
(async () => {
  elements['#diagnose-service']={value:'openai'};
  elements['#diagnose-client']={value:'surge'};
  elements['#diagnose-runtime']={checked:true};
  elements['#diagnose-result']={hidden:true,innerHTML:''};
  const report={
    service:{label:'AI <service>',status:'needs_review',domains:[
      {domain:'chat.example',path:['AI','US01'],status:'matched'},
      {domain:'<domain>',path:['<node>'],status:'runtime_rules_required'},
    ],evidence:{observed_at:'2026-10-05T00:00:00Z',reasons:['earlier_rule_requires_runtime']}},
    warnings:[{message:'不要隐藏 <warning>'}],
    runtime:{actual_node:null,message:'未完整验证 <runtime>',service_tested:false,
      identity:{client:'surge',version:'test',platform:'unknown',mode:'unknown',observed_at:'2026-10-05T00:00:00Z'},
      evidence:[{kind:'rule_match',status:'partial',reason:'部分证据 <partial>'},{kind:'configuration_identity',status:'unknown',reason:'未知证据'}],
      domain_routes:[{domain:'chat.example',actual_node:'US01',rule:'DOMAIN,<rule>'},{domain:'<runtime-domain>',actual_node:null,rule:null}],
      probes:[{status:'failed',url:'https://probe.example',sample_count:1,failure_rate:1}],
    },
  };
  let request;
  globalThis.fetch=async(path,options)=>{if(path!=='/diagnose') throw Error('wrong endpoint');request=JSON.parse(options.body);return {ok:true,json:async()=>report};};
  for(const consistent of [undefined,false,null,'true',true]) {
    report.runtime.consistent_exit=consistent;
    await diagnose();
    const html=elements['#diagnose-result'].innerHTML;
    const configTag=html.match(/<details\b[^>]*id="diagnostic-config-paths"[^>]*>/)?.[0];
    const runtimeTag=html.match(/<details\b[^>]*id="diagnostic-runtime-paths"[^>]*>/)?.[0];
    if(!configTag||/\bopen(?:[\s=>])/.test(configTag)) throw Error('configuration detail should start closed');
    if(!runtimeTag||/\bopen(?:[\s=>])/.test(runtimeTag)!==(consistent!==true)) throw Error('uncertain runtime detail was hidden');
    if(!html.includes('diagnosis-summary-heading')||!html.includes('配置路径 · 2 个域名 · 1 个需运行态核实（不是客户端实时选择）')) throw Error('domain coverage and uncertainty count missing');
    if(!html.includes('客户端规则解释 · 2 个域名')) throw Error('runtime coverage count missing');
    const visible=html.replace(/<details\b[\s\S]*?<\/details>/g,'');
    for(const text of ['不要隐藏 &lt;warning&gt;','证据有限','未知证据','前置规则需要运行态数据','探测未通过','仅检查通用连通性'])
      if(!visible.includes(text)) throw Error(`evidence hidden by detail: ${text}`);
    if(html.includes('<service>')||html.includes('<domain>')||html.includes('<node>')||html.includes('<rule>')||html.includes('<runtime-domain>')) throw Error('diagnosis text was not escaped');
    if(html.includes('实际连通')||html.includes('服务可访问')) throw Error('configuration evidence became connectivity proof');
    if(request.runtime!==true||request.samples!==3||request.client!=='surge') throw Error('diagnostic request changed');
  }
  elements['#diagnose-runtime'].checked=false;
  report.service.domains.forEach(d=>{d.status='matched';}); report.service.status='consistent';
  report.runtime={status:'not_tested',actual_node:null,message:'尚未请求客户端实测。'};
  await diagnose();
  const html=elements['#diagnose-result'].innerHTML;
  if(html.includes('0 个需运行态核实')||html.includes('diagnostic-runtime-paths')) throw Error('absent runtime evidence was invented');
  if(!html.includes('配置路径 · 2 个域名（不是客户端实时选择）')||!html.includes('尚未请求客户端实测')) throw Error('static-only boundary missing');
  if(request.runtime!==false||request.samples!==1) throw Error('static diagnosis began probing');
})()
''')


def test_dependency_counts_are_inventory_not_download_validation():
    _run_flow_runtime('''
const html = renderDependencySummary({summary: {total: 24, remote: 23, geodata: 1, unresolved: 1}});
if (!html.includes("23") || !html.includes("1 项地址未解析") || !html.includes("未下载审计")) throw Error(html);
if (renderDependencySummary(null) !== "") throw Error("old reports should still render");
''')
