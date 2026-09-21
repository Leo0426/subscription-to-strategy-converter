from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.models.request import ConvertRequest


class DiagnoseRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request: ConvertRequest
    service: str = Field(default='openai', max_length=80)
    runtime: bool = False
    client: Literal['mihomo','surge'] = 'mihomo'
    samples: int = Field(default=1, ge=1, le=3, strict=True)
