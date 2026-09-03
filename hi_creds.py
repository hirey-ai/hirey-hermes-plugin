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
    # Per-PROCESS temp name. The claude install.sh, the hi-onboard skill, and
    # this module can all rewrite ~/.config/hi/credentials.json concurrently on
    # one box (claude+hermes share the file). A SHARED temp name
    # (credentials.json.tmp) lets two concurrent writers interleave bytes and
    # publish a corrupt/truncated file; a unique per-PID temp isolates each
    # write while os.replace stays atomic (last complete write wins).
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
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


class CredentialsCorruptError(RuntimeError):
    """Creds file is PRESENT but unusable (unreadable / bad JSON / no client_id).

    Distinct from 'absent' (None) so callers NEVER silently re-register a new
    agent over a present-but-broken identity — doing so orphans the user's agent
    + data, and on a box that shares ~/.config/hi with the Claude plugin it
    re-identifies BOTH hosts. Mirrors the claude install.sh refuse-on-corrupt guard.
    """


def load_strict() -> Optional[Dict[str, Any]]:
    """Like load(), but RAISES CredentialsCorruptError when the file is present
    yet unusable. Returns None ONLY when the file is genuinely absent. Use this
    anywhere a None would otherwise trigger a re-register (ensure_ready /
    hi_agent_install) so a transient bad read can't spawn an orphan agent."""
    p = credentials_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CredentialsCorruptError(f"{p}: unreadable / invalid JSON ({exc})") from exc
    if not isinstance(data, dict) or not data.get("client_id"):
        raise CredentialsCorruptError(f"{p}: present but has no client_id (corrupt/empty)")
    return data


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
    metadata: Optional[Dict[str, Any]] = None,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Register a fresh anonymous Hi agent and persist its long-lived client_credentials.

    Optional metadata is forwarded to /v1/agents/register and lands on
    agents.metadata_json — currently used for `channel_code` referrer attribution
    when the user invoked Hi through an owner-page or invite-link prompt.

    Returns the saved credentials dict (no access_token yet — call refresh_token after).
    """
    body: Dict[str, Any] = {"display_name": display_name, "agent_kind": "external"}
    if metadata:
        body["metadata"] = metadata
    resp = httpx.post(
        f"{platform_base_url}/v1/agents/register",
        json=body,
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


def ensure_ready(
    *,
    platform_base_url: str = DEFAULT_PLATFORM_BASE_URL,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Idempotent end-to-end bootstrap.

    1. Load creds; register anonymously if missing.
    2. Refresh access_token if missing / near expiry.

    A pending installation is intentionally ready for anonymous public reads.
    Identity binding, rather than a client-side activation call, unlocks private
    Workspace reads and writes.

    Optional metadata is only used on the **first** register call (when creds
    don't exist yet). Once an agent identity is persisted, subsequent
    ensure_ready calls won't re-register, so passing metadata in later is a
    no-op — callers that need to update an existing installation's metadata
    after the fact should call /v1/agent-installation/update directly (or via
    a separate helper). Today the only metadata field in use is `channel_code`
    for owner-page / invite-link referrer attribution.

    Returns the live creds dict. Raises on platform unreachable.
    """
    # load_strict raises CredentialsCorruptError on a present-but-unusable file
    # so we NEVER silently mint a new agent over a broken identity (orphan + data
    # loss). Genuinely-absent (None) is the only case that registers fresh.
    creds = load_strict()
    if creds is None:
        creds = anonymous_register(platform_base_url=platform_base_url, metadata=metadata)
    if not token_is_fresh(creds):
        creds = refresh_token(creds)
    return creds
