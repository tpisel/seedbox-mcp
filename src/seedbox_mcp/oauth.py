from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

logger = logging.getLogger("seedbox_mcp.oauth")

_CODE_TTL = 600  # 10 minutes
_REFRESH_TTL = 2_592_000  # 30 days

# Access tokens, refresh tokens, and their expiry are persisted across restarts so
# that a process restart (cron @reboot, manual bounce) doesn't force every client
# through the consent form. Authorization codes are deliberately *not* persisted —
# they're short-lived (10 min) and only useful mid-flow.

_CONSENT_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Authorize – Seedbox MCP</title>
<style>
body{{font-family:sans-serif;max-width:420px;margin:4rem auto;padding:0 1rem}}
h2{{margin-bottom:.25rem}}p{{color:#555;margin-top:0}}
label{{display:block;margin-bottom:.25rem;font-size:.9rem}}
input[type=password]
{{width:100%;box-sizing:border-box;padding:.45rem .5rem;margin-bottom:1rem;border:1px solid #ccc;border-radius:4px}}
button{{background:#1a1a1a;color:#fff;border:none;padding:.5rem 1.2rem;border-radius:4px;cursor:pointer;font-size:1rem}}
button:hover{{background:#333}}.err{{color:#c00;margin-bottom:.75rem;font-size:.9rem}}
</style></head>
<body>
<h2>Seedbox MCP</h2>
<p>Authorizing <strong>{client_id}</strong></p>
{error_html}
<form method="post">
  <input type="hidden" name="client_id"       value="{client_id}">
  <input type="hidden" name="redirect_uri"    value="{redirect_uri}">
  <input type="hidden" name="code_challenge"  value="{code_challenge}">
  <input type="hidden" name="state"           value="{state}">
  <label>Bearer token
    <input type="password" name="bearer_token" autofocus autocomplete="current-password">
  </label>
  <button type="submit">Authorize</button>
</form>
</body></html>"""


@dataclass
class _AuthCode:
    code_challenge: str
    redirect_uri: str
    client_id: str
    expires_at: float


@dataclass
class _AccessToken:
    expires_at: float


@dataclass
class _RefreshToken:
    expires_at: float


@dataclass
class OAuthStore:
    _bearer_token: str
    _base_url: str
    _ttl: int = 3600
    _codes: dict[str, _AuthCode] = field(default_factory=dict)
    _access_tokens: dict[str, _AccessToken] = field(default_factory=dict)
    _refresh_tokens: dict[str, _RefreshToken] = field(default_factory=dict)

    def __init__(
        self,
        bearer_token: str,
        base_url: str,
        access_token_ttl: int = 3600,
        state_path: Path | None = None,
    ) -> None:
        self._bearer_token = bearer_token
        self._base_url = base_url.rstrip("/")
        self._ttl = access_token_ttl
        self._state_path = state_path
        self._codes: dict[str, _AuthCode] = {}
        self._access_tokens: dict[str, _AccessToken] = {}
        self._refresh_tokens: dict[str, _RefreshToken] = {}
        self._load_state()

    # ------------------------------------------------------------------
    # Auth code
    # ------------------------------------------------------------------

    def create_auth_code(self, code_challenge: str, redirect_uri: str, client_id: str) -> str:
        self._purge(_CODE_TTL, self._codes)
        code = secrets.token_urlsafe(32)
        self._codes[code] = _AuthCode(
            code_challenge=code_challenge,
            redirect_uri=redirect_uri,
            client_id=client_id,
            expires_at=time.time() + _CODE_TTL,
        )
        return code

    def consume_auth_code(self, code: str, verifier: str, redirect_uri: str, client_id: str) -> tuple[str, str] | None:
        entry = self._codes.pop(code, None)
        if not entry:
            return None
        if entry.expires_at < time.time():
            return None
        if entry.client_id != client_id or entry.redirect_uri != redirect_uri:
            return None
        if not _verify_pkce(verifier, entry.code_challenge):
            return None
        return self._issue_tokens()

    # ------------------------------------------------------------------
    # Token validation and refresh
    # ------------------------------------------------------------------

    def validate_access_token(self, token: str) -> bool:
        entry = self._access_tokens.get(token)
        if not entry:
            return False
        if entry.expires_at < time.time():
            self._access_tokens.pop(token, None)
            return False
        return True

    def refresh(self, refresh_token: str) -> tuple[str, str] | None:
        entry = self._refresh_tokens.pop(refresh_token, None)
        if not entry:
            return None
        if entry.expires_at < time.time():
            self._save_state()  # persist the pop even if expired
            return None
        return self._issue_tokens()

    # ------------------------------------------------------------------
    # Discovery metadata
    # ------------------------------------------------------------------

    def discovery_metadata(self) -> dict[str, Any]:
        return {
            "issuer": self._base_url,
            "authorization_endpoint": f"{self._base_url}/oauth/authorize",
            "token_endpoint": f"{self._base_url}/oauth/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
        }

    # ------------------------------------------------------------------
    # Starlette route handlers
    # ------------------------------------------------------------------

    async def handle_discovery(self, request: Request) -> JSONResponse:
        return JSONResponse(self.discovery_metadata())

    async def handle_authorize_get(self, request: Request) -> Response:
        params = request.query_params
        error = _validate_authorize_params(params)
        if error:
            return JSONResponse({"error": "invalid_request", "error_description": error}, status_code=400)
        return HTMLResponse(_render_consent(params, error_html=""))

    async def handle_authorize_post(self, request: Request) -> Response:
        form = await request.form()
        client_id = str(form.get("client_id", ""))
        redirect_uri = str(form.get("redirect_uri", ""))
        code_challenge = str(form.get("code_challenge", ""))
        state = str(form.get("state", ""))
        bearer_token = str(form.get("bearer_token", ""))

        if not hmac.compare_digest(bearer_token, self._bearer_token):
            return HTMLResponse(
                _render_consent(
                    {
                        "client_id": client_id,
                        "redirect_uri": redirect_uri,
                        "code_challenge": code_challenge,
                        "state": state,
                    },
                    error_html='<p class="err">Invalid token. Try again.</p>',
                ),
                status_code=200,
            )

        code = self.create_auth_code(code_challenge, redirect_uri, client_id)
        qs = urlencode({"code": code, "state": state} if state else {"code": code})
        return RedirectResponse(f"{redirect_uri}?{qs}", status_code=302)

    async def handle_token(self, request: Request) -> JSONResponse:
        form = await request.form()
        grant_type = str(form.get("grant_type", ""))

        if grant_type == "authorization_code":
            code = str(form.get("code", ""))
            verifier = str(form.get("code_verifier", ""))
            redirect_uri = str(form.get("redirect_uri", ""))
            client_id = str(form.get("client_id", ""))
            result = self.consume_auth_code(code, verifier, redirect_uri, client_id)
            if not result:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            access_token, refresh_token = result
            return JSONResponse(self._token_response(access_token, refresh_token))

        if grant_type == "refresh_token":
            refresh_token = str(form.get("refresh_token", ""))
            result = self.refresh(refresh_token)
            if not result:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            access_token, new_refresh = result
            return JSONResponse(self._token_response(access_token, new_refresh))

        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _issue_tokens(self) -> tuple[str, str]:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        now = time.time()
        self._access_tokens[access] = _AccessToken(expires_at=now + self._ttl)
        self._refresh_tokens[refresh] = _RefreshToken(expires_at=now + _REFRESH_TTL)
        self._save_state()
        return access, refresh

    def _token_response(self, access_token: str, refresh_token: str) -> dict[str, Any]:
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": self._ttl,
            "refresh_token": refresh_token,
        }

    @staticmethod
    def _purge(ttl: float, store: dict[str, Any]) -> None:
        now = time.time()
        expired = [k for k, v in store.items() if v.expires_at < now]
        for k in expired:
            del store[k]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read OAuth state file %s; starting fresh", self._state_path)
            return
        now = time.time()
        for token, entry in (raw.get("access_tokens") or {}).items():
            exp = float(entry.get("expires_at", 0))
            if exp > now:
                self._access_tokens[token] = _AccessToken(expires_at=exp)
        for token, entry in (raw.get("refresh_tokens") or {}).items():
            exp = float(entry.get("expires_at", 0))
            if exp > now:
                self._refresh_tokens[token] = _RefreshToken(expires_at=exp)
        logger.info(
            "Loaded OAuth state: %d access, %d refresh",
            len(self._access_tokens),
            len(self._refresh_tokens),
        )

    def _save_state(self) -> None:
        if self._state_path is None:
            return
        payload = {
            "access_tokens": {t: {"expires_at": e.expires_at} for t, e in self._access_tokens.items()},
            "refresh_tokens": {t: {"expires_at": e.expires_at} for t, e in self._refresh_tokens.items()},
        }
        # Atomic write: tmp file in the same dir, chmod 0600, then rename.
        # Same-dir tmp ensures the rename is atomic (no cross-device move).
        parent = self._state_path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=".oauth_state.", dir=str(parent))
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(payload, f)
                os.chmod(tmp_path, 0o600)
                os.replace(tmp_path, self._state_path)
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise
        except OSError:
            logger.exception("Failed to persist OAuth state to %s", self._state_path)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _verify_pkce(verifier: str, challenge: str) -> bool:
    digest = hashlib.sha256(verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return hmac.compare_digest(computed, challenge)


def _validate_authorize_params(params: Any) -> str:
    if params.get("response_type") != "code":
        return "response_type must be 'code'"
    if not params.get("client_id"):
        return "client_id is required"
    if not params.get("redirect_uri"):
        return "redirect_uri is required"
    if not params.get("code_challenge"):
        return "code_challenge is required"
    if params.get("code_challenge_method", "S256") != "S256":
        return "only code_challenge_method=S256 is supported"
    redirect_uri = params.get("redirect_uri", "")
    parsed = urlparse(redirect_uri)
    if not parsed.scheme or not parsed.netloc:
        return "redirect_uri must be an absolute URL"
    return ""


def _render_consent(params: Any, error_html: str) -> str:
    e = html.escape
    return _CONSENT_HTML.format(
        client_id=e(str(params.get("client_id", ""))),
        redirect_uri=e(str(params.get("redirect_uri", ""))),
        code_challenge=e(str(params.get("code_challenge", ""))),
        state=e(str(params.get("state", ""))),
        error_html=error_html,
    )
