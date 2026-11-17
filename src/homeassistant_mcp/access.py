"""Cloudflare Access JWT validation for the HTTP transport.

Defense-in-depth second gate: the Cloudflare Tunnel terminates Access at the edge
and injects a `Cf-Access-Jwt-Assertion` header, but the origin must not blindly
trust the network (any in-cluster foothold could otherwise reach the ClusterIP).
This middleware re-validates that JWT in-process before any request reaches the
MCP application.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import jwt
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from homeassistant_mcp.config import Settings

_ACCESS_HEADER = "cf-access-jwt-assertion"


class AccessValidationError(Exception):
    """Raised when a Cloudflare Access JWT fails validation."""


class CloudflareAccessValidator:
    """Validate Cloudflare Access JWTs against the team JWKS.

    Verifies the RS256 signature, `aud`, `iss`, and standard time claims
    (`exp`/`nbf`), and optionally that the `email` claim is allow-listed. Signing
    keys are cached and refreshed once on encountering an unknown `kid`.
    """

    def __init__(self, settings: Settings) -> None:
        self._jwks_url = settings.access_jwks_url
        assert settings.access_aud
        self._audience = settings.access_aud
        self._issuer = settings.access_issuer
        self._allowed_emails = frozenset(settings.access_allowed_emails)
        self._timeout = settings.request_timeout
        self._keys: dict[str, jwt.PyJWK] = {}

    async def _fetch_jwks(self) -> None:
        """Fetch the JWKS and replace the local key cache."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(self._jwks_url)
        response.raise_for_status()
        payload = response.json()
        keys: dict[str, jwt.PyJWK] = {}
        for entry in payload.get("keys", []):
            kid = entry.get("kid")
            if kid is None:
                continue
            try:
                keys[kid] = jwt.PyJWK.from_dict(entry)
            except jwt.PyJWKError:
                # Skip keys we cannot construct rather than failing the refresh.
                continue
        self._keys = keys

    async def _signing_key(self, kid: str) -> jwt.PyJWK:
        """Return the signing key for `kid`, refreshing the JWKS once if needed."""
        if kid not in self._keys:
            await self._fetch_jwks()
        try:
            return self._keys[kid]
        except KeyError as exc:
            raise AccessValidationError("unknown signing key") from exc

    async def validate(self, token: str) -> dict[str, Any]:
        """Validate `token` and return its claims, or raise AccessValidationError."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise AccessValidationError("malformed token") from exc

        kid = header.get("kid")
        if not kid:
            raise AccessValidationError("token missing kid")

        signing_key = await self._signing_key(kid)

        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except jwt.InvalidTokenError as exc:
            raise AccessValidationError("invalid token") from exc

        if self._allowed_emails:
            email = claims.get("email")
            if email not in self._allowed_emails:
                raise AccessValidationError("email not allowed")

        return claims


class CloudflareAccessMiddleware:
    """ASGI middleware that enforces Cloudflare Access JWT validation.

    Non-HTTP scopes (e.g. lifespan) pass straight through. Every HTTP request must
    carry a valid `Cf-Access-Jwt-Assertion` header or it is rejected with 401
    before reaching the wrapped application.
    """

    def __init__(self, app: ASGIApp, validator: CloudflareAccessValidator) -> None:
        self._app = app
        self._validator = validator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        token = headers.get(_ACCESS_HEADER)
        if not token:
            await self._reject(scope, receive, send, "missing access assertion")
            return

        try:
            await self._validator.validate(token)
        except AccessValidationError:
            await self._reject(scope, receive, send, "invalid access assertion")
            return

        await self._app(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, detail: str) -> None:
        """Send a 401 response without invoking the wrapped application."""
        response = JSONResponse({"error": "unauthorized", "detail": detail}, status_code=401)
        await response(scope, receive, send)
