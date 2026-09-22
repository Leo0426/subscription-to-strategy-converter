# Surge iOS Policy Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce safer, smaller Surge iOS automatic-test groups, honest iOS rule compatibility warnings, and redacted native-profile security findings without rewriting source-owned connectivity.

**Architecture:** Persist explicit Surge-only preferences on `ConvertRequest`, materialize them through a named build result, and keep membership filtering in the template-policy layer before the existing Surge compiler. Share one iOS capability constant between analysis and compilation, and keep native profile auditing in a focused read-only module whose warnings join existing compile warnings.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, ruamel.yaml, pytest, vanilla HTML/CSS/JavaScript, Git worktrees.

**Spec:** `docs/superpowers/specs/2026-09-22-surge-ios-policy-optimization-design.md`

## Global Constraints

- Native Surge `[General]`, `[Host]`, `[Proxy]`, and auxiliary sections remain source-owned and are not rewritten.
- Missing or empty `surge_preferences.auto_test_protocols` preserves legacy automatic-group membership.
- Protocol filtering applies only to generated Surge `url-test` group node members; manual selectors and explicit service fallback groups retain their members.
- The current `surge` target means Surge iOS; `PROCESS-NAME` is unsupported and must not be emitted.
- Audit warnings never include option values, resolver URLs, credentials, subscription tokens, or proxy passwords.
- AI egress remains manual or an explicit two-node `ServiceRoute` fallback; no broad latency-selected AI group is introduced.
- No new runtime dependency or remote RuleProvider is added.
- Both canonical latency groups use an explicit 100 ms tolerance.

## Review Focus

- A `url-test` group containing both node names and a nested group must filter only disallowed nodes and retain the nested group reference; Task 2 pins this.
- A protocol filter matching no nodes must close the empty group and parent references without synthesizing `DIRECT`; Task 2 pins this.
- A multi-target check whose base target is Mihomo must filter only the independently built Surge artifact; Task 3 pins this.
- Legacy Profile JSON without `surge_preferences` must validate and render with all automatic members; Task 3 pins this.
- Native settings containing mixed case, whitespace, and secret-bearing URLs must still be detected without echoing any value; Task 5 pins this.

---

## File structure

- Create `app/models/surge.py`: Surge-only persisted preference model.
- Create `app/core/platforms/surge_capabilities.py`: one public Surge iOS rule capability set.
- Create `app/core/platforms/surge_audit.py`: read-only, redacted native-profile security audit.
- Modify `app/models/request.py`: attach `SurgePreferences` to `ConvertRequest`.
- Modify `app/core/template_engine.py`: apply protocol constraints to materialized automatic groups and close the graph.
- Modify `app/api/convert.py`: carry policy diagnostics in `BuildResult`, build target-specific checks, and combine warnings.
- Modify `app/core/platforms/surge.py`: consume iOS capabilities, count skipped rules, and append audit warnings.
- Modify `app/core/template_policy_transform.py`: consume the same iOS capability set.
- Modify `community_templates/leo/leo.yaml`: set automatic-group tolerance to 100.
- Modify `app/static/index.html`, `app/static/flow.js`, `app/static/flow.css`: expose and persist the Surge automatic-test selector.
- Modify `community_templates/leo/README.md`, `README.md`, and `CONTEXT.md`: document the preference, iOS capability, and warning boundary.
- Modify targeted tests under `tests/`: prove each behavior before implementation.

### Task 1: Surge iOS capability truth and canonical tolerance

**Files:**
- Create: `app/core/platforms/surge_capabilities.py`
- Modify: `app/core/platforms/surge.py`
- Modify: `app/core/platforms/surge.py` warning assembly through `app/core/platforms/ini.py`
- Modify: `app/core/template_policy_transform.py`
- Modify: `community_templates/leo/leo.yaml`
- Test: `tests/test_surge.py`
- Test: `tests/test_leo_template.py`
- Test: `tests/test_claude_template_transform.py`

**Interfaces:**
- Consumes: existing rule strings and `build_ini_config(nodes, proxy_groups, rules, rule_providers, *, dialect, excluded_node_names=None)` warning flow.
- Produces: `SURGE_IOS_RULE_TYPES: frozenset[str]`; `unsupported_rule_types` warning with `count`, `rule_count`, `types`.

- [ ] **Step 1: Write failing Surge iOS rule tests**

Add to `tests/test_surge.py`:

```python
def test_process_name_is_dropped_for_surge_ios_and_counted() -> None:
    conf, warnings = build_surge_config(
        [],
        [],
        [
            "PROCESS-NAME,com.ss.android.ugc.aweme,默认代理",
            "PROCESS-NAME,com.xingin.xhs,社交通讯",
            "PROCESS-NAME,tv.danmaku.bili,流媒体",
            "MATCH,DIRECT",
        ],
        {},
    )

    assert "PROCESS-NAME" not in conf
    assert conf.count("FINAL,") == 1
    assert warnings == [{
        "code": "unsupported_rule_types",
        "count": 1,
        "rule_count": 3,
        "types": ["PROCESS-NAME"],
        "suggestion": "Surge 不支持这些 Mihomo 规则类型，已跳过对应规则",
    }]
```

Update the existing `DOMAIN-REGEX` warning expectation to include `rule_count: 1`.

- [ ] **Step 2: Write failing shared-capability and tolerance tests**

Add assertions that both template groups use 100 and that template analysis rejects `PROCESS-NAME` for Surge:

```python
def test_leo_latency_groups_use_surge_default_tolerance() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    assert _group(template, "自动选择")["tolerance"] == 100
    assert _group(template, "香港自动")["tolerance"] == 100
```

In `tests/test_claude_template_transform.py`, add:

```python
def test_claude_capability_rejects_mac_only_process_rules_for_surge_ios() -> None:
    config = {
        "proxy-groups": [{"name": "Claude", "type": "select", "proxies": ["DIRECT"]}],
        "rule-providers": {},
        "rules": ["PROCESS-NAME,Claude,Claude"],
    }
    capability = analyze_claude_template(config)
    assert capability.surge_compatible is False
    assert any("PROCESS-NAME" in reason for reason in capability.surge_incompatibility_reasons)
```

- [ ] **Step 3: Run the tests and observe RED**

Run:

```bash
uv run pytest -q \
  tests/test_surge.py::test_process_name_is_dropped_for_surge_ios_and_counted \
  tests/test_leo_template.py::test_leo_latency_groups_use_surge_default_tolerance \
  tests/test_claude_template_transform.py
```

Expected: the process rule is still emitted, tolerance is 150, and capability analysis still accepts `PROCESS-NAME`.

- [ ] **Step 4: Add the shared capability and skipped-rule count**

Create `app/core/platforms/surge_capabilities.py`:

```python
SURGE_IOS_RULE_TYPES: frozenset[str] = frozenset({
    "DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD",
    "IP-CIDR", "IP-CIDR6", "GEOIP",
    "USER-AGENT", "URL-REGEX", "DEST-PORT", "RULE-SET", "MATCH", "FINAL",
})
```

Import the constant in both consumers. In `build_ini_config`, retain the occurrence list as well as the deduplicated type list and emit `rule_count=len(unsupported_rule_types)`. Keep the existing user-facing dialect name `Surge`; the shared capability set, not display copy, defines the iOS contract.

Set both `tolerance` values in `leo.yaml` to `100`.

- [ ] **Step 5: Run targeted tests GREEN**

Run:

```bash
uv run pytest -q tests/test_surge.py tests/test_leo_template.py tests/test_claude_template_transform.py
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add app/core/platforms/surge_capabilities.py app/core/platforms/surge.py \
  app/core/platforms/ini.py app/core/template_policy_transform.py \
  community_templates/leo/leo.yaml tests/test_surge.py tests/test_leo_template.py \
  tests/test_claude_template_transform.py
git commit -m "fix: enforce Surge iOS rule capabilities"
```

### Task 2: Protocol-aware automatic-group policy

**Files:**
- Create: `app/models/surge.py`
- Modify: `app/models/request.py`
- Modify: `app/core/template_engine.py`
- Test: `tests/test_surge_policy.py`
- Test: `tests/test_leo_template.py`

**Interfaces:**
- Consumes: `ProxyNode.protocol`, materialized `config["proxy-groups"]`, existing group-closure helpers.
- Produces: `SurgePreferences(auto_test_protocols: list[str])`; `filter_auto_test_protocols(config: dict, nodes: list[ProxyNode], protocols: list[str]) -> list[dict]`.

- [ ] **Step 1: Write failing model tests**

Create `tests/test_surge_policy.py` with:

```python
from app.models.request import ConvertRequest


def test_surge_protocol_preferences_are_normalized_and_deduplicated() -> None:
    request = ConvertRequest.model_validate({
        "subscription_url": "https://example.com/sub",
        "surge_preferences": {"auto_test_protocols": [" AnyTLS ", "anytls", "SS"]},
    })
    assert request.surge_preferences.auto_test_protocols == ["anytls", "ss"]


def test_legacy_request_defaults_to_no_surge_filter() -> None:
    request = ConvertRequest(subscription_url="https://example.com/sub")
    assert request.surge_preferences.auto_test_protocols == []
```

- [ ] **Step 2: Write failing 144-node membership tests**

Build the exact fixture required by the spec:

```python
def node(name: str, protocol: str) -> ProxyNode:
    return ProxyNode(
        name=name,
        protocol=protocol,
        server=f"{name.replace(' ', '-').lower()}.example.com",
        port=443,
        extra={"password": "test"} if protocol == "anytls" else {},
    )


def group(config: dict, name: str) -> dict:
    return next(item for item in config["proxy-groups"] if item["name"] == name)


def mixed_nodes() -> list[ProxyNode]:
    nodes = [node(f"香港 SS {i:02}", "ss") for i in range(1, 16)]
    nodes += [node(f"其他 SS {i:02}", "ss") for i in range(1, 69)]
    nodes += [node(f"香港 AnyTLS {i:02}", "anytls") for i in range(1, 17)]
    nodes += [node(f"美国 AnyTLS {i:02}", "anytls") for i in range(1, 46)]
    assert len(nodes) == 144
    return nodes
```

Apply the Leo template, invoke the new filter with `['anytls']`, then assert:

```python
assert len(group(config, "自动选择")["proxies"]) == 61
assert len(group(config, "香港自动")["proxies"]) == 16
assert len(group(config, "手动选择")["proxies"]) == 144
assert diagnostics == [
    {"code": "auto_test_protocol_filter", "group": "自动选择", "protocols": ["anytls"], "before": 144, "after": 61},
    {"code": "auto_test_protocol_filter", "group": "香港自动", "protocols": ["anytls"], "before": 31, "after": 16},
]
```

- [ ] **Step 3: Write failing review-focus tests**

Add two focused cases:

```python
def test_filter_keeps_nested_groups_and_filters_only_node_members() -> None:
    config = {"proxy-groups": [
        {"name": "Nested", "type": "select", "proxies": ["SS"]},
        {"name": "Auto", "type": "url-test", "proxies": ["SS", "TLS", "Nested"]},
    ], "rules": ["MATCH,Auto"]}
    diagnostics = filter_auto_test_protocols(config, [node("SS", "ss"), node("TLS", "anytls")], ["anytls"])
    assert group(config, "Auto")["proxies"] == ["TLS", "Nested"]
    assert diagnostics[0]["before"] == 2
    assert diagnostics[0]["after"] == 1


def test_empty_filtered_group_is_closed_without_direct_synthesis() -> None:
    config = {"proxy-groups": [
        {"name": "Auto", "type": "url-test", "proxies": ["SS"]},
        {"name": "Default", "type": "select", "proxies": ["Auto", "SS"]},
    ], "rules": ["MATCH,Default"]}
    filter_auto_test_protocols(config, [node("SS", "ss")], ["anytls"])
    assert "Auto" not in {item["name"] for item in config["proxy-groups"]}
    assert group(config, "Default")["proxies"] == ["SS"]
    assert all("DIRECT" not in item.get("proxies", []) for item in config["proxy-groups"])
```

- [ ] **Step 4: Run the new test module RED**

Run: `uv run pytest -q tests/test_surge_policy.py`

Expected: import failures for `SurgePreferences` and `filter_auto_test_protocols`.

- [ ] **Step 5: Implement the preference model and filter**

Create `app/models/surge.py`:

```python
from pydantic import BaseModel, ConfigDict, Field, field_validator


class SurgePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_test_protocols: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("auto_test_protocols")
    @classmethod
    def normalize_protocols(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip().lower() for value in values if value.strip()))
```

Attach it to `ConvertRequest` with `Field(default_factory=SurgePreferences)`.

In `template_engine.py`, add the public filter. Count only node members in `before` and `after`, retain non-node members, update the group list, call existing closure and missing-target helpers, and return diagnostics in template order. If `protocols` is empty, return `[]` without mutation.

- [ ] **Step 6: Run Task 2 tests GREEN**

Run:

```bash
uv run pytest -q tests/test_surge_policy.py tests/test_leo_template.py tests/test_convert_api.py
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit Task 2**

```bash
git add app/models/surge.py app/models/request.py app/core/template_engine.py \
  tests/test_surge_policy.py tests/test_leo_template.py tests/test_convert_api.py
git commit -m "feat: filter Surge automatic groups by protocol"
```

### Task 3: Target-specific build diagnostics and Profile persistence

**Files:**
- Modify: `app/api/convert.py`
- Modify: `tests/test_workbench_v5.py`
- Modify: `tests/test_profiles.py`
- Test: `tests/test_surge_policy.py`

**Interfaces:**
- Consumes: `SurgePreferences`; `filter_auto_test_protocols(config: dict, nodes: list[ProxyNode], protocols: list[str]) -> list[dict]`.
- Produces: `BuildResult(nodes: list[ProxyNode], config: dict, source_config: dict, warnings: list[dict])`; target-specific `/check` warnings and persisted request JSON.

- [ ] **Step 1: Write failing render and multi-target check tests**

In `tests/test_surge_policy.py`, monkeypatch a source containing the 144-node fixture and post:

```python
payload = {
    "subscription_url": "https://example.com/sub",
    "publication_targets": ["mihomo", "surge"],
    "target": "mihomo",
    "surge_preferences": {"auto_test_protocols": ["anytls"]},
}
```

Assert the Mihomo render still has 144 members in `自动选择`; render the same request with `target="surge"` and assert 61 automatic, 16 Hong Kong automatic, and 144 manual members. Assert `/check` reports `auto_test_protocol_filter` only on the Surge client and includes both group counts.

- [ ] **Step 2: Write failing legacy-Profile and persistence tests**

Add to `tests/test_profiles.py`:

```python
def test_profile_persists_surge_preferences_across_draft_update_and_refresh(client):
    original = request(
        target="surge",
        publication_targets=["surge"],
        surge_preferences={"auto_test_protocols": ["anytls"]},
    )
    created = client.post("/profiles", json=original).json()
    path = f"/profiles/{created['id']}"
    params = {"token": created["token"]}
    draft = client.get(path + "/draft", params=params).json()["request"]
    assert draft["surge_preferences"] == {"auto_test_protocols": ["anytls"]}

    edited = {**original, "profile_name": "NR AnyTLS"}
    assert client.put(path, params=params, json=edited).status_code == 200
    updated = client.get(path + "/draft", params=params).json()["request"]
    assert updated["surge_preferences"] == {"auto_test_protocols": ["anytls"]}
    refreshed = client.get(created["subscribe_urls"]["surge"] + "&force_refresh=true")
    assert refreshed.status_code == 200


def test_legacy_request_without_surge_preferences_keeps_all_auto_members(client):
    response = client.post("/render", json=request(target="surge"))
    assert response.status_code == 200
    auto = next(line for line in response.text.splitlines() if line.startswith("自动选择 ="))
    assert "TW01" in auto
    assert "US01" in auto
```

Extend `test_legacy_upgrade_is_preview_only_and_preserves_link_on_explicit_save` so the legacy source request carries the preference and the upgrade candidate retains it.

- [ ] **Step 3: Run target tests RED**

Run:

```bash
uv run pytest -q tests/test_surge_policy.py tests/test_profiles.py tests/test_workbench_v5.py
```

Expected: Surge output is unfiltered and upgrade candidates discard the new field.

- [ ] **Step 4: Add `BuildResult` and target-specific application**

In `app/api/convert.py` add:

```python
@dataclass(slots=True)
class BuildResult:
    nodes: list[ProxyNode]
    config: dict
    source_config: dict
    warnings: list[dict]
```

Add `surge_preferences` to `RenderInputs.from_request`. In `_build_config`, after service transforms:

```python
warnings = []
if inputs.target == "surge":
    warnings.extend(filter_auto_test_protocols(
        config, nodes, inputs.surge_preferences.auto_test_protocols,
    ))
return BuildResult(nodes, config, raw_config, warnings)
```

Update every caller to use named fields. `_render_config` combines `result.warnings + compiler_warnings`. `/workspace/preview` exposes policy warnings through existing `compile_warnings` without inserting metadata into the client configuration.

In `_check_request`, call `_build_config(RenderInputs.from_request(request, target=target))` for every requested target. This preserves target-specific negotiation and ensures only Surge receives the filter. Do not reuse the base config.

Copy `surge_preferences=original.surge_preferences` into the upgrade-preview candidate request.

- [ ] **Step 5: Run Task 3 tests GREEN**

Run:

```bash
uv run pytest -q tests/test_surge_policy.py tests/test_profiles.py tests/test_workbench_v5.py tests/test_convert_api.py
```

Expected: all selected tests pass and warnings are target-local.

- [ ] **Step 6: Commit Task 3**

```bash
git add app/api/convert.py tests/test_surge_policy.py tests/test_profiles.py tests/test_workbench_v5.py tests/test_convert_api.py
git commit -m "feat: persist Surge publication preferences"
```

### Task 4: Workbench control for Surge automatic tests

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/flow.js`
- Modify: `app/static/flow.css`
- Modify: `tests/test_ui.py`

**Interfaces:**
- Consumes: `ConvertRequest.surge_preferences.auto_test_protocols` JSON shape.
- Produces: `#surge-auto-test-protocols` control and round-tripped payload state.

- [ ] **Step 1: Write failing UI surface tests**

Extend `tests/test_ui.py`:

```python
def test_page_exposes_surge_auto_test_protocol_preference() -> None:
    page = TestClient(app).get("/").text
    assert 'id="surge-auto-test-protocols"' in page
    assert 'value="all"' in page
    assert 'value="anytls"' in page
    assert "仅 AnyTLS" in page
```

Add source assertions that `flow.js` serializes `surge_preferences`, restores it from a draft, and hides/disables the row when Surge is not selected.

- [ ] **Step 2: Run UI test RED**

Run: `uv run pytest -q tests/test_ui.py`

Expected: the new control and JavaScript payload markers are absent.

- [ ] **Step 3: Implement the workbench control**

Add a compact field near client selection:

```html
<label class="field surge-preference" id="surge-auto-test-row">
  <span>Surge 自动测速节点</span>
  <select id="surge-auto-test-protocols">
    <option value="all">全部兼容节点</option>
    <option value="anytls">仅 AnyTLS</option>
  </select>
  <small>只影响自动测速组；手动选择仍保留全部节点。</small>
</label>
```

In `payload()` emit:

```javascript
surge_preferences: {
  auto_test_protocols: $("#surge-auto-test-protocols").value === "anytls" ? ["anytls"] : []
}
```

Restore from `body.request.surge_preferences?.auto_test_protocols`, invalidate checks on change, and update the row's hidden state whenever target checkboxes change. Preserve the preference in legacy payload merge order rather than overwriting the loaded request accidentally.

Bump the `flow.js` and `flow.css` asset query versions together.

- [ ] **Step 4: Validate JavaScript and tests GREEN**

Run:

```bash
node --check app/static/flow.js
uv run pytest -q tests/test_ui.py tests/test_workbench_v5.py
```

Expected: JavaScript exits 0 and all selected tests pass.

- [ ] **Step 5: Commit Task 4**

```bash
git add app/static/index.html app/static/flow.js app/static/flow.css tests/test_ui.py
git commit -m "feat: expose Surge probe protocol preference"
```

### Task 5: Redacted native Surge security audit

**Files:**
- Create: `app/core/platforms/surge_audit.py`
- Modify: `app/core/platforms/surge.py`
- Modify: `tests/test_surge_native_profile.py`
- Modify: `tests/test_surge.py`

**Interfaces:**
- Consumes: native source text and emitted `ProxyNode` values.
- Produces: `audit_native_surge_profile(source: str) -> list[dict]`; `tls_verification_warning(nodes: list[ProxyNode]) -> dict | None`.

- [ ] **Step 1: Write failing native audit tests**

Add a source fixture containing mixed-case keys and secret-bearing values:

```ini
[General]
Allow-WiFi-Access = true
doh-server = https://resolver.example/dns-query?token=private-token
loglevel = info
include-all-networks = true
include-apns = true
include-cellular-services = true
```

Assert the warning codes are exactly:

```python
{
    "wifi_proxy_access_without_auth",
    "legacy_surge_option",
    "surge_info_loglevel",
    "surge_full_tunnel_scope",
}
```

Serialize the warning list and assert it contains none of `private-token`, `resolver.example`, or the source URL. Also assert adding `wifi-access-http-auth = user:private-password` removes only the Wi-Fi warning and does not expose the password.

- [ ] **Step 2: Write failing TLS aggregation and preservation tests**

Create two insecure AnyTLS nodes and one verified TLS node; assert a single warning:

```python
assert warning == {
    "code": "insecure_tls_nodes",
    "count": 2,
    "suggestion": "2 个 TLS 节点关闭了服务器证书验证；仅在机场要求时保留，并确认节点来源可信",
}
```

Extend the native preservation test so all audited non-routing sections still equal their source sections after compilation.

- [ ] **Step 3: Write the failing end-to-end acceptance test**

In `tests/test_surge_policy.py`, create a saved Profile from a synthetic native Surge source containing 83 SS and 61 AnyTLS nodes plus the risky General settings. Fetch the Surge link and assert:

```python
def section(config: str, name: str) -> str:
    match = re.search(rf"(?ms)^\[{re.escape(name)}\]\n(.*?)(?=^\[|\Z)", config)
    assert match is not None
    return match.group(1).strip()


def member_count(config: str, name: str) -> int:
    line = next(item for item in section(config, "Proxy Group").splitlines() if item.startswith(name + " ="))
    parts = [part.strip() for part in line.split("=", 1)[1].split(",")]
    trailing_options = sum(part.startswith(("interval=", "tolerance=")) for part in parts[1:])
    return len(parts) - 1 - trailing_options


def warning_codes(response) -> set[str]:
    return {
        item["code"]
        for item in json.loads(response.headers.get("X-Compile-Warnings", "[]"))
    }


def native_source() -> str:
    proxies = [
        f"香港 SS {index:02} = ss, hk-ss-{index}.example.com, 443, "
        "encrypt-method=aes-128-gcm, password=test"
        for index in range(1, 16)
    ]
    proxies += [
        f"其他 SS {index:02} = ss, other-ss-{index}.example.com, 443, "
        "encrypt-method=aes-128-gcm, password=test"
        for index in range(1, 69)
    ]
    proxies += [
        f"香港 AnyTLS {index:02} = anytls, hk-any-{index}.example.com, 443, "
        "password=test, skip-cert-verify=true"
        for index in range(1, 17)
    ]
    proxies += [
        f"美国 AnyTLS {index:02} = anytls, us-any-{index}.example.com, 443, "
        "password=test, skip-cert-verify=true"
        for index in range(1, 46)
    ]
    return """[General]
allow-wifi-access = true
doh-server = https://resolver.example/dns-query?token=private-token
loglevel = info
include-all-networks = true
include-apns = true
include-cellular-services = true

[Proxy]
""" + "\n".join(proxies) + "\n"


def test_end_to_end_saved_surge_profile_filters_and_audits(tmp_path, monkeypatch) -> None:
    async def fetch(_url: str) -> str:
        return native_source()

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    with TestClient(app) as client:
        created = client.post("/profiles", json={
            "subscription_url": "https://example.com/sub",
            "target": "surge",
            "publication_targets": ["surge"],
            "surge_preferences": {"auto_test_protocols": ["anytls"]},
        }).json()
        response = client.get(created["subscribe_urls"]["surge"])

    conf = response.text
    assert member_count(conf, "自动选择") == 61
    assert member_count(conf, "香港自动") == 16
    assert member_count(conf, "手动选择") == 144
    assert "PROCESS-NAME" not in section(conf, "Rule")
    assert section(conf, "Rule").splitlines()[-1].startswith("FINAL,")
    assert "allow-wifi-access = true" in section(conf, "General")
    assert warning_codes(response) >= {
        "auto_test_protocol_filter",
        "unsupported_rule_types",
        "wifi_proxy_access_without_auth",
        "insecure_tls_nodes",
    }
```

- [ ] **Step 4: Run audit and acceptance tests RED**

Run:

```bash
uv run pytest -q tests/test_surge_native_profile.py tests/test_surge.py \
  tests/test_surge_policy.py -k 'audit or insecure_tls or preserves_connectivity or end_to_end'
```

Expected: audit imports or warning assertions fail because no audit exists.

- [ ] **Step 5: Implement redacted audit helpers**

Create `surge_audit.py` with a small section parser that:

- finds `[General]` case-insensitively;
- ignores blank/comment lines;
- splits only on the first `=`;
- lower-cases and trims keys and values for boolean/loglevel comparison;
- stores values only long enough to test exact booleans and presence;
- emits fixed code/suggestion dictionaries without echoing raw values.

Implement `tls_verification_warning` by counting `node.tls.insecure`. In `build_surge_config`, append TLS warnings for all emitted nodes and native-profile audit warnings only when `source_profile` is present. Keep existing AnyTLS version warnings.

- [ ] **Step 6: Run Task 5 tests GREEN**

Run:

```bash
uv run pytest -q tests/test_surge_native_profile.py tests/test_surge.py tests/test_anytls.py tests/test_workbench_v5.py
```

Expected: all selected tests pass; exact-warning tests are updated only where the new documented warning applies.

- [ ] **Step 7: Commit Task 5**

```bash
git add app/core/platforms/surge_audit.py app/core/platforms/surge.py \
  tests/test_surge_native_profile.py tests/test_surge.py tests/test_surge_policy.py \
  tests/test_anytls.py tests/test_workbench_v5.py
git commit -m "feat: audit native Surge security settings"
```

### Task 6: Documentation and release gates

**Files:**
- Modify: `README.md`
- Modify: `community_templates/leo/README.md`
- Modify: `CONTEXT.md`

**Interfaces:**
- Consumes: every public behavior produced by Tasks 1–5.
- Produces: documented domain invariant and final release-gate evidence.

- [ ] **Step 1: Document the stable behavior**

Update documentation with these exact claims:

- `surge_preferences.auto_test_protocols` is explicit Profile intent and applies only to automatic groups in Surge output.
- Manual selectors retain all compatible nodes.
- Surge means iOS compatibility; Mac-only process rules are skipped and reported.
- Native `[General]` remains source-owned; security findings are advisory and redacted.
- `skip-cert-verify` is never automatically disabled.
- AI fallback remains two explicit nodes and a generic probe does not prove service acceptance.

Add `SurgePreferences` and automatic-test filtering to the Module Map and invariants in `CONTEXT.md`.

- [ ] **Step 2: Run repository verification gates**

Run, reading every result:

```bash
uv run pytest -q
node --check app/static/flow.js
uv run python scripts/sync-service-rules.py --check
git diff --check
```

Expected: 0 test failures, JavaScript exit 0, service-rule sync exit 0, and no whitespace errors.

- [ ] **Step 3: Commit Task 6**

```bash
git add README.md community_templates/leo/README.md CONTEXT.md
git commit -m "docs: explain Surge iOS publication controls"
```

- [ ] **Step 4: Prepare whole-branch review**

Run the execution skill's review-package command against merge-base `ec2303a01e2de912bf7c00f5eb931c7318bef70f` and current `HEAD`. The reviewer must inspect target isolation, Profile backward compatibility, warning redaction, source preservation, and the five Review Focus cases above.
