"""FastMCP server definition and entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from homeassistant_mcp.client import HomeAssistantClient
from homeassistant_mcp.config import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage HA client lifecycle."""
    _ = server  # required by FastMCP lifespan signature
    settings = Settings()
    client = HomeAssistantClient(settings)
    await client.start()
    try:
        yield {"ha": client, "settings": settings}
    finally:
        await client.stop()


mcp = FastMCP(
    "homeassistant",
    lifespan=app_lifespan,
)

# Register tools — importing the modules triggers @mcp.tool() decorators
import homeassistant_mcp.tools.areas  # noqa: E402
import homeassistant_mcp.tools.automations  # noqa: E402
import homeassistant_mcp.tools.entities  # noqa: E402
import homeassistant_mcp.tools.history  # noqa: E402
import homeassistant_mcp.tools.scenes  # noqa: E402
import homeassistant_mcp.tools.services  # noqa: E402, F401


def main() -> None:
    settings = Settings()
    if settings.transport == "http":
        mcp.settings.port = settings.http_port
        mcp.settings.host = "0.0.0.0"
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
