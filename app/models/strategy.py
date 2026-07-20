import re
from typing import Literal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


GroupType = Literal["select", "url-test", "fallback", "load-balance"]


class CustomProxyGroup(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=80)
    type: GroupType = "select"
    proxies: list[str] = Field(default_factory=list)
    url: str | None = None
    interval: int | None = Field(default=None, ge=30, le=86400)
    include_all: bool = Field(default=False, alias="include-all")
    filter: str | None = None
    exclude_filter: str | None = Field(default=None, alias="exclude-filter")

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("group name cannot be empty")
        return cleaned

    @field_validator("proxies")
    @classmethod
    def clean_proxies(cls, value: list[str]) -> list[str]:
        return [item for item in (" ".join(proxy.split()) for proxy in value) if item]


class CustomStrategy(BaseModel):
    proxy_groups: list[CustomProxyGroup] = Field(default_factory=list)


class NodeSelector(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    name_regex: str = Field(default=".*", max_length=240)
    exclude_regex: str | None = Field(default=None, max_length=240)
    protocols: list[str] = Field(default_factory=list)

    @field_validator("name_regex", "exclude_regex")
    @classmethod
    def validate_regex(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        return value

    @field_validator("protocols")
    @classmethod
    def normalize_protocols(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip().lower() for item in value if item.strip()))


class SelectedPolicy(BaseModel):
    mode: Literal["merge", "replace"] = "merge"
    node_selectors: list[NodeSelector] = Field(default_factory=list)
    proxy_groups: list[dict[str, Any]] = Field(default_factory=list)
    rules: list[Any] = Field(default_factory=list)
    rule_providers: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_selector_ids(self) -> "SelectedPolicy":
        ids = [selector.id for selector in self.node_selectors]
        if len(ids) != len(set(ids)):
            raise ValueError("node selector ids must be unique")
        return self


class ClaudePolicy(BaseModel):
    enabled: bool = True
    egress: str | None = Field(default=None, max_length=160)
    fallback: str | None = Field(default=None, max_length=160)

    @field_validator("egress", "fallback")
    @classmethod
    def clean_route_target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None


class ServiceRoute(BaseModel):
    service: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    egress: str | None = Field(default=None, max_length=160)
    fallback: str | None = Field(default=None, max_length=160)

    @field_validator("service")
    @classmethod
    def clean_service(cls, value: str) -> str:
        cleaned = "-".join(value.strip().lower().split())
        if not cleaned:
            raise ValueError("service cannot be empty")
        return cleaned

    @field_validator("egress", "fallback")
    @classmethod
    def clean_route_target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None
