"""MCPServer definition and entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from homeassistant_mcp.client import HomeAssistantClient
from homeassistant_mcp.config import Settings
from homeassistant_mcp.ha_ws import HomeAssistantWebSocketClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.types import ASGIApp


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[dict[str, Any]]:
    """Manage HA client lifecycle."""
    _ = server  # required by MCPServer lifespan signature
    settings = Settings()
    client = HomeAssistantClient(settings)
    await client.start()
    # Ephemeral-per-op WebSocket client for registry/config mutation (person + registry).
    # It opens a fresh connection per command, so there is nothing to start/stop.
    ws_client = HomeAssistantWebSocketClient(settings)
    try:
        yield {"ha": client, "ha_ws": ws_client, "settings": settings}
    finally:
        await client.stop()


mcp = MCPServer(
    "homeassistant",
    lifespan=app_lifespan,
)

# Register tools — importing the modules triggers @mcp.tool() decorators
import homeassistant_mcp.tools.areas  # noqa: E402
import homeassistant_mcp.tools.automations  # noqa: E402
import homeassistant_mcp.tools.cameras  # noqa: E402
import homeassistant_mcp.tools.entities  # noqa: E402
import homeassistant_mcp.tools.history  # noqa: E402
import homeassistant_mcp.tools.logbook  # noqa: E402
import homeassistant_mcp.tools.media  # noqa: E402
import homeassistant_mcp.tools.person  # noqa: E402
import homeassistant_mcp.tools.registry  # noqa: E402
import homeassistant_mcp.tools.scenes  # noqa: E402
import homeassistant_mcp.tools.services  # noqa: E402, F401


def _run_http(settings: Settings) -> None:
    """Serve the streamable-HTTP app, gated by Cloudflare Access when enabled.

    Builds the Starlette app from the module-level `mcp` instance (the one the tool
    modules import and register against), optionally wraps it with the Access JWT
    middleware, and serves it via uvicorn — mirroring MCPServer.run_streamable_http_async
    so session/lifespan behavior is unchanged.

    (TRAP) Under mcp 1.x these were assigned onto `mcp.settings` before building the
    app. In 2.x MCPServer.settings has no host/port/transport_security fields at all
    (only debug, log_level, warn_on_duplicate_*, dependencies, lifespan, auth), so
    those assignments raise. They are streamable_http_app() arguments now.
    """
    import uvicorn

    from homeassistant_mcp.access import CloudflareAccessMiddleware, CloudflareAccessValidator

    # "0.0.0.0": the origin is reached via in-cluster ClusterIP.
    # DNS-rebinding protection stays OFF deliberately, as it was under 1.x — this
    # listener is not directly exposed. Passing it explicitly rather than relying on
    # the 2.x default keeps that a stated decision rather than an accident.
    host = "0.0.0.0"
    app: ASGIApp = mcp.streamable_http_app(
        host=host,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )
    if settings.require_access_jwt:
        validator = CloudflareAccessValidator(settings)
        app = CloudflareAccessMiddleware(app, validator)

    config = uvicorn.Config(
        app,
        host=host,
        port=settings.http_port,
        # log_level is one of the few fields that survives on MCPServer.settings.
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
