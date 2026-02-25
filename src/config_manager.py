#!/usr/bin/env python3
"""
KryptoBot - Coinbase Crypto Assistant
Config Manager: handles loading/saving the local configuration file.
"""

import json
import os
import shutil

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".kryptobot")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
EXAMPLE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.example.json")

DEFAULTS = {
    "coinbase": {
        "api_key": "",
        "api_secret": "",
        "use_sandbox": False,
    },
    "trading": {
        "enabled": False,
        "threshold_percent": 2.0,
        "check_interval_seconds": 60,
        "pairs": [],
    },
    "api": {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 8080,
    },
    "wizard_completed": False,
}


class ConfigManager:
    """Loads, saves and provides access to the local bot configuration."""

    def __init__(self):
        self._data = {}
        self._ensure_config()
        self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_config(self):
        """Create config directory and file from example if they don't exist."""
        os.makedirs(CONFIG_DIR, exist_ok=True)
        if not os.path.exists(CONFIG_FILE):
            if os.path.exists(EXAMPLE_FILE):
                shutil.copy(EXAMPLE_FILE, CONFIG_FILE)
            else:
                with open(CONFIG_FILE, "w") as f:
                    json.dump(DEFAULTS, f, indent=2)

    def _load(self):
        try:
            with open(CONFIG_FILE, "r") as f:
                self._data = json.load(f)
        except (json.JSONDecodeError, OSError):
            self._data = {}
        # Fill missing keys with defaults (shallow-merge top-level sections)
        for key, value in DEFAULTS.items():
            if key not in self._data:
                self._data[key] = value
            elif isinstance(value, dict):
                for sub_key, sub_val in value.items():
                    if sub_key not in self._data[key]:
                        self._data[key][sub_key] = sub_val

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self):
        """Persist the current in-memory config to disk."""
        with open(CONFIG_FILE, "w") as f:
            json.dump(self._data, f, indent=2)

    def get(self, key, default=None):
        """Get a top-level config value."""
        return self._data.get(key, default)

    def set(self, key, value):
        """Set a top-level config value and save."""
        self._data[key] = value
        self.save()

    def get_section(self, section: str) -> dict:
        """Return a whole config section (e.g. 'coinbase', 'trading')."""
        return self._data.get(section, {})

    def update_section(self, section: str, updates: dict):
        """Merge *updates* into *section* and save."""
        if section not in self._data:
            self._data[section] = {}
        self._data[section].update(updates)
        self.save()

    @property
    def config_path(self) -> str:
        return CONFIG_FILE
