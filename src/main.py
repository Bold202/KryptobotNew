#!/usr/bin/env python3
"""
KryptoBot – Coinbase Crypto Assistant
Entry point.

Run:
    python3 src/main.py

On first start the setup wizard is shown automatically.
It can be suppressed with --no-wizard.
"""

import argparse
import logging
import os
import sys
import tkinter as tk
from tkinter import messagebox

# Ensure project src directory is in the path when launched directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_manager import ConfigManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s – %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("kryptobot")


def parse_args():
    parser = argparse.ArgumentParser(description="KryptoBot – Coinbase Crypto Assistant")
    parser.add_argument("--no-wizard", action="store_true", help="Skip the setup wizard")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # ------------------------------------------------------------------ config
    config = ConfigManager()

    # ------------------------------------------------------------------ wizard
    root = tk.Tk()
    root.withdraw()  # hide until wizard / main window is ready

    wizard_needed = not config.get("wizard_completed", False) and not args.no_wizard
    if wizard_needed:
        from gui.wizard import SetupWizard

        wizard = SetupWizard(root, config)
        root.wait_window(wizard.window)

        if not config.get("wizard_completed", False):
            # User closed wizard without finishing → ask whether to continue anyway
            answer = messagebox.askyesno(
                "Einrichtung unvollständig",
                "Der Einrichtungsassistent wurde nicht abgeschlossen.\n\n"
                "Möchten Sie KryptoBot trotzdem starten?\n"
                "(Sie können den Assistenten später über das Menü aufrufen.)",
            )
            if not answer:
                root.destroy()
                return

    # ------------------------------------------------------------------ coinbase client
    coinbase_cfg = config.get_section("coinbase")
    client = None
    engine = None

    api_key = coinbase_cfg.get("api_key", "").strip()
    api_secret = coinbase_cfg.get("api_secret", "").strip()

    if api_key and api_secret:
        try:
            from coinbase_client import CoinbaseClient
            client = CoinbaseClient(
                api_key=api_key,
                api_secret=api_secret,
                use_sandbox=coinbase_cfg.get("use_sandbox", False),
            )
            logger.info("Coinbase client initialised (sandbox=%s)", coinbase_cfg.get("use_sandbox", False))
        except Exception as exc:
            logger.warning("Could not initialise Coinbase client: %s", exc)
    else:
        logger.info("No API credentials configured – running in demo mode.")

    # ------------------------------------------------------------------ trading engine
    from gui.main_window import MainWindow

    # We need the window reference before building the engine so we can pass
    # the event callback.  Build a stub callback first, then replace it.
    def _placeholder(*_):
        pass

    if client:
        from trading_engine import TradingEngine
        from session_manager import SessionManager
        session_mgr = SessionManager()
        engine = TradingEngine(
            client=client,
            config=config.get_section("trading"),
            on_event=_placeholder,
            session_manager=session_mgr,
        )
    else:
        session_mgr = None

    # ------------------------------------------------------------------ main window
    root.deiconify()
    app = MainWindow(root, config, engine=engine, client=client,
                     session_manager=session_mgr if client else None)

    # Wire the engine callback to the GUI now that the window exists
    if engine:
        def _event_cb(event_type, data):
            import api_server
            api_server.add_event(event_type, data)
            app.on_engine_event(event_type, data)

        engine._on_event = _event_cb

    # ------------------------------------------------------------------ REST API
    api_cfg = config.get_section("api")
    if api_cfg.get("enabled", False):
        try:
            import api_server
            api_server.init(engine, config, session_manager=session_mgr if client else None)
            api_server.start_server(
                host=api_cfg.get("host", "0.0.0.0"),
                port=int(api_cfg.get("port", 8080)),
            )
            logger.info("REST API started on port %s", api_cfg.get("port", 8080))
        except Exception as exc:
            logger.warning("Could not start REST API: %s", exc)

    # ------------------------------------------------------------------ run
    root.mainloop()


if __name__ == "__main__":
    main()
