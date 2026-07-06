from pydantic import AnyHttpUrl, BaseModel, Field

from app.models.powerfullz import PowerfullzOptions
from app.models.strategy import CustomStrategy, SelectedPolicy


class ConvertRequest(BaseModel):
    subscription_url: AnyHttpUrl
    template: str = Field(default="powerfullz")
    target: str = Field(default="mihomo")
    custom_strategy: CustomStrategy | None = None
    selected_policy: SelectedPolicy | None = None
    powerfullz: PowerfullzOptions | None = None
