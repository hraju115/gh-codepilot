#!/usr/bin/env bash
set -euo pipefail

# ── gh-codepilot installer ──────────────────────────────────────────────────
# Supports Linux (Ubuntu/Debian) and macOS.
# Installs to ~/.gh-codepilot, sets up a Python venv, cron job, and
# background service (systemd on Linux, launchd on macOS).

APP_NAME="gh-codepilot"
INSTALL_DIR="$HOME/.gh-codepilot"
REPO_URL="https://github.com/hraju115/gh-codepilot.git"
PORT=5050
POLL_INTERVAL=10  # minutes

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[x]${NC} $1"; exit 1; }

OS="$(uname -s)"
if [[ "$OS" != "Linux" && "$OS" != "Darwin" ]]; then
    error "Unsupported OS: $OS. Only Linux and macOS are supported."
fi

# ── Check prerequisites ─────────────────────────────────────────────────────

info "Checking prerequisites..."

command -v git >/dev/null || error "git is required. Install it first."
command -v gh >/dev/null || error "GitHub CLI (gh) is required. Install from https://cli.github.com/"
command -v claude >/dev/null || warn "Claude Code CLI (claude) not found. Terminal feature won't work until installed."

# Check gh auth
if ! gh auth status &>/dev/null; then
    error "GitHub CLI is not authenticated. Run: gh auth login"
fi

# Check Python 3.10+
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [[ "$major" -ge 3 && "$minor" -ge 10 ]]; then
            PYTHON="$cmd"
            break
        fi
    fi
done
[[ -n "$PYTHON" ]] || error "Python 3.10+ is required. Found none."
info "Using $PYTHON ($($PYTHON --version))"

# ── Install app ─────────────────────────────────────────────────────────────

if [[ -d "$INSTALL_DIR" ]]; then
    info "Updating existing installation..."
    git -C "$INSTALL_DIR" pull --ff-only || warn "Could not pull latest. Continuing with existing."
else
    info "Cloning $APP_NAME to $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

# ── Python venv ─────────────────────────────────────────────────────────────

VENV_DIR="$INSTALL_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating Python virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

info "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

VENV_PYTHON="$VENV_DIR/bin/python"

# ── Config ───────────────────────────────────────────────────────────────────

if [[ ! -f "$INSTALL_DIR/repos.conf" ]]; then
    cp "$INSTALL_DIR/repos.conf.example" "$INSTALL_DIR/repos.conf"
    warn "Created repos.conf from example. Edit $INSTALL_DIR/repos.conf to add your repos."
fi

# ── Cron job ─────────────────────────────────────────────────────────────────

CRON_CMD="$VENV_PYTHON $INSTALL_DIR/check_notifications.py >> $INSTALL_DIR/cron.log 2>&1"

if [[ "$OS" == "Linux" ]]; then
    CRON_CMD="DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus $CRON_CMD"
fi

CRON_LINE="*/$POLL_INTERVAL * * * * $CRON_CMD"

# Add cron job if not already present
if ! crontab -l 2>/dev/null | grep -qF "check_notifications.py"; then
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    info "Added cron job (every ${POLL_INTERVAL}min)"
else
    info "Cron job already exists, skipping"
fi

# ── Background service ───────────────────────────────────────────────────────

if [[ "$OS" == "Linux" ]]; then
    # systemd user service
    SERVICE_DIR="$HOME/.config/systemd/user"
    mkdir -p "$SERVICE_DIR"
    cat > "$SERVICE_DIR/$APP_NAME.service" <<EOF
[Unit]
Description=gh-codepilot — GitHub Activity Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_PYTHON $INSTALL_DIR/app.py
Restart=on-failure
RestartSec=5
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable "$APP_NAME"
    systemctl --user restart "$APP_NAME"
    info "Started systemd service ($APP_NAME)"

elif [[ "$OS" == "Darwin" ]]; then
    # launchd plist
    PLIST_DIR="$HOME/Library/LaunchAgents"
    PLIST_FILE="$PLIST_DIR/com.gh-codepilot.plist"
    mkdir -p "$PLIST_DIR"
    cat > "$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.gh-codepilot</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_PYTHON</string>
        <string>$INSTALL_DIR/app.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$INSTALL_DIR/app.log</string>
    <key>StandardErrorPath</key>
    <string>$INSTALL_DIR/app.log</string>
</dict>
</plist>
EOF
    launchctl unload "$PLIST_FILE" 2>/dev/null || true
    launchctl load "$PLIST_FILE"
    info "Started launchd service ($APP_NAME)"
fi

# ── Done ─────────────────────────────────────────────────────────────────────

echo ""
info "Installation complete!"
echo ""
echo "  Dashboard:  http://127.0.0.1:$PORT"
echo "  Config:     $INSTALL_DIR/repos.conf"
echo "  Logs:       $INSTALL_DIR/cron.log"
echo ""
if [[ "$OS" == "Linux" ]]; then
    echo "  Service:    systemctl --user status $APP_NAME"
    echo "  Stop:       systemctl --user stop $APP_NAME"
    echo "  Restart:    systemctl --user restart $APP_NAME"
elif [[ "$OS" == "Darwin" ]]; then
    echo "  Stop:       launchctl unload $PLIST_FILE"
    echo "  Restart:    launchctl unload $PLIST_FILE && launchctl load $PLIST_FILE"
fi
echo ""
warn "Edit $INSTALL_DIR/repos.conf to add your repos, then visit http://127.0.0.1:$PORT"
