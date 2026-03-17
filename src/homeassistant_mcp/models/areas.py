"""Pydantic models for area-related tool inputs."""

from pydantic import BaseModel, ConfigDict, Field


class GetAreaSummaryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    area: str = Field(
        ...,
        description="Area name (e.g., 'Living Room', 'Master Bedroom', 'Kitchen')",
        min_length=1,
    )
