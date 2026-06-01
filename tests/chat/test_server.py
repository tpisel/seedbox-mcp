from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from whatbox_media_mcp.chat.auth import make_session
from whatbox_media_mcp.chat.config import ChatSettings
from whatbox_media_mcp.chat.server import create_chat_app


def _session_cookie(chat_settings: ChatSettings) -> str:
    return make_session("mum", chat_settings.chat_session_secret.get_secret_value())


def _make_transport(chat_settings: ChatSettings) -> httpx.ASGITransport:
    app = create_chat_app(chat_settings)
    return httpx.ASGITransport(app=app)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Root redirect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_root_redirects_to_chat(chat_settings: ChatSettings) -> None:
    transport = _make_transport(chat_settings)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        response = await client.get("/")
    assert response.status_code in (301, 302, 307, 308)
    assert response.headers["location"] == "/chat"


# ---------------------------------------------------------------------------
# /api/chat — auth guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_endpoint_unauthenticated_redirects(chat_settings: ChatSettings) -> None:
    transport = _make_transport(chat_settings)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        response = await client.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 302
    assert "/auth/login" in response.headers["location"]


# ---------------------------------------------------------------------------
# /api/chat — authenticated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_endpoint_returns_reply(chat_settings: ChatSettings) -> None:
    with patch("whatbox_media_mcp.chat.server.chat_turn", new=AsyncMock(return_value=("Great choice!", []))):
        transport = _make_transport(chat_settings)
        cookie = _session_cookie(chat_settings)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
            client.cookies.set("plex_session", cookie)
            response = await client.post("/api/chat", json={"message": "hi", "history": []})

    assert response.status_code == 200
    data = response.json()
    assert data["reply"] == "Great choice!"
    assert "history" in data
