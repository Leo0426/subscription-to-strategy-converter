from pydantic import BaseModel, ConfigDict, Field, field_validator


class SurgePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_test_protocols: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("auto_test_protocols")
    @classmethod
    def normalize_protocols(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                value.strip().lower()
                for value in values
                if value.strip()
            )
        )
