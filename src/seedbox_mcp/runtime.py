from __future__ import annotations

from dataclasses import dataclass

from seedbox_mcp.clients.arr import ArrClient
from seedbox_mcp.clients.plex import PlexClient
from seedbox_mcp.clients.tautulli import TautulliClient
from seedbox_mcp.config import Settings


@dataclass(frozen=True)
class Services:
    settings: Settings
    radarr: ArrClient
    sonarr: ArrClient
    plex: PlexClient
    tautulli: TautulliClient | None = None


def build_services(settings: Settings) -> Services:
    tautulli = None
    if settings.tautulli_enabled and settings.tautulli_base_url and settings.tautulli_api_key:
        tautulli = TautulliClient(
            settings.tautulli_base_url,
            settings.tautulli_api_key.get_secret_value(),
        )
    return Services(
        settings=settings,
        radarr=ArrClient(settings.radarr_base_url, settings.radarr_api_key.get_secret_value()),
        sonarr=ArrClient(settings.sonarr_base_url, settings.sonarr_api_key.get_secret_value()),
        plex=PlexClient(settings.plex_base_url, settings.plex_token.get_secret_value(), settings.plex_verify_tls),
        tautulli=tautulli,
    )
