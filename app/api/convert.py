from functools import lru_cache
import json
import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

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
from app.core.template_policy_transform import (
    TemplatePolicyTransformError,
    analyze_claude_template,
    transform_claude_policy,
)
from app.core.subscription import SubscriptionError, load_subscription
from app.core.template_engine import (
    TemplateError,
    apply_template,
    list_templates,
    load_any_template,
    load_template,
)
from app.ir import ProxyNode
from app.models.powerfullz import PowerfullzOptions
from app.models.request import ConvertRequest
from app.models.strategy import ClaudePolicy, CustomStrategy, SelectedPolicy

router = APIRouter()
http_url_adapter = TypeAdapter(AnyHttpUrl)
custom_strategy_adapter = TypeAdapter(CustomStrategy)
selected_policy_adapter = TypeAdapter(SelectedPolicy)
powerfullz_options_adapter = TypeAdapter(PowerfullzOptions)
claude_policy_adapter = TypeAdapter(ClaudePolicy)


@router.get("/templates")
async def templates() -> dict[str, list[dict]]:
    return {"templates": _templates_with_claude_capability()}


@lru_cache(maxsize=1)
def _templates_with_claude_capability() -> list[dict]:
    result: list[dict] = []
    for meta in list_templates():
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
    claude_policy: ClaudePolicy | None = None,
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
        config = transform_claude_policy(config, nodes, claude_policy, target=target)
    except (TemplateError, TemplatePolicyTransformError) as exc:
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
    claude_policy: ClaudePolicy | None = None,
) -> tuple[int, str, list[dict]]:
    nodes, config, _ = await _build_config(
        subscription_url,
        template_name,
        target,
        custom_strategy,
        selected_policy,
        powerfullz,
        claude_policy,
    )
    output, warnings = _render_output(target, nodes, config)
    if target == "surge" and claude_policy and claude_policy.enabled and warnings:
        protocols = sorted(
            {str(warning.get("value")) for warning in warnings if warning.get("code") == "unsupported_protocol"}
        )
        if protocols:
            raise HTTPException(
                status_code=400,
                detail="Surge Claude generation has unsupported node protocols: "
                + ", ".join(protocols),
            )
    return len(nodes), output, warnings


@router.post("/preview")
async def preview_subscription(request: ConvertRequest) -> dict:
    try:
        nodes, raw_config = await load_subscription(str(request.subscription_url))
    except SubscriptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    preview_config = dict(raw_config)
    preview_config["proxies"] = _serialize_nodes(nodes)
    return {
        "node_count": len(nodes),
        "nodes": _serialize_nodes(nodes),
        "tree": build_config_tree(preview_config),
    }


@router.post("/workspace/preview")
async def workspace_preview(request: ConvertRequest) -> dict:
    nodes, config, _ = await _build_config(
        str(request.subscription_url),
        request.template,
        request.target,
        request.custom_strategy,
        request.selected_policy,
        request.powerfullz,
        request.claude_policy,
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


def _profile_urls(profile_id: str, token: str) -> dict[str, object]:
    base_url = f"/subscribe/{profile_id}?token={token}"
    return {
        "id": profile_id,
        "subscribe_url": base_url,
        "subscribe_urls": {
            "clash": f"{base_url}&target=clash",
            "surge": f"{base_url}&target=surge",
        },
    }


@router.post("/profiles", status_code=201)
async def create_profile(request: ConvertRequest) -> dict[str, object]:
    if request.target not in {"mihomo", "clash", "surge"}:
        raise HTTPException(status_code=422, detail="persistent profiles support Clash/Mihomo and Surge")
    if request.claude_policy and request.claude_policy.enabled:
        _validate_profile_claude_templates(request)
    stored_request = request.model_copy(
        update={
            "clash_template": request.clash_template or request.template,
            "surge_template": request.surge_template or request.template,
        }
    )
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
                "clash_template": profile.clash_template,
                "surge_template": profile.surge_template,
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
    return {"id": profile.id, "request": profile.request}


@router.put("/profiles/{profile_id}")
async def update_profile(
    profile_id: str,
    request: ConvertRequest,
    token: str = Query(...),
) -> dict[str, object]:
    if request.target not in {"mihomo", "clash", "surge"}:
        raise HTTPException(status_code=422, detail="persistent profiles support Clash/Mihomo and Surge")
    if request.claude_policy and request.claude_policy.enabled:
        _validate_profile_claude_templates(request)
    stored_request = request.model_copy(
        update={
            "clash_template": request.clash_template or request.template,
            "surge_template": request.surge_template or request.template,
        }
    )
    if not _profile_store().update(
        profile_id,
        token,
        stored_request.model_dump(mode="json"),
    ):
        raise HTTPException(status_code=404, detail="profile not found")
    return _profile_urls(profile_id, token)


@router.get("/subscribe/{profile_id}", response_class=PlainTextResponse)
async def subscribe_profile(
    profile_id: str,
    token: str = Query(...),
    target: str | None = Query(default=None),
) -> PlainTextResponse:
    store = _profile_store()
    profile = store.get(profile_id, token)
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")

    request = ConvertRequest.model_validate(profile.request)
    render_target = target or request.target
    if render_target not in {"mihomo", "clash", "surge"}:
        raise HTTPException(status_code=400, detail=f"unsupported profile target: {render_target}")
    template_name = _profile_template(request, render_target)
    try:
        _, config, warnings = await _render_config(
            str(request.subscription_url),
            template_name,
            render_target,
            request.custom_strategy,
            request.selected_policy,
            request.powerfullz,
            request.claude_policy,
        )
    except HTTPException as exc:
        external_failures = (SubscriptionError, TemplateError)
        artifact_target = _TARGET_ALIASES.get(render_target, render_target)
        artifact = profile.artifacts.get(artifact_target)
        if artifact is None or not isinstance(exc.__cause__, external_failures):
            raise
        return PlainTextResponse(
            artifact,
            media_type=_target_media_type(render_target),
            headers={
                "Content-Disposition": f'inline; filename="{_target_filename(render_target)}"',
                "X-Subflow-Stale": "true",
            },
        )
    store.save_artifact(profile.id, render_target, config)
    headers = {"Content-Disposition": f'inline; filename="{_target_filename(render_target)}"'}
    if warnings:
        headers["X-Compile-Warnings"] = json.dumps(warnings, ensure_ascii=True)
    return PlainTextResponse(config, media_type=_target_media_type(render_target), headers=headers)


def _profile_template(request: ConvertRequest, target: str) -> str:
    if target == "surge":
        return request.surge_template or request.template
    return request.clash_template or request.template


def _validate_profile_claude_templates(request: ConvertRequest) -> None:
    selected = {
        "Clash": request.clash_template or request.template,
        "Surge": request.surge_template or request.template,
    }
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
        if target == "Surge" and not capability.surge_compatible:
            raise HTTPException(
                status_code=400,
                detail="Surge template is incompatible: "
                + "; ".join(capability.surge_incompatibility_reasons),
            )


def _target_filename(target: str) -> str:
    if target == "surge":
        return "surge.conf"
    if target == "clash":
        return "clash.yaml"
    return "mihomo.yaml"


def _target_media_type(target: str) -> str:
    return "text/plain; charset=utf-8" if target == "surge" else "text/yaml; charset=utf-8"


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
    claude: str | None = Query(default=None),
) -> PlainTextResponse:
    if session_id:
        session_data = get_session(session_id)
        if session_data:
            strategy = session_data.get("strategy") or strategy
            policy = session_data.get("policy") or policy
            policy_ids = session_data.get("policy_ids") or policy_ids
            powerfullz = session_data.get("powerfullz") or powerfullz
            claude = session_data.get("claude") or claude
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

    claude_policy = None
    if claude:
        try:
            claude_policy = claude_policy_adapter.validate_json(claude)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid Claude policy") from exc

    _, config, warnings = await _render_config(
        subscription_url,
        template,
        target,
        custom_strategy,
        selected_policy,
        powerfullz_options,
        claude_policy,
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
