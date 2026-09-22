from dataclasses import dataclass, field
import asyncio
from functools import lru_cache
import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from app.core.config_tree import build_config_tree
from app.core.service_catalog import catalog_revision, service_catalog
from app.core.workbench import service_report, profile_mode
from app.core.runtime_diagnostics import diagnose_runtime, runtime_capabilities
from app.models.workbench import DiagnoseRequest
from app.core.parsers.clash import AnyTLSOptionError, ir_to_clash_dict
from app.core.platforms.singbox import build_singbox_config
from app.core.platforms.surge import build_surge_config
from app.core.platforms.surge_profile import NativeSurgeProfileError
from app.core.platforms.mihomo import build_mihomo_config, NativeMihomoProfileError
from app.core.platforms.shadowrocket import build_shadowrocket_config, build_shadowrocket_subscription
from app.core.platforms.ini import NoSupportedNodesError, incompatible_node_names
from app.core.policy_analyzer import analyze_workspace
from app.core.policy_graph import build_policy_graph
from app.core.policy_presets import list_policy_presets
from app.core.policy_resolution import PolicyResolutionError, resolve_product_policy
from app.core.rule_packs import list_rule_packs
from app.core.rule_source_audit import template_content_sha256
from app.core.intent_compiler import intent_catalog
from app.core.profiles import ProfileStore
from app.core.inflight import SingleFlight, BusyError
from app.core import publication
from app.core.network import fetch_timeout
from app.core.policy_simulator import simulate_destination
from app.core.policy_workspace import (
    POLICY_SECTIONS,
    config_to_workspace,
    workspace_from_dict,
    workspace_to_dict,
    workspace_to_mihomo_config,
)
from app.core.renderer import render_yaml
from app.core.policy_catalog import load_policy_catalog, selected_policy_from_ids
from app.core.sessions import create_session, get_session
from app.core.template_policy_transform import (
    TemplatePolicyTransformError,
    analyze_claude_template,
    transform_claude_policy,
    transform_service_routes,
)
from app.core.subscription import SubscriptionError, SubscriptionUnavailableError, load_subscription
from app.core.template_engine import (
    LEO_TEMPLATE_ID,
    TemplateError,
    apply_template,
    filter_auto_test_protocols,
    list_templates,
    load_any_template,
    load_template,
)
from app.ir import PolicyWorkspace, ProxyNode
from app.models.powerfullz import PowerfullzOptions
from app.models.request import ConvertRequest
from app.models.surge import SurgePreferences
from app.models.strategy import ClaudePolicy, CustomStrategy, SelectedPolicy, ServiceRoute

router = APIRouter()
_publications = SingleFlight()
_PROJECT_DIR = Path(__file__).resolve().parents[2]
_LEO_SOURCE_PATH = _PROJECT_DIR / "community_templates" / "leo" / "leo.yaml"
_LEO_AUDIT_PATH = _LEO_SOURCE_PATH.with_name("audit.json")
http_url_adapter = TypeAdapter(AnyHttpUrl)
custom_strategy_adapter = TypeAdapter(CustomStrategy)
selected_policy_adapter = TypeAdapter(SelectedPolicy)
powerfullz_options_adapter = TypeAdapter(PowerfullzOptions)
claude_policy_adapter = TypeAdapter(ClaudePolicy)


def _resolve_product_request(request: ConvertRequest) -> ConvertRequest:
    try:
        return resolve_product_policy(request)
    except PolicyResolutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/templates")
async def templates() -> dict[str, list[dict]]:
    return {"templates": _templates_with_claude_capability()}


@router.get("/presets")
async def presets() -> dict:
    return list_policy_presets()


@router.get("/intent/catalog")
async def route_intent_catalog() -> dict:
    return intent_catalog()


@router.get("/rule-packs")
async def rule_pack_catalog() -> dict:
    return list_rule_packs()


@lru_cache(maxsize=1)
def _templates_with_claude_capability() -> list[dict]:
    result: list[dict] = []
    for meta in list_templates():
        if meta["id"] != LEO_TEMPLATE_ID:
            continue
        enriched = dict(meta)
        try:
            loaded = load_template(str(meta["id"]))
        except TemplateError:
            capability = None
        else:
            capability = analyze_claude_template(loaded).to_dict()
        enriched["claude"] = capability
        result.append(enriched)
    return result


@router.get("/claude/templates")
async def claude_templates() -> dict[str, list[dict]]:
    return {
        "templates": [
            template
            for template in _templates_with_claude_capability()
            if (template.get("claude") or {}).get("contains_claude")
        ]
    }


def _template_meta(template_name: str) -> dict:
    for template in list_templates():
        if template["id"] == template_name:
            return template
    return {"id": template_name, "label": template_name, "source": "unknown", "path": None}


@router.get("/policy-catalog")
async def policy_catalog() -> dict:
    return load_policy_catalog()


@router.get("/templates/source", response_class=PlainTextResponse)
async def leo_template_source() -> PlainTextResponse:
    return PlainTextResponse(
        _LEO_SOURCE_PATH.read_text(encoding="utf-8"),
        media_type="text/yaml",
    )


@router.get("/templates/audit")
async def leo_template_audit() -> dict:
    if not _LEO_AUDIT_PATH.is_file():
        raise HTTPException(status_code=404, detail="Leo audit snapshot is not available")
    report = json.loads(_LEO_AUDIT_PATH.read_text(encoding="utf-8"))
    loaded = load_template(LEO_TEMPLATE_ID)
    providers = loaded.get("rule-providers") or {}
    provider_count = len(providers) if isinstance(providers, dict) else 0
    snapshot_template = report.get("template") or {}
    audited_template_sha256 = (
        str(snapshot_template.get("sha256") or "")
        if isinstance(snapshot_template, dict)
        else ""
    )
    current_template_sha256 = template_content_sha256(_LEO_SOURCE_PATH)
    report["publication"] = {
        "source_path": "community_templates/leo/audit.json",
        "template_provider_count": provider_count,
        "audited_template_sha256": audited_template_sha256,
        "current_template_sha256": current_template_sha256,
        "template_current": (
            bool(audited_template_sha256)
            and audited_template_sha256 == current_template_sha256
        ),
        "contains_remote_rule_content": False,
        "contains_subscription_credentials": False,
    }
    return report


@router.get("/templates/detail")
async def template_detail(
    template: str = Query(default=LEO_TEMPLATE_ID),
    powerfullz: str | None = Query(default=None),
) -> dict:
    _require_leo_template(template)
    if powerfullz:
        raise HTTPException(status_code=400, detail="powerfullz options are not supported with leo.yaml")
    try:
        powerfullz_options = None
        if powerfullz:
            powerfullz_options = powerfullz_options_adapter.validate_json(powerfullz)

        loaded = await load_any_template(template, powerfullz_options)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="invalid powerfullz options") from exc
    except TemplateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    proxy_groups = loaded.get("proxy-groups")
    rules = loaded.get("rules")
    rule_providers = loaded.get("rule-providers")
    proxy_providers = loaded.get("proxy-providers")

    return {
        "template": _template_meta(template),
        "public_data": [
            {"label": "完整模板 YAML", "href": "/templates/source"},
            {"label": "全部规则与来源", "href": "/community/rules"},
            {"label": "完整质量审计", "href": "/templates/audit"},
        ],
        "summary": {
            "proxy_group_count": len(proxy_groups) if isinstance(proxy_groups, list) else 0,
            "rule_count": len(rules) if isinstance(rules, list) else 0,
            "rule_provider_count": len(rule_providers) if isinstance(rule_providers, dict) else 0,
            "proxy_provider_count": len(proxy_providers) if isinstance(proxy_providers, dict) else 0,
            "has_dns": isinstance(loaded.get("dns"), dict),
            "has_tun": isinstance(loaded.get("tun"), dict),
        },
        "yaml": render_yaml(loaded),
        "proxy_groups": proxy_groups if isinstance(proxy_groups, list) else [],
    }


_SUPPORTED_TARGETS = {"mihomo", "clash", "singbox", "surge", "shadowrocket", "shadowrocket-config"}
_TARGET_ALIASES = {"clash": "mihomo"}
_LEO_TARGETS = {"mihomo", "clash", "surge", "shadowrocket", "shadowrocket-config"}


def _require_leo_template(template_name: str) -> None:
    if template_name != LEO_TEMPLATE_ID:
        raise HTTPException(status_code=400, detail="only leo.yaml template is supported")


def _require_supported_target(target: str) -> None:
    if target not in _LEO_TARGETS:
        raise HTTPException(status_code=400, detail="leo.yaml only supports Clash/Mihomo, Surge and Shadowrocket targets")


@dataclass(frozen=True, slots=True)
class RenderInputs:
    """The complete, named input to `_build_config`/`_render_config`.

    Collapses the render pipeline's parameters into one type so call sites cannot
    mismatch positional order when passing the same five optional overrides.
    """

    subscription_url: str
    template_name: str
    target: str
    custom_strategy: CustomStrategy | None = None
    selected_policy: SelectedPolicy | None = None
    powerfullz: PowerfullzOptions | None = None
    claude_policy: ClaudePolicy | None = None
    service_routes: list[ServiceRoute] | None = None
    surge_preferences: SurgePreferences = field(default_factory=SurgePreferences)

    @classmethod
    def from_request(
        cls,
        request: ConvertRequest,
        *,
        template_name: str | None = None,
        target: str | None = None,
    ) -> "RenderInputs":
        return cls(
            subscription_url=str(request.subscription_url),
            template_name=template_name or request.template,
            target=target or request.target,
            custom_strategy=request.custom_strategy,
            selected_policy=request.selected_policy,
            claude_policy=request.claude_policy,
            service_routes=request.service_routes,
            surge_preferences=request.surge_preferences,
        )


@dataclass(slots=True)
class BuildResult:
    nodes: list[ProxyNode]
    config: dict
    source_config: dict
    warnings: list[dict]


async def _build_config(inputs: RenderInputs) -> BuildResult:
    _require_leo_template(inputs.template_name)
    _require_supported_target(inputs.target)

    try:
        validated_url = str(http_url_adapter.validate_python(inputs.subscription_url))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    try:
        nodes, raw_config = await load_subscription(validated_url, target=inputs.target)
    except SubscriptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        template = await load_any_template(inputs.template_name, inputs.powerfullz)
        config = apply_template(
            template,
            nodes,
            inputs.custom_strategy,
            inputs.selected_policy,
            source_config=raw_config,
        )
        if inputs.service_routes is not None:
            config = transform_service_routes(config, nodes, inputs.service_routes, target=inputs.target)
        else:
            config = transform_claude_policy(config, nodes, inputs.claude_policy, target=inputs.target)
    except (TemplateError, TemplatePolicyTransformError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    warnings: list[dict] = []
    if inputs.target == "surge":
        warnings.extend(
            filter_auto_test_protocols(
                config,
                nodes,
                inputs.surge_preferences.auto_test_protocols,
            )
        )

    return BuildResult(
        nodes=nodes,
        config=config,
        source_config=raw_config,
        warnings=warnings,
    )


def _serialize_nodes(nodes: list[ProxyNode]) -> list[dict]:
    return [ir_to_clash_dict(node) for node in nodes]


def _render_output(
    target: str, nodes: list[ProxyNode], config: dict, *, source_config: dict | None = None,
) -> tuple[str, list[dict]]:
    render_target = _TARGET_ALIASES.get(target, target)
    if render_target == "singbox":
        sb_config = build_singbox_config(
            nodes,
            config.get("proxy-groups", []),
            config.get("rules", []),
            config.get("rule-providers", {}),
        )
        return json.dumps(sb_config, ensure_ascii=False, indent=2), []
    if render_target == "surge":
        try:
            return build_surge_config(
                nodes,
                config.get("proxy-groups", []),
                config.get("rules", []),
                config.get("rule-providers", {}),
                dns_config=config.get("dns"),
                source_profile=(source_config or {}).get("_surge_source"),
            )
        except NativeSurgeProfileError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if render_target in {"shadowrocket", "shadowrocket-config"}:
        try:
            if render_target == "shadowrocket":
                return build_shadowrocket_subscription(nodes, source_config=source_config)
            return build_shadowrocket_config(
                nodes, config.get("proxy-groups", []),
                config.get("rules", []), config.get("rule-providers", {}),
                source_config=source_config,
            )
        except (NoSupportedNodesError, NativeSurgeProfileError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        compiled, warnings = build_mihomo_config(nodes, config, source_config=source_config)
    except NativeMihomoProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return render_yaml(compiled), warnings


def _service_output_errors(nodes: list[ProxyNode], routes: list[ServiceRoute], warnings: list[dict]) -> list[str]:
    unavailable = incompatible_node_names(nodes, warnings)
    groups_lost = {group for warning in warnings if warning.get("code") == "unavailable_proxy_groups" for group in warning.get("groups", [])}
    errors = []
    for route in routes:
        if not route.enabled or route.mode == "legacy":
            continue
        for chosen in (route.egress, route.fallback):
            if chosen and chosen in unavailable:
                errors.append(f"{route.service} 指定节点 {chosen} 无法导出到该客户端")
        if route.egress in groups_lost:
            errors.append(f"{route.service} 指定策略组在该客户端没有可用节点")
    return errors


async def _render_config(inputs: RenderInputs) -> tuple[int, str, list[dict]]:
    result = await _build_config(inputs)
    output, compiler_warnings = _render_output(
        inputs.target,
        result.nodes,
        result.config,
        source_config=result.source_config,
    )
    warnings = result.warnings + compiler_warnings
    routes = inputs.service_routes or (
        [
            ServiceRoute(
                service="claude",
                enabled=inputs.claude_policy.enabled,
                egress=inputs.claude_policy.egress,
                fallback=inputs.claude_policy.fallback,
            )
        ]
        if inputs.claude_policy is not None
        else []
    )
    errors = _service_output_errors(result.nodes, routes, warnings)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    has_claude_route = any(route.enabled and route.service == "claude" and route.mode == "legacy" for route in routes)
    if inputs.target == "surge" and has_claude_route and warnings:
        protocols = sorted(
            {str(warning.get("value")) for warning in warnings if warning.get("code") == "unsupported_protocol"}
        )
        if protocols:
            raise HTTPException(
                status_code=400,
                detail="Surge Claude generation has unsupported node protocols: "
                + ", ".join(protocols),
            )
        if any(warning.get("code") == "unsupported_node_options" for warning in warnings):
            raise HTTPException(status_code=400, detail="Surge Claude generation has unsupported node options")
    return len(result.nodes), output, warnings


@router.post("/preview")
async def preview_subscription(request: ConvertRequest) -> dict:
    try:
        nodes, raw_config = await load_subscription(str(request.subscription_url), target=request.target)
    except SubscriptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    preview_config = {key: value for key, value in raw_config.items() if not key.startswith("_")}
    preview_config["proxies"] = _serialize_nodes(nodes)
    return {
        "node_count": len(nodes),
        "nodes": _serialize_nodes(nodes),
        "tree": build_config_tree(preview_config),
    }


@router.post("/workspace/preview")
async def workspace_preview(request: ConvertRequest) -> dict:
    request = _resolve_product_request(request)
    result = await _build_config(RenderInputs.from_request(request))
    nodes = result.nodes
    config = result.config
    source_config = result.source_config
    materialized_mihomo = request.target in {"mihomo", "clash"} and "_surge_source" not in source_config
    compile_warnings = list(result.warnings)
    if materialized_mihomo:
        try:
            config, compiler_warnings = build_mihomo_config(nodes, config, source_config=source_config)
            compile_warnings.extend(compiler_warnings)
        except NativeMihomoProfileError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        # This remains a policy preview. Show only source-owned common settings;
        # native INI envelopes cannot be represented by these YAML settings.
        config = {key: value for key, value in config.items() if key in POLICY_SECTIONS}
        config.update({key: value for key, value in source_config.items()
                       if key not in POLICY_SECTIONS | {"source-format"} and not key.startswith("_")})
    workspace = config_to_workspace(config, None if materialized_mihomo else nodes, request.target)
    serialized_workspace = workspace_to_dict(workspace)
    source_requires_context = (
        bool(source_config.keys() - {"proxies", "source-format"})
        or source_config.get("proxies", []) != _serialize_nodes(nodes)
    )
    if compile_warnings:
        serialized_workspace["compile_warnings"] = compile_warnings
    if "_surge_source" in source_config:
        # PolicyWorkspace contains policy IR, not a lossless native profile.
        serialized_workspace["native_source_format"] = "surge"
    elif materialized_mihomo:
        serialized_workspace["native_source_format"] = "mihomo"
        serialized_workspace["native_requires_source"] = (
            config.get("proxies", []) != [ir_to_clash_dict(node) for node in workspace.proxies]
        )
    if not materialized_mihomo and "_surge_source" not in source_config and source_requires_context:
        serialized_workspace["requires_source_render"] = True
    if request.target in {"shadowrocket", "shadowrocket-config"} and source_requires_context:
        serialized_workspace["requires_source_render"] = True
    return {
        "node_count": len(nodes),
        "resolved_policy": (
            request.selected_policy.model_dump(mode="json", by_alias=True)
            if request.selected_policy is not None
            else None
        ),
        "workspace": serialized_workspace,
        "graph": workspace_to_dict(build_policy_graph(workspace)),
        "findings": workspace_to_dict(analyze_workspace(workspace)),
    }


@router.post("/render", response_class=PlainTextResponse)
async def render_request(request: ConvertRequest) -> PlainTextResponse:
    request = _resolve_product_request(request)
    inputs = RenderInputs.from_request(request, template_name=LEO_TEMPLATE_ID)
    _, output, warnings = await _render_config(inputs)
    headers = {"Content-Disposition": f'inline; filename="{_target_filename(request.target)}"'}
    if warnings:
        headers["X-Compile-Warnings"] = json.dumps(warnings, ensure_ascii=True)
    return PlainTextResponse(
        output,
        media_type=_target_media_type(request.target),
        headers=headers,
    )


def _request_workspace(body: dict) -> PolicyWorkspace:
    try:
        return workspace_from_dict(body.get("workspace", body))
    except AnyTLSOptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/simulate")
async def simulate(body: dict) -> dict:
    destination = str(body.get("destination") or "").strip()
    if not destination:
        raise HTTPException(status_code=422, detail="destination is required")
    workspace = _request_workspace(body)
    return {"trace": workspace_to_dict(simulate_destination(workspace, destination))}


@router.post("/compile")
async def compile_workspace(body: dict) -> Response:
    workspace = _request_workspace(body)
    if any(node.extra.get("_shadowrocket_inventory_only") for node in workspace.proxies):
        raise HTTPException(status_code=400, detail="Shadowrocket 节点清单不含可重建的连接参数；请通过 /render 或订阅接口保留原生来源")
    target = str(body.get("target", "mihomo"))
    target = _TARGET_ALIASES.get(target, target)
    if target not in _SUPPORTED_TARGETS:
        raise HTTPException(status_code=400, detail=f"unsupported target: {target}")
    if body.get("workspace", body).get("requires_source_render"):
        raise HTTPException(status_code=400, detail="跨格式策略工作区未携带完整机场连接配置；请通过 /render 或订阅接口生成")
    if target == "surge" and body.get("workspace", body).get("native_source_format") == "surge":
        raise HTTPException(status_code=400, detail="原生 Surge 配置请通过 /render 或订阅接口生成，以保留机场连接设置")
    native_mihomo = body.get("workspace", body).get("native_source_format") == "mihomo"
    if native_mihomo and target in {"shadowrocket", "shadowrocket-config"}:
        raise HTTPException(status_code=400, detail="Shadowrocket 请通过 /render 或订阅接口读取其原生来源，不能用 Mihomo 工作区重建机场配置")
    if native_mihomo and body.get("workspace", body).get("native_requires_source"):
        raise HTTPException(status_code=400, detail="策略工作区不能无损保留这些原生节点字段；请通过 /render 或订阅接口生成，以保留机场连接设置")
    config = workspace_to_mihomo_config(workspace)
    if native_mihomo and target == "mihomo":
        # Preview already compiled generated routing and preserved native
        # provider egress. Recompilation would rewrite that source-owned data.
        saved_warnings = body.get("workspace", body).get("compile_warnings", [])
        warnings = [warning for warning in saved_warnings if isinstance(warning, dict)] if isinstance(saved_warnings, list) else []
        output = render_yaml(config)
    else:
        output, warnings = _render_output(target, workspace.proxies, config,
                                          source_config=config if native_mihomo else None)
    if target == "singbox":
        return JSONResponse(json.loads(output))
    headers = {"X-Compile-Warnings": json.dumps(warnings, ensure_ascii=True)} if warnings else {}
    return PlainTextResponse(output, media_type=_target_media_type(target), headers=headers)


@router.get("/services")
async def services() -> dict:
    return {"revision": catalog_revision(), "services": service_catalog()}


async def _check_request(request: ConvertRequest) -> dict:
    request = _resolve_product_request(request)
    targets = request.publication_targets or [request.target]
    base_target = request.target if _TARGET_ALIASES.get(request.target, request.target) in targets else targets[0]
    base_result = await _build_config(RenderInputs.from_request(request, target=base_target))
    findings = workspace_to_dict(
        analyze_workspace(config_to_workspace(base_result.config, base_result.nodes))
    )
    clients = []
    for target in targets:
        warnings: list[dict] = []
        errors = []
        try:
            result = (
                base_result
                if target == base_target
                else await _build_config(RenderInputs.from_request(request, target=target))
            )
            _, compiler_warnings = _render_output(
                target,
                result.nodes,
                result.config,
                source_config=result.source_config,
            )
            warnings = result.warnings + compiler_warnings
            if target == "shadowrocket":
                _, policy_warnings = _render_output(
                    "shadowrocket-config",
                    result.nodes,
                    result.config,
                    source_config=result.source_config,
                )
                warnings += policy_warnings
            supported = {node.name for node in result.nodes} - incompatible_node_names(result.nodes, warnings)
            if not supported:
                errors.append("该客户端没有可用节点（协议或参数不兼容）")
            errors.extend(_service_output_errors(result.nodes, request.service_routes, warnings))
        except (HTTPException, ValueError) as exc:
            errors.append(str(exc.detail if isinstance(exc, HTTPException) else exc))
        clients.append({"target": target, "status": "error" if errors else ("warning" if warnings else "passed"),
                        "warnings": warnings, "errors": errors})
    return {"can_publish": not any(f["severity"] == "error" for f in findings) and not any(c["errors"] for c in clients),
            "node_count": len(base_result.nodes), "findings": findings, "clients": clients,
            "services": service_report(base_result.config, base_result.nodes), "revision": catalog_revision(),
            "runtime": {"status": "not_tested", "actual_node": None,
                        "message": "尚未连接客户端；配置检查不代表实际访问、登录或对话已通过。"}}


@router.get("/runtime/capabilities")
async def runtime_connections() -> dict:
    return runtime_capabilities()


@router.post("/diagnose")
async def diagnose(request: DiagnoseRequest) -> dict:
    service = next((s for s in service_catalog() if s["id"] == request.service), None)
    if service is None:
        raise HTTPException(status_code=400, detail="unknown service")
    result = await _build_config(RenderInputs.from_request(_resolve_product_request(request.request)))
    report = service_report(result.config, result.nodes, request.service)[0]
    runtime = {"status":"not_tested", "actual_node":None, "message":"尚未请求客户端实测。"}
    if request.runtime:
        expected = report["domains"][0]["target"]
        runtime = await diagnose_runtime(request.client, service, expected, samples=request.samples)
    return {"service":report,"runtime":runtime}


@router.post("/check")
async def check_request(request: ConvertRequest) -> dict:
    return await _check_request(request)


async def _validate_publication(request: ConvertRequest) -> ConvertRequest:
    if request.publication_targets is not None:
        report = await _check_request(request)
        if not report["can_publish"]:
            raise HTTPException(status_code=400, detail={"message": "目标客户端检查未通过", "checks": report})
        request = request.model_copy(update={"policy_revision": catalog_revision()})
    return request


@router.post("/session")
async def create_policy_session(body: dict) -> dict[str, str]:
    """Store a large policy payload server-side and return a short session ID."""
    return {"session_id": create_session(body)}


def _profile_store() -> ProfileStore:
    return ProfileStore(os.environ.get("SUBFLOW_DB_PATH", "data/subflow.db"))


def _public_base_url() -> str:
    """Origin that proxy clients use to reach this instance.

    The page can only guess it from `location.origin`, which is wrong whenever the
    operator browses on `127.0.0.1` but the client runs on another host (a router
    running OpenClash, for example). Setting this pins the published address.
    """
    return os.environ.get("SUBFLOW_PUBLIC_BASE_URL", "").strip().rstrip("/")


def _profile_urls(profile_id: str, token: str) -> dict[str, object]:
    base_url = f"{_public_base_url()}/subscribe/{profile_id}?token={token}"
    return {
        "id": profile_id,
        "subscribe_url": base_url,
        "subscribe_urls": {
            "clash": f"{base_url}&target=clash",
            "surge": f"{base_url}&target=surge",
            "shadowrocket": f"{base_url}&target=shadowrocket",
        },
        "config_urls": {"shadowrocket": f"{base_url}&target=shadowrocket-config"},
    }


@router.post("/profiles", status_code=201)
async def create_profile(request: ConvertRequest) -> dict[str, object]:
    request = _resolve_product_request(request)
    _validate_profile_service_routes(request)
    stored_request = await _validate_publication(request)
    created = _profile_store().create(stored_request.model_dump(mode="json"))
    return {**_profile_urls(created.id, created.token), "token": created.token}


@router.get("/profiles")
async def list_profiles() -> dict[str, list[dict[str, object]]]:
    return {
        "profiles": [
            {
                "id": profile.id,
                "target": profile.target,
                "template": profile.template,
                **({"preset": profile.preset} if profile.preset else {}),
                "has_artifact": profile.has_artifact,
            }
            for profile in _profile_store().list()
        ]
    }


@router.get("/profiles/{profile_id}/draft")
async def get_profile_draft(profile_id: str, token: str = Query(...)) -> dict[str, object]:
    profile = _profile_store().get(profile_id, token)
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return {"id": profile.id, "request": profile.request, "mode": profile_mode(profile.request),
            "current_revision": catalog_revision(),
            "generation": profile.generation, "publications": profile.artifact_metadata,
            "current_publication_revision": publication.publication_revision(),
            "cache_ttl_seconds": publication.cache_ttl(),
            "update_available": profile.request.get("policy_revision") != catalog_revision()}


@router.post("/profiles/{profile_id}/upgrade-preview")
async def upgrade_profile_preview(profile_id: str, token: str = Query(...)) -> dict:
    profile = _profile_store().get(profile_id, token)
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")
    original = ConvertRequest.model_validate(profile.request)
    old_result = await _build_config(RenderInputs.from_request(original))
    old_nodes = old_result.nodes
    old_config = old_result.config
    old_groups = {g["name"]: g for g in (original.selected_policy.proxy_groups if original.selected_policy else [])}
    routes = [route.model_dump() for route in original.service_routes if route.mode != "legacy"]
    preserved = []
    if not routes:
        for service in service_catalog():
            group = old_groups.get(service["group"], {})
            members = group.get("proxies", [])
            if members and isinstance(members[0], str) and not members[0].startswith("selector:"):
                routes.append({"service":service["id"],"mode":"manual","egress":members[0]})
                preserved.append(service["label"])
        for route in original.service_routes:
            if route.enabled and route.egress and not any(r["service"] == route.service for r in routes):
                routes.append({"service":route.service,"mode":"manual","egress":route.egress})
    template = load_template(LEO_TEMPLATE_ID)
    fresh = apply_template(template, old_nodes)
    available = {n.name for n in old_nodes} | {g['name'] for g in fresh.get('proxy-groups', [])} | {'DIRECT', 'REJECT'}
    discarded = [f"{r['service']}: {r['egress']}" for r in routes if r['egress'] not in available]
    routes = [r for r in routes if r['egress'] in available]
    candidate = ConvertRequest(subscription_url=original.subscription_url, target=original.target,
                               profile_name=original.profile_name, service_routes=routes,
                               publication_targets=original.publication_targets or ["mihomo", "surge"],
                               surge_preferences=original.surge_preferences)
    fresh = transform_service_routes(fresh, old_nodes, candidate.service_routes)
    old_rules = set(map(str, old_config.get("rules", [])))
    new_rules = set(map(str, fresh.get("rules", [])))
    return {"request":candidate.model_dump(mode="json"), "changes": {
        "added_rules":sorted(new_rules-old_rules), "removed_rules":sorted(old_rules-new_rules),
        "preserved_services":[s["label"] for s in service_catalog() if any(r["service"] == s["id"] for r in routes)], "discarded_preferences":discarded, "removed_groups":sorted(set(old_groups)-{s["group"] for s in service_catalog() if any(r["service"] == s["id"] for r in routes)}),
        "message":"仅保留可识别的首选出口；其他自定义规则与候选将由当前 Leo 替换。尚未保存，应用前请检查。"}}


@router.put("/profiles/{profile_id}")
async def update_profile(
    profile_id: str,
    request: ConvertRequest,
    token: str = Query(...),
) -> dict[str, object]:
    if _profile_store().get(profile_id, token) is None:
        raise HTTPException(status_code=404, detail="profile not found")
    request = _resolve_product_request(request)
    _validate_profile_service_routes(request)
    stored_request = await _validate_publication(request)
    if not _profile_store().update(
        profile_id,
        token,
        stored_request.model_dump(mode="json"),
    ):
        raise HTTPException(status_code=404, detail="profile not found")
    return _profile_urls(profile_id, token)


def _published_response(config: str, target: str, metadata: dict, state: str) -> PlainTextResponse:
    headers = publication.headers(metadata, state)
    headers["Content-Disposition"] = f'inline; filename="{_target_filename(target)}"'
    if metadata.get("warnings"):
        headers["X-Compile-Warnings"] = json.dumps(metadata["warnings"], ensure_ascii=True)
    return PlainTextResponse(config, media_type=_target_media_type(target), headers=headers)


async def _publication_before(deadline: float, key: tuple, factory):
    try:
        async with asyncio.timeout_at(deadline):
            return await _publications.run(key, factory)
    except TimeoutError:
        cause = SubscriptionUnavailableError('subscription refresh deadline exceeded')
        raise HTTPException(status_code=400, detail=str(cause)) from cause


@router.get("/subscribe/{profile_id}", response_class=PlainTextResponse)
async def subscribe_profile(
    profile_id: str,
    token: str = Query(...),
    target: str | None = Query(default=None),
    force_refresh: bool = Query(default=False),
) -> PlainTextResponse:
    store = _profile_store()
    deadline = asyncio.get_running_loop().time() + fetch_timeout()
    for _ in range(3):
        profile = store.get(profile_id, token)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile not found")
        request = ConvertRequest.model_validate(profile.request)
        render_target = _TARGET_ALIASES.get(target or request.target, target or request.target)
        _require_supported_target(render_target)
        revision = publication.publication_revision()
        metadata = profile.artifact_metadata.get(render_target, {})
        if not force_refresh and render_target in profile.artifacts and publication.is_fresh(metadata, profile.generation, revision):
            return _published_response(profile.artifacts[render_target], render_target, metadata, "hit")
        inputs = RenderInputs.from_request(request, template_name=_profile_template(request, render_target), target=render_target)

        async def compile_publication(inputs=inputs, generation=profile.generation, revision=revision):
            _, config, warnings = await _render_config(inputs)
            return publication.stamp(config, generation, revision, warnings, annotate=render_target != "shadowrocket")

        try:
            config, metadata = await _publication_before(deadline,
                (str(store.database.resolve()), profile.id, profile.generation, render_target, revision), compile_publication)
        except BusyError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except HTTPException as exc:
            current = store.get(profile_id, token)
            if current is None:
                raise HTTPException(status_code=404, detail="profile not found")
            if current.generation != profile.generation or publication.publication_revision() != revision:
                continue
            artifact = current.artifacts.get(render_target)
            if not isinstance(exc.__cause__, SubscriptionUnavailableError):
                if not store.discard_artifact(profile.id, render_target, expected_generation=profile.generation):
                    continue
                raise
            if artifact is None:
                raise
            metadata = current.artifact_metadata.get(render_target, {})
            if metadata:
                metadata = {**metadata, "last_status": "stale"}
                if not store.save_artifact(profile.id, render_target, artifact, expected_generation=profile.generation, metadata=metadata):
                    continue
            return _published_response(artifact, render_target, metadata, "stale")
        if publication.publication_revision() != revision:
            continue
        if not store.save_artifact(profile.id, render_target, config, expected_generation=profile.generation, metadata=metadata):
            continue
        return _published_response(config, render_target, metadata, "fresh")
    raise HTTPException(status_code=409, detail="配置正在被修改，请重试刷新")



def _profile_template(request: ConvertRequest, target: str) -> str:
    return LEO_TEMPLATE_ID


def _validate_profile_claude_templates(request: ConvertRequest) -> None:
    selected = {"Clash": LEO_TEMPLATE_ID}
    for target, template_name in selected.items():
        try:
            template = load_template(template_name)
        except TemplateError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        capability = analyze_claude_template(template)
        if not capability.contains_claude:
            raise HTTPException(
                status_code=400,
                detail=f"{target} template does not contain a recognizable Claude policy",
            )


def _validate_profile_service_routes(request: ConvertRequest) -> None:
    enabled = [route for route in request.service_routes if route.enabled]
    known = {service["id"] for service in service_catalog()}
    unsupported = sorted({route.service for route in enabled
                          if route.service not in known or (route.mode == "legacy" and route.service != "claude")})
    if unsupported:
        raise HTTPException(
            status_code=400,
            detail="unsupported service route: " + ", ".join(unsupported),
        )
    if any(route.service == "claude" and route.mode == "legacy" for route in enabled):
        if request.selected_policy is not None:
            group_names = {
                str(group.get("name", "")).casefold()
                for group in request.selected_policy.proxy_groups
                if isinstance(group, dict)
            }
            has_claude_rule = any(
                ",claude" in str(rule).casefold()
                for rule in request.selected_policy.rules
            )
            if "claude" not in group_names or not has_claude_rule:
                raise HTTPException(
                    status_code=400,
                    detail="selected policy does not contain a recognizable Claude route",
                )
        else:
            _validate_profile_claude_templates(request)


def _parse_json_query(adapter: TypeAdapter, raw: str | None, label: str) -> Any:
    if not raw:
        return None
    try:
        return adapter.validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"invalid {label}") from exc


def _target_filename(target: str) -> str:
    if target == "shadowrocket-config":
        return "shadowrocket.conf"
    if target == "shadowrocket":
        return "shadowrocket.txt"
    if target == "surge":
        return "surge.conf"
    if target == "clash":
        return "clash.yaml"
    return "mihomo.yaml"


def _target_media_type(target: str) -> str:
    return "text/plain; charset=utf-8" if target in {"surge", "shadowrocket", "shadowrocket-config"} else "text/yaml; charset=utf-8"


@router.get("/subscribe", response_class=PlainTextResponse)
async def subscribe(
    subscription_url: str = Query(...),
    template: str = Query(default=LEO_TEMPLATE_ID),
    target: str = Query(default="mihomo"),
    session_id: str | None = Query(default=None),
    strategy: str | None = Query(default=None),
    policy: str | None = Query(default=None),
    policy_ids: str | None = Query(default=None),
    powerfullz: str | None = Query(default=None),
    claude: str | None = Query(default=None),
) -> PlainTextResponse:
    _require_leo_template(template)
    _require_supported_target(target)
    if powerfullz:
        raise HTTPException(status_code=400, detail="powerfullz options are not supported with leo.yaml")
    if session_id:
        session_data = get_session(session_id)
        if session_data:
            strategy = session_data.get("strategy") or strategy
            policy = session_data.get("policy") or policy
            policy_ids = session_data.get("policy_ids") or policy_ids
            powerfullz = session_data.get("powerfullz") or powerfullz
            claude = session_data.get("claude") or claude

    custom_strategy = _parse_json_query(custom_strategy_adapter, strategy, "custom strategy")

    selected_policy = _parse_json_query(selected_policy_adapter, policy, "selected policy")
    if selected_policy is None and policy_ids:
        raw_policy_ids = _parse_json_query(TypeAdapter(dict), policy_ids, "selected policy ids")
        try:
            selected_policy = selected_policy_adapter.validate_python(
                selected_policy_from_ids(
                    raw_policy_ids.get("proxy_groups"),
                    raw_policy_ids.get("rules"),
                    raw_policy_ids.get("rule_providers"),
                    raw_policy_ids.get("rule_targets"),
                )
            )
        except (TypeError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail="invalid selected policy ids") from exc

    powerfullz_options = _parse_json_query(powerfullz_options_adapter, powerfullz, "powerfullz options")
    claude_policy = _parse_json_query(claude_policy_adapter, claude, "Claude policy")

    inputs = RenderInputs(
        subscription_url=subscription_url,
        template_name=template,
        target=target,
        custom_strategy=custom_strategy,
        selected_policy=selected_policy,
        powerfullz=powerfullz_options,
        claude_policy=claude_policy,
    )
    _, config, warnings = await _render_config(inputs)
    headers: dict[str, str] = {"Content-Disposition": f'inline; filename="{_target_filename(target)}"'}
    if warnings:
        headers["X-Compile-Warnings"] = json.dumps(warnings, ensure_ascii=True)
    return PlainTextResponse(config, media_type=_target_media_type(target), headers=headers)
