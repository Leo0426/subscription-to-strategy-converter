from typing import Any

from pydantic import BaseModel


class PreviewResponse(BaseModel):
    node_count: int
    nodes: list[dict[str, Any]]
    tree: dict[str, Any] | None = None


class ConvertResponse(BaseModel):
    target: str
    template: str
    node_count: int
    config: str
    warnings: list[dict[str, Any]] = []
