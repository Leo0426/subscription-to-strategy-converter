from pydantic import AnyHttpUrl, BaseModel, Field

from app.models.powerfullz import PowerfullzOptions
from app.models.strategy import ClaudePolicy, CustomStrategy, SelectedPolicy


class ConvertRequest(BaseModel):
    subscription_url: AnyHttpUrl
    template: str = Field(default="powerfullz")
    clash_template: str | None = None
    surge_template: str | None = None
    target: str = Field(default="mihomo")
    custom_strategy: CustomStrategy | None = None
    selected_policy: SelectedPolicy | None = None
    powerfullz: PowerfullzOptions | None = None
    claude_policy: ClaudePolicy | None = None
