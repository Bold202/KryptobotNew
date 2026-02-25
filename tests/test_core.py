#!/usr/bin/env python3
"""
KryptoBot – unit tests for non-GUI, non-network components.
Run with:  python3 -m pytest tests/
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Make sure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# =============================================================================
# ConfigManager tests
# =============================================================================

class TestConfigManager(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._config_file = os.path.join(self._tmpdir.name, "config.json")
        # Patch the module-level paths before importing
        import config_manager as cm
        cm.CONFIG_DIR = self._tmpdir.name
        cm.CONFIG_FILE = self._config_file

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_manager(self):
        # Re-import after path patching
        import importlib, config_manager as cm
        importlib.reload(cm)
        cm.CONFIG_DIR = self._tmpdir.name
        cm.CONFIG_FILE = self._config_file
        return cm.ConfigManager()

    def test_creates_config_file_on_first_run(self):
        mgr = self._make_manager()
        self.assertTrue(os.path.exists(self._config_file))

    def test_default_wizard_not_completed(self):
        mgr = self._make_manager()
        self.assertFalse(mgr.get("wizard_completed", True))

    def test_set_and_get(self):
        mgr = self._make_manager()
        mgr.set("wizard_completed", True)
        self.assertTrue(mgr.get("wizard_completed"))

    def test_update_section(self):
        mgr = self._make_manager()
        mgr.update_section("trading", {"threshold_percent": 5.0})
        self.assertEqual(mgr.get_section("trading")["threshold_percent"], 5.0)

    def test_persistence(self):
        import importlib, config_manager as cm
        # write
        mgr = self._make_manager()
        mgr.set("wizard_completed", True)
        mgr.update_section("trading", {"threshold_percent": 3.5})
        # reload
        importlib.reload(cm)
        cm.CONFIG_DIR = self._tmpdir.name
        cm.CONFIG_FILE = self._config_file
        mgr2 = cm.ConfigManager()
        self.assertTrue(mgr2.get("wizard_completed"))
        self.assertEqual(mgr2.get_section("trading")["threshold_percent"], 3.5)

    def test_malformed_config_falls_back_to_defaults(self):
        with open(self._config_file, "w") as f:
            f.write("not-valid-json{{{")
        mgr = self._make_manager()
        # Should not raise and should provide defaults
        self.assertIsNotNone(mgr.get_section("coinbase"))


# =============================================================================
# CoinbaseClient auth tests (no real network calls)
# =============================================================================

class TestCoinbaseClientAuth(unittest.TestCase):
    def _make_client(self):
        from coinbase_client import CoinbaseClient
        return CoinbaseClient("test_key", "test_secret", use_sandbox=False)

    def test_auth_headers_contain_required_keys(self):
        client = self._make_client()
        headers = client._auth_headers("GET", "/api/v3/brokerage/accounts")
        self.assertIn("CB-ACCESS-KEY", headers)
        self.assertIn("CB-ACCESS-SIGN", headers)
        self.assertIn("CB-ACCESS-TIMESTAMP", headers)

    def test_auth_key_matches_supplied(self):
        client = self._make_client()
        headers = client._auth_headers("GET", "/api/v3/brokerage/accounts")
        self.assertEqual(headers["CB-ACCESS-KEY"], "test_key")

    def test_sandbox_uses_different_base_url(self):
        from coinbase_client import CoinbaseClient, SANDBOX_URL, BASE_URL
        live = CoinbaseClient("k", "s", use_sandbox=False)
        sand = CoinbaseClient("k", "s", use_sandbox=True)
        self.assertEqual(live.base_url, BASE_URL)
        self.assertEqual(sand.base_url, SANDBOX_URL)

    def test_get_accounts_parses_response(self):
        from coinbase_client import CoinbaseClient
        client = CoinbaseClient("k", "s")
        fake_response = {
            "accounts": [
                {
                    "uuid": "abc",
                    "name": "BTC Wallet",
                    "currency": "BTC",
                    "available_balance": {"value": "0.5"},
                    "hold": {"value": "0.0"},
                }
            ]
        }
        with patch.object(client, "_get", return_value=fake_response):
            accounts = client.get_accounts()
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["currency"], "BTC")
        self.assertAlmostEqual(accounts[0]["balance"], 0.5)

    def test_get_accounts_filters_zero_balance(self):
        from coinbase_client import CoinbaseClient
        client = CoinbaseClient("k", "s")
        fake_response = {
            "accounts": [
                {
                    "uuid": "a",
                    "name": "Empty",
                    "currency": "ETH",
                    "available_balance": {"value": "0"},
                    "hold": {"value": "0.0"},
                }
            ]
        }
        with patch.object(client, "_get", return_value=fake_response):
            accounts = client.get_accounts()
        self.assertEqual(accounts, [])


# =============================================================================
# TradingEngine tests
# =============================================================================

class TestTradingEngine(unittest.TestCase):
    def _make_engine(self, events=None):
        from trading_engine import TradingEngine
        client = MagicMock()
        cfg = {
            "threshold_percent": 5.0,
            "check_interval_seconds": 1,
            "pairs": [],
        }
        collected = events if events is not None else []
        engine = TradingEngine(client, cfg, on_event=lambda t, d: collected.append((t, d)))
        return engine, client, collected

    def test_initial_state_inactive(self):
        engine, _, _ = self._make_engine()
        self.assertFalse(engine.is_active)

    def test_stop_without_start_does_not_raise(self):
        engine, _, _ = self._make_engine()
        engine.stop()  # should not raise

    def test_start_sets_active(self):
        engine, client, events = self._make_engine()
        client.get_owned_coins_with_prices.return_value = []
        engine.start()
        self.assertTrue(engine.is_active)
        engine.stop()

    def test_threshold_event_fired(self):
        """Reference price is set on first tick; threshold event fires on second tick."""
        from trading_engine import TradingEngine
        client = MagicMock()
        cfg = {"threshold_percent": 5.0, "check_interval_seconds": 9999, "pairs": []}
        events = []
        engine = TradingEngine(client, cfg, on_event=lambda t, d: events.append((t, d)))

        coin = {"currency": "BTC", "balance": 1.0, "price_usd": 100.0, "value_usd": 100.0, "product_id": "BTC-USD"}
        client.get_owned_coins_with_prices.return_value = [coin]

        engine._tick()  # sets reference price → no threshold event

        # Price rises 10 % → threshold should fire
        coin2 = {**coin, "price_usd": 110.0, "value_usd": 110.0}
        client.get_owned_coins_with_prices.return_value = [coin2]
        engine._tick()

        threshold_events = [e for e in events if e[0] == "threshold_reached"]
        self.assertEqual(len(threshold_events), 1)
        data = threshold_events[0][1]
        self.assertEqual(data["product_id"], "BTC-USD")
        self.assertAlmostEqual(data["change_pct"], 10.0, places=4)

    def test_no_threshold_event_below_threshold(self):
        from trading_engine import TradingEngine
        client = MagicMock()
        cfg = {"threshold_percent": 5.0, "check_interval_seconds": 9999, "pairs": []}
        events = []
        engine = TradingEngine(client, cfg, on_event=lambda t, d: events.append((t, d)))

        coin = {"currency": "BTC", "balance": 1.0, "price_usd": 100.0, "value_usd": 100.0, "product_id": "BTC-USD"}
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        coin2 = {**coin, "price_usd": 102.0, "value_usd": 102.0}
        client.get_owned_coins_with_prices.return_value = [coin2]
        engine._tick()

        threshold_events = [e for e in events if e[0] == "threshold_reached"]
        self.assertEqual(len(threshold_events), 0)

    def test_update_config(self):
        engine, _, _ = self._make_engine()
        engine.update_config({"threshold_percent": 10.0, "check_interval_seconds": 30, "pairs": ["ETH-USD"]})
        self.assertEqual(engine._config["threshold_percent"], 10.0)


# =============================================================================
# API server tests (no real Flask serving)
# =============================================================================

class TestAPIServer(unittest.TestCase):
    def setUp(self):
        import api_server
        # Reset module state
        api_server._engine = None
        api_server._config = None
        api_server._event_log.clear()
        self._app = api_server.app.test_client()
        self._app.testing = True

    def test_status_no_engine(self):
        import api_server
        api_server._engine = None
        resp = self._app.get("/status")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertFalse(data["engine_active"])

    def test_portfolio_empty(self):
        import api_server
        api_server._engine = None
        resp = self._app.get("/portfolio")
        data = json.loads(resp.data)
        self.assertEqual(data["portfolio"], [])

    def test_engine_start_no_engine(self):
        import api_server
        api_server._engine = None
        resp = self._app.post("/engine/start")
        self.assertEqual(resp.status_code, 500)

    def test_engine_state_with_mock(self):
        import api_server
        mock_engine = MagicMock()
        mock_engine.is_active = True
        mock_engine.portfolio_snapshot = []
        api_server._engine = mock_engine
        resp = self._app.get("/engine")
        data = json.loads(resp.data)
        self.assertTrue(data["active"])

    def test_add_event_populates_log(self):
        import api_server
        api_server._event_log.clear()
        api_server.add_event("test_event", {"foo": "bar"})
        resp = self._app.get("/events")
        data = json.loads(resp.data)
        self.assertEqual(len(data["events"]), 1)
        self.assertEqual(data["events"][0]["type"], "test_event")

    def test_trade_buy_missing_params(self):
        import api_server
        mock_engine = MagicMock()
        api_server._engine = mock_engine
        resp = self._app.post("/trade/buy", json={})
        self.assertEqual(resp.status_code, 400)

    def test_config_hides_secret(self):
        import api_server
        from unittest.mock import MagicMock

        mock_config = MagicMock()
        mock_config.get_section.side_effect = lambda section: {
            "coinbase": {"api_key": "mykey", "api_secret": "mysecret", "use_sandbox": False},
            "trading": {"threshold_percent": 2.0},
            "api": {"enabled": True, "port": 8080, "host": "0.0.0.0"},
        }.get(section, {})
        api_server._config = mock_config

        resp = self._app.get("/config")
        data = json.loads(resp.data)
        # Secret should not appear
        self.assertNotIn("mysecret", json.dumps(data))
        # api_key should be masked
        self.assertEqual(data["coinbase"]["api_key"], "***")


if __name__ == "__main__":
    unittest.main()
