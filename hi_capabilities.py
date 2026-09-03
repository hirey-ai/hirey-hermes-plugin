"""
Capability discovery + on-disk snapshot.

Hi's tool catalog lives at `GET /v1/capabilities` and each capability has a
JSON Schema at `GET /v1/capabilities/<id>/schema`. We fetch once at install
time (or first session) and persist to `~/.config/hi/capabilities.cache.json`
so every subsequent Hermes startup loads tools from disk without network.

Cache TTL is 24h — long enough to survive transient network blips, short
enough that schema drift gets picked up within a day. A `hi_agent_status`
tool call can also force a refresh.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from . import hi_client, hi_creds

logger = logging.getLogger(__name__)


CAPABILITIES_CACHE_TTL_SECONDS = 24 * 3600
CAPABILITIES_CACHE_FORMAT_VERSION = 2  # bumped in v0.2.2: schema wrap fix + pipe-enum promotion


def cache_path() -> Path:
    return hi_creds.credentials_dir() / "capabilities.cache.json"


def load_cache() -> Optional[Dict[str, Any]]:
    p = cache_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "fetched_at" not in data:
            return None
        # Older format (pre-v0.2.2) cached the wrapped Hi server response as
        # the tool schema, which hid the real `action` field one level deep.
        # Discard any cache that doesn't declare the current format version.
        if int(data.get("format_version") or 1) < CAPABILITIES_CACHE_FORMAT_VERSION:
            logger.info("hirey-hi: discarding cache (format_version too old)")
            return None
        return data
    except Exception as exc:
        logger.debug("hirey-hi: capability cache unreadable: %s", exc)
        return None


def cache_is_fresh(cache: Dict[str, Any]) -> bool:
    fetched_at = int(cache.get("fetched_at") or 0)
    return time.time() < fetched_at + CAPABILITIES_CACHE_TTL_SECONDS


_PIPE_ENUM_RE = re.compile(r"^\s*'[^']+'(?:\s*\|\s*'[^']+')+\s*$")


def _promote_pipe_enums(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Hi's pipe-separated enum-in-description into a real JSON-Schema `enum`.

    Hi's capability schemas often declare allowed values as a string description like
    ``'upsert'|'update_status'|'get'|'list'|'browse_recent'``. LLMs handle a proper
    ``enum: [...]`` list dramatically better than free-form text, so we sniff that
    pattern at register time and rewrite to canonical JSON Schema. The original
    description is preserved (`enum` is additive, not destructive).
    """
    if not isinstance(schema, dict):
        return schema
    props = schema.get("properties")
    if not isinstance(props, dict):
        return schema
    for _name, spec in props.items():
        if not isinstance(spec, dict) or spec.get("type") != "string" or "enum" in spec:
            continue
        desc = (spec.get("description") or "").strip()
        if not _PIPE_ENUM_RE.match(desc):
            continue
        values = [m.group(1) for m in re.finditer(r"'([^']+)'", desc)]
        if len(values) >= 2:
            spec["enum"] = values
    return schema


def fetch_live(
    *, platform_base_url: str = hi_creds.DEFAULT_PLATFORM_BASE_URL, timeout: float = 15.0
) -> List[Dict[str, Any]]:
    """Fetch the live capability list + per-capability JSON Schema.

    Public endpoints (no bearer needed). Returns a list of:
        {"capability_id": "...", "tool_name": "...", "title": "...",
         "description": "...", "schema": { ... JSON Schema ... }}

    Important: Hi's `/v1/capabilities/<id>/schema` returns a wrapper
    ``{"capability_id": ..., "tool_name": ..., "schema": { ...real JSON Schema... }}``.
    We unwrap to the inner ``.schema`` so the registered tool gets a clean
    properties tree (otherwise the LLM sees `capability_id` / `tool_name` as
    top-level props and the real `action` field is hidden one level down).
    """
    with httpx.Client(timeout=timeout) as c:
        catalog = c.get(
            f"{platform_base_url}/v1/capabilities",
            headers=hi_client.plugin_headers(),
        )
        catalog.raise_for_status()
        items = catalog.json().get("capabilities", [])
        out: List[Dict[str, Any]] = []
        for item in items:
            cap_id = item.get("capability_id")
            if not cap_id:
                continue
            try:
                sch = c.get(
                    f"{platform_base_url}/v1/capabilities/{cap_id}/schema",
                    headers=hi_client.plugin_headers(),
                )
                sch.raise_for_status()
                body = sch.json()
                # Unwrap if response is wrapped; otherwise treat body as schema.
                inner = body.get("schema") if isinstance(body, dict) and isinstance(body.get("schema"), dict) else body
                schema = _promote_pipe_enums(inner if isinstance(inner, dict) else {"type": "object", "additionalProperties": True})
            except Exception as exc:
                logger.warning("hirey-hi: schema fetch failed for %s: %s", cap_id, exc)
                schema = {"type": "object", "additionalProperties": True}
            out.append({
                "capability_id": cap_id,
                "tool_name":     item.get("tool_name") or cap_id.replace("hi.", "").replace("-", "_"),
                "title":         item.get("title") or cap_id,
                "description":   item.get("description") or "",
                "schema":        schema,
            })
    return out


def save_cache(specs: List[Dict[str, Any]]) -> None:
    d = hi_creds.credentials_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    payload = {
        "format_version": CAPABILITIES_CACHE_FORMAT_VERSION,
        "fetched_at":     int(time.time()),
        "platform":       hi_creds.DEFAULT_PLATFORM_BASE_URL,
        "capabilities":   specs,
    }
    tmp = cache_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, cache_path())


def load_or_refresh(*, force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Return the active capability spec list.

    Tries cache first; falls back to live fetch; on network failure returns
    cached-even-if-stale, or empty list if no cache exists. Never raises —
    capability tools missing is recoverable; failing register() is not.
    """
    if not force_refresh:
        cache = load_cache()
        if cache is not None and cache_is_fresh(cache):
            return cache.get("capabilities", [])
    try:
        specs = fetch_live()
        save_cache(specs)
        return specs
    except Exception as exc:
        logger.warning("hirey-hi: live capability fetch failed (%s) — using stale cache if any", exc)
        cache = load_cache()
        return (cache or {}).get("capabilities", [])
