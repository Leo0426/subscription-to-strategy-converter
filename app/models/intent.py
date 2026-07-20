from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


RegionId = Literal["hk", "us", "jp", "sg", "tw", "kr", "gb", "de", "ca", "au"]
FinalTarget = Literal["Proxy", "Auto", "Fallback", "DIRECT", "REJECT"]


class NodePoolIntent(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=80)
    regions: list[RegionId] = Field(default_factory=list)
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    protocols: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("include_keywords", "exclude_keywords", "protocols")
    @classmethod
    def clean_values(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class ServiceRoutingIntent(BaseModel):
    service: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    primary_pool: str = Field(min_length=1, max_length=80)
    fallback_pool: str | None = Field(default=None, max_length=80)
    final_target: FinalTarget = "Proxy"

    @field_validator("service")
    @classmethod
    def normalize_service(cls, value: str) -> str:
        return value.strip().lower()


class RouteIntent(BaseModel):
    node_pools: list[NodePoolIntent] = Field(default_factory=list)
    routes: list[ServiceRoutingIntent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> "RouteIntent":
        pool_ids = [pool.id for pool in self.node_pools]
        if len(pool_ids) != len(set(pool_ids)):
            raise ValueError("node pool ids must be unique")
        services = [route.service for route in self.routes]
        if len(services) != len(set(services)):
            raise ValueError("service route intents must be unique")
        known = set(pool_ids)
        referenced = {
            pool_id
            for route in self.routes
            for pool_id in (route.primary_pool, route.fallback_pool)
            if pool_id
        }
        unknown = sorted(referenced - known)
        if unknown:
            raise ValueError("unknown node pools: " + ", ".join(unknown))
        return self
