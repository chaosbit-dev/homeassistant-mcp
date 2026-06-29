"""Deep-link knowledge for media-player app launches.

This module encodes hard-won, confirmed knowledge about how to launch content
on the media players in this environment via the `play_media` service. It is
deliberately pure data + small pure helpers (no I/O, no HA client) so it stays
unit-testable and the tool layer can stay thin.

Confirmed patterns (v1):
- **Disney+** deep link: a title is launched by passing the canonical browse URL
  `https://www.disneyplus.com/browse/entity-{id}` as `media_content_id` with a
  `media_content_type` of `"url"`. The `{id}` is Disney+'s opaque entity id for
  the title (not a human title), so v1 takes the entity id as input.
- **Raw passthrough**: any caller that already knows the `media_content_id` /
  `media_content_type` HA expects can pass them straight through `play_media`.

Deferred to v2 (intentionally NOT built here):
- **Watchmode dynamic lookup**: resolving a human title ("Andor") to a streaming
  deep link via the Watchmode API. That requires a network call + API key and is
  a clean v2; v1 stays offline and deterministic.
- **Apple TV Companion app-launch specifics**: launching a specific app (vs. a
  deep link) over the Companion protocol. App launch is confirmed working in the
  environment, but encoding per-app launch payloads is left to v2.

Netflix caveat (confirmed live): `media_player.living_room` (Apple TV) exposes
`app_id`/`app_name` but NOT `media_title` while Netflix is playing — Netflix does
not publish now-playing metadata over the Companion protocol. The tool can launch
and control Netflix, it just cannot report the current title. That is a platform
limitation, not a bug in this tool.
"""

from __future__ import annotations

from typing import Final

#: Canonical Disney+ browse-URL template. `{entity_id}` is Disney+'s opaque
#: title id (e.g. the trailing segment of a disneyplus.com/browse/entity-... URL).
DISNEY_PLUS_BROWSE_URL: Final = "https://www.disneyplus.com/browse/entity-{entity_id}"

#: `media_content_type` HA expects for a URL-style deep link.
URL_CONTENT_TYPE: Final = "url"


def disney_plus_deeplink(entity_id: str) -> tuple[str, str]:
    """Build the `play_media` payload for a Disney+ title.

    Returns a `(media_content_id, media_content_type)` pair ready to hand to the
    `media_player.play_media` service.

    `entity_id` is Disney+'s opaque title id — the `entity-...` segment of a
    `https://www.disneyplus.com/browse/entity-{id}` URL — not a human-readable
    title. Mapping a human title to this id is the Watchmode v2 work.
    """
    eid = entity_id.strip()
    if not eid:
        msg = "Disney+ entity_id must be a non-empty title id."
        raise ValueError(msg)
    return DISNEY_PLUS_BROWSE_URL.format(entity_id=eid), URL_CONTENT_TYPE
