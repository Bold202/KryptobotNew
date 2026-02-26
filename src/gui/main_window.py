#!/usr/bin/env python3
"""
KryptoBot - Coinbase Crypto Assistant
Main Application Window (tkinter).
"""

import logging
import os
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

logger = logging.getLogger(__name__)

# Colour palette
BG_DARK = "#1a1a2e"
BG_MID = "#16213e"
BG_CARD = "#0f3460"
ACCENT = "#e94560"
TEXT_LIGHT = "#e0e0e0"
TEXT_DIM = "#888888"
GREEN = "#00c896"
RED = "#ff4757"


class MainWindow:
    """The primary application window."""

    def __init__(self, root: tk.Tk, config, engine=None, client=None):
        self._root = root
        self._config = config
        self._engine = engine
        self._client = client

        self._setup_window()
        self._build_ui()
        self._start_refresh()

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _setup_window(self):
        self._root.title("KryptoBot – Coinbase Assistent")
        self._root.geometry("900x620")
        self._root.minsize(700, 480)
        self._root.configure(bg=BG_DARK)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Center
        self._root.update_idletasks()
        x = (self._root.winfo_screenwidth() - 900) // 2
        y = (self._root.winfo_screenheight() - 620) // 2
        self._root.geometry(f"+{x}+{y}")

        # Style
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG_DARK)
        style.configure("Card.TFrame", background=BG_MID)
        style.configure("TLabel", background=BG_DARK, foreground=TEXT_LIGHT)
        style.configure("Card.TLabel", background=BG_MID, foreground=TEXT_LIGHT)
        style.configure("Dim.TLabel", background=BG_DARK, foreground=TEXT_DIM)
        style.configure(
            "Toggle.TButton",
            font=("Helvetica", 11, "bold"),
            padding=8,
        )
        style.configure(
            "Treeview",
            background=BG_MID,
            foreground=TEXT_LIGHT,
            fieldbackground=BG_MID,
            rowheight=26,
        )
        style.configure("Treeview.Heading", background=BG_CARD, foreground=TEXT_LIGHT)
        style.map("Treeview", background=[("selected", BG_CARD)])

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ---- Header ----
        header = tk.Frame(self._root, bg=BG_DARK, pady=8)
        header.pack(fill=tk.X, padx=16)

        tk.Label(
            header,
            text="🤖 KryptoBot",
            font=("Helvetica", 18, "bold"),
            fg=TEXT_LIGHT,
            bg=BG_DARK,
        ).pack(side=tk.LEFT)

        # Toggle button (right side of header)
        self._toggle_var = tk.StringVar(value="● AN")
        self._toggle_btn = tk.Button(
            header,
            textvariable=self._toggle_var,
            font=("Helvetica", 11, "bold"),
            width=10,
            bg=GREEN,
            fg="white",
            relief=tk.FLAT,
            cursor="hand2",
            command=self._on_toggle,
        )
        self._toggle_btn.pack(side=tk.RIGHT, padx=(0, 4))
        tk.Label(header, text="Automatik:", fg=TEXT_DIM, bg=BG_DARK).pack(
            side=tk.RIGHT, padx=(0, 6)
        )

        # Status label
        self._status_var = tk.StringVar(value="Nicht verbunden")
        tk.Label(
            header,
            textvariable=self._status_var,
            font=("Helvetica", 9),
            fg=TEXT_DIM,
            bg=BG_DARK,
        ).pack(side=tk.LEFT, padx=16)

        ttk.Separator(self._root, orient="horizontal").pack(fill=tk.X, padx=8)

        # ---- Main content area (left + right panels) ----
        content = tk.Frame(self._root, bg=BG_DARK)
        content.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)

        self._build_left_panel(content)
        self._build_right_panel(content)

        # ---- Bottom status bar ----
        self._build_status_bar()

    def _build_left_panel(self, parent):
        left = tk.Frame(parent, bg=BG_DARK, width=340)
        left.pack(side=tk.LEFT, fill=tk.BOTH, padx=(0, 8))
        left.pack_propagate(False)

        # --- Portfolio section ---
        tk.Label(
            left,
            text="Mein Portfolio",
            font=("Helvetica", 12, "bold"),
            fg=TEXT_LIGHT,
            bg=BG_DARK,
        ).pack(anchor="w")

        portfolio_frame = tk.Frame(left, bg=BG_MID, relief=tk.FLAT, bd=1)
        portfolio_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 8))

        cols = ("Coin", "Guthaben", "Kurs (USD)", "Wert (USD)")
        self._portfolio_tree = ttk.Treeview(
            portfolio_frame,
            columns=cols,
            show="headings",
            selectmode="browse",
        )
        for col in cols:
            self._portfolio_tree.heading(col, text=col)
            self._portfolio_tree.column(col, width=75, anchor="center")
        self._portfolio_tree.column("Coin", width=55)
        self._portfolio_tree.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        scroll_y = ttk.Scrollbar(portfolio_frame, orient="vertical", command=self._portfolio_tree.yview)
        self._portfolio_tree.configure(yscrollcommand=scroll_y.set)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Button(left, text="🔄 Portfolio aktualisieren", command=self._refresh_portfolio).pack(
            fill=tk.X, pady=(0, 4)
        )

        # --- Pair selector ---
        tk.Label(left, text="Handelspaar wählen:", fg=TEXT_LIGHT, bg=BG_DARK).pack(anchor="w")
        self._pair_var = tk.StringVar()
        self._pair_combo = ttk.Combobox(left, textvariable=self._pair_var, state="readonly", width=30)
        self._pair_combo.pack(fill=tk.X, pady=(2, 4))

    def _build_right_panel(self, parent):
        right = tk.Frame(parent, bg=BG_DARK)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # --- Trading controls ---
        tk.Label(
            right,
            text="Handelssteuerung",
            font=("Helvetica", 12, "bold"),
            fg=TEXT_LIGHT,
            bg=BG_DARK,
        ).pack(anchor="w")

        ctrl = tk.Frame(right, bg=BG_MID, relief=tk.FLAT, bd=1, padx=12, pady=10)
        ctrl.pack(fill=tk.X, pady=(4, 8))

        row1 = tk.Frame(ctrl, bg=BG_MID)
        row1.pack(fill=tk.X)
        tk.Label(row1, text="Schwellenwert (%):", fg=TEXT_LIGHT, bg=BG_MID, width=20, anchor="w").pack(
            side=tk.LEFT
        )
        self._threshold_var = tk.StringVar(
            value=str(self._config.get_section("trading").get("threshold_percent", 2.0))
        )
        ttk.Entry(row1, textvariable=self._threshold_var, width=10).pack(side=tk.LEFT, padx=4)

        row2 = tk.Frame(ctrl, bg=BG_MID)
        row2.pack(fill=tk.X, pady=(4, 0))
        tk.Label(row2, text="Intervall (Sek.):", fg=TEXT_LIGHT, bg=BG_MID, width=20, anchor="w").pack(
            side=tk.LEFT
        )
        self._interval_var = tk.StringVar(
            value=str(self._config.get_section("trading").get("check_interval_seconds", 60))
        )
        ttk.Entry(row2, textvariable=self._interval_var, width=10).pack(side=tk.LEFT, padx=4)

        ttk.Button(ctrl, text="Einstellungen speichern", command=self._save_settings).pack(
            anchor="w", pady=(8, 0)
        )

        # --- Manual trade ---
        tk.Label(
            right,
            text="Manueller Handel",
            font=("Helvetica", 12, "bold"),
            fg=TEXT_LIGHT,
            bg=BG_DARK,
        ).pack(anchor="w", pady=(4, 0))

        trade = tk.Frame(right, bg=BG_MID, relief=tk.FLAT, bd=1, padx=12, pady=10)
        trade.pack(fill=tk.X, pady=(4, 8))

        trow1 = tk.Frame(trade, bg=BG_MID)
        trow1.pack(fill=tk.X)
        tk.Label(trow1, text="Menge (Basis-Coin):", fg=TEXT_LIGHT, bg=BG_MID, width=20, anchor="w").pack(
            side=tk.LEFT
        )
        self._trade_size_var = tk.StringVar(value="0.001")
        ttk.Entry(trow1, textvariable=self._trade_size_var, width=14).pack(side=tk.LEFT, padx=4)

        trow2 = tk.Frame(trade, bg=BG_MID)
        trow2.pack(fill=tk.X, pady=(6, 0))
        tk.Button(
            trow2,
            text="  ▲ KAUFEN  ",
            font=("Helvetica", 10, "bold"),
            bg=GREEN,
            fg="white",
            relief=tk.FLAT,
            cursor="hand2",
            command=self._on_buy,
        ).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(
            trow2,
            text="  ▼ VERKAUFEN  ",
            font=("Helvetica", 10, "bold"),
            bg=RED,
            fg="white",
            relief=tk.FLAT,
            cursor="hand2",
            command=self._on_sell,
        ).pack(side=tk.LEFT)

        # --- Event log ---
        tk.Label(
            right,
            text="Ereignis-Log",
            font=("Helvetica", 12, "bold"),
            fg=TEXT_LIGHT,
            bg=BG_DARK,
        ).pack(anchor="w", pady=(4, 0))

        log_frame = tk.Frame(right, bg=BG_MID, relief=tk.FLAT, bd=1)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        self._log_text = tk.Text(
            log_frame,
            bg=BG_MID,
            fg=TEXT_LIGHT,
            font=("Courier", 9),
            state=tk.DISABLED,
            wrap=tk.WORD,
            relief=tk.FLAT,
        )
        self._log_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        log_scroll = ttk.Scrollbar(log_frame, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_status_bar(self):
        bar = tk.Frame(self._root, bg=BG_CARD, height=24)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)

        self._api_status_var = tk.StringVar(value="API: –")
        tk.Label(bar, textvariable=self._api_status_var, fg=TEXT_DIM, bg=BG_CARD, font=("Helvetica", 8)).pack(
            side=tk.LEFT, padx=8
        )
        cfg_path = self._config.config_path
        tk.Label(bar, text=f"Config: {cfg_path}", fg=TEXT_DIM, bg=BG_CARD, font=("Helvetica", 8)).pack(
            side=tk.RIGHT, padx=8
        )

    # ------------------------------------------------------------------
    # Engine toggle
    # ------------------------------------------------------------------

    def _on_toggle(self):
        if self._engine is None:
            self._log("⚠ Kein Trading-Engine – API-Schlüssel konfigurieren.")
            return
        if self._engine.is_active:
            self._engine.stop()
            self._update_toggle_state(False)
        else:
            self._engine.start()
            self._update_toggle_state(True)

    def _update_toggle_state(self, active: bool):
        if active:
            self._toggle_var.set("● AN")
            self._toggle_btn.config(bg=GREEN)
        else:
            self._toggle_var.set("○ AUS")
            self._toggle_btn.config(bg=RED)

    # ------------------------------------------------------------------
    # Portfolio refresh
    # ------------------------------------------------------------------

    def _refresh_portfolio(self):
        if self._client is None:
            self._log("⚠ Kein Coinbase-Client – bitte API-Schlüssel konfigurieren.")
            return
        self._status_var.set("Lade Portfolio …")

        def _fetch():
            try:
                coins = self._client.get_owned_coins_with_prices()
                self._root.after(0, lambda: self._update_portfolio_table(coins))
            except Exception as exc:
                self._root.after(0, lambda e=exc: self._log(f"❌ Portfolio-Fehler: {e}"))
            finally:
                self._root.after(0, lambda: self._status_var.set("Portfolio aktualisiert"))

        threading.Thread(target=_fetch, daemon=True).start()

    def _update_portfolio_table(self, coins: list):
        for row in self._portfolio_tree.get_children():
            self._portfolio_tree.delete(row)

        pair_choices = []
        for coin in coins:
            currency = coin["currency"]
            balance = f"{coin['balance']:.6f}"
            price = f"{coin['price_usd']:.4f}" if coin["price_usd"] else "–"
            value = f"{coin['value_usd']:.2f}"
            self._portfolio_tree.insert("", tk.END, values=(currency, balance, price, value))
            if coin.get("product_id"):
                pair_choices.append(coin["product_id"])

        self._pair_combo["values"] = pair_choices
        if pair_choices and not self._pair_var.get():
            self._pair_combo.set(pair_choices[0])

    # ------------------------------------------------------------------
    # Settings save
    # ------------------------------------------------------------------

    def _save_settings(self):
        try:
            threshold = float(self._threshold_var.get())
            interval = int(self._interval_var.get())
        except ValueError:
            messagebox.showerror("Ungültige Eingabe", "Bitte gültige Zahlen eingeben.")
            return
        updates = {
            "threshold_percent": threshold,
            "check_interval_seconds": interval,
        }
        self._config.update_section("trading", updates)
        if self._engine:
            self._engine.update_config(self._config.get_section("trading"))
        self._log("✔ Einstellungen gespeichert.")

    # ------------------------------------------------------------------
    # Manual trade
    # ------------------------------------------------------------------

    def _on_buy(self):
        self._place_order("BUY")

    def _on_sell(self):
        self._place_order("SELL")

    def _place_order(self, side: str):
        if self._engine is None:
            self._log("⚠ Kein Trading-Engine.")
            return
        pair = self._pair_var.get()
        if not pair:
            messagebox.showwarning("Kein Handelspaar", "Bitte ein Handelspaar auswählen.")
            return
        size = self._trade_size_var.get().strip()
        action = "kaufen" if side == "BUY" else "verkaufen"
        is_sandbox = getattr(self._engine, "use_sandbox", False)
        if is_sandbox:
            warning_text = "Dies ist ein SIMULIERTER Marktauftrag (Sandbox/Testmodus)."
        else:
            warning_text = "Dies ist ein echter Marktauftrag!"
        if not messagebox.askyesno(
            "Auftrag bestätigen",
            f"{size} {pair} {action}?\n\n{warning_text}",
        ):
            return

        def _do():
            try:
                if side == "BUY":
                    result = self._engine.manual_buy(pair, size)
                else:
                    result = self._engine.manual_sell(pair, size)
                order_id = result.get("order_id") or result.get("success_response", {}).get("order_id", "–")
                self._root.after(0, lambda: self._log(f"✔ Auftrag platziert: {side} {size} {pair} | ID: {order_id}"))
            except Exception as exc:
                self._root.after(0, lambda e=exc: self._log(f"❌ Auftrags-Fehler: {e}"))

        threading.Thread(target=_do, daemon=True).start()

    # ------------------------------------------------------------------
    # Event callback (called by trading engine from background thread)
    # ------------------------------------------------------------------

    def on_engine_event(self, event_type: str, data: dict):
        """Safely dispatch engine events to the GUI thread."""
        self._root.after(0, lambda: self._handle_event(event_type, data))

    def _handle_event(self, event_type: str, data: dict):
        if event_type == "portfolio_update":
            self._update_portfolio_table(data.get("coins", []))
        elif event_type == "engine_started":
            self._update_toggle_state(True)
            self._log("▶ Trading-Automatik gestartet.")
        elif event_type == "engine_stopped":
            self._update_toggle_state(False)
            self._log("■ Trading-Automatik gestoppt.")
        elif event_type == "threshold_reached":
            pid = data.get("product_id")
            pct = data.get("change_pct", 0)
            self._log(
                f"🔔 Schwellenwert erreicht: {pid}  {pct:+.2f}%  "
                f"(Ref: {data.get('ref_price'):.4f} → Jetzt: {data.get('current_price'):.4f})"
            )
        elif event_type == "order_placed":
            self._log(f"✅ Auftrag ausgeführt: {data.get('side')} {data.get('product_id')}")
        elif event_type == "error":
            self._log(f"❌ Fehler: {data.get('message')}")

    # ------------------------------------------------------------------
    # Log helper
    # ------------------------------------------------------------------

    def _log(self, message: str):
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"
        self._log_text.config(state=tk.NORMAL)
        self._log_text.insert(tk.END, line)
        self._log_text.see(tk.END)
        self._log_text.config(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Periodic refresh
    # ------------------------------------------------------------------

    def _start_refresh(self):
        api_cfg = self._config.get_section("api")
        if api_cfg.get("enabled", False):
            port = api_cfg.get("port", 8080)
            self._api_status_var.set(f"REST API: Port {port}")
        else:
            self._api_status_var.set("REST API: deaktiviert")

        if self._engine and self._engine.is_active:
            self._update_toggle_state(True)
        else:
            self._update_toggle_state(False)

        # Greet
        self._log("🚀 KryptoBot gestartet. Portfolio laden, um zu beginnen.")

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def _on_close(self):
        if self._engine and self._engine.is_active:
            if not messagebox.askyesno(
                "Beenden",
                "Die Trading-Automatik ist aktiv.\nWirklich beenden?",
            ):
                return
        if self._engine:
            self._engine.stop()
        self._root.destroy()
