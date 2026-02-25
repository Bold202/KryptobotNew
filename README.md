# KryptoBot – Coinbase Crypto Assistant

Ein automatisierter Krypto-Trading-Assistent für den Raspberry Pi, der über die **Coinbase Advanced Trade API** Kurse überwacht und bei eingestellter Differenz handelt.

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
| `api.enabled`                           | REST-API für externe Integrationen aktivieren      |
| `api.port`                              | REST-API Port (Standard: `8080`)                   |

---

## REST API (Home Assistant / externe Monitoring-Tools)

Wenn die API aktiviert ist, lauscht sie auf Port 8080 (konfigurierbar).

| Methode | Endpunkt            | Beschreibung                                   |
|---------|---------------------|------------------------------------------------|
| GET     | `/status`           | Bot-Status + Portfolio-Snapshot                |
| GET     | `/portfolio`        | Liste der gehaltenen Coins mit Kursen          |
| GET     | `/engine`           | Trading-Automatik aktiv/inaktiv                |
| POST    | `/engine/start`     | Trading-Automatik starten                      |
| POST    | `/engine/stop`      | Trading-Automatik stoppen                      |
| GET     | `/events?n=50`      | Letzte N Ereignisse (Log)                      |
| GET     | `/config`           | Aktuelle Konfiguration (API-Keys versteckt)    |
| POST    | `/config`           | Trading-Einstellungen aktualisieren            |
| POST    | `/trade/buy`        | Manuellen Markt-Kauf ausführen                 |
| POST    | `/trade/sell`       | Manuellen Markt-Verkauf ausführen              |

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
