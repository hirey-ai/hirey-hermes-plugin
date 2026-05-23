"""
hirey-hi — Hermes Agent plugin for Hirey Hi (people-to-people platform).

Anonymous client_credentials identity (no human Hi account, no browser OAuth,
no `requires_env` install prompts). Same on-disk credentials file as the
sibling Hirey Claude Code plugin (`~/.config/hi/credentials.json`, mode 600)
so a user who installed both keeps a single Hi identity across hosts.

Register-time work:

  1. Bootstrap / refresh credentials if needed (idempotent, network-OK).
  2. Load capability catalog from disk cache (live-fetch on TTL expiry).
  3. ctx.register_tool(...) for three control tools + one tool per Hi capability.
  4. ctx.register_command(...) for `/hi-onboard` (operator convenience).
  5. ctx.register_hook("on_session_start", ...) to silently refresh expiring tokens.

Push delivery (gateway-mode webhook subscription + CLI-mode inject_message)
is deferred to v0.2 — tool-driven pull is sufficient for v0.1 and matches
what the existing Claude Code / Codex plugins ship today.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from tools.registry import tool_error, tool_result  # type: ignore

from . import hi_capabilities, hi_creds, hi_push, hi_tools

logger = logging.getLogger(__name__)


_REGISTERED_FLAG = "_hirey_hi_registered"


def register(ctx) -> None:  # noqa: C901 — keep wiring linear/readable
    if getattr(ctx, _REGISTERED_FLAG, False):
        return
    setattr(ctx, _REGISTERED_FLAG, True)

    # 1. Bootstrap credentials best-effort. Don't fail the whole plugin if
    #    Hi cloud is unreachable at process start — we want the control
    #    tools registered either way so the user can debug.
    try:
        hi_creds.ensure_ready()
    except Exception as exc:
        logger.warning("hirey-hi: credentials bootstrap failed at register (%s) — continuing", exc)

    # 2. Control tools — always registered, even with no creds.
    ctx.register_tool(
        name="hi_agent_status",
        toolset="hirey_hi",
        schema=hi_tools.HI_AGENT_STATUS_SCHEMA,
        handler=hi_tools.handle_hi_agent_status,
        description=(
            "Report whether Hi credentials exist, whether the access_token is "
            "fresh, and how many capabilities are loaded. Call before any other "
            "hi_* tool when in doubt."
        ),
        emoji="🟢",
    )
    ctx.register_tool(
        name="hi_agent_install",
        toolset="hirey_hi",
        schema=hi_tools.HI_AGENT_INSTALL_SCHEMA,
        handler=hi_tools.handle_hi_agent_install,
        description=(
            "Bootstrap an anonymous Hi identity (zero-touch — no Hi account, "
            "no browser OAuth, no user input). Idempotent: a no-op when the "
            "credentials file already exists with a fresh token."
        ),
        emoji="🔧",
    )
    ctx.register_tool(
        name="hi_pull_events",
        toolset="hirey_hi",
        schema=hi_tools.HI_PULL_EVENTS_SCHEMA,
        handler=hi_tools.handle_hi_pull_events,
        description=(
            "Claim + fetch inbound Hi events (pairing replies, meeting confirms, "
            "match updates). Returns immediately whether or not events are pending. "
            "Pass `ack_event_ids=[...]` from a previous response to mark events seen."
        ),
        emoji="📨",
    )

    # Push delivery (v0.2) — three opt-in tools.
    ctx.register_tool(
        name="hi_push_install",
        toolset="hirey_hi",
        schema=hi_tools.HI_PUSH_INSTALL_SCHEMA,
        handler=hi_tools.handle_hi_push_install,
        description=(
            "Wire push delivery: create the local Hermes webhook subscription "
            "+ register a `generic.event-webhook.v1` endpoint on Hi cloud. After "
            "this, Hi POSTs inbound events to the registered URL instead of "
            "(or in addition to) the pull path. Use `public_url` arg when "
            "Hermes is behind a tunnel or reverse proxy."
        ),
        emoji="📡",
    )
    ctx.register_tool(
        name="hi_push_status",
        toolset="hirey_hi",
        schema=hi_tools.HI_PUSH_STATUS_SCHEMA,
        handler=hi_tools.handle_hi_push_status,
        description=(
            "Report whether the local webhook subscription exists and what "
            "endpoints are registered on Hi side. Pass `trigger_test: true` "
            "to fire a synthetic event through the delivery worker."
        ),
        emoji="🔍",
    )
    ctx.register_tool(
        name="hi_push_remove",
        toolset="hirey_hi",
        schema=hi_tools.HI_PUSH_REMOVE_SCHEMA,
        handler=hi_tools.handle_hi_push_remove,
        description=(
            "Disable the `generic.event-webhook.v1` endpoint on Hi side (stops "
            "push delivery; pull keeps working). Pass "
            "`remove_local_subscription: true` to also delete the Hermes "
            "webhook subscription file."
        ),
        emoji="🗑️",
    )

    # Idempotent local-only step: make sure ~/.hermes/webhook_subscriptions.json
    # has a `hi` entry so the URL is reserved. Hi-side registration stays
    # explicit (user calls hi_push_install) to avoid surprise endpoint upserts.
    try:
        hi_push.ensure_local_subscription()
    except Exception as exc:
        logger.warning("hirey-hi: ensure_local_subscription failed: %s", exc)

    # 3. Capability tools — one Hermes tool per Hi capability.
    specs = hi_capabilities.load_or_refresh()
    for spec in specs:
        cap_id    = spec["capability_id"]
        tool_name = spec["tool_name"]
        # Hermes tool names accept [a-zA-Z0-9_]; Hi tool_names are already
        # snake_case per the platform contract, but defensively re-shape.
        safe = tool_name.replace("-", "_").replace(".", "_")
        ctx.register_tool(
            name=safe,
            toolset="hirey_hi",
            schema=spec.get("schema") or {"type": "object", "additionalProperties": True},
            handler=hi_tools.build_capability_handler(cap_id),
            description=(spec.get("description") or spec.get("title") or cap_id)[:512],
            emoji="🛠️",
        )

    # 4. Slash command — operator convenience. Mirrors the hi-onboard skill.
    def _slash_hi_onboard(_raw_args: str) -> str:
        result_json = hi_tools.handle_hi_agent_install({})
        return result_json

    ctx.register_command(
        name="hi-onboard",
        handler=_slash_hi_onboard,
        description="Bootstrap or repair the anonymous Hi identity (no user input).",
    )

    # 5. Session-start hook — silently refresh expiring token so the first
    #    real Hi call in the session never pays a refresh round-trip.
    def _on_session_start(*_args: Any, **_kw: Any) -> None:
        try:
            creds = hi_creds.load()
            if creds and not hi_creds.token_is_fresh(creds):
                hi_creds.refresh_token(creds)
        except Exception as exc:
            logger.debug("hirey-hi: on_session_start token refresh skipped: %s", exc)

    ctx.register_hook("on_session_start", _on_session_start)

    logger.info(
        "hirey-hi: registered %d control tools + %d capability tools",
        6, len(specs),
    )
