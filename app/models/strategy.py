from typing import Literal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class SelectedPolicy(BaseModel):
    proxy_groups: list[dict[str, Any]] = Field(default_factory=list)
    rules: list[Any] = Field(default_factory=list)
    rule_providers: dict[str, Any] = Field(default_factory=dict)


class ClaudePolicy(BaseModel):
    enabled: bool = True
    egress: str | None = Field(default=None, max_length=160)

    @field_validator("egress")
    @classmethod
    def clean_egress(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None
