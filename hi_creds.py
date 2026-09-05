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
import base64
import re
import tempfile
from contextlib import contextmanager
from urllib.parse import urlsplit
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
PLUGIN_VERSION = "0.2.4"


def validate_platform_base(raw: str) -> str:
    base = raw.rstrip('/')
    parsed = urlsplit(base)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.scheme not in ('http', 'https')
            or (parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'))):
        raise ValueError("Hi requires HTTPS except for an explicit loopback test endpoint")
    return base


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
    fd, name = tempfile.mkstemp(prefix=f"{path.name}.tmp.", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def credential_lock():
    """Shared with Claude's installer; registration and pending-token rotation serialize."""
    directory = credentials_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    lock = directory / ".register.lock"
    deadline = time.monotonic() + 30
    while True:
        try:
            lock.mkdir(mode=0o700)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Another Hi process holds the credential lock; retry after it completes")
            time.sleep(0.1)
    try:
        yield
    finally:
        lock.rmdir()


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
    if not isinstance(data, dict) or any(not isinstance(data.get(k), str) or not data[k] for k in ("client_id", "client_secret")):
        raise CredentialsCorruptError(f"{p}: present but has incomplete client credentials")
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
    """Create one pending Agent through the modern API-key bootstrap contract.

    A durable non-secret marker prevents duplicate identity creation after an
    uncertain response. Registration has no server-side idempotency contract.
    """
    if metadata:
        raise ValueError("Referral metadata is not supported by the current Hi bootstrap; no registration was attempted")
    platform_base_url = validate_platform_base(platform_base_url)
    with credential_lock():
        existing = load_strict()
        if existing is not None:
            return existing
        marker = credentials_dir() / ".registration-pending.json"
        if marker.exists():
            raise RuntimeError("Previous Hi registration outcome is uncertain; reconcile it before retrying")
        _atomic_write(marker, json.dumps({"status": "outcome_unknown", "host": "hermes", "started_at": int(time.time())}))
        try:
            resp = httpx.post(f"{platform_base_url.rstrip('/')}/v1/agents/api-keys",
                              json={"agent_type": "hermes", "client_version": PLUGIN_VERSION,
                                    "display_name": display_name}, timeout=timeout)
            resp.raise_for_status()
            creds = decode_registration(resp.json(), platform_base_url)
            save(creds)
        except Exception:
            raise RuntimeError("Hi registration failed or returned an invalid response; reconcile the pending attempt before retrying") from None
        marker.unlink()
        return creds


def decode_registration(body: Any, platform_base_url: str) -> Dict[str, Any]:
    """Strict decode without ever including the response or API key in errors."""
    try:
        if not isinstance(body, dict) or body.get("status") != "pending":
            raise ValueError()
        agent_id, api_key = body.get("agent_id"), body.get("api_key")
        if not isinstance(agent_id, str) or not agent_id or not isinstance(api_key, str) or not api_key.startswith("hi_ak_"):
            raise ValueError()
        encoded = api_key[6:]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
            raise ValueError()
        decoded = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
        key = json.loads(decoded.decode('utf-8'))
        if not isinstance(key, dict) or type(key.get('v')) is not int or key['v'] != 1:
            raise ValueError()
        if any(not isinstance(key.get(k), str) or not key[k] or len(key[k]) > limit for k, limit in (("id", 100), ("secret", 500))):
            raise ValueError()
    except Exception:
        raise ValueError("Invalid Hi registration response") from None
    base = platform_base_url.rstrip('/')
    return dict(client_id=key['id'], client_secret=key['secret'], agent_id=agent_id,
                status='pending', audience=DEFAULT_AUDIENCE, token_url=f'{base}/oauth/token',
                platform_base_url=base, access_token=None, access_token_issued_at=0, access_token_expires_in=0)


def refresh_token(creds: Dict[str, Any], *, timeout: float = 15.0, force: bool = False) -> Dict[str, Any]:
    """Mint a fresh access_token via client_credentials. Mutates + persists creds."""
    with credential_lock():
        current = load_strict()
        if current is not None:
            if (current['client_id'] != creds['client_id'] or
                    validate_platform_base(current.get('platform_base_url') or DEFAULT_PLATFORM_BASE_URL) !=
                    validate_platform_base(creds.get('platform_base_url') or DEFAULT_PLATFORM_BASE_URL)):
                raise RuntimeError("Hi identity changed; reload before refreshing")
            # Another host may have already rotated the pending Agent token.
            if token_is_fresh(current) and (not force or current.get('access_token') != creds.get('access_token')):
                return current
            creds = current
        return _refresh_token_locked(creds, timeout=timeout)


def _refresh_token_locked(creds: Dict[str, Any], *, timeout: float) -> Dict[str, Any]:
    base = validate_platform_base(creds.get("platform_base_url") or DEFAULT_PLATFORM_BASE_URL)
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
    if not isinstance(tok, dict) or not isinstance(tok.get("access_token"), str) or not tok["access_token"] or type(tok.get("expires_in")) not in (int, float) or tok["expires_in"] <= 0:
        raise RuntimeError("Hi token endpoint returned an invalid response")
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

    Legacy metadata arguments are accepted for compatibility but the modern
    API-key bootstrap does not persist them. Never call retired registration
    or installation-update routes to work around this boundary.

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
