#!/usr/bin/env python3
"""
KryptoBot – AI Analyst (heuristik-basiert, ohne externe API).

Analysiert historische Kerzendaten und schlägt Trading-Parameter vor.
"""

import math
import time


class AIAnalyst:
    """Schlägt Trading-Parameter auf Basis technischer Analyse vor."""

    def __init__(self, client):
        self._client = client

    def analyze_market(self, product_id: str) -> dict:
        """Analysiere den Markt für *product_id* und gib Parametervorschläge zurück.

        Ruft die letzten 24 Stunden-Kerzen ab und berechnet:
        - ``base_value``: letzter Schlusskurs
        - ``step``: basierend auf der Preisvolatilität (Standardabweichung)
        - ``cooldown_seconds``: abhängig von der Volatilität

        Gibt ein Dict mit den Schlüsseln ``base_value``, ``step``,
        ``cooldown_seconds``, ``reason``, ``volatility_pct``, ``std_dev``
        und ``candles_analyzed`` zurück, oder ``{"error": "..."}`` bei Fehler.
        """
        end = int(time.time())
        start = end - 24 * 3600

        try:
            candles = self._client.get_candles(
                product_id, start=start, end=end, granularity="ONE_HOUR"
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Fehler beim Abrufen der Kerzendaten: {exc}"}

        if not candles:
            return {"error": "Keine Kerzendaten verfügbar."}

        closes = []
        for c in candles:
            try:
                closes.append(float(c.get("close", 0)))
            except (ValueError, TypeError):
                pass

        if not closes:
            return {"error": "Keine Preisdaten in den Kerzen."}

        # --- Statistik ---
        n = len(closes)
        mean_price = sum(closes) / n
        variance = sum((x - mean_price) ** 2 for x in closes) / n
        std_dev = math.sqrt(variance)
        last_price = closes[0]  # jüngste Kerze zuerst (Coinbase-Sortierung)

        base_value = round(last_price, 2)

        # Step: mindestens 0,5 % des Basiswerts (Gebührenfalle vermeiden),
        # aber skaliert mit der halben Standardabweichung.
        min_step = base_value * 0.005
        suggested_step = max(min_step, 0.5 * std_dev)
        suggested_step = round(suggested_step, 4)

        # Cooldown: kürzere Pausen bei hoher Volatilität (mehr Handelschancen)
        volatility_pct = (std_dev / mean_price * 100) if mean_price > 0 else 0.0
        if volatility_pct > 3.0:
            cooldown = 300
            reason = (
                f"Hohe Volatilität erkannt ({volatility_pct:.1f} %) "
                "→ Step erhöht, kurzes Cooldown (5 min)."
            )
        elif volatility_pct > 1.0:
            cooldown = 600
            reason = (
                f"Mittlere Volatilität ({volatility_pct:.1f} %) "
                "→ Step moderat, Cooldown 10 min."
            )
        else:
            cooldown = 1800
            reason = (
                f"Niedrige Volatilität erkannt ({volatility_pct:.1f} %) "
                "→ Step reduziert, längeres Cooldown (30 min)."
            )

        return {
            "base_value": base_value,
            "step": suggested_step,
            "cooldown_seconds": cooldown,
            "reason": reason,
            "volatility_pct": round(volatility_pct, 2),
            "std_dev": round(std_dev, 4),
            "candles_analyzed": n,
        }
