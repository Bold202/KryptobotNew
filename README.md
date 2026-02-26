# KryptoBot – Coinbase Crypto Assistant

Ein automatisierter Krypto-Trading-Assistent für den Raspberry Pi, der über die **Coinbase Advanced Trade API** Kurse überwacht und bei eingestellter Differenz handelt.

---

## ⚠ Sicher starten in 10 Minuten

> **Für Einsteiger – bitte diese Reihenfolge einhalten!**

**Schritt 1 – Sandbox aktivieren**  
In `~/.kryptobot/config.json` setze:
```json
"coinbase": { "use_sandbox": true }
```
Im Sandbox-Modus werden **keine echten Trades** ausgeführt.  
Das GUI zeigt ein grünes Badge **„🟢 SANDBOX (sicher)"**.

**Schritt 2 – `live_trading_armed` bleibt `false`**  
Lass den Standardwert stehen:
```json
"trading": { "live_trading_armed": false }
```
Solange dieser Wert `false` ist und `use_sandbox` ebenfalls `false`, werden alle Trades blockiert (Event `trade_blocked_safety`).

**Schritt 3 – Mindestens 24 Stunden beobachten**  
Beobachte die Bot-Entscheidungen im Event-Log, ohne echtes Geld zu riskieren.

**Schritt 4 – Erst dann Live scharf schalten**  
Nur wenn du mit dem Verhalten zufrieden bist:
1. Setze `use_sandbox: false`
2. Setze `live_trading_armed: true`
3. Das GUI zeigt dann ein rotes Badge **„🔴 LIVE (ECHTES GELD)"** – damit weißt du jederzeit, in welchem Modus der Bot läuft.

> 🔴 **Warnung:** Echte Trades können zu Verlusten führen. Starte immer mit kleinen `order_size_percent`-Werten (z.B. 2–5 %) und niedrigen `max_daily_loss_percent`-Werten.

---

## Schnellstart (Raspberry Pi)

1. `install.sh` auf den Desktop kopieren und ausführbar machen:
   ```bash
   chmod +x ~/Desktop/install.sh
   ./~/Desktop/install.sh
   ```
   Das Skript holt die aktuelle Version aus dem Repo, installiert alle Abhängigkeiten und startet die App.

2. Beim ersten Start öffnet sich der **Einrichtungsassistent** und führt durch die Konfiguration (API-Schlüssel, Schwellenwerte, REST-API-Port).

---

## Voraussetzungen

| Software     | Paket / Quelle                             |
|--------------|--------------------------------------------|
| Python 3.9+  | vorinstalliert auf Raspberry Pi OS         |
| python3-tk   | `sudo apt install python3-tk`              |
| Git          | `sudo apt install git`                     |

Python-Abhängigkeiten (werden von `install.sh` automatisch installiert):

```
requests
flask
flask-cors
cryptography
PyJWT
```

---

## Manuelle Installation

```bash
git clone https://github.com/Bold202/KryptobotNew.git ~/KryptobotNew
cd ~/KryptobotNew
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 src/main.py
```

---

## Konfiguration

Die Konfigurationsdatei liegt unter `~/.kryptobot/config.json`.  
Beim ersten Start wird sie automatisch aus `config.example.json` erstellt.

| Schlüssel                               | Bedeutung                                          |
|-----------------------------------------|----------------------------------------------------|
| `coinbase.api_key`                      | Coinbase Advanced Trade API Key                    |
| `coinbase.api_secret`                   | Coinbase Advanced Trade API Secret                 |
| `coinbase.use_sandbox`                  | `true` für Sandbox-Tests (kein echter Handel)      |
| `trading.threshold_percent`             | Kursabweichung (%) die einen Handel auslöst        |
| `trading.check_interval_seconds`        | Prüfintervall in Sekunden                          |
| `trading.pairs`                         | Liste der Handelspaare (z. B. `["BTC-USD"]`)       |
| `trading.auto_trade_enabled`            | `true` = Bot handelt vollautomatisch               |
| `trading.order_size_percent`            | Auftragsgröße in % des Portfolios je Trade         |
| `trading.max_position_percent`          | Max. Anteil des Portfolios je Coin (%)             |
| `trading.max_daily_loss_percent`        | Stopp-Schwelle: max. Tagesverlust in % des Portfolios |
| `trading.live_trading_armed`            | `true` = Live-Trading freigegeben (Sicherheitsschalter, default `false`) |
| `api.enabled`                           | REST-API für externe Integrationen aktivieren      |
| `api.port`                              | REST-API Port (Standard: `8080`)                   |

### Coin-Strategie Felder (`trading.coin_strategies[]`)

| Feld                    | Bedeutung                                          |
|-------------------------|----------------------------------------------------|
| `product_id`            | Handelspaar, z. B. `BTC-USD`                       |
| `enabled`               | `true`/`false` – Strategie aktiv?                  |
| `base_value`            | Referenzwert in USD für Kauf-/Verkauf-Entscheidung |
| `step`                  | Schrittgröße in USD (muss `< base_value`)          |
| `cooldown_seconds`      | Mindestabstand zwischen zwei Trades (Standard: 60) |
| `max_trades_per_hour`   | Max. Anzahl Trades pro Stunde (Standard: 6)        |

---

## REST API (Home Assistant / externe Monitoring-Tools)

Wenn die API aktiviert ist, lauscht sie auf Port 8080 (konfigurierbar).

| Methode | Endpunkt                  | Beschreibung                                   |
|---------|---------------------------|------------------------------------------------|
| GET     | `/status`                 | Bot-Status + Portfolio-Snapshot                |
| GET     | `/portfolio`              | Liste der gehaltenen Coins mit Kursen          |
| GET     | `/engine`                 | Trading-Automatik aktiv/inaktiv                |
| POST    | `/engine/start`           | Trading-Automatik starten                      |
| POST    | `/engine/stop`            | Trading-Automatik stoppen                      |
| GET     | `/events?n=50`            | Letzte N Ereignisse (Log)                      |
| GET     | `/config`                 | Aktuelle Konfiguration (API-Keys versteckt)    |
| POST    | `/config`                 | Trading-Einstellungen aktualisieren            |
| POST    | `/trade/buy`              | Manuellen Markt-Kauf ausführen                 |
| POST    | `/trade/sell`             | Manuellen Markt-Verkauf ausführen              |
| GET     | `/sessions`               | Liste vergangener Sitzungen (neueste zuerst)   |
| GET     | `/sessions/current`       | Aktuelle laufende Sitzung (oder `null`)        |
| GET     | `/strategies/effective`   | Effektiver Laufzeitzustand aller Coin-Strategien |

### `GET /strategies/effective`

Gibt für jede konfigurierte Coin-Strategie den aktuellen Laufzeitzustand zurück.

**Beispiel-Response:**
```json
{
  "strategies": [
    {
      "product_id": "BTC-USD",
      "enabled": true,
      "base_value": 25.0,
      "step": 0.5,
      "cooldown_seconds": 60,
      "max_trades_per_hour": 6,
      "last_action": "BUY",
      "cooldown_remaining": 43.2,
      "trades_last_hour": 2,
      "next_sell_trigger": 25.5,
      "next_buy_trigger": 24.5
    }
  ]
}
```

| Feld                  | Bedeutung                                             |
|-----------------------|-------------------------------------------------------|
| `product_id`          | Handelspaar                                           |
| `enabled`             | Strategie aktiv?                                      |
| `base_value`          | Konfigurierter Referenzwert (USD)                     |
| `step`                | Konfigurierte Schrittgröße (USD)                      |
| `cooldown_seconds`    | Konfigurierter Cooldown                               |
| `max_trades_per_hour` | Konfiguriertes Stundenlimit                           |
| `last_action`         | Letzter Trade: `"BUY"`, `"SELL"` oder `null`         |
| `cooldown_remaining`  | Verbleibende Cooldown-Zeit in Sekunden (0 = frei)    |
| `trades_last_hour`    | Anzahl Trades der letzten 60 Minuten                 |
| `next_sell_trigger`   | Wert (USD) ab dem ein SELL ausgelöst wird            |
| `next_buy_trigger`    | Wert (USD) ab dem ein BUY ausgelöst wird             |

### Home Assistant Sensor Beispiel

```yaml
sensor:
  - platform: rest
    name: "KryptoBot Status"
    resource: "http://<raspberry-pi-ip>:8080/status"
    value_template: "{{ value_json.engine_active }}"
    json_attributes:
      - portfolio
```

---

## Projektstruktur

```
KryptobotNew/
├── install.sh              # Desktop-Installer / Updater für Raspberry Pi
├── kryptobot.desktop       # Desktop-Verknüpfung
├── requirements.txt        # Python-Abhängigkeiten
├── config.example.json     # Beispiel-Konfiguration
├── src/
│   ├── main.py             # Einstiegspunkt
│   ├── config_manager.py   # Konfigurations-Verwaltung (~/.kryptobot/config.json)
│   ├── coinbase_client.py  # Coinbase Advanced Trade API Client
│   ├── trading_engine.py   # Kursüberwachung & Auto-Handel
│   ├── session_manager.py  # Sitzungsverfolgung & -persistenz (~/.kryptobot/sessions.json)
│   ├── api_server.py       # REST API Server (Flask)
│   └── gui/
│       ├── main_window.py  # Hauptfenster (tkinter)
│       └── wizard.py       # Einrichtungsassistent
└── tests/
    └── test_core.py        # Unit-Tests
```

---

## Sicherheitshinweise

- API-Schlüssel werden **nur lokal** auf dem Raspberry Pi in `~/.kryptobot/config.json` gespeichert.
- Für Tests immer zuerst `use_sandbox: true` verwenden.
- Den REST-API Port **nicht** ohne Authentifizierung aus dem Internet erreichbar machen.
- Halte `live_trading_armed: false`, bis du sicher bist, dass der Bot korrekt funktioniert.
- Verwende niedrige `order_size_percent`- und `max_daily_loss_percent`-Werte für erste Live-Tests.
