from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

import json

from app.core.config_tree import build_config_tree
from app.core.parsers.clash import ir_to_clash_dict
from app.core.platforms.singbox import build_singbox_config
from app.core.platforms.surge import build_surge_config
from app.core.policy_analyzer import analyze_workspace
from app.core.policy_graph import build_policy_graph
from app.core.policy_simulator import simulate_destination
from app.core.policy_workspace import (
    config_to_workspace,
    workspace_from_dict,
    workspace_to_dict,
    workspace_to_mihomo_config,
)
from app.core.renderer import render_yaml
from app.core.policy_catalog import load_policy_catalog, selected_policy_from_ids
from app.core.sessions import create_session, get_session
from app.core.subconverter import (
    SUBCONVERTER_TARGET_IDS,
    SUBCONVERTER_TARGETS,
    SubconverterError,
    convert_subscription,
)
from app.core.subscription import SubscriptionError, load_subscription
from app.core.template_engine import TemplateError, apply_template, list_templates, load_any_template
from app.ir import ProxyNode
from app.models.powerfullz import PowerfullzOptions
from app.models.request import ConvertRequest
from app.models.response import ConvertResponse, PreviewResponse
from app.models.strategy import CustomStrategy, SelectedPolicy
from app.models.subconverter import SubconverterOptions

router = APIRouter()
http_url_adapter = TypeAdapter(AnyHttpUrl)
custom_strategy_adapter = TypeAdapter(CustomStrategy)
selected_policy_adapter = TypeAdapter(SelectedPolicy)
powerfullz_options_adapter = TypeAdapter(PowerfullzOptions)
subconverter_options_adapter = TypeAdapter(SubconverterOptions)


@router.get("/templates")
async def templates() -> dict[str, list[dict]]:
    return {"templates": list_templates()}


def _template_meta(template_name: str) -> dict:
    for template in list_templates():
        if template["id"] == template_name:
            return template
    return {"id": template_name, "label": template_name, "source": "unknown", "path": None}


@router.get("/policy-catalog")
async def policy_catalog() -> dict:
    return load_policy_catalog()


@router.get("/templates/detail")
async def template_detail(
    template: str = Query(default="powerfullz"),
    powerfullz: str | None = Query(default=None),
) -> dict:
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


_SUPPORTED_TARGETS = {"mihomo", "clash", "openclash", "singbox", "surge"}
_TARGET_ALIASES = {"clash": "mihomo", "openclash": "mihomo"}
_SUBCONVERTER_TARGET_PREFIX = "subconverter:"


@router.get("/subconverter/targets")
async def subconverter_targets() -> dict[str, list[dict]]:
    app_targets = [
        {"id": "surge", "label": "Surge", "kind": "app"},
        {"id": "mihomo", "label": "Mihomo", "kind": "app"},
        {"id": "clash", "label": "Clash", "kind": "app"},
        {"id": "openclash", "label": "OpenClash", "kind": "app"},
        {"id": "singbox", "label": "sing-box", "kind": "app"},
    ]
    converter_targets = [
        {
            "id": f"{_SUBCONVERTER_TARGET_PREFIX}{item['id']}",
            "target": item["id"],
            "label": f"{item['label']} (subconverter)",
            "kind": "subconverter",
        }
        for item in SUBCONVERTER_TARGETS
    ]
    return {"targets": app_targets + converter_targets}


def _subconverter_target(target: str) -> str | None:
    if not target.startswith(_SUBCONVERTER_TARGET_PREFIX):
        return None
    actual = target.removeprefix(_SUBCONVERTER_TARGET_PREFIX)
    if actual not in SUBCONVERTER_TARGET_IDS:
        raise HTTPException(status_code=400, detail=f"unsupported subconverter target '{actual}'")
    return actual


async def _build_config(
    subscription_url: str,
    template_name: str,
    target: str,
    custom_strategy: CustomStrategy | None = None,
    selected_policy: SelectedPolicy | None = None,
    powerfullz: PowerfullzOptions | None = None,
    subconverter: SubconverterOptions | None = None,
) -> tuple[list[ProxyNode], dict, dict]:
    if _subconverter_target(target) is None and target not in _SUPPORTED_TARGETS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported target '{target}'",
        )

    try:
        validated_url = str(http_url_adapter.validate_python(subscription_url))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    try:
        nodes, raw_config = await load_subscription(validated_url, subconverter)
    except SubscriptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        template = await load_any_template(template_name, powerfullz)
        config = apply_template(template, nodes, custom_strategy, selected_policy)
    except TemplateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return nodes, config, raw_config


def _serialize_nodes(nodes: list[ProxyNode]) -> list[dict]:
    return [ir_to_clash_dict(node) for node in nodes]


def _render_output(target: str, nodes: list[ProxyNode], config: dict) -> tuple[str, list[dict]]:
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
        return build_surge_config(
            nodes,
            config.get("proxy-groups", []),
            config.get("rules", []),
            config.get("rule-providers", {}),
        )
    return render_yaml(config), []


async def _render_config(
    subscription_url: str,
    template_name: str,
    target: str,
    custom_strategy: CustomStrategy | None = None,
    selected_policy: SelectedPolicy | None = None,
    powerfullz: PowerfullzOptions | None = None,
    subconverter: SubconverterOptions | None = None,
) -> tuple[int, str, list[dict]]:
    subconverter_target = _subconverter_target(target)
    if subconverter_target is not None:
        try:
            validated_url = str(http_url_adapter.validate_python(subscription_url))
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

        try:
            nodes, _ = await load_subscription(validated_url, subconverter)
            output = await convert_subscription(validated_url, subconverter_target, subconverter)
        except (SubscriptionError, SubconverterError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return len(nodes), output, []

    nodes, config, _ = await _build_config(
        subscription_url,
        template_name,
        target,
        custom_strategy,
        selected_policy,
        powerfullz,
        subconverter,
    )
    output, warnings = _render_output(target, nodes, config)
    return len(nodes), output, warnings


@router.post("/preview", response_model=PreviewResponse)
async def preview(request: ConvertRequest) -> PreviewResponse:
    nodes, _, raw_config = await _build_config(
        str(request.subscription_url),
        request.template,
        request.target,
        request.custom_strategy,
        request.selected_policy,
        request.powerfullz,
        request.subconverter,
    )
    return PreviewResponse(
        node_count=len(nodes),
        nodes=_serialize_nodes(nodes),
        tree=build_config_tree(raw_config),
    )


@router.post("/convert", response_model=ConvertResponse)
async def convert(request: ConvertRequest) -> ConvertResponse:
    node_count, config, warnings = await _render_config(
        str(request.subscription_url),
        request.template,
        request.target,
        request.custom_strategy,
        request.selected_policy,
        request.powerfullz,
        request.subconverter,
    )
    return ConvertResponse(
        target=request.target,
        template=request.template,
        node_count=node_count,
        config=config,
        warnings=warnings,
    )


@router.post("/workspace/preview")
async def workspace_preview(request: ConvertRequest) -> dict:
    nodes, config, _ = await _build_config(
        str(request.subscription_url),
        request.template,
        request.target,
        request.custom_strategy,
        request.selected_policy,
        request.powerfullz,
        request.subconverter,
    )
    workspace = config_to_workspace(config, nodes, request.target)
    return {
        "node_count": len(nodes),
        "workspace": workspace_to_dict(workspace),
        "graph": workspace_to_dict(build_policy_graph(workspace)),
        "findings": workspace_to_dict(analyze_workspace(workspace)),
    }


@router.post("/analyze")
async def analyze(body: dict) -> dict:
    workspace = workspace_from_dict(body.get("workspace", body))
    return {"findings": workspace_to_dict(analyze_workspace(workspace))}


@router.post("/simulate")
async def simulate(body: dict) -> dict:
    destination = str(body.get("destination") or "").strip()
    if not destination:
        raise HTTPException(status_code=422, detail="destination is required")
    workspace = workspace_from_dict(body.get("workspace", body))
    return {"trace": workspace_to_dict(simulate_destination(workspace, destination))}


@router.post("/compile/mihomo", response_class=PlainTextResponse)
async def compile_mihomo(body: dict) -> PlainTextResponse:
    workspace = workspace_from_dict(body.get("workspace", body))
    config = workspace_to_mihomo_config(workspace)
    return PlainTextResponse(render_yaml(config), media_type="text/yaml; charset=utf-8")


@router.post("/session")
async def create_policy_session(body: dict) -> dict[str, str]:
    """Store a large policy payload server-side and return a short session ID.

    The browser POSTs the selected policy here, then embeds the returned
    ``session_id`` in the /subscribe GET URL so proxy clients never see
    a multi-kilobyte query string.
    """
    return {"session_id": create_session(body)}


@router.get("/subscribe", response_class=PlainTextResponse)
async def subscribe(
    subscription_url: str = Query(...),
    template: str = Query(default="powerfullz"),
    target: str = Query(default="mihomo"),
    session_id: str | None = Query(default=None),
    strategy: str | None = Query(default=None),
    policy: str | None = Query(default=None),
    policy_ids: str | None = Query(default=None),
    powerfullz: str | None = Query(default=None),
    subconverter: str | None = Query(default=None),
) -> PlainTextResponse:
    if session_id:
        session_data = get_session(session_id)
        if session_data:
            strategy = session_data.get("strategy") or strategy
            policy = session_data.get("policy") or policy
            policy_ids = session_data.get("policy_ids") or policy_ids
            powerfullz = session_data.get("powerfullz") or powerfullz
            subconverter = session_data.get("subconverter") or subconverter
    custom_strategy = None
    if strategy:
        try:
            custom_strategy = custom_strategy_adapter.validate_json(strategy)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid custom strategy") from exc

    selected_policy = None
    if policy:
        try:
            selected_policy = selected_policy_adapter.validate_json(policy)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid selected policy") from exc
    elif policy_ids:
        try:
            raw_policy_ids = TypeAdapter(dict).validate_json(policy_ids)
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

    powerfullz_options = None
    if powerfullz:
        try:
            powerfullz_options = powerfullz_options_adapter.validate_json(powerfullz)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid powerfullz options") from exc

    subconverter_options = None
    if subconverter:
        try:
            subconverter_options = subconverter_options_adapter.validate_json(subconverter)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid subconverter options") from exc

    _, config, warnings = await _render_config(
        subscription_url,
        template,
        target,
        custom_strategy,
        selected_policy,
        powerfullz_options,
        subconverter_options,
    )
    actual_subconverter_target = _subconverter_target(target)
    if actual_subconverter_target is not None:
        if actual_subconverter_target in {"clash", "clashr"}:
            media_type = "text/yaml; charset=utf-8"
            filename = f"{actual_subconverter_target}.yaml"
        elif actual_subconverter_target in {"quan", "quanx", "loon", "surge", "surfboard", "mellow", "mixed"}:
            media_type = "text/plain; charset=utf-8"
            filename = f"{actual_subconverter_target}.conf"
        else:
            media_type = "text/plain; charset=utf-8"
            filename = f"{actual_subconverter_target}.txt"
    elif target == "singbox":
        media_type = "application/json; charset=utf-8"
        filename = "singbox.json"
    elif target == "surge":
        media_type = "text/plain; charset=utf-8"
        filename = "surge.conf"
    elif target == "openclash":
        media_type = "text/yaml; charset=utf-8"
        filename = "openclash.yaml"
    elif target == "clash":
        media_type = "text/yaml; charset=utf-8"
        filename = "clash.yaml"
    else:
        media_type = "text/yaml; charset=utf-8"
        filename = "mihomo.yaml"
    headers: dict[str, str] = {"Content-Disposition": f'inline; filename="{filename}"'}
    if warnings:
        headers["X-Compile-Warnings"] = json.dumps(warnings, ensure_ascii=True)
    return PlainTextResponse(config, media_type=media_type, headers=headers)
