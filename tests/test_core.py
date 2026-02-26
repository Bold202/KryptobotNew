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


# =============================================================================
# SessionManager tests
# =============================================================================

class TestSessionManager(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._sessions_file = os.path.join(self._tmpdir.name, "sessions.json")

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_manager(self):
        from session_manager import SessionManager
        return SessionManager(sessions_file=self._sessions_file)

    def test_no_current_session_initially(self):
        mgr = self._make_manager()
        self.assertIsNone(mgr.get_current())

    def test_start_session_returns_dict(self):
        mgr = self._make_manager()
        sess = mgr.start_session()
        self.assertIn("session_id", sess)
        self.assertIn("start_time", sess)
        self.assertIsNone(sess["end_time"])

    def test_get_current_after_start(self):
        mgr = self._make_manager()
        mgr.start_session()
        curr = mgr.get_current()
        self.assertIsNotNone(curr)
        self.assertEqual(curr["auto_trades_count"], 0)

    def test_record_auto_trade_increments_counter(self):
        mgr = self._make_manager()
        mgr.start_session()
        mgr.record_trade("BUY", "BTC-USD", 0.1, 30000.0, is_auto=True)
        curr = mgr.get_current()
        self.assertEqual(curr["auto_trades_count"], 1)
        self.assertEqual(curr["manual_trades_count"], 0)

    def test_record_manual_trade_increments_counter(self):
        mgr = self._make_manager()
        mgr.start_session()
        mgr.record_trade("SELL", "BTC-USD", 0.05, 32000.0, is_auto=False)
        curr = mgr.get_current()
        self.assertEqual(curr["manual_trades_count"], 1)

    def test_pnl_buy_negative_sell_positive(self):
        mgr = self._make_manager()
        mgr.start_session()
        mgr.record_trade("BUY", "BTC-USD", 0.1, 30000.0, is_auto=True)   # -3000
        mgr.record_trade("SELL", "BTC-USD", 0.1, 32000.0, is_auto=True)  # +3200
        curr = mgr.get_current()
        self.assertAlmostEqual(curr["pnl_estimate"], 200.0, places=2)

    def test_volume_traded_tracks_product(self):
        mgr = self._make_manager()
        mgr.start_session()
        mgr.record_trade("BUY", "BTC-USD", 0.1, 30000.0, is_auto=True)
        curr = mgr.get_current()
        self.assertAlmostEqual(curr["volume_traded"]["BTC-USD"], 3000.0, places=2)

    def test_end_session_saves_to_history(self):
        mgr = self._make_manager()
        mgr.start_session()
        mgr.end_session()
        self.assertIsNone(mgr.get_current())
        history = mgr.get_history()
        self.assertEqual(len(history), 1)
        self.assertIsNotNone(history[0]["end_time"])

    def test_history_persisted_to_file(self):
        from session_manager import SessionManager
        mgr = SessionManager(sessions_file=self._sessions_file)
        mgr.start_session()
        mgr.end_session()
        # Reload
        mgr2 = SessionManager(sessions_file=self._sessions_file)
        self.assertEqual(len(mgr2.get_history()), 1)

    def test_history_capped_at_100(self):
        from session_manager import SessionManager
        mgr = SessionManager(sessions_file=self._sessions_file)
        for _ in range(105):
            mgr.start_session()
            mgr.end_session()
        self.assertLessEqual(len(mgr.get_history()), 100)

    def test_no_internal_fields_in_get_current(self):
        mgr = self._make_manager()
        mgr.start_session()
        curr = mgr.get_current()
        for key in curr:
            self.assertFalse(key.startswith("_"), f"Internal field exposed: {key}")

    def test_set_portfolio_start_value(self):
        mgr = self._make_manager()
        mgr.start_session()
        mgr.set_portfolio_start_value(10000.0)
        self.assertAlmostEqual(mgr.get_portfolio_start_value(), 10000.0)

    def test_portfolio_start_value_not_overwritten(self):
        mgr = self._make_manager()
        mgr.start_session()
        mgr.set_portfolio_start_value(10000.0)
        mgr.set_portfolio_start_value(9000.0)  # should not overwrite
        self.assertAlmostEqual(mgr.get_portfolio_start_value(), 10000.0)


# =============================================================================
# TradingEngine auto-trade tests
# =============================================================================

class TestTradingEngineAutoTrade(unittest.TestCase):
    def _make_engine(self, cfg_extra=None, events=None):
        from trading_engine import TradingEngine
        from session_manager import SessionManager
        import tempfile
        client = MagicMock()
        cfg = {
            "threshold_percent": 5.0,
            "check_interval_seconds": 9999,
            "pairs": [],
            "auto_trade_enabled": True,
            "order_size_percent": 10.0,
            "max_position_percent": 80.0,
            "max_daily_loss_percent": 10.0,
        }
        if cfg_extra:
            cfg.update(cfg_extra)
        collected = events if events is not None else []
        tmpdir = tempfile.mkdtemp()
        sess = SessionManager(sessions_file=os.path.join(tmpdir, "s.json"))
        sess.start_session()
        engine = TradingEngine(client, cfg, on_event=lambda t, d: collected.append((t, d)),
                               session_manager=sess)
        return engine, client, collected, sess

    def test_auto_buy_on_price_drop(self):
        """Kurs fällt stark → automatischer Kauf wird ausgeführt."""
        # Portfolio: 0.5 BTC @ 100 USD = 50 USD + 950 USD cash = 1000 USD total
        # → BTC position is 5% of portfolio (well under 80% limit)
        engine, client, events, sess = self._make_engine({"max_daily_loss_percent": 20.0})
        client.place_market_order.return_value = {"order_id": "auto-1"}

        btc = {"currency": "BTC", "balance": 0.5, "price_usd": 100.0,
               "value_usd": 50.0, "product_id": "BTC-USD"}
        usd = {"currency": "USD", "balance": 950.0, "price_usd": 1.0,
               "value_usd": 950.0, "product_id": None}
        client.get_owned_coins_with_prices.return_value = [btc, usd]
        engine._tick()  # sets reference

        # Preis fällt um 10% (über Schwelle von 5%)
        btc2 = {**btc, "price_usd": 90.0, "value_usd": 45.0}
        usd2 = {**usd}
        client.get_owned_coins_with_prices.return_value = [btc2, usd2]
        engine._tick()

        # place_market_order sollte mit BUY aufgerufen worden sein
        calls = client.place_market_order.call_args_list
        self.assertTrue(any(c[0][1] == "BUY" for c in calls), "Expected BUY order")

    def test_auto_sell_on_price_rise(self):
        """Kurs steigt stark → automatischer Verkauf wird ausgeführt."""
        engine, client, events, sess = self._make_engine()
        client.place_market_order.return_value = {"order_id": "auto-2"}

        coin = {"currency": "BTC", "balance": 1.0, "price_usd": 100.0,
                "value_usd": 100.0, "product_id": "BTC-USD"}
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        coin2 = {**coin, "price_usd": 110.0, "value_usd": 110.0}
        client.get_owned_coins_with_prices.return_value = [coin2]
        engine._tick()

        calls = client.place_market_order.call_args_list
        self.assertTrue(any(c[0][1] == "SELL" for c in calls), "Expected SELL order")

    def test_no_auto_trade_when_disabled(self):
        """Kein Auto-Trade wenn auto_trade_enabled=False."""
        engine, client, events, sess = self._make_engine({"auto_trade_enabled": False})

        coin = {"currency": "BTC", "balance": 1.0, "price_usd": 100.0,
                "value_usd": 100.0, "product_id": "BTC-USD"}
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        coin2 = {**coin, "price_usd": 115.0, "value_usd": 115.0}
        client.get_owned_coins_with_prices.return_value = [coin2]
        engine._tick()

        client.place_market_order.assert_not_called()

    def test_daily_loss_limit_blocks_trade(self):
        """Tagesverlust-Limit verhindert weiteren Auto-Trade."""
        engine, client, events, sess = self._make_engine({"max_daily_loss_percent": 5.0})
        # Portfolio startet bei 100 USD
        sess.set_portfolio_start_value(100.0)
        client.place_market_order.return_value = {"order_id": "x"}

        coin = {"currency": "BTC", "balance": 1.0, "price_usd": 100.0,
                "value_usd": 100.0, "product_id": "BTC-USD"}
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        # Portfolio fällt auf 90 USD (10% Verlust > 5% Limit)
        coin2 = {"currency": "BTC", "balance": 1.0, "price_usd": 90.0,
                 "value_usd": 90.0, "product_id": "BTC-USD"}
        client.get_owned_coins_with_prices.return_value = [coin2]
        engine._tick()

        limit_events = [e for e in events if e[0] == "limit_blocked"]
        self.assertTrue(len(limit_events) > 0, "Expected limit_blocked event")
        client.place_market_order.assert_not_called()

    def test_auto_trade_recorded_in_session(self):
        """Auto-Trade wird in Sitzung erfasst."""
        engine, client, events, sess = self._make_engine()
        client.place_market_order.return_value = {"order_id": "s-1"}

        coin = {"currency": "BTC", "balance": 1.0, "price_usd": 100.0,
                "value_usd": 100.0, "product_id": "BTC-USD"}
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        coin2 = {**coin, "price_usd": 115.0, "value_usd": 115.0}
        client.get_owned_coins_with_prices.return_value = [coin2]
        engine._tick()

        curr = sess.get_current()
        self.assertGreater(curr["auto_trades_count"], 0)

    def test_order_placed_event_has_is_auto_flag(self):
        """order_placed Event enthält is_auto=True bei Auto-Trade."""
        engine, client, events, sess = self._make_engine()
        client.place_market_order.return_value = {"order_id": "a"}

        coin = {"currency": "BTC", "balance": 1.0, "price_usd": 100.0,
                "value_usd": 100.0, "product_id": "BTC-USD"}
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        coin2 = {**coin, "price_usd": 112.0, "value_usd": 112.0}
        client.get_owned_coins_with_prices.return_value = [coin2]
        engine._tick()

        order_events = [e for e in events if e[0] == "order_placed"]
        auto_orders = [e for e in order_events if e[1].get("is_auto")]
        self.assertTrue(len(auto_orders) > 0)


# =============================================================================
# API server session endpoint tests
# =============================================================================

class TestAPIServerSessions(unittest.TestCase):
    def setUp(self):
        import api_server
        api_server._engine = None
        api_server._config = None
        api_server._session = None
        api_server._event_log.clear()
        self._app = api_server.app.test_client()
        self._app.testing = True

    def test_sessions_list_no_session_manager(self):
        import api_server
        api_server._session = None
        resp = self._app.get("/sessions")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["sessions"], [])

    def test_sessions_current_no_session_manager(self):
        import api_server
        api_server._session = None
        resp = self._app.get("/sessions/current")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIsNone(data["session"])

    def test_sessions_list_with_history(self):
        import api_server
        import tempfile
        from session_manager import SessionManager

        tmpdir = tempfile.mkdtemp()
        sess = SessionManager(sessions_file=os.path.join(tmpdir, "s.json"))
        sess.start_session()
        sess.end_session()
        api_server._session = sess

        resp = self._app.get("/sessions")
        data = json.loads(resp.data)
        self.assertEqual(len(data["sessions"]), 1)

    def test_sessions_current_with_active_session(self):
        import api_server
        import tempfile
        from session_manager import SessionManager

        tmpdir = tempfile.mkdtemp()
        sess = SessionManager(sessions_file=os.path.join(tmpdir, "s.json"))
        sess.start_session()
        api_server._session = sess

        resp = self._app.get("/sessions/current")
        data = json.loads(resp.data)
        self.assertIsNotNone(data["session"])
        self.assertIn("session_id", data["session"])


if __name__ == "__main__":
    unittest.main()
