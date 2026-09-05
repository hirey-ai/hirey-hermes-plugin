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

from . import hi_capabilities, hi_client, hi_creds, hi_push

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
        "metadata": {
            "type": "object",
            "description": (
                "Legacy field retained for compatibility; nonempty metadata is rejected "
                "before registration because the modern bootstrap does not persist referrals."
            ),
            "additionalProperties": True,
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


def _api_error(exc: hi_client.HiAPIError) -> str:
    """Expose recovery information, never arbitrary response/debug/credential fields."""
    allowed = {
        "error", "code", "error_code", "message", "detail", "reason", "recovery",
        "next_step", "next_steps", "action", "instructions", "required_scope",
        "required_scopes", "missing_scopes", "auth_url", "login_url", "recovery_url",
        "upgrade", "upgrade_url", "update_required", "update_recommended", "_meta",
        "hirey_plugin", "host", "name", "latest", "minimum_supported",
        "update_command", "restart_required",
        "next", "data", "plugin", "retryable", "command", "stale",
    }

    def select(value: Any, depth: int = 0) -> Any:
        if depth > 6:
            return None
        if isinstance(value, dict):
            return {key: select(item, depth + 1) for key, item in value.items() if key in allowed}
        if isinstance(value, list):
            return [select(item, depth + 1) for item in value[:50]]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value[:8000] if isinstance(value, str) else value
        return None

    return tool_error(str(exc), status_code=exc.status_code,
                      details=select(exc.body) if isinstance(exc.body, dict) else {})


def handle_hi_agent_status(args: Dict[str, Any], **_: Any) -> str:
    try:
        plugin_policy = _client().plugin_policy(force_refresh=bool(args.get("refresh_capabilities")))
    except Exception as exc:
        logger.warning("hirey-hi: plugin policy unavailable: %s", exc)
        plugin_policy = {
            "host": hi_client.PLUGIN_HOST,
            "latest": None,
            "minimum_supported": None,
            "update_required": None,
            "update_recommended": None,
        }
    creds = hi_creds.load()
    if creds is None:
        return tool_result({
            "connected":           False,
            "activated":           False,
            "agent_id":            None,
            "next_step":           "call hi_agent_install (zero-touch, no human input).",
            "credentials_path":    str(hi_creds.credentials_path()),
            "plugin":              plugin_policy,
        })

    token_fresh = hi_creds.token_is_fresh(creds)
    if not token_fresh:
        try:
            creds = hi_creds.refresh_token(creds, timeout=5.0)
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

    if str(creds.get("access_token") or "").startswith("hi_ai_"):
        return tool_result({
            "connected": True, "activated": False, "identity_bound": False,
            "ready_for_public_reads": True, "installation_status": "pending",
            "agent_id": creds.get("agent_id"), "token_fresh": token_fresh,
            "capability_count": capability_count, "plugin": plugin_policy,
            "next_step": "Use google_link, email_binding or phone_binding only when private access is needed.",
        })
    try:
        me = hi_client.HiClient(timeout=5.0).get("/v1/agents/me")
    except hi_client.HiAPIError as exc:
        return _api_error(exc)
    except Exception as exc:
        return tool_error(f"hi /v1/agents/me unreachable: {exc}")

    identity_bound = bool(isinstance(me.get("person_id"), str) and me["person_id"].strip()
                          and me.get("agent_id") == creds.get("agent_id"))
    return tool_result({
        "connected":         True,
        "activated":         identity_bound,
        "identity_bound":    identity_bound,
        "ready_for_public_reads": True,
        "installation_status": "active" if identity_bound else "unknown",
        "agent_id":          me.get("agent_id") or creds.get("agent_id"),
        "token_fresh":       token_fresh,
        "capability_count":  capability_count,
        "platform_base_url": creds.get("platform_base_url"),
        "plugin":            plugin_policy,
    })


def handle_hi_agent_install(args: Dict[str, Any], **_: Any) -> str:
    force = bool(args.get("force_reregister"))
    raw_metadata = args.get("metadata")
    # Accept only dict-shaped metadata; silently drop other types to avoid 400 from gateway.
    install_metadata: Optional[Dict[str, Any]] = (
        raw_metadata if isinstance(raw_metadata, dict) else None
    )
    if force:
        try:
            hi_creds.credentials_path().unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("hirey-hi: could not unlink existing credentials: %s", exc)
    try:
        creds = hi_creds.ensure_ready(metadata=install_metadata)
        specs = hi_capabilities.load_or_refresh(force_refresh=True)
    except hi_creds.CredentialsCorruptError as exc:
        return tool_error(
            f"hi install BLOCKED: your Hi credentials file is present but unusable ({exc}). "
            "Refusing to auto-register — that would orphan your existing agent and its data "
            "(and, if you also use the Claude plugin, re-identify both hosts). Fix the file's "
            "permissions or restore it from backup. Only if you intentionally want a brand-new "
            "agent (abandoning the old one's data) call hi_agent_install with force_reregister=true."
        )
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
# Push delivery tools (v0.2)
# ---------------------------------------------------------------------------

HI_PUSH_INSTALL_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "public_url": {
            "type": "string",
            "description": (
                "Override the URL Hi cloud will POST events to. Use this when "
                "Hermes is behind a reverse proxy or tunnel — e.g. "
                "`https://hermes.example.com/webhooks/hi` or "
                "`https://abcd1234.ngrok.app/webhooks/hi`. If omitted, defaults "
                "to the local gateway URL inferred from WEBHOOK_PUBLIC_HOST / "
                "WEBHOOK_HOST / localhost on port 8644."
            ),
        },
        "force_register_with_loopback": {
            "type": "boolean",
            "description": (
                "Register with Hi cloud even when the URL is loopback "
                "(localhost / 127.x). Hi cloud cannot reach loopback addresses, "
                "so push delivery will silently fail — events stay in the outbox. "
                "Read messages with workspace_workflows agent_message.list. Useful for "
                "exercising the registration path during testing."
            ),
            "default": False,
        },
    },
    "additionalProperties": False,
}


HI_PUSH_STATUS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "trigger_test": {
            "type": "boolean",
            "description": (
                "If true, POST /v1/agents/me/test-delivery to fire a synthetic "
                "event through Hi's delivery worker. Useful for verifying that "
                "Hi cloud can actually reach your webhook URL."
            ),
            "default": False,
        },
    },
    "additionalProperties": False,
}


HI_PUSH_REMOVE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "remove_local_subscription": {
            "type": "boolean",
            "description": "If true, also delete the local Hermes webhook subscription.",
            "default": False,
        },
    },
    "additionalProperties": False,
}


def handle_hi_push_install(args: Dict[str, Any], **_: Any) -> str:
    public_url_override = (args.get("public_url") or "").strip() or None
    force_loopback = bool(args.get("force_register_with_loopback"))

    # 1) Ensure the local subscription exists (always safe).
    sub = hi_push.ensure_local_subscription()

    # 2) Decide which URL to register with Hi cloud.
    webhook_url = public_url_override or hi_push._resolve_local_webhook_url()
    is_loopback = hi_push.url_is_loopback(webhook_url)
    if is_loopback and not force_loopback:
        return tool_result({
            "ok":            False,
            "local_sub":     {"name": hi_push.SUBSCRIPTION_NAME, "url": webhook_url},
            "registered":    False,
            "reason":        "loopback_url_unreachable_from_hi_cloud",
            "next_step": (
                "Hi cloud cannot POST events to a loopback URL "
                f"({webhook_url}). Either set WEBHOOK_PUBLIC_HOST env to your "
                "public hostname (or run a tunnel like ngrok/cloudflared), "
                "then call hi_push_install({public_url: 'https://...'}), or "
                "pass force_register_with_loopback=true if you just want to "
                "exercise the registration path for testing."
            ),
        })

    # 3) PUT the endpoint on Hi side.
    try:
        result = hi_push.register_hi_endpoint(webhook_url=webhook_url, secret=sub["secret"])
    except hi_client.HiAPIError as exc:
        return tool_error(f"hi endpoint register failed: {exc}", status_code=exc.status_code)
    except Exception as exc:
        return tool_error(f"hi endpoint register failed: {type(exc).__name__}: {exc}")

    return tool_result({
        "ok":              True,
        "local_sub":       {"name": hi_push.SUBSCRIPTION_NAME, "url": webhook_url},
        "registered":      True,
        "loopback_warning": is_loopback,
        "hi_endpoints":    result.get("endpoints", []),
        "next_step": (
            "Hi will POST inbound events to the registered URL. Run "
            "`hi_push_status({trigger_test: true})` to verify end-to-end "
            "delivery. To check user messages, use workspace_workflows with "
            "action=agent_message.list; transport acknowledgements belong to the delivery worker."
        ),
    })


def handle_hi_push_status(args: Dict[str, Any], **_: Any) -> str:
    subs_path = hi_push.subscriptions_path()
    has_local = False
    try:
        import json as _json
        local = _json.loads(subs_path.read_text(encoding="utf-8")) if subs_path.exists() else {}
        has_local = hi_push.SUBSCRIPTION_NAME in local
        local_sub = local.get(hi_push.SUBSCRIPTION_NAME, {})
    except Exception:
        local_sub = {}

    try:
        eps = hi_push.list_hi_endpoints()
    except Exception as exc:
        return tool_error(f"hi endpoint list failed: {exc}")

    relevant = [e for e in eps.get("endpoints", []) if e.get("profile") == hi_push.HI_ENDPOINT_PROFILE]
    payload = {
        "local_subscription_present": has_local,
        "local_subscription": {
            "name":        hi_push.SUBSCRIPTION_NAME,
            "description": local_sub.get("description"),
            "deliver":     local_sub.get("deliver"),
            "events":      local_sub.get("events"),
        } if has_local else None,
        "hi_endpoints": relevant,
    }

    if bool(args.get("trigger_test")):
        try:
            payload["test_delivery"] = hi_push.trigger_test_delivery()
        except Exception as exc:
            payload["test_delivery_error"] = str(exc)

    return tool_result(payload)


def handle_hi_push_remove(args: Dict[str, Any], **_: Any) -> str:
    also_local = bool(args.get("remove_local_subscription"))
    try:
        hi_result = hi_push.remove_hi_endpoint()
    except Exception as exc:
        return tool_error(f"hi endpoint disable failed: {exc}")

    local_removed = False
    if also_local:
        local_removed = hi_push.remove_local_subscription()

    return tool_result({
        "ok":             True,
        "hi_endpoint":    hi_result,
        "local_removed":  local_removed,
    })


# ---------------------------------------------------------------------------
# Capability tools — one Hermes tool per Hi capability
# ---------------------------------------------------------------------------


def build_capability_handler(capability_id: str):
    """Return a Hermes-shaped handler that proxies to Hi's capability/call."""

    def _handler(args: Dict[str, Any], **_: Any) -> str:
        try:
            payload = _client().call_capability(capability_id, args or {})
            # Binding completion must rotate the pending bearer before private
            # calls. Do not infer this from a merely pending login response.
            result = payload.get("result", payload) if isinstance(payload, dict) else {}
            if capability_id in ("hi.google-link", "hi.email-binding", "hi.phone-binding") and isinstance(result, dict) and result.get("status") == "verified":
                with hi_creds.credential_lock():
                    creds = hi_creds.load_strict()
                    if creds is not None:
                        creds["status"] = "active"
                        hi_creds._refresh_token_locked(creds, timeout=15.0)
        except hi_client.HiAuthError as exc:
            return tool_error(
                f"{exc}. Call hi_agent_install to bootstrap an anonymous Hi identity."
            )
        except hi_client.HiAPIError as exc:
            return _api_error(exc)
        except Exception as exc:
            return tool_error(f"hi {capability_id} failed: {type(exc).__name__}: {exc}")
        return tool_result(payload)

    _handler.__name__ = f"hi_capability_{capability_id.replace('.', '_').replace('-', '_')}"
    return _handler
