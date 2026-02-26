#!/usr/bin/env bash
# =============================================================================
#  KryptoBot – Installer / Updater for Raspberry Pi
#  Place this file on your Desktop. Double-click (or run in a terminal) to
#  install or update KryptoBot and then start it.
# =============================================================================

set -euo pipefail

REPO_URL="https://github.com/Bold202/KryptobotNew.git"
INSTALL_DIR="$HOME/KryptobotNew"
BRANCH="main"

echo "=============================================="
echo "  KryptoBot – Installer / Updater"
echo "=============================================="
echo ""

# --------------------------------------------------------------------------
# Helper: check + install a package
# --------------------------------------------------------------------------
require_apt() {
    local pkg="$1"
    if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
        echo "▶ Installiere $pkg …"
        sudo apt-get install -y "$pkg"
    fi
}

# --------------------------------------------------------------------------
# System dependencies
# --------------------------------------------------------------------------
echo "▶ Systemabhängigkeiten prüfen …"
sudo apt-get update -qq

require_apt git
require_apt python3
require_apt python3-pip
require_apt python3-tk   # tkinter for the GUI
require_apt python3-venv

# --------------------------------------------------------------------------
# Clone or update the repository
# --------------------------------------------------------------------------
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "▶ Aktualisiere bestehendes Repository …"
    cd "$INSTALL_DIR"
    git fetch origin
    git reset --hard "origin/$BRANCH"
else
    echo "▶ Klone Repository nach $INSTALL_DIR …"
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# --------------------------------------------------------------------------
# Python virtual environment + dependencies
# --------------------------------------------------------------------------
echo "▶ Richte Python-Umgebung ein …"
VENV_DIR="$INSTALL_DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet -r "$INSTALL_DIR/requirements.txt"

# --------------------------------------------------------------------------
# Create config if it doesn't exist yet
# --------------------------------------------------------------------------
CONFIG_DIR="$HOME/.kryptobot"
CONFIG_FILE="$CONFIG_DIR/config.json"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "▶ Erstelle Standard-Konfiguration in $CONFIG_FILE …"
    mkdir -p "$CONFIG_DIR"
    cp "$INSTALL_DIR/config.example.json" "$CONFIG_FILE"
fi

# --------------------------------------------------------------------------
# Create a .desktop launcher on the Desktop (if not already there)
# --------------------------------------------------------------------------
DESKTOP_LAUNCHER="$HOME/Desktop/kryptobot.desktop"
if [ ! -f "$DESKTOP_LAUNCHER" ]; then
    echo "▶ Erstelle Desktop-Verknüpfung …"
    cat > "$DESKTOP_LAUNCHER" <<DESKTOPEOF
[Desktop Entry]
Version=1.0
Type=Application
Name=KryptoBot
Comment=Coinbase Crypto Assistant
Exec=bash -c "source $VENV_DIR/bin/activate && python3 $INSTALL_DIR/src/main.py"
Icon=$INSTALL_DIR/assets/icon.png
Terminal=false
Categories=Finance;
DESKTOPEOF
    chmod +x "$DESKTOP_LAUNCHER"
    # Mark as trusted on Raspberry Pi OS / LXDE
    gio set "$DESKTOP_LAUNCHER" metadata::trusted true 2>/dev/null || true
fi

# --------------------------------------------------------------------------
# Start the application
# --------------------------------------------------------------------------
echo ""
echo "=============================================="
echo "  Installation abgeschlossen. Starte KryptoBot …"
echo "=============================================="
echo ""

source "$VENV_DIR/bin/activate"
python3 "$INSTALL_DIR/src/main.py"
