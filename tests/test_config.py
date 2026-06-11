from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from whatbox_media_mcp.config import Settings


def test_config_redacts_secrets(settings: Settings) -> None:
    summary = settings.redacted_summary()
    assert summary["radarr_api_key"] == "********"
    assert summary["sonarr_api_key"] == "********"
    assert summary["plex_token"] == "********"
    assert summary["mcp_bearer_token"] == "********"


def test_config_requires_core_urls_and_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in [
        "RADARR_URL",
        "RADARR_API_KEY",
        "RADARR_DEFAULT_ROOT_FOLDER",
        "RADARR_DEFAULT_QUALITY_PROFILE_ID",
        "SONARR_URL",
        "SONARR_API_KEY",
        "SONARR_DEFAULT_ROOT_FOLDER",
        "SONARR_DEFAULT_QUALITY_PROFILE_ID",
        "PLEX_URL",
        "PLEX_TOKEN",
    ]:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, mcp_bearer_token=SecretStr("dev"))  # type: ignore[call-arg]


def test_config_verifies_plex_tls_by_default(settings: Settings) -> None:
    assert settings.plex_verify_tls is True
