from __future__ import annotations

from pydantic import BaseModel, Field


class SubconverterOptions(BaseModel):
    include: str | None = None
    exclude: str | None = None
    rename: str | None = None
    filter_script: str | None = None
    sort_script: str | None = None
    emoji: bool | None = None
    udp: bool | None = None
    tfo: bool | None = None
    sort: bool | None = None
    append_type: bool | None = None
    scv: bool | None = None
    list: bool | None = None
    new_name: bool | None = None

    def query_params(self) -> dict[str, str]:
        params: dict[str, str] = {}
        for key, value in self.model_dump(exclude_none=True).items():
            if value == "":
                continue
            params[key] = "true" if value is True else "false" if value is False else str(value)
        return params
