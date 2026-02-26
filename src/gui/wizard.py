#!/usr/bin/env python3
"""
KryptoBot - Coinbase Crypto Assistant
First-Run Setup Wizard (tkinter).
"""

import tkinter as tk
from tkinter import ttk, messagebox


class SetupWizard:
    """
    A simple step-by-step wizard that collects the Coinbase API credentials
    and basic trading preferences on first run.

    Usage::

        wizard = SetupWizard(root, config)
        root.wait_window(wizard.window)
        if config.get('wizard_completed'):
            # user finished the wizard
    """

    STEPS = ["Willkommen", "Coinbase API", "Handels-Einstellungen", "API-Server", "Fertig"]

    def __init__(self, parent: tk.Tk, config):
        self._config = config
        self._step = 0

        self.window = tk.Toplevel(parent)
        self.window.title("KryptoBot – Einrichtungsassistent")
        self.window.geometry("520x420")
        self.window.resizable(False, False)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

        # Center window
        self.window.update_idletasks()
        x = (self.window.winfo_screenwidth() - 520) // 2
        y = (self.window.winfo_screenheight() - 420) // 2
        self.window.geometry(f"+{x}+{y}")

        # ---- header ----
        header_frame = tk.Frame(self.window, bg="#1a1a2e", height=70)
        header_frame.pack(fill=tk.X)
        header_frame.pack_propagate(False)
        tk.Label(
            header_frame,
            text="🤖 KryptoBot Setup",
            font=("Helvetica", 16, "bold"),
            fg="white",
            bg="#1a1a2e",
        ).pack(expand=True)

        # ---- step indicator ----
        self._step_var = tk.StringVar()
        tk.Label(
            self.window,
            textvariable=self._step_var,
            font=("Helvetica", 9),
            fg="#888",
        ).pack(pady=(4, 0))

        # ---- content area ----
        self._content = tk.Frame(self.window, padx=20, pady=10)
        self._content.pack(fill=tk.BOTH, expand=True)

        # ---- navigation buttons ----
        nav = tk.Frame(self.window, pady=8)
        nav.pack(fill=tk.X, padx=20)
        self._btn_back = ttk.Button(nav, text="◀ Zurück", command=self._prev)
        self._btn_back.pack(side=tk.LEFT)
        self._btn_next = ttk.Button(nav, text="Weiter ▶", command=self._next)
        self._btn_next.pack(side=tk.RIGHT)
        self._btn_skip = ttk.Button(nav, text="Überspringen", command=self._skip)
        self._btn_skip.pack(side=tk.RIGHT, padx=(0, 8))

        self._render_step()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _prev(self):
        if self._step > 0:
            self._step -= 1
            self._render_step()

    def _next(self):
        if not self._validate():
            return
        self._save_step()
        if self._step < len(self.STEPS) - 1:
            self._step += 1
            self._render_step()

    def _skip(self):
        """Close without completing the wizard."""
        self.window.destroy()

    def _on_close(self):
        self.window.destroy()

    # ------------------------------------------------------------------
    # Step rendering
    # ------------------------------------------------------------------

    def _clear_content(self):
        for w in self._content.winfo_children():
            w.destroy()

    def _render_step(self):
        self._clear_content()
        total = len(self.STEPS)
        self._step_var.set(f"Schritt {self._step + 1} von {total} – {self.STEPS[self._step]}")
        self._btn_back.config(state=tk.NORMAL if self._step > 0 else tk.DISABLED)
        last = self._step == total - 1
        self._btn_next.config(text="Fertig ✔" if last else "Weiter ▶")
        self._btn_skip.config(state=tk.NORMAL if not last else tk.DISABLED)

        method = getattr(self, f"_step_{self._step}")
        method()

    def _label(self, text, bold=False):
        font = ("Helvetica", 10, "bold") if bold else ("Helvetica", 10)
        tk.Label(self._content, text=text, font=font, justify=tk.LEFT, anchor="w").pack(
            fill=tk.X, pady=(4, 0)
        )

    def _entry(self, var, show=None):
        e = ttk.Entry(self._content, textvariable=var, show=show, width=50)
        e.pack(fill=tk.X, pady=(2, 6))
        return e

    # ------------------------------------------------------------------
    # Individual steps
    # ------------------------------------------------------------------

    def _step_0(self):
        """Welcome."""
        self._label("Willkommen beim KryptoBot-Einrichtungsassistenten!", bold=True)
        self._label("")
        self._label(
            "Dieser Assistent hilft Ihnen, den Bot für Ihr Coinbase-Konto\n"
            "zu konfigurieren. Sie benötigen:\n\n"
            "  • Einen Coinbase Advanced Trade API-Schlüssel\n"
            "  • Den zugehörigen API-Secret\n\n"
            "API-Schlüssel erstellen:\n"
            "  coinbase.com → Einstellungen → API",
            bold=False,
        )

    def _step_1(self):
        """Coinbase API credentials."""
        coinbase_cfg = self._config.get_section("coinbase")

        self._label("Coinbase API-Schlüssel", bold=True)
        self._label("API Key:")
        self._api_key_var = tk.StringVar(value=coinbase_cfg.get("api_key", ""))
        self._entry(self._api_key_var)

        self._label("API Secret:")
        self._api_secret_var = tk.StringVar(value=coinbase_cfg.get("api_secret", ""))
        self._entry(self._api_secret_var, show="•")

        self._sandbox_var = tk.BooleanVar(value=coinbase_cfg.get("use_sandbox", False))
        ttk.Checkbutton(
            self._content,
            text="Sandbox-Modus (nur für Tests, kein echter Handel)",
            variable=self._sandbox_var,
        ).pack(anchor="w", pady=4)

    def _step_2(self):
        """Trading settings."""
        trading_cfg = self._config.get_section("trading")

        self._label("Handels-Schwellenwert (%)", bold=True)
        self._label("Handel wird ausgelöst, wenn der Kurs um diesen Prozentsatz\nvom Referenzpreis abweicht:")
        self._threshold_var = tk.StringVar(
            value=str(trading_cfg.get("threshold_percent", 2.0))
        )
        self._entry(self._threshold_var)

        self._label("Prüfintervall (Sekunden):")
        self._interval_var = tk.StringVar(
            value=str(trading_cfg.get("check_interval_seconds", 60))
        )
        self._entry(self._interval_var)

        self._label("Handelspaare (kommagetrennt, z.B. BTC-USD,ETH-USD):")
        self._pairs_var = tk.StringVar(
            value=",".join(trading_cfg.get("pairs", []))
        )
        self._entry(self._pairs_var)

    def _step_3(self):
        """API server settings."""
        api_cfg = self._config.get_section("api")

        self._label("REST API (für Home Assistant etc.)", bold=True)
        self._api_enabled_var = tk.BooleanVar(value=api_cfg.get("enabled", True))
        ttk.Checkbutton(
            self._content,
            text="REST API aktivieren",
            variable=self._api_enabled_var,
        ).pack(anchor="w", pady=4)

        self._label("API Port:")
        self._api_port_var = tk.StringVar(value=str(api_cfg.get("port", 8080)))
        self._entry(self._api_port_var)

        self._label("API Host (0.0.0.0 = von allen Geräten erreichbar):")
        self._api_host_var = tk.StringVar(value=api_cfg.get("host", "0.0.0.0"))
        self._entry(self._api_host_var)

    def _step_4(self):
        """Done."""
        self._label("Einrichtung abgeschlossen! ✔", bold=True)
        self._label("")
        self._label(
            "Die Konfiguration wurde gespeichert.\n\n"
            "Der Assistent kann jederzeit über das Menü\n"
            "neu gestartet werden.\n\n"
            "Klicken Sie auf 'Fertig', um KryptoBot zu starten."
        )

    # ------------------------------------------------------------------
    # Validation & saving
    # ------------------------------------------------------------------

    def _validate(self) -> bool:
        if self._step == 1:
            key = getattr(self, "_api_key_var", None)
            secret = getattr(self, "_api_secret_var", None)
            key_val = key.get().strip() if key else ""
            secret_val = secret.get().strip() if secret else ""
            if not key_val and not secret_val:
                # Allow proceeding without credentials – runs in demo mode
                answer = messagebox.askyesno(
                    "Kein API-Schlüssel",
                    "Es wurden keine API-Zugangsdaten eingegeben.\n\n"
                    "KryptoBot läuft dann im Demo-Modus:\n"
                    "  • Keine Verbindung zu Coinbase\n"
                    "  • Kein automatischer oder manueller Handel\n"
                    "  • Portfolio-Anzeige nicht verfügbar\n\n"
                    "Sie können die Zugangsdaten später in den Einstellungen hinterlegen.\n\n"
                    "Trotzdem fortfahren?",
                    parent=self.window,
                )
                return answer
            if key and not key_val:
                messagebox.showwarning(
                    "Fehlende Eingabe",
                    "Bitte geben Sie Ihren Coinbase API-Schlüssel ein.",
                    parent=self.window,
                )
                return False
            if secret and not secret_val:
                messagebox.showwarning(
                    "Fehlende Eingabe",
                    "Bitte geben Sie Ihren Coinbase API-Secret ein.",
                    parent=self.window,
                )
                return False
        if self._step == 2:
            try:
                float(getattr(self, "_threshold_var", tk.StringVar()).get())
            except ValueError:
                messagebox.showerror("Ungültige Eingabe", "Schwellenwert muss eine Zahl sein.", parent=self.window)
                return False
            try:
                int(getattr(self, "_interval_var", tk.StringVar()).get())
            except ValueError:
                messagebox.showerror("Ungültige Eingabe", "Intervall muss eine ganze Zahl sein.", parent=self.window)
                return False
        if self._step == 3:
            try:
                int(getattr(self, "_api_port_var", tk.StringVar()).get())
            except ValueError:
                messagebox.showerror("Ungültige Eingabe", "API Port muss eine Zahl sein.", parent=self.window)
                return False
        return True

    def _save_step(self):
        if self._step == 1:
            self._config.update_section(
                "coinbase",
                {
                    "api_key": self._api_key_var.get().strip(),
                    "api_secret": self._api_secret_var.get().strip(),
                    "use_sandbox": self._sandbox_var.get(),
                },
            )
        elif self._step == 2:
            pairs_raw = self._pairs_var.get().strip()
            pairs = [p.strip().upper() for p in pairs_raw.split(",") if p.strip()]
            self._config.update_section(
                "trading",
                {
                    "threshold_percent": float(self._threshold_var.get()),
                    "check_interval_seconds": int(self._interval_var.get()),
                    "pairs": pairs,
                },
            )
        elif self._step == 3:
            self._config.update_section(
                "api",
                {
                    "enabled": self._api_enabled_var.get(),
                    "port": int(self._api_port_var.get()),
                    "host": self._api_host_var.get().strip(),
                },
            )
        elif self._step == 4:
            self._config.set("wizard_completed", True)
            self.window.destroy()
