"""Offline host-contract and recovery tests; no credentials or network used."""
import importlib
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

registry = types.ModuleType("tools.registry")
registry.tool_error = lambda message, **extra: json.dumps({"error": message, **extra})
registry.tool_result = lambda data=None, **extra: json.dumps({**(data or {}), **extra})
sys.modules.setdefault("tools", types.ModuleType("tools"))
sys.modules.setdefault("tools.registry", registry)
package = types.ModuleType("hirey_safety_test")
package.__path__ = [str(Path(__file__).resolve().parent)]
sys.modules[package.__name__] = package
client = importlib.import_module("hirey_safety_test.hi_client")
caps = importlib.import_module("hirey_safety_test.hi_capabilities")
handlers = importlib.import_module("hirey_safety_test.hi_tools")
push = importlib.import_module("hirey_safety_test.hi_push")


class PluginSafetyTests(unittest.TestCase):
    def test_recovery_and_upgrade_survive_long_error_without_debug_secrets(self):
        body = {"message": "x" * 900, "error_code": "upgrade_required",
                "recovery": {"next_step": "upgrade then retry", "client_secret": "secret"},
                "required_scopes": ["messages:read"], "debug": {"api_key": "secret"},
                "_meta": {"hirey_plugin": {"update_required": True, "update_command": "upgrade"}}}
        api = MagicMock()
        api.call_capability.side_effect = client.HiAPIError("blocked", status_code=426, body=body)
        with patch.object(handlers, "_client", return_value=api):
            result = json.loads(handlers.build_capability_handler("hi.workspace-workflows")({}))
        self.assertEqual(result["details"]["error_code"], "upgrade_required")
        self.assertEqual(result["details"]["recovery"], {"next_step": "upgrade then retry"})
        self.assertTrue(result["details"]["_meta"]["hirey_plugin"]["update_required"])
        self.assertNotIn("secret", json.dumps(result))

    def test_policy_cache_is_host_scoped_forced_refresh_and_short_timeout(self):
        client._policy_cache.clear()
        http = MagicMock()
        http.get.return_value.json.return_value = {"_meta": {"hirey_plugin": {"latest": "0.2.4"}}}
        api = client.HiClient()
        api._http = http
        creds = {"platform_base_url": "https://one.example"}
        with patch.object(client.hi_creds, "load", side_effect=lambda: creds):
            api.plugin_policy()
            api.plugin_policy()
            self.assertEqual(http.get.call_count, 1)
            api.plugin_policy(force_refresh=True)
            self.assertEqual(http.get.call_count, 2)
            creds["platform_base_url"] = "https://two.example"
            api.plugin_policy()
            self.assertEqual(http.get.call_count, 3)
            self.assertEqual(http.get.call_args.kwargs["timeout"], 5.0)

    def test_failed_policy_refresh_preserves_required_upgrade_as_stale(self):
        client._policy_cache.clear()
        http = MagicMock()
        http.get.return_value.json.return_value = {"_meta": {"hirey_plugin": {"update_required": True}}}
        api = client.HiClient()
        api._http = http
        with patch.object(client.hi_creds, "load", return_value={"platform_base_url": "https://one.example"}):
            api.plugin_policy()
            http.get.side_effect = httpx.ReadTimeout("offline")
            self.assertEqual(api.plugin_policy(force_refresh=True), {"update_required": True, "stale": True})
        with patch.object(client.hi_creds, "load", return_value={"platform_base_url": "https://other.example"}):
            with self.assertRaises(httpx.ReadTimeout):
                api.plugin_policy()

    def test_real_error_shapes_keep_recovery_scope_and_update_fields(self):
        bodies = [
            {"error": {"code": "insufficient_oauth_scope", "data": {"required_scope": "hirey.f07.actions.overview", "access_token": "secret"}}, "retryable": False},
            {"error": "plugin_update_required", "plugin": {"update_required": True}, "next": {"action": "upgrade", "command": "upgrade hirey"}},
            {"error": "invalid_token", "error_code": "invalid_token", "next": {"action": "reconnect"}, "retryable": True},
        ]
        for body in bodies:
            details = json.loads(handlers._api_error(client.HiAPIError("blocked", status_code=403, body=body)))["details"]
            if isinstance(body["error"], dict):
                self.assertEqual(details["error"]["data"], {"required_scope": "hirey.f07.actions.overview"})
                self.assertFalse(details["retryable"])
            else:
                self.assertEqual(details, body)
            self.assertNotIn("secret", json.dumps(details))

    def test_catalog_embedded_schema_avoids_all_schema_roundtrips(self):
        requests = []
        def respond(request):
            requests.append(request.url.path)
            return httpx.Response(200, json={"capabilities": [{
                "capability_id": "hi.workspace-workflows", "parameters": {
                    "type": "object", "properties": {"action": {"type": "string"}}}}]})
        real = httpx.Client
        with patch.object(caps.httpx, "Client", side_effect=lambda **kw: real(transport=httpx.MockTransport(respond), **kw)):
            specs = caps.fetch_live(platform_base_url="https://test.example")
        self.assertEqual(requests, ["/v1/capabilities"])
        self.assertIn("action", specs[0]["schema"]["properties"])

    def test_register_never_exposes_claim_tool_or_registers_identity(self):
        spec = importlib.util.spec_from_file_location(package.__name__, Path(__file__).with_name("__init__.py"), submodule_search_locations=package.__path__)
        spec.loader.exec_module(package)
        ctx = MagicMock()
        setattr(ctx, "_hirey_hi_registered", False)
        with patch.object(package.hi_creds, "load", return_value=None), \
             patch.object(package.hi_creds, "ensure_ready") as register, \
             patch.object(package.hi_push, "ensure_local_subscription"), \
             patch.object(package.hi_capabilities, "load_or_refresh", return_value=[]):
            package.register(ctx)
        names = [call.kwargs["name"] for call in ctx.register_tool.call_args_list]
        self.assertNotIn("hi_pull_events", names)
        self.assertEqual(len(names), 5)
        register.assert_not_called()

    def test_only_owned_legacy_subscription_prompt_is_updated(self):
        for owner in ("hirey-hi-plugin", "user"):
            sub = {"secret": "unchanged", "created_by": owner, "prompt": "call hi_pull_events", "deliver": "log"}
            with patch.object(push, "_load_subs", return_value={push.SUBSCRIPTION_NAME: sub}), \
                 patch.object(push, "_save_subs") as save:
                result = push.ensure_local_subscription()
            self.assertEqual(result["secret"], "unchanged")
            self.assertEqual(save.call_count, int(owner == "hirey-hi-plugin"))
            if owner == "hirey-hi-plugin":
                self.assertNotIn("hi_pull_events", result["prompt"])


if __name__ == "__main__":
    unittest.main()
