"""
Tool handlers for hirey-hi.

Hermes tool handler contract (verified against `tools/registry.py`): a
handler is a sync callable returning a string. We use the registry's
`tool_result(payload)` / `tool_error(msg)` helpers so payloads marshal
identically to first-party Hermes tools (Spotify, github, etc.).

Three control tools — `hi_agent_status`, `hi_agent_install`, `hi_pull_events` —
plus one factory `build_capability_handler(spec)` that closes over a capability
spec and emits a per-capability handler so each Hi capability becomes a real
Hermes tool with its own JSON-schema, name, and description.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from tools.registry import tool_error, tool_result  # type: ignore

from . import hi_capabilities, hi_client, hi_creds

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Control tools
# ---------------------------------------------------------------------------

HI_AGENT_STATUS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "refresh_capabilities": {
            "type": "boolean",
            "description": "If true, re-fetch the capability catalog from Hi cloud.",
            "default": False,
        },
    },
    "additionalProperties": False,
}


HI_AGENT_INSTALL_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "force_reregister": {
            "type": "boolean",
            "description": (
                "If true, ignore any existing credentials and register a fresh "
                "anonymous Hi identity. Use only when the user explicitly says "
                '"reset" or the existing identity is unrecoverable.'
            ),
            "default": False,
        },
    },
    "additionalProperties": False,
}


HI_PULL_EVENTS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "max": {
            "type": "integer",
            "description": "Max events to claim in this pull (default 25, cap 50).",
            "minimum": 1,
            "maximum": 50,
            "default": 25,
        },
        "lease_ms": {
            "type": "integer",
            "description": (
                "Lease lifetime in ms — if you don't ack inside this window, the "
                "platform redelivers the events to the next caller. Default 60000."
            ),
            "minimum": 5000,
            "maximum": 600000,
            "default": 60000,
        },
        "ack_event_ids": {
            "type": "array",
            "description": (
                "Event IDs to ack (mark as seen by the user). Pass the event_ids "
                "from a previous hi_pull_events response after surfacing them — "
                "un-acked events redeliver on the next pull."
            ),
            "items": {"type": "string"},
        },
    },
    "additionalProperties": False,
}


def _client() -> hi_client.HiClient:
    return hi_client.HiClient()


def handle_hi_agent_status(args: Dict[str, Any], **_: Any) -> str:
    creds = hi_creds.load()
    if creds is None:
        return tool_result({
            "connected":           False,
            "activated":           False,
            "agent_id":            None,
            "next_step":           "call hi_agent_install (zero-touch, no human input).",
            "credentials_path":    str(hi_creds.credentials_path()),
        })

    token_fresh = hi_creds.token_is_fresh(creds)
    if not token_fresh:
        try:
            creds = hi_creds.refresh_token(creds)
            token_fresh = True
        except Exception as exc:
            return tool_error(f"token refresh failed: {exc}")

    capability_count: Optional[int] = None
    if bool(args.get("refresh_capabilities")):
        try:
            specs = hi_capabilities.load_or_refresh(force_refresh=True)
            capability_count = len(specs)
        except Exception as exc:
            return tool_error(f"capability refresh failed: {exc}")
    else:
        cache = hi_capabilities.load_cache()
        capability_count = len(cache.get("capabilities", [])) if cache else None

    try:
        me = _client().get("/v1/agents/me")
    except Exception as exc:
        return tool_error(f"hi /v1/agents/me unreachable: {exc}")

    return tool_result({
        "connected":         True,
        "activated":         (me.get("installation", {}).get("status") == "active"),
        "agent_id":          (me.get("agent", {}).get("agent_id")),
        "installation_id":   (me.get("installation", {}).get("installation_id")),
        "token_fresh":       token_fresh,
        "capability_count":  capability_count,
        "platform_base_url": creds.get("platform_base_url"),
    })


def handle_hi_agent_install(args: Dict[str, Any], **_: Any) -> str:
    force = bool(args.get("force_reregister"))
    if force:
        try:
            hi_creds.credentials_path().unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("hirey-hi: could not unlink existing credentials: %s", exc)
    try:
        creds = hi_creds.ensure_ready()
        specs = hi_capabilities.load_or_refresh(force_refresh=True)
    except Exception as exc:
        return tool_error(f"hi install failed: {exc}")
    return tool_result({
        "ok":               True,
        "agent_id":         creds.get("agent_id"),
        "installation_id":  creds.get("installation_id"),
        "capability_count": len(specs),
        "credentials_path": str(hi_creds.credentials_path()),
        "next_step": (
            "Hi is ready. Ask the user what kind of person they want to find "
            "(hire, date, cofounder, lawyer, tenant, etc.) — the hi-use skill "
            "and the hi_<capability> tools will take it from here."
        ),
    })


def handle_hi_pull_events(args: Dict[str, Any], **_: Any) -> str:
    """Claim + fetch + (optionally) ack a batch of pending Hi events.

    Uses the `/claim` → `GET /v1/agent-events/<id>` → `/ack` triplet rather
    than `/stream` because `/stream` is SSE (text/event-stream) and ignores
    `timeout_ms` — it would block the LLM tool call indefinitely. `/claim`
    returns immediately whether or not events are pending.
    """
    max_events = max(1, min(50, int(args.get("max") or 25)))
    lease_ms   = max(5000, min(600000, int(args.get("lease_ms") or 60000)))
    ack_ids: List[str] = list(args.get("ack_event_ids") or [])

    client = _client()

    ack_result: Optional[Dict[str, Any]] = None
    if ack_ids:
        try:
            ack_result = client.post("/v1/agent-events/ack", {"event_ids": ack_ids})
        except Exception as exc:
            return tool_error(f"hi ack failed: {exc}")

    try:
        claim = client.post(
            "/v1/agent-events/claim",
            {"max": max_events, "lease_ms": lease_ms},
        )
    except Exception as exc:
        return tool_error(f"hi claim failed: {exc}")

    event_ids: List[str] = list(claim.get("event_ids") or [])
    lease_id  = claim.get("lease_id")
    events: List[Dict[str, Any]] = []

    for eid in event_ids:
        try:
            evt = client.get(f"/v1/agent-events/{eid}")
            events.append(evt)
        except Exception as exc:
            logger.warning("hirey-hi: fetch %s failed: %s", eid, exc)

    return tool_result({
        "events":       events,
        "event_ids":    event_ids,
        "lease_id":     lease_id,
        "acked":        ack_result,
        "ack_reminder": (
            "After surfacing these events to the user, call hi_pull_events again "
            "with ack_event_ids=[...] (and optionally lease_id) to mark them seen. "
            "Un-acked events redeliver after the lease expires."
        ) if events else None,
    })


# ---------------------------------------------------------------------------
# Capability tools — one Hermes tool per Hi capability
# ---------------------------------------------------------------------------


def build_capability_handler(capability_id: str):
    """Return a Hermes-shaped handler that proxies to Hi's capability/call."""

    def _handler(args: Dict[str, Any], **_: Any) -> str:
        try:
            payload = _client().call_capability(capability_id, args or {})
        except hi_client.HiAuthError as exc:
            return tool_error(
                f"{exc}. Call hi_agent_install to bootstrap an anonymous Hi identity."
            )
        except hi_client.HiAPIError as exc:
            body_excerpt = (
                json.dumps(exc.body)[:400]
                if isinstance(exc.body, (dict, list))
                else str(exc.body)[:400]
            )
            return tool_error(f"{exc}: {body_excerpt}", status_code=exc.status_code)
        except Exception as exc:
            return tool_error(f"hi {capability_id} failed: {type(exc).__name__}: {exc}")
        return tool_result(payload)

    _handler.__name__ = f"hi_capability_{capability_id.replace('.', '_').replace('-', '_')}"
    return _handler
