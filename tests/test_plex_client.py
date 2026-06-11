from whatbox_media_mcp.clients.plex import PlexClient


def test_plex_client_verifies_tls_by_default() -> None:
    assert PlexClient("https://plex.example", "token")._session().verify is True


def test_plex_client_can_disable_tls_verification() -> None:
    assert PlexClient("https://plex.example", "token", verify_tls=False)._session().verify is False
