"""Offline regression tests for capability discovery and cache isolation."""
import importlib
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

package = types.ModuleType("hirey_cache_test")
package.__path__ = [str(Path(__file__).resolve().parent)]
sys.modules[package.__name__] = package
caps = importlib.import_module("hirey_cache_test.hi_capabilities")


class CapabilityPlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.base = "https://test.example"
        self.creds = {"platform_base_url": self.base + "/"}
        for target, kwargs in [
            ("credentials_dir", {"return_value": self.directory}),
            ("load", {"side_effect": lambda: self.creds}),
        ]:
            mock = patch.object(caps.hi_creds, target, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)

    def test_discovery_fetches_configured_host_and_reuses_its_cache(self):
        specs = [{"capability_id": "hi.workspace-workflows"}]
        with patch.object(caps, "fetch_live", return_value=specs) as fetch:
            self.assertEqual(caps.load_or_refresh(), specs)
            fetch.assert_called_once_with(platform_base_url=self.base)
        self.assertEqual(json.loads(caps.cache_path().read_text())["platform"], self.base)
        with patch.object(caps, "fetch_live", side_effect=AssertionError("unexpected network")):
            self.assertEqual(caps.load_or_refresh(), specs)

    def test_switch_host_never_uses_previous_hosts_cache_even_on_failure(self):
        caps.save_cache([{"capability_id": "old-host-only"}])
        self.creds = {"platform_base_url": "https://other.example"}
        with patch.object(caps, "fetch_live", side_effect=RuntimeError("offline")) as fetch:
            self.assertEqual(caps.load_or_refresh(), [])
            fetch.assert_called_once_with(platform_base_url="https://other.example")

    def test_same_host_stale_cache_survives_network_failure(self):
        specs = [{"capability_id": "hi.workspace-workflows"}]
        caps.save_cache(specs)
        data = json.loads(caps.cache_path().read_text())
        data["fetched_at"] = 0
        caps.cache_path().write_text(json.dumps(data))
        with patch.object(caps, "fetch_live", side_effect=RuntimeError("offline")):
            self.assertEqual(caps.load_or_refresh(), specs)

    def test_old_format_is_discarded_and_default_host_works_without_credentials(self):
        self.creds = None
        caps.save_cache([])
        self.assertEqual(caps.load_cache()["platform"], caps.hi_creds.DEFAULT_PLATFORM_BASE_URL)
        data = json.loads(caps.cache_path().read_text())
        data["format_version"] = 2
        caps.cache_path().write_text(json.dumps(data))
        self.assertIsNone(caps.load_cache())


if __name__ == "__main__":
    unittest.main()
