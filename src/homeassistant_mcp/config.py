"""Configuration via environment variables."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HA_")

    url: str = Field(
        description="Home Assistant base URL (e.g., http://ha.example.internal:8123)"
    )
    token: str = Field(description="Long-Lived Access Token from HA")
    transport: Literal["stdio", "http"] = Field(
        default="stdio",
        description="MCP transport mode",
    )
    http_port: int = Field(
        default=8100,
        description="Port for HTTP transport mode",
    )
    request_timeout: float = Field(
        default=10.0,
        description="HTTP request timeout in seconds",
    )
