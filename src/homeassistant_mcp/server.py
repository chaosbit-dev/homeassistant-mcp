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

    from starlette.types import ASGIApp


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


def _run_http(settings: Settings) -> None:
    """Serve the streamable-HTTP app, gated by Cloudflare Access when enabled.

    Builds the Starlette app from the module-level `mcp` instance (the one the tool
    modules import and register against), optionally wraps it with the Access JWT
    middleware, and serves it via uvicorn — mirroring FastMCP.run_streamable_http_async
    so session/lifespan behavior is unchanged.
    """
    import uvicorn

    from homeassistant_mcp.access import CloudflareAccessMiddleware, CloudflareAccessValidator

    mcp.settings.port = settings.http_port
    mcp.settings.host = "0.0.0.0"  # origin is reached via in-cluster ClusterIP
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )

    app: ASGIApp = mcp.streamable_http_app()
    if settings.require_access_jwt:
        validator = CloudflareAccessValidator(settings)
        app = CloudflareAccessMiddleware(app, validator)

    config = uvicorn.Config(
        app,
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_level=mcp.settings.log_level.lower(),
    )
    uvicorn.Server(config).run()


def main() -> None:
    settings = Settings()
    if settings.transport == "http":
        _run_http(settings)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
