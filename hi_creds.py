"""
Hi credentials lifecycle — share `~/.config/hi/credentials.json` (mode 600)
with the Claude Code installer so a user who installed Hirey for both hosts
keeps a single anonymous Hi identity across them.

No env vars, no `requires_env` in plugin.yaml: Hi anonymous client_credentials
register is zero-touch (one HTTP POST, no human input), so blocking install
on a Hermes env-var prompt would be strictly worse UX than the Claude path.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


DEFAULT_PLATFORM_BASE_URL = "https://hi.hirey.ai"
DEFAULT_AUDIENCE = "hirey-hi"
TOKEN_REFRESH_SKEW_SECONDS = 300
ANON_REGISTER_DISPLAY_NAME = "Hermes Agent (hirey-hi plugin)"


def credentials_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "hi"


def credentials_path() -> Path:
    return credentials_dir() / "credentials.json"


def _atomic_write(path: Path, data: str, *, mode: int = 0o600) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def load() -> Optional[Dict[str, Any]]:
    p = credentials_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("hirey-hi: credentials file unreadable (%s): %s", p, exc)
        return None


def save(creds: Dict[str, Any]) -> None:
    d = credentials_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    _atomic_write(credentials_path(), json.dumps(creds, indent=2))


def token_is_fresh(creds: Dict[str, Any]) -> bool:
    issued_at = int(creds.get("access_token_issued_at") or 0)
    expires_in = int(creds.get("access_token_expires_in") or 0)
    if not creds.get("access_token") or not expires_in:
        return False
    return time.time() < issued_at + expires_in - TOKEN_REFRESH_SKEW_SECONDS


def anonymous_register(
    *,
    platform_base_url: str = DEFAULT_PLATFORM_BASE_URL,
    display_name: str = ANON_REGISTER_DISPLAY_NAME,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Register a fresh anonymous Hi agent and persist its long-lived client_credentials.

    Returns the saved credentials dict (no access_token yet — call refresh_token after).
    """
    resp = httpx.post(
        f"{platform_base_url}/v1/agents/register",
        json={"display_name": display_name, "agent_kind": "external"},
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    creds = {
        "client_id":          body["auth"]["client_id"],
        "client_secret":      body["auth"]["client_secret"],
        "agent_id":           body["agent"]["agent_id"],
        "installation_id":    body["installation"]["installation_id"],
        "issuer":             body["auth"]["issuer"],
        "audience":           body["auth"]["audience"],
        "token_url":          body["auth"]["token_url"],
        "platform_base_url":  platform_base_url,
        "access_token":           None,
        "access_token_issued_at": 0,
        "access_token_expires_in": 0,
    }
    save(creds)
    return creds


def refresh_token(creds: Dict[str, Any], *, timeout: float = 15.0) -> Dict[str, Any]:
    """Mint a fresh access_token via client_credentials. Mutates + persists creds."""
    base = creds.get("platform_base_url") or DEFAULT_PLATFORM_BASE_URL
    audience = creds.get("audience") or DEFAULT_AUDIENCE
    resp = httpx.post(
        f"{base}/oauth/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     creds["client_id"],
            "client_secret": creds["client_secret"],
            "audience":      audience,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    tok = resp.json()
    if not tok.get("access_token"):
        raise RuntimeError(f"hi token endpoint returned no access_token: {tok}")
    creds["access_token"]            = tok["access_token"]
    creds["access_token_issued_at"]  = int(time.time())
    creds["access_token_expires_in"] = int(tok.get("expires_in") or 3600)
    save(creds)
    return creds


def activate(creds: Dict[str, Any], *, timeout: float = 15.0) -> Dict[str, Any]:
    """Idempotent `POST /v1/agents/activate` — moves install pending → active."""
    base = creds.get("platform_base_url") or DEFAULT_PLATFORM_BASE_URL
    resp = httpx.post(
        f"{base}/v1/agents/activate",
        headers={"authorization": f"Bearer {creds['access_token']}"},
        json={},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def ensure_ready(*, platform_base_url: str = DEFAULT_PLATFORM_BASE_URL) -> Dict[str, Any]:
    """Idempotent end-to-end bootstrap.

    1. Load creds; register anonymously if missing.
    2. Refresh access_token if missing / near expiry.
    3. Activate the install (no-op on subsequent calls).

    Returns the live creds dict. Raises on platform unreachable.
    """
    creds = load()
    if creds is None or not creds.get("client_id"):
        creds = anonymous_register(platform_base_url=platform_base_url)
    if not token_is_fresh(creds):
        creds = refresh_token(creds)
        try:
            activate(creds)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "hirey-hi: activate failed (status=%s body=%s) — token is fresh, continuing",
                exc.response.status_code, exc.response.text[:200],
            )
    return creds
