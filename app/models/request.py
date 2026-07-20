from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.intent import RouteIntent
from app.models.strategy import ClaudePolicy, CustomStrategy, SelectedPolicy, ServiceRoute
from app.core.template_engine import LEO_TEMPLATE_ID


class ConvertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscription_url: AnyHttpUrl
    template: Literal["local:community_templates/leo/leo.yaml"] = LEO_TEMPLATE_ID
    preset: str | None = Field(default=None, max_length=40)
    rule_packs: list[str] | None = None
    route_intent: RouteIntent | None = None
    target: Literal["mihomo", "clash", "surge"] = "mihomo"
    custom_strategy: CustomStrategy | None = None
    selected_policy: SelectedPolicy | None = None
    claude_policy: ClaudePolicy | None = None
    service_routes: list[ServiceRoute] = Field(default_factory=list)

    @field_validator("template", mode="before")
    @classmethod
    def require_leo_template(cls, value: str) -> str:
        if value != LEO_TEMPLATE_ID:
            raise ValueError("only leo.yaml template is supported")
        return value

    @field_validator("target", mode="before")
    @classmethod
    def require_mihomo_target(cls, value: str) -> str:
        if value not in {"mihomo", "clash", "surge"}:
            raise ValueError("leo.yaml only supports Clash/Mihomo and Surge targets")
        return value

    @model_validator(mode="after")
    def normalize_service_routes(self) -> "ConvertRequest":
        self.template = LEO_TEMPLATE_ID
        if self.rule_packs is not None:
            self.rule_packs = list(
                dict.fromkeys(pack.strip().lower() for pack in self.rule_packs if pack.strip())
            )
        services = [route.service for route in self.service_routes]
        duplicates = sorted(
            service for service in set(services) if services.count(service) > 1
        )
        if duplicates:
            raise ValueError(
                "duplicate service routes: " + ", ".join(duplicates)
            )
        claude_route = next(
            (route for route in self.service_routes if route.service == "claude"),
            None,
        )
        if claude_route is not None:
            self.claude_policy = ClaudePolicy(
                enabled=claude_route.enabled,
                egress=claude_route.egress,
                fallback=claude_route.fallback,
            )
        elif self.claude_policy is not None:
            self.service_routes.append(
                ServiceRoute(
                    service="claude",
                    enabled=self.claude_policy.enabled,
                    egress=self.claude_policy.egress,
                    fallback=self.claude_policy.fallback,
                )
            )
        return self
