#!/usr/bin/env python3
"""
KryptoBot – Session Manager
Verwaltet die aktuelle Trading-Sitzung und speichert abgeschlossene Sitzungen
in ~/.kryptobot/sessions.json (maximal 100 Einträge).
"""

import datetime
import json
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

SESSIONS_FILE = os.path.join(os.path.expanduser("~"), ".kryptobot", "sessions.json")
MAX_SESSIONS = 100


class SessionManager:
    """
    Verfolgt die aktuelle Trading-Sitzung und persistiert abgeschlossene Sitzungen.

    Jede Sitzung enthält:
      - session_id           eindeutige ID (Zeitstempel beim Start)
      - start_time           ISO-Zeitstempel Sitzungsstart
      - end_time             ISO-Zeitstempel Sitzungsende (null solange aktiv)
      - auto_trades_count    Anzahl automatisch ausgeführter Trades
      - manual_trades_count  Anzahl manuell ausgeführter Trades
      - volume_traded        Dict {product_id: gehandeltes_volumen_usd}
      - pnl_estimate         Vereinfachter Gewinn/Verlust (Verkäufe – Käufe in USD)
    """

    def __init__(self, sessions_file: Optional[str] = None):
        self._sessions_file = sessions_file or SESSIONS_FILE
        self._current: Optional[dict] = None
        self._history: list = []
        self._lock = threading.Lock()
        self._load_history()

    # ------------------------------------------------------------------
    # Sitzungs-Lifecycle
    # ------------------------------------------------------------------

    def start_session(self) -> dict:
        """Startet eine neue Sitzung und gibt sie zurück."""
        with self._lock:
            session_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
            self._current = {
                "session_id": session_id,
                "start_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "end_time": None,
                "auto_trades_count": 0,
                "manual_trades_count": 0,
                "volume_traded": {},       # product_id → USD-Volumen
                "pnl_estimate": 0.0,       # Vereinfachter Gewinn/Verlust
                "_portfolio_value_start": None,  # Für Verlustlimit (intern)
            }
            logger.info("Neue Sitzung gestartet: %s", session_id)
            return dict(self._current)

    def end_session(self) -> Optional[dict]:
        """Beendet die aktuelle Sitzung, speichert sie in der Historie."""
        with self._lock:
            if self._current is None:
                return None
            self._current["end_time"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            # Internes Feld vor dem Speichern entfernen
            session_copy = {k: v for k, v in self._current.items() if not k.startswith("_")}
            self._history.insert(0, session_copy)
            if len(self._history) > MAX_SESSIONS:
                self._history = self._history[:MAX_SESSIONS]
            self._current = None
            self._save_history()
            logger.info("Sitzung beendet und gespeichert.")
            return session_copy

    # ------------------------------------------------------------------
    # Trade-Erfassung
    # ------------------------------------------------------------------

    def record_trade(self, side: str, product_id: str, size: float, price_usd: float, is_auto: bool):
        """
        Erfasst einen ausgeführten Trade in der aktuellen Sitzung.

        :param side:        'BUY' oder 'SELL'
        :param product_id:  z. B. 'BTC-USD'
        :param size:        Handelsmenge in Basis-Coin (z. B. BTC)
        :param price_usd:   Aktueller Preis in USD
        :param is_auto:     True = automatischer Trade, False = manueller Trade
        """
        with self._lock:
            if self._current is None:
                return
            value_usd = float(size) * float(price_usd)

            # Zähler aktualisieren
            if is_auto:
                self._current["auto_trades_count"] += 1
            else:
                self._current["manual_trades_count"] += 1

            # Volumen aktualisieren
            vol = self._current["volume_traded"]
            vol[product_id] = vol.get(product_id, 0.0) + value_usd

            # Vereinfachter P&L: Verkauf = Einnahme (+), Kauf = Ausgabe (-)
            if side == "SELL":
                self._current["pnl_estimate"] += value_usd
            else:
                self._current["pnl_estimate"] -= value_usd

    def set_portfolio_start_value(self, value_usd: float):
        """Setzt den Portfolio-Startwert für das Tages-Verlustlimit."""
        with self._lock:
            if self._current and self._current.get("_portfolio_value_start") is None:
                self._current["_portfolio_value_start"] = float(value_usd)
                logger.info("Portfolio-Startwert gesetzt: %.2f USD", value_usd)

    def get_portfolio_start_value(self) -> Optional[float]:
        """Gibt den gespeicherten Portfolio-Startwert zurück (für Verlustlimit-Prüfung)."""
        with self._lock:
            if self._current:
                return self._current.get("_portfolio_value_start")
            return None

    # ------------------------------------------------------------------
    # Abfragen
    # ------------------------------------------------------------------

    def get_current(self) -> Optional[dict]:
        """Gibt die aktuelle Sitzung zurück (ohne interne Felder)."""
        with self._lock:
            if self._current is None:
                return None
            return {k: v for k, v in self._current.items() if not k.startswith("_")}

    def get_history(self) -> list:
        """Gibt die Liste abgeschlossener Sitzungen zurück (neueste zuerst)."""
        with self._lock:
            return list(self._history)

    # ------------------------------------------------------------------
    # Persistenz
    # ------------------------------------------------------------------

    def _load_history(self):
        try:
            if os.path.exists(self._sessions_file):
                with open(self._sessions_file, "r") as f:
                    self._history = json.load(f)
                logger.debug("Sitzungshistorie geladen: %d Einträge", len(self._history))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Konnte Sitzungshistorie nicht laden: %s", exc)
            self._history = []

    def _save_history(self):
        try:
            os.makedirs(os.path.dirname(self._sessions_file), exist_ok=True)
            with open(self._sessions_file, "w") as f:
                json.dump(self._history, f, indent=2)
        except OSError as exc:
            logger.warning("Konnte Sitzungshistorie nicht speichern: %s", exc)
