#!/usr/bin/env bash
set -euo pipefail

APP_NAME="gh-codepilot"
INSTALL_DIR="$HOME/.gh-codepilot"
OS="$(uname -s)"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

info() { echo -e "${GREEN}[+]${NC} $1"; }

echo "This will remove gh-codepilot, its service, and cron job."
echo "Cloned repos in $INSTALL_DIR/repos/ will also be deleted."
read -p "Continue? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || exit 0

# Stop service
if [[ "$OS" == "Linux" ]]; then
    systemctl --user stop "$APP_NAME" 2>/dev/null || true
    systemctl --user disable "$APP_NAME" 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/$APP_NAME.service"
    systemctl --user daemon-reload 2>/dev/null || true
    info "Removed systemd service"
elif [[ "$OS" == "Darwin" ]]; then
    PLIST="$HOME/Library/LaunchAgents/com.gh-codepilot.plist"
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    info "Removed launchd service"
fi

# Remove cron job
if crontab -l 2>/dev/null | grep -qF "check_notifications.py"; then
    crontab -l 2>/dev/null | grep -vF "check_notifications.py" | crontab -
    info "Removed cron job"
fi

# Remove install directory
if [[ -d "$INSTALL_DIR" ]]; then
    rm -rf "$INSTALL_DIR"
    info "Removed $INSTALL_DIR"
fi

echo ""
info "gh-codepilot has been uninstalled."
