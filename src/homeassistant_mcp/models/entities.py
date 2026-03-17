"""Pydantic models for entity-related tool inputs."""

from pydantic import BaseModel, ConfigDict, Field


class ListEntitiesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    domain: str | None = Field(
        default=None,
        description=(
            "Filter by domain: light, switch, climate, sensor, "
            "binary_sensor, automation, fan, cover, media_player, etc."
        ),
    )
    area: str | None = Field(
        default=None,
        description="Filter by area name (e.g., 'Living Room', 'Kitchen')",
    )
    state: str | None = Field(
        default=None,
        description="Filter by current state (e.g., 'on', 'off', 'unavailable')",
    )


class GetEntityStateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    entity_id: str = Field(
        ...,
        description="Full entity ID (e.g., 'climate.living_room', 'light.kitchen')",
        pattern=r"^[a-z_]+\.[a-z0-9_]+$",
    )


class SearchEntitiesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="Search term to match against entity names and IDs",
        min_length=1,
        max_length=100,
    )
    domain: str | None = Field(
        default=None,
        description="Optional domain filter to narrow search",
    )
