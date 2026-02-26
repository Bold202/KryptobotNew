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
from collections import deque
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
        engine = TradingEngine(client, cfg, on_event=lambda t, d: collected.append((t, d)),
                               coinbase_config={"use_sandbox": True})
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
                               session_manager=sess, coinbase_config={"use_sandbox": True})
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




# =============================================================================
# TradingEngine fixed_eur_steps strategy tests
# =============================================================================

class TestTradingEngineFixedSteps(unittest.TestCase):
    def _make_engine(self, strategies, events=None):
        from trading_engine import TradingEngine
        client = MagicMock()
        cfg = {
            "mode": "fixed_eur_steps",
            "check_interval_seconds": 9999,
            "coin_strategies": strategies,
        }
        collected = events if events is not None else []
        engine = TradingEngine(client, cfg, on_event=lambda t, d: collected.append((t, d)),
                               coinbase_config={"use_sandbox": True})
        return engine, client, collected

    def _coin(self, product_id, balance, price_usd):
        return {
            "currency": product_id.split("-")[0],
            "balance": balance,
            "price_usd": price_usd,
            "value_usd": balance * price_usd,
            "product_id": product_id,
        }

    def test_sell_when_value_exceeds_base_plus_step(self):
        """Value ≥ base + step → SELL."""
        strategies = [{"product_id": "BTC-USD", "base_value_usd": 25.0, "step_usd": 0.5}]
        engine, client, events = self._make_engine(strategies)
        client.place_market_order.return_value = {"order_id": "s1"}

        # value = 0.1 * 255.0 = 25.5  (= base + step)
        coin = self._coin("BTC-USD", 0.1, 255.0)
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        calls = client.place_market_order.call_args_list
        self.assertTrue(any(c[0][1] == "SELL" for c in calls))

    def test_buy_when_value_below_base_minus_step(self):
        """Value ≤ base − step → BUY."""
        strategies = [{"product_id": "BTC-USD", "base_value_usd": 25.0, "step_usd": 0.5}]
        engine, client, events = self._make_engine(strategies)
        client.place_market_order.return_value = {"order_id": "b1"}

        # value = 0.1 * 245.0 = 24.5  (= base - step)
        coin = self._coin("BTC-USD", 0.1, 245.0)
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        calls = client.place_market_order.call_args_list
        self.assertTrue(any(c[0][1] == "BUY" for c in calls))

    def test_no_trade_within_band(self):
        """Value within (base-step, base+step) → no trade."""
        strategies = [{"product_id": "BTC-USD", "base_value_usd": 25.0, "step_usd": 0.5}]
        engine, client, events = self._make_engine(strategies)

        # value = 0.1 * 250.0 = 25.0 exactly at base
        coin = self._coin("BTC-USD", 0.1, 250.0)
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        client.place_market_order.assert_not_called()

    def test_sell_blocked_after_buy(self):
        """After a BUY, an immediate SELL is blocked (anti-oscillation)."""
        strategies = [{"product_id": "BTC-USD", "base_value_usd": 25.0, "step_usd": 0.5}]
        engine, client, events = self._make_engine(strategies)
        client.place_market_order.return_value = {"order_id": "x"}

        # First tick: BUY (value drops to 24.5)
        coin_buy = self._coin("BTC-USD", 0.1, 245.0)
        client.get_owned_coins_with_prices.return_value = [coin_buy]
        engine._tick()
        client.place_market_order.reset_mock()

        # Second tick: value rises to 25.5 → SELL should be blocked (last_action=BUY)
        coin_sell = self._coin("BTC-USD", 0.1, 255.0)
        client.get_owned_coins_with_prices.return_value = [coin_sell]
        engine._tick()

        client.place_market_order.assert_not_called()

    def test_sell_allowed_after_buy_then_sell(self):
        """After BUY → SELL sequence, next SELL trigger is allowed."""
        strategies = [{"product_id": "BTC-USD", "base_value_usd": 25.0, "step_usd": 0.5}]
        engine, client, events = self._make_engine(strategies)
        client.place_market_order.return_value = {"order_id": "x"}

        # Force last_action = SELL directly (simulating a prior SELL)
        engine._coin_states["BTC-USD"] = {"last_action": "SELL"}

        coin_sell = self._coin("BTC-USD", 0.1, 255.0)
        client.get_owned_coins_with_prices.return_value = [coin_sell]
        engine._tick()

        calls = client.place_market_order.call_args_list
        self.assertTrue(any(c[0][1] == "SELL" for c in calls))

    def test_state_last_action_set_after_buy(self):
        """last_action is set to BUY after a buy trade."""
        strategies = [{"product_id": "ETH-USD", "base_value_usd": 10.0, "step_usd": 1.0}]
        engine, client, events = self._make_engine(strategies)
        client.place_market_order.return_value = {"order_id": "e1"}

        # value = 1.0 * 9.0 = 9.0  (≤ base - step = 9.0)
        coin = self._coin("ETH-USD", 1.0, 9.0)
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        self.assertEqual(engine._coin_states["ETH-USD"]["last_action"], "BUY")

    def test_state_last_action_set_after_sell(self):
        """last_action is set to SELL after a sell trade."""
        strategies = [{"product_id": "ETH-USD", "base_value_usd": 10.0, "step_usd": 1.0}]
        engine, client, events = self._make_engine(strategies)
        client.place_market_order.return_value = {"order_id": "e2"}

        # value = 1.0 * 11.0 = 11.0  (≥ base + step = 11.0)
        coin = self._coin("ETH-USD", 1.0, 11.0)
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        self.assertEqual(engine._coin_states["ETH-USD"]["last_action"], "SELL")

    def test_multiple_coins_independent(self):
        """Multiple coins in strategy are handled independently."""
        strategies = [
            {"product_id": "BTC-USD", "base_value_usd": 25.0, "step_usd": 0.5},
            {"product_id": "ETH-USD", "base_value_usd": 10.0, "step_usd": 1.0},
        ]
        engine, client, events = self._make_engine(strategies)
        client.place_market_order.return_value = {"order_id": "m1"}

        # BTC triggers SELL, ETH triggers BUY
        coins = [
            self._coin("BTC-USD", 0.1, 255.0),  # 25.5 ≥ 25.5 → SELL
            self._coin("ETH-USD", 1.0, 9.0),    # 9.0 ≤ 9.0 → BUY
        ]
        client.get_owned_coins_with_prices.return_value = coins
        engine._tick()

        sides = [c[0][1] for c in client.place_market_order.call_args_list]
        self.assertIn("SELL", sides)
        self.assertIn("BUY", sides)


# =============================================================================
# TradingEngine fixed_steps mode: new field names, enabled flag, mode alias
# =============================================================================

class TestTradingEngineFixedStepsNewFields(unittest.TestCase):
    """Tests for the extended fixed-step strategy:
    - 'base_value'/'step' field names (preferred)
    - 'enabled' per-coin flag
    - 'fixed_steps' mode alias
    """

    def _make_engine(self, strategies, mode="fixed_steps", events=None):
        from trading_engine import TradingEngine
        client = MagicMock()
        cfg = {
            "mode": mode,
            "check_interval_seconds": 9999,
            "coin_strategies": strategies,
        }
        collected = events if events is not None else []
        engine = TradingEngine(client, cfg, on_event=lambda t, d: collected.append((t, d)),
                               coinbase_config={"use_sandbox": True})
        return engine, client, collected

    def _coin(self, product_id, balance, price_usd):
        return {
            "currency": product_id.split("-")[0],
            "balance": balance,
            "price_usd": price_usd,
            "value_usd": balance * price_usd,
            "product_id": product_id,
        }

    def test_new_field_names_sell_trigger(self):
        """'base_value'/'step' field names trigger SELL correctly."""
        strategies = [{"product_id": "BTC-USD", "base_value": 25.0, "step": 0.5}]
        engine, client, _ = self._make_engine(strategies)
        client.place_market_order.return_value = {"order_id": "n1"}

        coin = self._coin("BTC-USD", 0.1, 255.0)  # value = 25.5 >= 25.5
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        calls = client.place_market_order.call_args_list
        self.assertTrue(any(c[0][1] == "SELL" for c in calls))

    def test_new_field_names_buy_trigger(self):
        """'base_value'/'step' field names trigger BUY correctly."""
        strategies = [{"product_id": "BTC-USD", "base_value": 25.0, "step": 0.5}]
        engine, client, _ = self._make_engine(strategies)
        client.place_market_order.return_value = {"order_id": "n2"}

        coin = self._coin("BTC-USD", 0.1, 245.0)  # value = 24.5 <= 24.5
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        calls = client.place_market_order.call_args_list
        self.assertTrue(any(c[0][1] == "BUY" for c in calls))

    def test_enabled_false_skips_coin(self):
        """Strategy with enabled=False is not executed."""
        strategies = [{"product_id": "BTC-USD", "base_value": 25.0, "step": 0.5, "enabled": False}]
        engine, client, _ = self._make_engine(strategies)

        coin = self._coin("BTC-USD", 0.1, 255.0)  # would normally trigger SELL
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        client.place_market_order.assert_not_called()

    def test_enabled_true_executes_coin(self):
        """Strategy with enabled=True is executed."""
        strategies = [{"product_id": "BTC-USD", "base_value": 25.0, "step": 0.5, "enabled": True}]
        engine, client, _ = self._make_engine(strategies)
        client.place_market_order.return_value = {"order_id": "e1"}

        coin = self._coin("BTC-USD", 0.1, 255.0)  # value = 25.5 >= 25.5 → SELL
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        calls = client.place_market_order.call_args_list
        self.assertTrue(any(c[0][1] == "SELL" for c in calls))

    def test_fixed_steps_mode_alias(self):
        """mode='fixed_steps' activates fixed-step strategy (alias for fixed_eur_steps)."""
        strategies = [{"product_id": "ETH-USD", "base_value": 10.0, "step": 1.0}]
        engine, client, _ = self._make_engine(strategies, mode="fixed_steps")
        client.place_market_order.return_value = {"order_id": "alias1"}

        coin = self._coin("ETH-USD", 1.0, 11.0)  # value = 11.0 >= 11.0 → SELL
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        calls = client.place_market_order.call_args_list
        self.assertTrue(any(c[0][1] == "SELL" for c in calls))

    def test_mixed_old_new_field_names_fallback(self):
        """Legacy 'base_value_usd'/'step_usd' still work when new names absent."""
        strategies = [{"product_id": "BTC-USD", "base_value_usd": 25.0, "step_usd": 0.5}]
        engine, client, _ = self._make_engine(strategies, mode="fixed_steps")
        client.place_market_order.return_value = {"order_id": "leg1"}

        coin = self._coin("BTC-USD", 0.1, 255.0)  # value = 25.5 >= 25.5 → SELL
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        calls = client.place_market_order.call_args_list
        self.assertTrue(any(c[0][1] == "SELL" for c in calls))

    def test_security_limits_applied_when_configured(self):
        """max_position_percent blocks BUY when explicitly configured."""
        from trading_engine import TradingEngine
        client = MagicMock()
        cfg = {
            "mode": "fixed_steps",
            "check_interval_seconds": 9999,
            "coin_strategies": [{"product_id": "BTC-USD", "base_value": 25.0, "step": 0.5}],
            "max_position_percent": 10.0,  # very tight limit
        }
        events = []
        engine = TradingEngine(client, cfg, on_event=lambda t, d: events.append((t, d)),
                               coinbase_config={"use_sandbox": True})

        # total = 24.5, limit = 2.45; coin + step = 25.0 > 2.45 → blocked
        coin = {"currency": "BTC", "balance": 0.1, "price_usd": 245.0,
                "value_usd": 24.5, "product_id": "BTC-USD"}
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        client.place_market_order.assert_not_called()
        limit_events = [e for e in events if e[0] == "limit_blocked"]
        self.assertTrue(len(limit_events) > 0)


# =============================================================================
# Safe Live Mode tests
# =============================================================================

class TestSafeLiveMode(unittest.TestCase):
    def _make_engine(self, use_sandbox=False, live_trading_armed=False,
                     mode="fixed_steps", events=None):
        from trading_engine import TradingEngine
        client = MagicMock()
        cfg = {
            "mode": mode,
            "check_interval_seconds": 9999,
            "live_trading_armed": live_trading_armed,
            "auto_trade_enabled": True,
            "threshold_percent": 5.0,
            "order_size_percent": 10.0,
            "pairs": [],
            "coin_strategies": [
                {"product_id": "BTC-USD", "base_value": 25.0, "step": 0.5, "enabled": True}
            ],
        }
        collected = events if events is not None else []
        engine = TradingEngine(client, cfg, on_event=lambda t, d: collected.append((t, d)),
                               coinbase_config={"use_sandbox": use_sandbox})
        return engine, client, collected

    def _btc_coin(self, price):
        return {"currency": "BTC", "balance": 0.1, "price_usd": price,
                "value_usd": 0.1 * price, "product_id": "BTC-USD"}

    # --- fixed_steps mode ---

    def test_live_unarmed_blocks_fixed_step_trade(self):
        """Live mode without armed flag must block all fixed-step trades."""
        engine, client, events = self._make_engine(use_sandbox=False, live_trading_armed=False)
        coin = self._btc_coin(255.0)  # value=25.5 >= base+step → SELL trigger
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        client.place_market_order.assert_not_called()
        blocked = [e for e in events if e[0] == "trade_blocked_safety"]
        self.assertTrue(len(blocked) > 0, "Expected trade_blocked_safety event")

    def test_live_armed_allows_fixed_step_trade(self):
        """Live mode WITH armed flag must allow fixed-step trades."""
        engine, client, events = self._make_engine(use_sandbox=False, live_trading_armed=True)
        client.place_market_order.return_value = {"order_id": "x"}
        coin = self._btc_coin(255.0)  # value=25.5 >= base+step → SELL trigger
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        calls = client.place_market_order.call_args_list
        self.assertTrue(any(c[0][1] == "SELL" for c in calls), "Expected SELL order")

    def test_sandbox_allows_fixed_step_trade_without_armed(self):
        """Sandbox mode must allow trades even without live_trading_armed."""
        engine, client, events = self._make_engine(use_sandbox=True, live_trading_armed=False)
        client.place_market_order.return_value = {"order_id": "y"}
        coin = self._btc_coin(255.0)
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        calls = client.place_market_order.call_args_list
        self.assertTrue(any(c[0][1] == "SELL" for c in calls))

    # --- manual trade ---

    def test_live_unarmed_blocks_manual_buy(self):
        """manual_buy raises RuntimeError in live unarmed mode."""
        engine, client, events = self._make_engine(use_sandbox=False, live_trading_armed=False)
        with self.assertRaises(RuntimeError):
            engine.manual_buy("BTC-USD", "0.001")
        blocked = [e for e in events if e[0] == "trade_blocked_safety"]
        self.assertTrue(len(blocked) > 0)

    def test_live_unarmed_blocks_manual_sell(self):
        """manual_sell raises RuntimeError in live unarmed mode."""
        engine, client, events = self._make_engine(use_sandbox=False, live_trading_armed=False)
        with self.assertRaises(RuntimeError):
            engine.manual_sell("BTC-USD", "0.001")

    def test_sandbox_allows_manual_buy(self):
        """manual_buy succeeds in sandbox mode."""
        engine, client, events = self._make_engine(use_sandbox=True, live_trading_armed=False)
        client.place_market_order.return_value = {"order_id": "m1"}
        result = engine.manual_buy("BTC-USD", "0.001")
        self.assertEqual(result["order_id"], "m1")

    # --- threshold mode ---

    def test_live_unarmed_blocks_auto_trade_threshold_mode(self):
        """Auto-trade in threshold mode is blocked in live unarmed mode."""
        engine, client, events = self._make_engine(
            use_sandbox=False, live_trading_armed=False, mode="threshold_percent")
        coin = {"currency": "BTC", "balance": 1.0, "price_usd": 100.0,
                "value_usd": 100.0, "product_id": "BTC-USD"}
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()
        coin2 = {**coin, "price_usd": 115.0, "value_usd": 115.0}
        client.get_owned_coins_with_prices.return_value = [coin2]
        engine._tick()

        client.place_market_order.assert_not_called()
        blocked = [e for e in events if e[0] == "trade_blocked_safety"]
        self.assertTrue(len(blocked) > 0)


# =============================================================================
# Anti-Churn (cooldown + rate limit) tests
# =============================================================================

class TestAntiChurn(unittest.TestCase):
    def _make_engine(self, strategy_extra=None, events=None):
        from trading_engine import TradingEngine
        strategy = {"product_id": "BTC-USD", "base_value": 25.0, "step": 0.5, "enabled": True}
        if strategy_extra:
            strategy.update(strategy_extra)
        client = MagicMock()
        cfg = {
            "mode": "fixed_steps",
            "check_interval_seconds": 9999,
            "coin_strategies": [strategy],
        }
        collected = events if events is not None else []
        engine = TradingEngine(client, cfg, on_event=lambda t, d: collected.append((t, d)),
                               coinbase_config={"use_sandbox": True})
        return engine, client, collected

    def _btc_sell_coin(self):
        return {"currency": "BTC", "balance": 0.1, "price_usd": 255.0,
                "value_usd": 25.5, "product_id": "BTC-USD"}

    def test_cooldown_blocks_second_trade(self):
        """After a trade, a second trade within cooldown_seconds is blocked."""
        events = []
        engine, client, events = self._make_engine(
            strategy_extra={"cooldown_seconds": 3600, "max_trades_per_hour": 100})
        client.place_market_order.return_value = {"order_id": "c1"}
        coin = self._btc_sell_coin()
        client.get_owned_coins_with_prices.return_value = [coin]

        # First trade goes through
        engine._tick()
        self.assertEqual(client.place_market_order.call_count, 1)
        client.place_market_order.reset_mock()

        # Reset last_action so SELL is not blocked by anti-oscillation
        engine._coin_states["BTC-USD"]["last_action"] = "SELL"

        # Second trade within cooldown → blocked
        engine._tick()
        client.place_market_order.assert_not_called()
        cooldown_events = [e for e in events if e[0] == "limit_blocked"
                           and e[1].get("reason") == "cooldown"]
        self.assertTrue(len(cooldown_events) > 0, "Expected cooldown limit_blocked event")

    def test_rate_limit_blocks_trade(self):
        """After max_trades_per_hour trades, further trades are blocked."""
        events = []
        engine, client, events = self._make_engine(
            strategy_extra={"cooldown_seconds": 0, "max_trades_per_hour": 2})
        client.place_market_order.return_value = {"order_id": "r1"}

        # Manually inject 2 trade timestamps (fills the rate-limit bucket)
        import time
        engine._trade_timestamps["BTC-USD"] = deque(
            [time.time() - 10, time.time() - 5])

        # Reset state so trade logic is not blocked by anti-oscillation
        engine._coin_states["BTC-USD"] = {"last_action": "SELL"}

        coin = self._btc_sell_coin()
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        client.place_market_order.assert_not_called()
        rate_events = [e for e in events if e[0] == "limit_blocked"
                       and e[1].get("reason") == "rate_limit"]
        self.assertTrue(len(rate_events) > 0, "Expected rate_limit limit_blocked event")

    def test_cooldown_allows_trade_after_expiry(self):
        """Trade is allowed after cooldown has expired."""
        events = []
        engine, client, events = self._make_engine(
            strategy_extra={"cooldown_seconds": 1, "max_trades_per_hour": 100})
        client.place_market_order.return_value = {"order_id": "e1"}

        # Inject a timestamp 2 seconds ago (cooldown=1s → expired)
        import time
        engine._trade_timestamps["BTC-USD"] = deque([time.time() - 2])
        engine._coin_states["BTC-USD"] = {"last_action": "SELL"}

        coin = self._btc_sell_coin()
        client.get_owned_coins_with_prices.return_value = [coin]
        engine._tick()

        calls = client.place_market_order.call_args_list
        self.assertTrue(any(c[0][1] == "SELL" for c in calls), "Expected SELL after cooldown")

    def test_get_cooldown_remaining(self):
        """get_cooldown_remaining returns correct remaining seconds."""
        engine, _, _ = self._make_engine(strategy_extra={"cooldown_seconds": 60})
        import time
        engine._trade_timestamps["BTC-USD"] = deque([time.time() - 10])
        strategy = {"cooldown_seconds": 60}
        remaining = engine.get_cooldown_remaining("BTC-USD", strategy)
        self.assertAlmostEqual(remaining, 50.0, delta=1.0)

    def test_get_trades_last_hour(self):
        """get_trades_last_hour counts recent trades."""
        engine, _, _ = self._make_engine()
        import time
        engine._trade_timestamps["BTC-USD"] = deque(
            [time.time() - 100, time.time() - 200, time.time() - 7200])  # 2 recent, 1 old
        count = engine.get_trades_last_hour("BTC-USD")
        self.assertEqual(count, 2)


# =============================================================================
# GET /strategies/effective API tests
# =============================================================================

class TestStrategiesEffectiveEndpoint(unittest.TestCase):
    def setUp(self):
        import api_server
        api_server._engine = None
        api_server._config = None
        api_server._session = None
        api_server._event_log.clear()
        self._app = api_server.app.test_client()
        self._app.testing = True

    def test_strategies_effective_no_config(self):
        import api_server
        api_server._config = None
        resp = self._app.get("/strategies/effective")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["strategies"], [])

    def test_strategies_effective_returns_entries(self):
        import api_server
        from unittest.mock import MagicMock
        mock_config = MagicMock()
        mock_config.get_section.side_effect = lambda s: {
            "trading": {
                "coin_strategies": [
                    {"product_id": "BTC-USD", "base_value": 25.0, "step": 0.5,
                     "enabled": True, "cooldown_seconds": 60, "max_trades_per_hour": 6},
                ]
            }
        }.get(s, {})
        api_server._config = mock_config
        api_server._engine = None

        resp = self._app.get("/strategies/effective")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(len(data["strategies"]), 1)
        s = data["strategies"][0]
        self.assertEqual(s["product_id"], "BTC-USD")
        self.assertEqual(s["base_value"], 25.0)
        self.assertEqual(s["step"], 0.5)
        self.assertTrue(s["enabled"])
        self.assertEqual(s["cooldown_seconds"], 60)
        self.assertEqual(s["max_trades_per_hour"], 6)
        self.assertIsNone(s["last_action"])
        self.assertEqual(s["cooldown_remaining"], 0.0)
        self.assertEqual(s["trades_last_hour"], 0)
        self.assertAlmostEqual(s["next_sell_trigger"], 25.5)
        self.assertAlmostEqual(s["next_buy_trigger"], 24.5)

    def test_strategies_effective_with_engine_state(self):
        import api_server
        import time
        from unittest.mock import MagicMock
        from trading_engine import TradingEngine

        mock_config = MagicMock()
        strategies = [
            {"product_id": "ETH-USD", "base_value": 10.0, "step": 1.0,
             "enabled": True, "cooldown_seconds": 30, "max_trades_per_hour": 4},
        ]
        mock_config.get_section.side_effect = lambda s: {
            "trading": {"coin_strategies": strategies}
        }.get(s, {})
        api_server._config = mock_config

        client = MagicMock()
        cfg = {"mode": "fixed_steps", "coin_strategies": strategies}
        engine = TradingEngine(client, cfg, coinbase_config={"use_sandbox": True})
        engine._coin_states["ETH-USD"] = {"last_action": "BUY"}
        engine._trade_timestamps["ETH-USD"] = deque(
            [time.time() - 10])
        api_server._engine = engine

        resp = self._app.get("/strategies/effective")
        data = json.loads(resp.data)
        s = data["strategies"][0]
        self.assertEqual(s["last_action"], "BUY")
        self.assertEqual(s["trades_last_hour"], 1)
        self.assertGreater(s["cooldown_remaining"], 0)

if __name__ == "__main__":
    unittest.main()
