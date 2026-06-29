"""Configuration via environment variables."""

from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# DESIGN-GAP: These Cloudflare Access defaults are the live, non-secret values for
# the public `mcp.example.com` app (already committed in docs/cloudflare-access.md).
# The AUD tag is an identifier, not a credential, so baking it in as a default keeps
# the HTTP path fail-closed-secure by default while remaining overridable via env.
_DEFAULT_ACCESS_AUD = "<your-access-aud>"
_DEFAULT_ACCESS_TEAM_DOMAIN = "example.cloudflareaccess.com"
_DEFAULT_ACCESS_ALLOWED_EMAILS = ["user@example.com", "alt@example.com"]


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

    # --- Camera snapshot processing ---
    camera_max_dimension: int = Field(
        default=1280,
        description=(
            "Max pixel size of a snapshot's longest side. Larger frames are "
            "downscaled (aspect preserved) before being returned to the model."
        ),
    )
    camera_jpeg_quality: int = Field(
        default=75,
        ge=1,
        le=95,
        description="JPEG quality (1-95) used when recompressing camera snapshots.",
    )

    # --- Cloudflare Access JWT validation (HTTP transport only) ---
    require_access_jwt: bool = Field(
        default=True,
        description=(
            "Enforce Cloudflare Access `Cf-Access-Jwt-Assertion` validation. "
            "Only takes effect when transport == 'http'; stdio/local usage is "
            "never affected."
        ),
    )
    access_aud: str = Field(
        default=_DEFAULT_ACCESS_AUD,
        description="Expected Cloudflare Access application AUD tag.",
    )
    access_team_domain: str = Field(
        default=_DEFAULT_ACCESS_TEAM_DOMAIN,
        description="Cloudflare Access team domain, e.g. <team>.cloudflareaccess.com.",
    )
    access_allowed_emails: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(_DEFAULT_ACCESS_ALLOWED_EMAILS),
        description=(
            "Allowlist of `email` claim values. Comma-separated in env "
            "(HA_ACCESS_ALLOWED_EMAILS). Empty list disables the email check."
        ),
    )

    @field_validator("access_allowed_emails", mode="before")
    @classmethod
    def _split_emails(cls, value: object) -> object:
        """Accept comma-separated env strings as well as native lists."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def access_issuer(self) -> str:
        """Expected JWT `iss` claim for the configured team."""
        return f"https://{self.access_team_domain}"

    @property
    def access_jwks_url(self) -> str:
        """Cloudflare Access signing-key (JWKS) endpoint for the configured team."""
        return f"https://{self.access_team_domain}/cdn-cgi/access/certs"
