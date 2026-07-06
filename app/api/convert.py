from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

import json
import os

from app.core.config_tree import build_config_tree
from app.core.parsers.clash import ir_to_clash_dict
from app.core.platforms.singbox import build_singbox_config
from app.core.platforms.surge import build_surge_config
from app.core.policy_analyzer import analyze_workspace
from app.core.policy_graph import build_policy_graph
from app.core.profiles import ProfileStore
from app.core.policy_simulator import simulate_destination
from app.core.policy_workspace import (
    compile_mihomo_config,
    config_to_workspace,
    workspace_from_dict,
    workspace_to_dict,
    workspace_to_mihomo_config,
)
from app.core.renderer import render_yaml
from app.core.policy_catalog import load_policy_catalog, selected_policy_from_ids
from app.core.sessions import create_session, get_session
from app.core.subscription import SubscriptionError, load_subscription
from app.core.template_engine import TemplateError, apply_template, list_templates, load_any_template
from app.ir import ProxyNode
from app.models.powerfullz import PowerfullzOptions
from app.models.request import ConvertRequest
from app.models.strategy import CustomStrategy, SelectedPolicy

router = APIRouter()
http_url_adapter = TypeAdapter(AnyHttpUrl)
custom_strategy_adapter = TypeAdapter(CustomStrategy)
selected_policy_adapter = TypeAdapter(SelectedPolicy)
powerfullz_options_adapter = TypeAdapter(PowerfullzOptions)


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


_SUPPORTED_TARGETS = {"mihomo", "clash", "singbox", "surge"}
_TARGET_ALIASES = {"clash": "mihomo"}


async def _build_config(
    subscription_url: str,
    template_name: str,
    target: str,
    custom_strategy: CustomStrategy | None = None,
    selected_policy: SelectedPolicy | None = None,
    powerfullz: PowerfullzOptions | None = None,
) -> tuple[list[ProxyNode], dict, dict]:
    if target not in _SUPPORTED_TARGETS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported target '{target}'",
        )

    try:
        validated_url = str(http_url_adapter.validate_python(subscription_url))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    try:
        nodes, raw_config = await load_subscription(validated_url)
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
    return render_yaml(compile_mihomo_config(config, nodes)), []


async def _render_config(
    subscription_url: str,
    template_name: str,
    target: str,
    custom_strategy: CustomStrategy | None = None,
    selected_policy: SelectedPolicy | None = None,
    powerfullz: PowerfullzOptions | None = None,
) -> tuple[int, str, list[dict]]:
    nodes, config, _ = await _build_config(
        subscription_url,
        template_name,
        target,
        custom_strategy,
        selected_policy,
        powerfullz,
    )
    output, warnings = _render_output(target, nodes, config)
    return len(nodes), output, warnings


@router.post("/workspace/preview")
async def workspace_preview(request: ConvertRequest) -> dict:
    nodes, config, _ = await _build_config(
        str(request.subscription_url),
        request.template,
        request.target,
        request.custom_strategy,
        request.selected_policy,
        request.powerfullz,
    )
    workspace = config_to_workspace(config, nodes, request.target)
    return {
        "node_count": len(nodes),
        "workspace": workspace_to_dict(workspace),
        "graph": workspace_to_dict(build_policy_graph(workspace)),
        "findings": workspace_to_dict(analyze_workspace(workspace)),
    }


@router.post("/simulate")
async def simulate(body: dict) -> dict:
    destination = str(body.get("destination") or "").strip()
    if not destination:
        raise HTTPException(status_code=422, detail="destination is required")
    workspace = workspace_from_dict(body.get("workspace", body))
    return {"trace": workspace_to_dict(simulate_destination(workspace, destination))}


@router.post("/compile")
async def compile_workspace(body: dict) -> Response:
    workspace = workspace_from_dict(body.get("workspace", body))
    target = str(body.get("target", "mihomo"))
    target = _TARGET_ALIASES.get(target, target)
    if target not in _SUPPORTED_TARGETS:
        raise HTTPException(status_code=400, detail=f"unsupported target: {target}")
    config = workspace_to_mihomo_config(workspace)
    output, _ = _render_output(target, workspace.proxies, config)
    if target == "singbox":
        return JSONResponse(json.loads(output))
    return PlainTextResponse(output, media_type="text/yaml; charset=utf-8")


@router.post("/session")
async def create_policy_session(body: dict) -> dict[str, str]:
    """Store a large policy payload server-side and return a short session ID."""
    return {"session_id": create_session(body)}


def _profile_store() -> ProfileStore:
    return ProfileStore(os.environ.get("SUBFLOW_DB_PATH", "data/subflow.db"))


@router.post("/profiles", status_code=201)
async def create_profile(request: ConvertRequest) -> dict[str, str]:
    if request.target != "mihomo":
        raise HTTPException(status_code=422, detail="persistent profiles currently support Mihomo only")
    created = _profile_store().create(request.model_dump(mode="json"))
    return {
        "id": created.id,
        "token": created.token,
        "subscribe_url": f"/subscribe/{created.id}?token={created.token}",
    }


@router.get("/profiles")
async def list_profiles() -> dict[str, list[dict[str, object]]]:
    return {
        "profiles": [
            {
                "id": profile.id,
                "target": profile.target,
                "template": profile.template,
                "has_artifact": profile.has_artifact,
            }
            for profile in _profile_store().list()
        ]
    }


@router.get("/subscribe/{profile_id}", response_class=PlainTextResponse)
async def subscribe_profile(profile_id: str, token: str = Query(...)) -> PlainTextResponse:
    store = _profile_store()
    profile = store.get(profile_id, token)
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")

    request = ConvertRequest.model_validate(profile.request)
    try:
        _, config, warnings = await _render_config(
            str(request.subscription_url),
            request.template,
            request.target,
            request.custom_strategy,
            request.selected_policy,
            request.powerfullz,
        )
    except HTTPException as exc:
        external_failures = (SubscriptionError, TemplateError)
        if profile.artifact is None or not isinstance(exc.__cause__, external_failures):
            raise
        return PlainTextResponse(
            profile.artifact,
            media_type="text/yaml; charset=utf-8",
            headers={
                "Content-Disposition": 'inline; filename="mihomo.yaml"',
                "X-Subflow-Stale": "true",
            },
        )
    store.save_artifact(profile.id, config)
    headers = {"Content-Disposition": 'inline; filename="mihomo.yaml"'}
    if warnings:
        headers["X-Compile-Warnings"] = json.dumps(warnings, ensure_ascii=True)
    return PlainTextResponse(config, media_type="text/yaml; charset=utf-8", headers=headers)


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
) -> PlainTextResponse:
    if session_id:
        session_data = get_session(session_id)
        if session_data:
            strategy = session_data.get("strategy") or strategy
            policy = session_data.get("policy") or policy
            policy_ids = session_data.get("policy_ids") or policy_ids
            powerfullz = session_data.get("powerfullz") or powerfullz
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

    _, config, warnings = await _render_config(
        subscription_url,
        template,
        target,
        custom_strategy,
        selected_policy,
        powerfullz_options,
    )
    if target == "singbox":
        media_type = "application/json; charset=utf-8"
        filename = "singbox.json"
    elif target == "surge":
        media_type = "text/plain; charset=utf-8"
        filename = "surge.conf"
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
