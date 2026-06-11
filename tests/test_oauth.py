from __future__ import annotations

import base64
import hashlib
import secrets
import time

import pytest
from starlette.testclient import TestClient

from seedbox_mcp.oauth import OAuthStore, _verify_pkce
from seedbox_mcp.server import BearerAuthApp, create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _authorize(store: OAuthStore, *, bearer: str = "dev") -> tuple[str, str, str]:
    verifier, challenge = _pkce_pair()
    code = store.create_auth_code(challenge, "https://example.test/cb", "test-client")
    return code, verifier, challenge


# ---------------------------------------------------------------------------
# OAuthStore unit tests
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> OAuthStore:
    return OAuthStore(bearer_token="dev", base_url="https://example.test", access_token_ttl=3600)


def test_verify_pkce_valid() -> None:
    verifier, challenge = _pkce_pair()
    assert _verify_pkce(verifier, challenge) is True


def test_verify_pkce_wrong_verifier() -> None:
    _, challenge = _pkce_pair()
    assert _verify_pkce("wrong", challenge) is False


def test_consume_auth_code_valid(store: OAuthStore) -> None:
    code, verifier, _ = _authorize(store)
    result = store.consume_auth_code(code, verifier, "https://example.test/cb", "test-client")
    assert result is not None
    access, refresh = result
    assert len(access) > 0
    assert len(refresh) > 0


def test_consume_auth_code_wrong_verifier(store: OAuthStore) -> None:
    code, _, _ = _authorize(store)
    result = store.consume_auth_code(code, "bad-verifier", "https://example.test/cb", "test-client")
    assert result is None


def test_consume_auth_code_expired(store: OAuthStore) -> None:
    code, verifier, _ = _authorize(store)
    store._codes[code].expires_at = time.monotonic() - 1
    result = store.consume_auth_code(code, verifier, "https://example.test/cb", "test-client")
    assert result is None


def test_consume_auth_code_reuse(store: OAuthStore) -> None:
    code, verifier, _ = _authorize(store)
    store.consume_auth_code(code, verifier, "https://example.test/cb", "test-client")
    result = store.consume_auth_code(code, verifier, "https://example.test/cb", "test-client")
    assert result is None


def test_validate_access_token_valid(store: OAuthStore) -> None:
    code, verifier, _ = _authorize(store)
    access, _ = store.consume_auth_code(code, verifier, "https://example.test/cb", "test-client")
    assert store.validate_access_token(access) is True


def test_validate_access_token_expired(store: OAuthStore) -> None:
    code, verifier, _ = _authorize(store)
    access, _ = store.consume_auth_code(code, verifier, "https://example.test/cb", "test-client")
    store._access_tokens[access].expires_at = time.monotonic() - 1
    assert store.validate_access_token(access) is False


def test_refresh_valid(store: OAuthStore) -> None:
    code, verifier, _ = _authorize(store)
    _, refresh = store.consume_auth_code(code, verifier, "https://example.test/cb", "test-client")
    result = store.refresh(refresh)
    assert result is not None
    new_access, new_refresh = result
    assert new_access != refresh
    assert new_refresh != refresh


def test_refresh_rotation(store: OAuthStore) -> None:
    code, verifier, _ = _authorize(store)
    _, refresh = store.consume_auth_code(code, verifier, "https://example.test/cb", "test-client")
    store.refresh(refresh)
    assert store.refresh(refresh) is None  # old refresh token is gone


def test_discovery_metadata(store: OAuthStore) -> None:
    meta = store.discovery_metadata()
    assert meta["issuer"] == "https://example.test"
    assert "authorization_endpoint" in meta
    assert "token_endpoint" in meta
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert "refresh_token" in meta["grant_types_supported"]


# ---------------------------------------------------------------------------
# HTTP integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def client(settings) -> TestClient:  # type: ignore[no-untyped-def]
    return TestClient(create_app(settings), raise_server_exceptions=True)


def test_discovery_endpoint(client: TestClient) -> None:
    r = client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    data = r.json()
    assert "authorization_endpoint" in data
    assert data["code_challenge_methods_supported"] == ["S256"]


def test_authorize_get_valid(client: TestClient) -> None:
    _, challenge = _pkce_pair()
    r = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "test",
            "redirect_uri": "https://example.test/cb",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert r.status_code == 200
    assert b"<form" in r.content


def test_authorize_get_missing_challenge(client: TestClient) -> None:
    r = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "test",
            "redirect_uri": "https://example.test/cb",
        },
    )
    assert r.status_code == 400


def test_authorize_get_unsupported_method(client: TestClient) -> None:
    r = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "test",
            "redirect_uri": "https://example.test/cb",
            "code_challenge": "abc",
            "code_challenge_method": "plain",
        },
    )
    assert r.status_code == 400


def test_authorize_post_correct_token(client: TestClient) -> None:
    _, challenge = _pkce_pair()
    r = client.post(
        "/oauth/authorize",
        data={
            "bearer_token": "dev",
            "client_id": "test",
            "redirect_uri": "https://example.test/cb",
            "code_challenge": challenge,
            "state": "xyz",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    location = r.headers["location"]
    assert "code=" in location
    assert "state=xyz" in location


def test_authorize_post_wrong_token(client: TestClient) -> None:
    _, challenge = _pkce_pair()
    r = client.post(
        "/oauth/authorize",
        data={
            "bearer_token": "wrong",
            "client_id": "test",
            "redirect_uri": "https://example.test/cb",
            "code_challenge": challenge,
        },
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "location" not in r.headers
    assert b"Invalid token" in r.content


def test_token_exchange_valid(client: TestClient) -> None:
    verifier, challenge = _pkce_pair()
    # Get a code
    r = client.post(
        "/oauth/authorize",
        data={
            "bearer_token": "dev",
            "client_id": "test",
            "redirect_uri": "https://example.test/cb",
            "code_challenge": challenge,
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    code = r.headers["location"].split("code=")[1].split("&")[0]

    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": "https://example.test/cb",
            "client_id": "test",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_token_exchange_bad_verifier(client: TestClient) -> None:
    _, challenge = _pkce_pair()
    r = client.post(
        "/oauth/authorize",
        data={
            "bearer_token": "dev",
            "client_id": "test",
            "redirect_uri": "https://example.test/cb",
            "code_challenge": challenge,
        },
        follow_redirects=False,
    )
    code = r.headers["location"].split("code=")[1].split("&")[0]

    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": "bad-verifier",
            "redirect_uri": "https://example.test/cb",
            "client_id": "test",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_token_refresh(client: TestClient) -> None:
    verifier, challenge = _pkce_pair()
    r = client.post(
        "/oauth/authorize",
        data={
            "bearer_token": "dev",
            "client_id": "test",
            "redirect_uri": "https://example.test/cb",
            "code_challenge": challenge,
        },
        follow_redirects=False,
    )
    code = r.headers["location"].split("code=")[1].split("&")[0]
    exchange = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": "https://example.test/cb",
            "client_id": "test",
        },
    ).json()

    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": exchange["refresh_token"],
        },
    )
    assert r.status_code == 200
    new_data = r.json()
    assert "access_token" in new_data
    assert new_data["access_token"] != exchange["access_token"]


# ---------------------------------------------------------------------------
# BearerAuthApp accepts OAuth tokens
# ---------------------------------------------------------------------------


def test_bearer_auth_accepts_oauth_token(settings) -> None:  # type: ignore[no-untyped-def]
    store = OAuthStore(bearer_token="dev", base_url="https://example.test", access_token_ttl=3600)
    code, verifier, _ = _authorize(store)
    access, _ = store.consume_auth_code(code, verifier, "https://example.test/cb", "test-client")

    from starlette.types import Scope

    app = BearerAuthApp(None, "dev", store)  # type: ignore[arg-type]
    scope: Scope = {"type": "http", "headers": [(b"authorization", f"Bearer {access}".encode())]}
    assert app._authorized(scope) is True


def test_bearer_auth_rejects_expired_oauth_token(settings) -> None:  # type: ignore[no-untyped-def]
    store = OAuthStore(bearer_token="dev", base_url="https://example.test", access_token_ttl=3600)
    code, verifier, _ = _authorize(store)
    access, _ = store.consume_auth_code(code, verifier, "https://example.test/cb", "test-client")
    store._access_tokens[access].expires_at = time.monotonic() - 1

    from starlette.types import Scope

    app = BearerAuthApp(None, "dev", store)  # type: ignore[arg-type]
    scope: Scope = {"type": "http", "headers": [(b"authorization", f"Bearer {access}".encode())]}
    assert app._authorized(scope) is False
