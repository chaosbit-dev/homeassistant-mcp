"""Tests for Cloudflare Access JWT validation and the ASGI middleware."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from homeassistant_mcp.access import (
    AccessValidationError,
    CloudflareAccessMiddleware,
    CloudflareAccessValidator,
)
from homeassistant_mcp.config import Settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from starlette.requests import Request

TEAM_DOMAIN = "example.cloudflareaccess.com"
JWKS_URL = f"https://{TEAM_DOMAIN}/cdn-cgi/access/certs"
ISSUER = f"https://{TEAM_DOMAIN}"
AUD = "test-aud-tag"
ALLOWED_EMAIL = "user@example.com"


def _make_key(kid: str) -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    assert isinstance(jwk, dict)
    jwk["kid"] = kid
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"
    return private_key, jwk


def _sign(
    private_key: rsa.RSAPrivateKey,
    kid: str,
    *,
    aud: str = AUD,
    iss: str = ISSUER,
    email: str = ALLOWED_EMAIL,
    exp_delta: int = 300,
    nbf_delta: int = 0,
) -> str:
    now = int(time.time())
    claims = {
        "aud": aud,
        "iss": iss,
        "email": email,
        "iat": now,
        "nbf": now + nbf_delta,
        "exp": now + exp_delta,
        "sub": "user-123",
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def keypair() -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    return _make_key("kid-1")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        url="http://ha.test:8123",
        token="ha-token",
        require_access_jwt=True,
        access_aud=AUD,
        access_team_domain=TEAM_DOMAIN,
        access_allowed_emails=[ALLOWED_EMAIL],
    )


@pytest.fixture
def jwks_route(keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]]) -> Iterator[dict[str, Any]]:
    _, jwk = keypair
    body = {"keys": [jwk]}
    with respx.mock:
        respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=body))
        yield body


class TestValidator:
    async def test_valid_token_passes(
        self,
        settings: Settings,
        keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
        jwks_route: dict[str, Any],
    ) -> None:
        private_key, _ = keypair
        validator = CloudflareAccessValidator(settings)
        claims = await validator.validate(_sign(private_key, "kid-1"))
        assert claims["email"] == ALLOWED_EMAIL

    async def test_bad_signature_rejected(
        self,
        settings: Settings,
        jwks_route: dict[str, Any],
    ) -> None:
        # Sign with a *different* key than the one published in the JWKS.
        other_key, _ = _make_key("kid-1")
        validator = CloudflareAccessValidator(settings)
        with pytest.raises(AccessValidationError):
            await validator.validate(_sign(other_key, "kid-1"))

    async def test_wrong_aud_rejected(
        self,
        settings: Settings,
        keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
        jwks_route: dict[str, Any],
    ) -> None:
        private_key, _ = keypair
        validator = CloudflareAccessValidator(settings)
        with pytest.raises(AccessValidationError):
            await validator.validate(_sign(private_key, "kid-1", aud="someone-else"))

    async def test_wrong_issuer_rejected(
        self,
        settings: Settings,
        keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
        jwks_route: dict[str, Any],
    ) -> None:
        private_key, _ = keypair
        validator = CloudflareAccessValidator(settings)
        with pytest.raises(AccessValidationError):
            await validator.validate(_sign(private_key, "kid-1", iss="https://evil.example"))

    async def test_expired_rejected(
        self,
        settings: Settings,
        keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
        jwks_route: dict[str, Any],
    ) -> None:
        private_key, _ = keypair
        validator = CloudflareAccessValidator(settings)
        with pytest.raises(AccessValidationError):
            await validator.validate(_sign(private_key, "kid-1", exp_delta=-10))

    async def test_email_not_allowed_rejected(
        self,
        settings: Settings,
        keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
        jwks_route: dict[str, Any],
    ) -> None:
        private_key, _ = keypair
        validator = CloudflareAccessValidator(settings)
        with pytest.raises(AccessValidationError):
            await validator.validate(_sign(private_key, "kid-1", email="intruder@evil.example"))

    async def test_empty_allowlist_skips_email_check(
        self,
        keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
        jwks_route: dict[str, Any],
    ) -> None:
        private_key, _ = keypair
        settings = Settings(
            url="http://ha.test:8123",
            token="ha-token",
            access_aud=AUD,
            access_team_domain=TEAM_DOMAIN,
            access_allowed_emails=[],
        )
        validator = CloudflareAccessValidator(settings)
        claims = await validator.validate(_sign(private_key, "kid-1", email="anyone@example.com"))
        assert claims["email"] == "anyone@example.com"

    async def test_unknown_kid_triggers_refresh(
        self,
        settings: Settings,
    ) -> None:
        # First key published, then key rotates; validator must refresh on unknown kid.
        old_priv, old_jwk = _make_key("kid-old")
        new_priv, new_jwk = _make_key("kid-new")
        with respx.mock:
            route = respx.get(JWKS_URL)
            route.side_effect = [
                httpx.Response(200, json={"keys": [old_jwk]}),
                httpx.Response(200, json={"keys": [new_jwk]}),
            ]
            validator = CloudflareAccessValidator(settings)
            # Warm the cache with the old key.
            await validator.validate(_sign(old_priv, "kid-old"))
            # New kid is unknown -> forces a second fetch.
            claims = await validator.validate(_sign(new_priv, "kid-new"))
            assert claims["sub"] == "user-123"
            assert route.call_count == 2

    async def test_unknown_kid_after_refresh_rejected(
        self,
        settings: Settings,
        keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
        jwks_route: dict[str, Any],
    ) -> None:
        private_key, _ = keypair
        validator = CloudflareAccessValidator(settings)
        with pytest.raises(AccessValidationError, match="unknown signing key"):
            await validator.validate(_sign(private_key, "kid-missing"))


def _build_app(validator: CloudflareAccessValidator) -> CloudflareAccessMiddleware:
    async def endpoint(request: Request) -> PlainTextResponse:
        _ = request
        return PlainTextResponse("ok")

    inner = Starlette(routes=[Route("/mcp", endpoint, methods=["GET", "POST"])])
    return CloudflareAccessMiddleware(inner, validator)


class TestMiddleware:
    def test_missing_header_returns_401(
        self,
        settings: Settings,
        jwks_route: dict[str, Any],
    ) -> None:
        validator = CloudflareAccessValidator(settings)
        client = TestClient(_build_app(validator))
        resp = client.get("/mcp")
        assert resp.status_code == 401

    def test_valid_header_passes_through(
        self,
        settings: Settings,
        keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
        jwks_route: dict[str, Any],
    ) -> None:
        private_key, _ = keypair
        validator = CloudflareAccessValidator(settings)
        client = TestClient(_build_app(validator))
        token = _sign(private_key, "kid-1")
        resp = client.get("/mcp", headers={"Cf-Access-Jwt-Assertion": token})
        assert resp.status_code == 200
        assert resp.text == "ok"

    def test_invalid_header_returns_401(
        self,
        settings: Settings,
        keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
        jwks_route: dict[str, Any],
    ) -> None:
        validator = CloudflareAccessValidator(settings)
        client = TestClient(_build_app(validator))
        resp = client.get("/mcp", headers={"Cf-Access-Jwt-Assertion": "not-a-jwt"})
        assert resp.status_code == 401


class TestConfigToggle:
    def test_stdio_default_does_not_require_jwt(self) -> None:
        settings = Settings(url="http://ha.test:8123", token="ha-token")
        # stdio is the default transport; enforcement is gated on http elsewhere.
        assert settings.transport == "stdio"

    def test_toggle_off_disables_enforcement(self) -> None:
        settings = Settings(
            url="http://ha.test:8123",
            token="ha-token",
            transport="http",
            require_access_jwt=False,
        )
        # main()/_run_http only constructs the validator when require_access_jwt is True.
        assert settings.require_access_jwt is False

    def test_comma_separated_emails_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HA_URL", "http://ha.test:8123")
        monkeypatch.setenv("HA_TOKEN", "ha-token")
        monkeypatch.setenv("HA_ACCESS_ALLOWED_EMAILS", "a@example.com, b@example.com")
        settings = Settings()
        assert settings.access_allowed_emails == ["a@example.com", "b@example.com"]

    def test_derived_urls(self) -> None:
        settings = Settings(
            url="http://ha.test:8123",
            token="ha-token",
            access_team_domain=TEAM_DOMAIN,
        )
        assert settings.access_jwks_url == JWKS_URL
        assert settings.access_issuer == ISSUER
