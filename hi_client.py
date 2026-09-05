"""
Hi REST client — thin httpx wrapper that auto-refreshes the bearer on 401
and routes every business call through `POST /v1/capabilities/<id>/call`.

Same as mem9-hermes-plugin: one long-lived sync httpx.Client per process,
no provider gymnastics, raises on transport errors so the tool handler can
surface them to the LLM.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import httpx

from . import hi_creds

logger = logging.getLogger(__name__)

PLUGIN_HOST = "hermes"
PLUGIN_VERSION = "0.2.4"
POLICY_TIMEOUT_SECONDS = 5.0
POLICY_CACHE_TTL_SECONDS = 300
_policy_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}


def plugin_headers() -> Dict[str, str]:
    return {
        "x-hirey-plugin-host": PLUGIN_HOST,
        "x-hirey-plugin-version": PLUGIN_VERSION,
    }


class HiAuthError(RuntimeError):
    """Raised when bearer cannot be obtained even after a refresh attempt."""


class HiAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class HiClient:
    def __init__(self, *, timeout: float = 30.0) -> None:
        self._http: Optional[httpx.Client] = None
        self._timeout = timeout

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout)
        return self._http

    def _bearer(self) -> tuple[str, str, str]:
        creds = hi_creds.load()
        if creds is None or not creds.get("client_id"):
            raise HiAuthError(
                "Hi credentials missing at ~/.config/hi/credentials.json. "
                "Run the `hi_agent_install` tool, or `/hi-onboard`, to bootstrap."
            )
        if not hi_creds.token_is_fresh(creds):
            creds = hi_creds.refresh_token(creds)
        base = hi_creds.validate_platform_base(creds.get("platform_base_url") or hi_creds.DEFAULT_PLATFORM_BASE_URL)
        return base, creds["access_token"], creds["client_id"]

    def _refresh_after_401(self, base: str, token: str, client_id: str):
        creds = hi_creds.load_strict()
        if creds is None:
            return None
        current_base = hi_creds.validate_platform_base(creds.get("platform_base_url") or hi_creds.DEFAULT_PLATFORM_BASE_URL)
        if current_base != base or creds['client_id'] != client_id:
            raise HiAuthError("Hi credential environment changed during the request; reconnect before retrying")
        return hi_creds.refresh_token({**creds, "access_token": token}, force=True)

    def call_capability(self, capability_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """POST /v1/capabilities/<id>/call. Retries once on 401 with a refreshed token."""
        base, token, client_id = self._bearer()
        url = f"{base}/v1/capabilities/{capability_id}/call"
        resp = self._client().post(
            url,
            headers={
                **plugin_headers(),
                "authorization": f"Bearer {token}",
                "content-type": "application/json",
            },
            json=args,
        )
        if resp.status_code == 401:
            creds = self._refresh_after_401(base, token, client_id)
            if creds is not None:
                resp = self._client().post(
                    url,
                    headers={
                        **plugin_headers(),
                        "authorization": f"Bearer {creds['access_token']}",
                        "content-type": "application/json",
                    },
                    json=args,
                )
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise HiAPIError(
                f"hi {capability_id} returned {resp.status_code}",
                status_code=resp.status_code,
                body=body,
            )
        return resp.json()

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        base, token, client_id = self._bearer()
        url = f"{base}{path}"
        resp = self._client().get(
            url,
            headers={**plugin_headers(), "authorization": f"Bearer {token}"},
            params=params,
        )
        if resp.status_code == 401:
            creds = self._refresh_after_401(base, token, client_id)
            if creds is not None:
                resp = self._client().get(
                    url,
                    headers={
                        **plugin_headers(),
                        "authorization": f"Bearer {creds['access_token']}",
                    },
                    params=params,
                )
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise HiAPIError(
                f"hi GET {path} returned {resp.status_code}",
                status_code=resp.status_code,
                body=body,
            )
        return resp.json()

    def post(self, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        base, token, client_id = self._bearer()
        url = f"{base}{path}"
        resp = self._client().post(
            url,
            headers={
                **plugin_headers(),
                "authorization": f"Bearer {token}",
                "content-type": "application/json",
            },
            json=body or {},
        )
        if resp.status_code == 401:
            creds = self._refresh_after_401(base, token, client_id)
            if creds is not None:
                resp = self._client().post(
                    url,
                    headers={
                        **plugin_headers(),
                        "authorization": f"Bearer {creds['access_token']}",
                        "content-type": "application/json",
                    },
                    json=body or {},
                )
        if resp.status_code >= 400:
            try:
                rb = resp.json()
            except Exception:
                rb = resp.text
            raise HiAPIError(
                f"hi POST {path} returned {resp.status_code}",
                status_code=resp.status_code,
                body=rb,
            )
        return resp.json()

    def plugin_policy(self, *, force_refresh: bool = False) -> Dict[str, Any]:
        """Read the public host-specific release policy without requiring login."""
        creds = hi_creds.load() or {}
        base = (creds.get("platform_base_url") or hi_creds.DEFAULT_PLATFORM_BASE_URL).rstrip("/")
        cached = _policy_cache.get(base)
        if not force_refresh and cached and time.monotonic() - cached[0] < POLICY_CACHE_TTL_SECONDS:
            return dict(cached[1])
        try:
            resp = self._client().get(
                f"{base}/v1/capabilities",
                headers=plugin_headers(),
                timeout=POLICY_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            body = resp.json()
            meta = body.get("_meta") if isinstance(body, dict) else None
            policy = meta.get("hirey_plugin") if isinstance(meta, dict) else None
            if not isinstance(policy, dict):
                raise ValueError("plugin policy missing from catalog")
        except Exception:
            if cached:
                return {**cached[1], "stale": True}
            raise
        result = policy if isinstance(policy, dict) else {}
        _policy_cache[base] = (time.monotonic(), dict(result))
        return result
