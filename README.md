# GitHub Activity Dashboard

A self-hosted PWA that monitors GitHub PR/issue activity across multiple repos, sends desktop notifications, and provides an integrated Claude Code terminal for interactive AI-assisted code review.

## Features

- **PR & Issue Tracking** — All open PRs and issues across watched repos, sorted by recent activity
- **Activity Feed** — Comments, reviews, commits shown under each item with correct timestamps
- **Desktop Notifications** — Ubuntu `notify-send` alerts for new PRs, issues, and comments
- **Browser Notifications** — PWA push notifications with click-to-open
- **CI & Review Status** — Green/red/yellow CI badges, review decision badges, draft indicators
- **Inline Diff Viewer** — View PR diffs without leaving the dashboard
- **Detail Viewer** — Expand any item to see the full description, comments, and reviews rendered as markdown
- **Mute/Archive** — Hide noisy items, toggle visibility
- **Filters** — Filter by repo, review status (needs review, approved, changes requested, draft)
- **Claude Code Terminal** — Launch an interactive Claude Code session scoped to any PR or issue
- **Session History** — View and resume past Claude Code sessions per repo/PR

## Setup

### Prerequisites

- Python 3.12+
- [GitHub CLI](https://cli.github.com/) (`gh`) authenticated with `notifications` scope
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude`) installed
- Ubuntu (for `notify-send` desktop notifications)

### Install

```bash
# Clone or copy this directory
cd ~/notifications

# Create pyenv virtualenv
pyenv virtualenv 3.12.6 notifications
pyenv local notifications

# Install dependencies
pip install flask flask-socketio eventlet

# Configure repos to watch
cp repos.conf.example repos.conf
# Edit repos.conf — add your repos, one per line

# Ensure GitHub notifications scope
gh auth refresh -h github.com -s notifications

# Subscribe to repo activity (run once per repo)
for repo in $(grep -v '^#' repos.conf | grep -v '^\s*$'); do
  gh api -X PUT "/repos/$repo/subscription" --input - <<< '{"subscribed":true,"ignored":false}'
done
```

### Run

```bash
# Start the web app
python app.py
# Open http://127.0.0.1:5050

# Set up the cron job for polling (every 10 minutes)
crontab -e
# Add:
# */10 * * * * DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus /path/to/pyenv/python /path/to/check_notifications.py >> /path/to/cron.log 2>&1
```

### Install as PWA

Open `http://127.0.0.1:5050` in Chrome/Edge and click the install icon in the address bar.

## Architecture

```
check_notifications.py (cron, every 10min)
  ├─ gh pr/issue list     → open_items.json (all open PRs/issues)
  ├─ /repos/{repo}/events → events.jsonl (activity log)
  └─ notify-send          → desktop notifications

app.py (Flask + SocketIO, port 5050)
  ├─ GET /                → dashboard (index.html)
  ├─ GET /api/events      → polling endpoint
  ├─ GET /api/detail      → PR/issue body + comments
  ├─ GET /api/diff        → PR diff
  ├─ GET /api/sessions    → Claude Code session history
  ├─ POST /api/mute       → mute an item
  ├─ GET /terminal        → Claude Code terminal page
  └─ WebSocket            → PTY I/O for Claude Code
```

## Files

| File | Purpose |
|------|---------|
| `app.py` | Flask web app with SocketIO |
| `check_notifications.py` | Cron script — polls GitHub, sends notifications |
| `repo_manager.py` | Git clone/fetch/worktree management |
| `session_reader.py` | Reads Claude Code session history |
| `pty_manager.py` | PTY lifecycle for Claude Code terminals |
| `repos.conf` | List of repos to watch |
| `templates/index.html` | Dashboard UI |
| `templates/terminal.html` | Claude Code terminal page |
| `static/` | PWA manifest, service worker, icons |
