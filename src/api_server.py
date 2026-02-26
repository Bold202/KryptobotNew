#!/usr/bin/env python3
"""
KryptoBot - Coinbase Crypto Assistant
REST API server (Flask) for external integrations such as Home Assistant.

Endpoints
---------
GET  /status           – bot status + portfolio snapshot
GET  /portfolio        – list of owned coins with current prices
GET  /engine           – trading engine state (active/inactive)
POST /engine/start     – start the trading engine
POST /engine/stop      – stop the trading engine
GET  /events           – last N events (simple log)
GET  /config           – current (sanitised) config
POST /config           – update writable config keys
POST /trade/buy        – place a manual market buy
POST /trade/sell       – place a manual market sell
"""

import logging
import threading
from collections import deque
from typing import Optional

from flask import Flask, jsonify, request
from flask_cors import CORS

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Module-level state injected from main.py
# -----------------------------------------------------------------------

_engine = None       # TradingEngine instance
_config = None       # ConfigManager instance
_session = None      # SessionManager instance
_event_log: deque = deque(maxlen=200)


def init(engine, config, session_manager=None):
    """Inject dependencies before calling start_server()."""
    global _engine, _config, _session
    _engine = engine
    _config = config
    _session = session_manager


def add_event(event_type: str, data: dict):
    """Called by the trading engine callback to populate the event log."""
    import datetime
    _event_log.appendleft(
        {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), "type": event_type, "data": data}
    )


# -----------------------------------------------------------------------
# Flask app
# -----------------------------------------------------------------------

app = Flask(__name__)
CORS(app)


def _portfolio():
    if _engine and _engine.portfolio_snapshot:
        return _engine.portfolio_snapshot
    return []


@app.route("/status")
def status():
    return jsonify(
        {
            "status": "ok",
            "engine_active": bool(_engine and _engine.is_active),
            "portfolio": _portfolio(),
        }
    )


@app.route("/portfolio")
def portfolio():
    return jsonify({"portfolio": _portfolio()})


@app.route("/engine")
def engine_state():
    return jsonify({"active": bool(_engine and _engine.is_active)})


@app.route("/engine/start", methods=["POST"])
def engine_start():
    if _engine is None:
        return jsonify({"error": "Engine not initialised"}), 500
    _engine.start()
    return jsonify({"status": "started"})


@app.route("/engine/stop", methods=["POST"])
def engine_stop():
    if _engine is None:
        return jsonify({"error": "Engine not initialised"}), 500
    _engine.stop()
    return jsonify({"status": "stopped"})


@app.route("/events")
def events():
    n = request.args.get("n", 50, type=int)
    return jsonify({"events": list(_event_log)[:n]})


@app.route("/config")
def get_config():
    if _config is None:
        return jsonify({}), 500
    # Return config but hide secrets
    cfg = {
        "trading": _config.get_section("trading"),
        "api": _config.get_section("api"),
        "coinbase": {
            "api_key": ("***" if _config.get_section("coinbase").get("api_key") else ""),
            "use_sandbox": _config.get_section("coinbase").get("use_sandbox", False),
        },
    }
    return jsonify(cfg)


@app.route("/config", methods=["POST"])
def update_config():
    if _config is None:
        return jsonify({"error": "Config not initialised"}), 500
    data = request.get_json(silent=True) or {}
    allowed = {"trading"}
    for section in allowed:
        if section in data:
            _config.update_section(section, data[section])
            if _engine:
                _engine.update_config(_config.get_section("trading"))
    return jsonify({"status": "updated"})


@app.route("/trade/buy", methods=["POST"])
def trade_buy():
    if _engine is None:
        return jsonify({"error": "Engine not initialised"}), 500
    data = request.get_json(silent=True) or {}
    product_id = data.get("product_id")
    base_size = data.get("base_size")
    if not product_id or not base_size:
        return jsonify({"error": "product_id and base_size required"}), 400
    try:
        result = _engine.manual_buy(product_id, str(base_size))
        return jsonify({"status": "ok", "result": result})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/trade/sell", methods=["POST"])
def trade_sell():
    if _engine is None:
        return jsonify({"error": "Engine not initialised"}), 500
    data = request.get_json(silent=True) or {}
    product_id = data.get("product_id")
    base_size = data.get("base_size")
    if not product_id or not base_size:
        return jsonify({"error": "product_id and base_size required"}), 400
    try:
        result = _engine.manual_sell(product_id, str(base_size))
        return jsonify({"status": "ok", "result": result})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/sessions")
def sessions_list():
    """Liste der vergangenen Sitzungen (neueste zuerst)."""
    if _session is None:
        return jsonify({"sessions": []})
    return jsonify({"sessions": _session.get_history()})


@app.route("/sessions/current")
def sessions_current():
    """Aktuelle laufende Sitzung (oder null, wenn keine aktiv)."""
    if _session is None:
        return jsonify({"session": None})
    return jsonify({"session": _session.get_current()})


# -----------------------------------------------------------------------
# Server thread
# -----------------------------------------------------------------------

_server_thread: Optional[threading.Thread] = None


def start_server(host: str = "0.0.0.0", port: int = 8080):
    """Launch the Flask server in a daemon thread."""
    global _server_thread

    def run():
        import logging as _logging
        _logging.getLogger("werkzeug").setLevel(_logging.WARNING)
        app.run(host=host, port=port, threaded=True, use_reloader=False)

    _server_thread = threading.Thread(target=run, daemon=True, name="api-server")
    _server_thread.start()
    logger.info("REST API server listening on %s:%s", host, port)
