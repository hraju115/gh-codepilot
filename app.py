import eventlet
eventlet.monkey_patch()

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, emit

import re
import subprocess

import pty_manager
import repo_manager
import session_reader

REPOS_CONF = Path(__file__).parent / "repos.conf"


def _load_allowed_repos():
    if not REPOS_CONF.exists():
        return set()
    return {
        line.strip()
        for line in REPOS_CONF.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#") and "/" in line.strip()
    }


UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


def _validate_repo(repo):
    return repo in _load_allowed_repos()


def _validate_session_id(sid):
    return sid is None or bool(UUID_RE.match(str(sid)))

app = Flask(__name__)
socketio = SocketIO(app, async_mode="eventlet")

BASE_DIR = Path(__file__).parent
EVENTS_FILE = BASE_DIR / "events.jsonl"
ITEMS_FILE = BASE_DIR / "open_items.json"
MUTED_FILE = BASE_DIR / "muted.json"


# ── Existing dashboard routes (unchanged) ────────────────────────────────────

def load_open_items(category):
    if not ITEMS_FILE.exists():
        return {}
    data = json.loads(ITEMS_FILE.read_text())
    return data.get("prs" if category == "pr" else "issues", {})


def read_events(hours=168, since=None, category=None):
    if not EVENTS_FILE.exists():
        return []

    cutoff = since or (datetime.now(timezone.utc) - timedelta(hours=hours))

    events = []
    for line in EVENTS_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            ts = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
            if ts > cutoff:
                if category and event.get("category", "pr") != category:
                    continue
                events.append(event)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue

    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return events


def build_items_list(category):
    open_items = load_open_items(category)
    events = read_events(hours=168, category=category)

    events_by_key = {}
    for event in events:
        number = event.get("number") or event.get("pr_number", 0)
        key = f"{event['repo']}#{number}"
        events_by_key.setdefault(key, []).append(event)

    items = []
    for key, item in open_items.items():
        number = item["number"]
        activity = events_by_key.get(key, [])
        activity = [e for e in activity if e["event_type"] not in ("new_pr", "new_issue")]

        last_activity = activity[0]["timestamp"] if activity else item.get("created_at", "")

        items.append({
            "number": number,
            "title": item["title"],
            "url": item["url"],
            "repo": item["repo"],
            "author": item.get("author", ""),
            "created_at": item.get("created_at", ""),
            "last_activity": last_activity,
            "activity": activity[:15],
            "review_decision": item.get("review_decision", ""),
            "is_draft": item.get("is_draft", False),
            "ci_status": item.get("ci_status", ""),
        })

    items.sort(key=lambda i: i["last_activity"], reverse=True)
    return items


def get_repos():
    if not ITEMS_FILE.exists():
        return []
    data = json.loads(ITEMS_FILE.read_text())
    repos = set()
    for section in (data.get("prs", {}), data.get("issues", {})):
        for item in section.values():
            repos.add(item["repo"])
    return sorted(repos)


def load_muted():
    if MUTED_FILE.exists() and MUTED_FILE.stat().st_size > 0:
        return json.loads(MUTED_FILE.read_text())
    return []


def save_muted(muted):
    tmp = MUTED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(muted))
    tmp.replace(MUTED_FILE)


@app.route("/")
def index():
    tab = request.args.get("tab", "pr")
    repo_filter = request.args.get("repo", "")
    review_filter = request.args.get("review", "")
    show_muted = request.args.get("muted", "") == "1"

    pr_items = build_items_list("pr")
    issue_items = build_items_list("issue")
    repos = get_repos()
    muted = load_muted()

    # Filter muted
    if not show_muted:
        pr_items = [i for i in pr_items if f"{i['repo']}#{i['number']}" not in muted]
        issue_items = [i for i in issue_items if f"{i['repo']}#{i['number']}" not in muted]

    # Filter by repo
    if repo_filter:
        pr_items = [i for i in pr_items if i["repo"] == repo_filter]
        issue_items = [i for i in issue_items if i["repo"] == repo_filter]

    # Filter by review status (PRs only)
    if review_filter:
        if review_filter == "needs_review":
            pr_items = [i for i in pr_items if i.get("review_decision") in ("", "REVIEW_REQUIRED")]
        elif review_filter == "approved":
            pr_items = [i for i in pr_items if i.get("review_decision") == "APPROVED"]
        elif review_filter == "changes_requested":
            pr_items = [i for i in pr_items if i.get("review_decision") == "CHANGES_REQUESTED"]
        elif review_filter == "draft":
            pr_items = [i for i in pr_items if i.get("is_draft")]

    server_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return render_template(
        "index.html",
        active_tab=tab,
        pr_items=pr_items,
        issue_items=issue_items,
        pr_count=len(pr_items),
        issue_count=len(issue_items),
        repos=repos,
        active_repo=repo_filter,
        active_review=review_filter,
        show_muted=show_muted,
        muted=muted,
        server_time=server_time,
    )


@app.route("/api/events")
def api_events():
    since_str = request.args.get("since")
    category = request.args.get("category")
    if since_str:
        try:
            since = datetime.fromisoformat(since_str.replace("Z", "+00:00"))
        except ValueError:
            return jsonify(error="invalid since parameter"), 400
        events = read_events(since=since, category=category)
    else:
        events = read_events(24, category=category)

    server_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return jsonify(events=events, server_time=server_time)


# ── Mute/unmute ──────────────────────────────────────────────────────────────

@app.route("/api/mute", methods=["POST"])
def api_mute():
    data = request.get_json()
    key = data.get("key", "")
    muted = load_muted()
    if key and key not in muted:
        muted.append(key)
        save_muted(muted)
    return jsonify(ok=True)


@app.route("/api/unmute", methods=["POST"])
def api_unmute():
    data = request.get_json()
    key = data.get("key", "")
    muted = load_muted()
    if key in muted:
        muted.remove(key)
        save_muted(muted)
    return jsonify(ok=True)


# ── Diff viewer ──────────────────────────────────────────────────────────────

@app.route("/api/diff")
def api_diff():
    repo = request.args.get("repo", "")
    number = request.args.get("number", 0, type=int)
    if not repo or not number:
        return jsonify(error="missing repo or number"), 400
    result = subprocess.run(
        ["gh", "pr", "diff", str(number), "--repo", repo],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return jsonify(error="failed to fetch diff"), 500
    return jsonify(diff=result.stdout)


# ── Detail viewer ────────────────────────────────────────────────────────────

@app.route("/api/detail")
def api_detail():
    repo = request.args.get("repo", "")
    number = request.args.get("number", 0, type=int)
    category = request.args.get("type", "pr")
    if not repo or not number:
        return jsonify(error="missing repo or number"), 400

    if category == "pr":
        item = subprocess.run(
            ["gh", "pr", "view", str(number), "--repo", repo,
             "--json", "body,comments,reviews"],
            capture_output=True, text=True,
        )
    else:
        item = subprocess.run(
            ["gh", "issue", "view", str(number), "--repo", repo,
             "--json", "body,comments"],
            capture_output=True, text=True,
        )

    if item.returncode != 0:
        return jsonify(error="failed to fetch details"), 500

    try:
        data = json.loads(item.stdout)
    except json.JSONDecodeError:
        return jsonify(error="invalid response"), 500

    body = data.get("body", "") or ""
    comments = []
    for c in data.get("comments", []):
        comments.append({
            "author": c.get("author", {}).get("login", ""),
            "body": c.get("body", ""),
            "created_at": c.get("createdAt", ""),
        })
    reviews = []
    if category == "pr":
        for r in data.get("reviews", []):
            if r.get("body"):
                reviews.append({
                    "author": r.get("author", {}).get("login", ""),
                    "body": r.get("body", ""),
                    "state": r.get("state", ""),
                    "created_at": r.get("submittedAt", ""),
                })

    return jsonify(body=body, comments=comments, reviews=reviews)


# ── Terminal routes ──────────────────────────────────────────────────────────

@app.route("/terminal")
def terminal_page():
    repo = request.args.get("repo", "")
    number = request.args.get("number", 0, type=int)
    category = request.args.get("type", "pr")
    title = request.args.get("title", "")
    return render_template("terminal.html", repo=repo, number=number, category=category, title=title)


@app.route("/api/sessions")
def api_sessions():
    repo = request.args.get("repo", "")
    number = request.args.get("number", 0, type=int)
    category = request.args.get("type", "pr")

    cwd = repo_manager.get_worktree_path(repo, number, category)
    sessions = session_reader.get_sessions(str(cwd))
    return jsonify(sessions=sessions)


# ── SocketIO handlers ────────────────────────────────────────────────────────

@socketio.on("start_terminal")
def handle_start_terminal(data):
    sid = request.sid
    repo = data.get("repo", "")
    number = data.get("number", 0)
    category = data.get("type", "pr")
    resume_id = data.get("session_id")

    if not _validate_repo(repo):
        emit("terminal_error", {"message": f"Repo not in allowlist: {repo}"})
        return

    if not _validate_session_id(resume_id):
        emit("terminal_error", {"message": "Invalid session ID format"})
        return

    # Kill any existing PTY for this socket before spawning a new one
    pty_manager.kill_pty(sid)

    emit("terminal_status", {"message": "Cloning repository..." if not (repo_manager.REPOS_DIR / repo_manager._repo_dir_name(repo)).exists() else "Fetching latest changes..."})

    try:
        cwd = repo_manager.ensure_worktree(repo, number, category)
    except Exception as e:
        emit("terminal_error", {"message": f"Failed to prepare repo: {e}"})
        return

    emit("terminal_status", {"message": "Starting Claude Code..."})

    pty_manager.spawn_claude(sid, socketio, cwd, resume_session_id=resume_id)
    emit("terminal_ready", {})


@socketio.on("pty_input")
def handle_pty_input(data):
    pty_manager.write_to_pty(request.sid, data.get("data", ""))


@socketio.on("resize")
def handle_resize(data):
    pty_manager.resize_pty(request.sid, data.get("rows", 24), data.get("cols", 80))


@socketio.on("disconnect")
def handle_disconnect():
    pty_manager.kill_pty(request.sid)


if __name__ == "__main__":
    socketio.run(app, host="127.0.0.1", port=5050, debug=True)
