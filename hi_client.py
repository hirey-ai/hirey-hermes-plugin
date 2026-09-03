"""
Hi REST client — thin httpx wrapper that auto-refreshes the bearer on 401
and routes every business call through `POST /v1/capabilities/<id>/call`.

Same as mem9-hermes-plugin: one long-lived sync httpx.Client per process,
no provider gymnastics, raises on transport errors so the tool handler can
surface them to the LLM.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from . import hi_creds

logger = logging.getLogger(__name__)

PLUGIN_HOST = "hermes"
PLUGIN_VERSION = "0.2.3"


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

    def _bearer(self) -> tuple[str, str]:
        creds = hi_creds.load()
        if creds is None or not creds.get("client_id"):
            raise HiAuthError(
                "Hi credentials missing at ~/.config/hi/credentials.json. "
                "Run the `hi_agent_install` tool, or `/hi-onboard`, to bootstrap."
            )
        if not hi_creds.token_is_fresh(creds):
            creds = hi_creds.refresh_token(creds)
        base = creds.get("platform_base_url") or hi_creds.DEFAULT_PLATFORM_BASE_URL
        return base, creds["access_token"]

    def call_capability(self, capability_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """POST /v1/capabilities/<id>/call. Retries once on 401 with a refreshed token."""
        base, token = self._bearer()
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
            creds = hi_creds.load()
            if creds is not None:
                creds = hi_creds.refresh_token(creds)
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
        base, token = self._bearer()
        url = f"{base}{path}"
        resp = self._client().get(
            url,
            headers={**plugin_headers(), "authorization": f"Bearer {token}"},
            params=params,
        )
        if resp.status_code == 401:
            creds = hi_creds.load()
            if creds is not None:
                creds = hi_creds.refresh_token(creds)
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
        base, token = self._bearer()
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
            creds = hi_creds.load()
            if creds is not None:
                creds = hi_creds.refresh_token(creds)
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

    def plugin_policy(self) -> Dict[str, Any]:
        """Read the public host-specific release policy without requiring login."""
        creds = hi_creds.load() or {}
        base = creds.get("platform_base_url") or hi_creds.DEFAULT_PLATFORM_BASE_URL
        resp = self._client().get(
            f"{base}/v1/capabilities",
            headers=plugin_headers(),
        )
        resp.raise_for_status()
        body = resp.json()
        meta = body.get("_meta") if isinstance(body, dict) else None
        policy = meta.get("hirey_plugin") if isinstance(meta, dict) else None
        return policy if isinstance(policy, dict) else {}
