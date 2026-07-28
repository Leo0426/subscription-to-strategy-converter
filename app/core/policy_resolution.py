from __future__ import annotations

from app.core.intent_compiler import compile_route_intent
from app.core.policy_presets import get_policy_preset
from app.core.rule_packs import assemble_rule_packs
from app.core.template_engine import LEO_TEMPLATE_ID
from app.models.request import ConvertRequest
from app.models.strategy import SelectedPolicy


class PolicyResolutionError(ValueError):
    """A preset, RulePackSelection, or RouteIntent could not be resolved into a policy."""


def resolve_product_policy(request: ConvertRequest) -> ConvertRequest:
    """Apply the RulePackSelection/PolicyPreset/RouteIntent precedence (CONTEXT.md).

    Precedence: an explicit `rule_packs` selection, or else the preset's default policy;
    an explicit `selected_policy` overrides that wholesale; `route_intent` then narrows the
    result with per-service egress overrides.
    """
    if request.preset is None and request.route_intent is None and request.rule_packs is None:
        return request
    preset = get_policy_preset(request.preset) if request.preset else None
    if request.preset and preset is None:
        raise PolicyResolutionError(f"unknown policy preset: {request.preset}")
    policy_data = (preset or get_policy_preset("general"))["selected_policy"]
    try:
        assembled_policy = (
            assemble_rule_packs(request.rule_packs)
            if request.rule_packs is not None
            else SelectedPolicy.model_validate(policy_data)
        )
    except ValueError as exc:
        raise PolicyResolutionError(str(exc)) from exc
    selected_policy = request.selected_policy or assembled_policy
    if request.route_intent is not None:
        try:
            selected_policy = compile_route_intent(selected_policy, request.route_intent)
        except ValueError as exc:
            raise PolicyResolutionError(str(exc)) from exc
    return request.model_copy(
        update={
            "template": LEO_TEMPLATE_ID,
            "selected_policy": selected_policy,
        }
    )
