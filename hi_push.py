"""
Push delivery wiring — v0.2.

Two pieces:

1. **Local webhook subscription** in `~/.hermes/webhook_subscriptions.json`. The
   Hermes gateway exposes one HTTP endpoint per subscription at
   `http://<host>:<port>/webhooks/<name>`. Events POSTed there fire an LLM
   turn (or a direct delivery to Telegram/Slack/etc., per the subscription's
   `deliver` field).

2. **Hi-side endpoint registration** via `PUT /v1/agents/me/endpoints` with
   profile `generic.event-webhook.v1`. Hi cloud's delivery worker reads this
   table and POSTs each outbox event to the registered URL.

`ensure_local_subscription()` is safe to call at register() time — it's a
local file mutation, no network. `register_hi_endpoint()` makes a network
call and is left to an explicit user-triggered tool (`hi_push_install`) so
register() stays side-effect-free against Hi cloud.

For laptop users behind NAT, Hi cloud cannot reach `localhost` URLs. The
plugin won't lie about that — the `hi_push_install` tool surfaces a clear
warning when the local subscription URL is loopback, and offers to register
a user-supplied public URL (e.g. `https://hermes.example.com/webhooks/hi`)
or skip Hi-side registration entirely (keeping the local URL ready for when
the user later puts a tunnel in front).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

from . import hi_creds, hi_client

logger = logging.getLogger(__name__)


SUBSCRIPTION_NAME = "hi"
DEFAULT_WEBHOOK_PORT = 8644
HI_ENDPOINT_PROFILE = "generic.event-webhook.v1"


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def subscriptions_path() -> Path:
    return hermes_home() / "webhook_subscriptions.json"


def _load_subs() -> Dict[str, Any]:
    p = subscriptions_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_subs(subs: Dict[str, Any]) -> None:
    p = subscriptions_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(subs, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _resolve_local_webhook_url(public_host_override: Optional[str] = None) -> str:
    """Best-guess of the URL Hi cloud should POST events to."""
    host = (public_host_override or "").strip() or _detected_public_host()
    port = int(os.environ.get("WEBHOOK_PORT") or DEFAULT_WEBHOOK_PORT)
    return f"http://{host}:{port}/webhooks/{SUBSCRIPTION_NAME}"


def _detected_public_host() -> str:
    """Pick the best display host for the user's webhook URL.

    Priority: ``WEBHOOK_PUBLIC_HOST`` env > ``WEBHOOK_HOST`` env > ``localhost``.
    Loopback addresses are returned as-is — `register_hi_endpoint()` warns
    when it sees one.
    """
    return (
        os.environ.get("WEBHOOK_PUBLIC_HOST")
        or os.environ.get("WEBHOOK_HOST")
        or "localhost"
    )


def url_is_loopback(url: str) -> bool:
    lowered = url.lower()
    return (
        "://localhost" in lowered
        or "://127." in lowered
        or "://0.0.0.0" in lowered
    )


def ensure_local_subscription() -> Dict[str, Any]:
    """Idempotent — create the local `hi` subscription if missing.

    Returns the live subscription dict. The HMAC secret stays stable across
    re-runs so the matching auth blob in the Hi-side endpoint stays valid.
    """
    subs = _load_subs()
    if SUBSCRIPTION_NAME in subs and subs[SUBSCRIPTION_NAME].get("secret"):
        return subs[SUBSCRIPTION_NAME]
    subs[SUBSCRIPTION_NAME] = {
        "description": "Hirey Hi inbound events (replies, meeting confirms, match updates)",
        "events": [],   # accept all event types
        "secret": secrets.token_urlsafe(32),
        "prompt": (
            "You received a Hirey Hi event of kind `{event_type}`. The full payload is:\n\n"
            "```json\n{payload}\n```\n\n"
            "Surface the human-relevant fields to the user (sender, body, meeting time, "
            "listing title — whatever applies). Group multiple events by primary entity. "
            "Then call `hi_pull_events` with the event_id in `ack_event_ids` so the event "
            "stops redelivering."
        ),
        "skills": ["hi-events"],
        "deliver": "log",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "created_by": "hirey-hi-plugin",
    }
    _save_subs(subs)
    return subs[SUBSCRIPTION_NAME]


def remove_local_subscription() -> bool:
    """Remove the local `hi` subscription. Returns True if it existed."""
    subs = _load_subs()
    if SUBSCRIPTION_NAME not in subs:
        return False
    del subs[SUBSCRIPTION_NAME]
    _save_subs(subs)
    return True


def _client() -> hi_client.HiClient:
    return hi_client.HiClient()


def register_hi_endpoint(
    *,
    webhook_url: str,
    secret: str,
    status: str = "active",
) -> Dict[str, Any]:
    """PUT a `generic.event-webhook.v1` endpoint on Hi platform side.

    Hi's delivery worker reads `agent_endpoints` and POSTs each outbox event
    to the registered URL with an HMAC-SHA256 signature derived from `secret`.
    Returns the Hi response (includes the updated endpoint list).
    """
    body = [{
        "kind":    "webhook",
        "profile": HI_ENDPOINT_PROFILE,
        "status":  status,
        "url":     webhook_url,
        "auth":    {"type": "hmac-sha256", "secret": secret},
        "config":  {},
    }]
    # Hermes plugin uses HTTP put via httpx — extend HiClient if not present.
    # HiClient only exposes get + post + call_capability today, so we do this
    # inline against the bearer-bound client.
    base, token = _client()._bearer()
    import httpx
    resp = httpx.put(
        f"{base}/v1/agents/me/endpoints",
        headers={
            "authorization": f"Bearer {token}",
            "content-type":  "application/json",
        },
        json=body,
        timeout=30.0,
    )
    if resp.status_code >= 400:
        try:
            err_body = resp.json()
        except Exception:
            err_body = resp.text
        raise hi_client.HiAPIError(
            f"hi endpoint register returned {resp.status_code}",
            status_code=resp.status_code,
            body=err_body,
        )
    return resp.json()


def remove_hi_endpoint() -> Dict[str, Any]:
    """Remove the Hermes webhook endpoint on Hi side (status=disabled).

    We disable rather than DELETE because `agent_endpoints` doesn't have a
    public DELETE route — `disabled` stops delivery and surfaces in the
    listing so the user can re-enable later.
    """
    base, token = _client()._bearer()
    import httpx
    # Fetch current endpoints, keep all non-generic-webhook ones, push the
    # generic webhook back as disabled.
    cur = httpx.get(
        f"{base}/v1/agents/me/endpoints",
        headers={"authorization": f"Bearer {token}"},
        timeout=15.0,
    )
    cur.raise_for_status()
    current = cur.json().get("endpoints", [])
    next_set = []
    found = False
    for ep in current:
        if ep.get("profile") == HI_ENDPOINT_PROFILE:
            next_set.append({**ep, "status": "disabled"})
            found = True
        else:
            next_set.append(ep)
    if not found:
        return {"ok": True, "noop": True, "reason": "no generic.event-webhook.v1 endpoint to remove"}
    resp = httpx.put(
        f"{base}/v1/agents/me/endpoints",
        headers={"authorization": f"Bearer {token}", "content-type": "application/json"},
        json=next_set,
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def list_hi_endpoints() -> Dict[str, Any]:
    return _client().get("/v1/agents/me/endpoints")


def trigger_test_delivery(
    *,
    topic: str = "agent.message.created",
    text: str = "Push delivery probe from hi_push_status",
) -> Dict[str, Any]:
    """POST /v1/agents/me/test-delivery — fire a synthetic event through the
    delivery worker so the user can verify push end-to-end without waiting
    for a real Hi event.

    Valid topics (from `AGENT_GATEWAY_EVENT_TOPICS`):
    `agent.message.created`, `pairing.created`, `pairing.updated`,
    `listing_matching_session.updated`, `meeting.negotiation.updated`,
    `meeting.execution.requested`, `hi.release.published`.
    """
    return _client().post(
        "/v1/agents/me/test-delivery",
        {"topic": topic, "text": text, "profile": HI_ENDPOINT_PROFILE},
    )
