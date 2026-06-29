"""Tests for the deep-link knowledge module (pure, no I/O)."""

from __future__ import annotations

import pytest

from homeassistant_mcp.media.deeplinks import (
    URL_CONTENT_TYPE,
    disney_plus_deeplink,
)


def test_disney_plus_deeplink_documented_format() -> None:
    content_id, content_type = disney_plus_deeplink("abc123")
    assert content_id == "https://www.disneyplus.com/browse/entity-abc123"
    assert content_type == "url"
    assert content_type == URL_CONTENT_TYPE


def test_disney_plus_deeplink_strips_whitespace() -> None:
    content_id, _ = disney_plus_deeplink("  xyz789  ")
    assert content_id == "https://www.disneyplus.com/browse/entity-xyz789"


def test_disney_plus_deeplink_rejects_empty() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        disney_plus_deeplink("   ")
