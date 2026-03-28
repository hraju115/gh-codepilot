#!/usr/bin/env python3
"""
Tracks all open PRs/issues and their activity for watched GitHub repos.

Data files:
  - open_items.json: all currently open PRs and issues (refreshed every run)
  - events.jsonl: activity log from repo events API (correct timestamps)
  - .notif_state.json: tracking state (seen event IDs, last check time)
"""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPOS_FILE = SCRIPT_DIR / "repos.conf"
EVENTS_FILE = SCRIPT_DIR / "events.jsonl"
ITEMS_FILE = SCRIPT_DIR / "open_items.json"
STATE_FILE = SCRIPT_DIR / ".notif_state.json"


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists() and STATE_FILE.stat().st_size > 0:
        return json.loads(STATE_FILE.read_text())
    return {"seen_event_ids": {}}


def _atomic_write(path, content):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content)
    tmp.replace(path)


def save_state(state):
    _atomic_write(STATE_FILE, json.dumps(state, indent=2))


def load_repos():
    if not REPOS_FILE.exists():
        print(f"Error: {REPOS_FILE} not found. Copy repos.conf.example to repos.conf and add your repos.")
        return []
    repos = []
    for line in REPOS_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "/" in line:
            repos.append(line)
    return repos


def gh_api(endpoint):
    result = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def gh_cli(args):
    result = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def desktop_notify(title, body):
    subprocess.run(
        ["notify-send", "--app-name=GitHub", "--icon=dialog-information",
         "--urgency=normal", title, body],
        capture_output=True,
    )


def log_event(event_type, category, repo, number, title, author, url, details, timestamp):
    entry = json.dumps({
        "timestamp": timestamp, "event_type": event_type, "category": category,
        "repo": repo, "number": number, "title": title, "author": author,
        "url": url, "details": details,
    })
    with open(EVENTS_FILE, "a") as f:
        f.write(entry + "\n")


# ── Open Items Sync ──────────────────────────────────────────────────────────

def load_open_items():
    if ITEMS_FILE.exists() and ITEMS_FILE.stat().st_size > 0:
        return json.loads(ITEMS_FILE.read_text())
    return {"prs": {}, "issues": {}}


def save_open_items(items):
    _atomic_write(ITEMS_FILE, json.dumps(items, indent=2))


def _compute_ci_status(checks):
    """Summarize CI check results into a single status string."""
    if not checks:
        return ""
    statuses = set()
    for check in checks:
        conclusion = (check.get("conclusion") or "").upper()
        status = (check.get("status") or "").upper()
        if status != "COMPLETED":
            statuses.add("PENDING")
        elif conclusion == "SUCCESS" or conclusion == "NEUTRAL" or conclusion == "SKIPPED":
            statuses.add("SUCCESS")
        elif conclusion == "FAILURE" or conclusion == "TIMED_OUT":
            statuses.add("FAILURE")
        else:
            statuses.add("PENDING")
    if "FAILURE" in statuses:
        return "failure"
    if "PENDING" in statuses:
        return "pending"
    return "success"


def sync_open_items(repos):
    """Fetch all open PRs and issues. Notify on newly appeared ones."""
    prev = load_open_items()
    prev_pr_keys = set(prev.get("prs", {}).keys())
    prev_issue_keys = set(prev.get("issues", {}).keys())
    is_first_run = not ITEMS_FILE.exists() or ITEMS_FILE.stat().st_size == 0

    current = {"prs": {}, "issues": {}}
    fetched_any = False

    for repo in repos:
        prs = gh_cli(["pr", "list", "--repo", repo, "--state", "open",
                       "--json", "number,title,url,author,createdAt,reviewDecision,isDraft,statusCheckRollup",
                       "--limit", "100"])
        if prs is not None:
            fetched_any = True
            for pr in prs:
                key = f"{repo}#{pr['number']}"
                author = pr.get("author", {}).get("login", "")
                checks = pr.get("statusCheckRollup") or []
                ci_status = _compute_ci_status(checks)
                current["prs"][key] = {
                    "repo": repo, "number": pr["number"], "title": pr["title"],
                    "url": pr["url"], "author": author,
                    "created_at": pr.get("createdAt", ""),
                    "review_decision": pr.get("reviewDecision", ""),
                    "is_draft": pr.get("isDraft", False),
                    "ci_status": ci_status,
                }
                if key not in prev_pr_keys:
                    if not is_first_run:
                        desktop_notify(
                            f"New PR in {repo}",
                            f"#{pr['number']}: {pr['title']}\nby {author}\n{pr['url']}",
                        )
                    log_event("new_pr", "pr", repo, pr["number"], pr["title"],
                              author, pr["url"], "Pull request opened",
                              timestamp=pr.get("createdAt", ""))

        issues = gh_cli(["issue", "list", "--repo", repo, "--state", "open",
                          "--json", "number,title,url,author,createdAt", "--limit", "100"])
        if issues is not None:
            fetched_any = True
            for issue in issues:
                key = f"{repo}#{issue['number']}"
                author = issue.get("author", {}).get("login", "")
                current["issues"][key] = {
                    "repo": repo, "number": issue["number"], "title": issue["title"],
                    "url": issue["url"], "author": author,
                    "created_at": issue.get("createdAt", ""),
                }
                if key not in prev_issue_keys:
                    if not is_first_run:
                        desktop_notify(
                            f"New issue in {repo}",
                            f"#{issue['number']}: {issue['title']}\nby {author}\n{issue['url']}",
                        )
                    log_event("new_issue", "issue", repo, issue["number"], issue["title"],
                              author, issue["url"], "Issue opened",
                          timestamp=issue.get("createdAt", ""))

    if fetched_any:
        save_open_items(current)
    else:
        current = prev
    return current


# ── Repo Events API ──────────────────────────────────────────────────────────

def process_repo_events(repos, open_items, state):
    """Fetch repo events for all watched repos. Correct timestamps, all users."""
    seen = state.get("seen_event_ids", {})
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build lookup for open item titles
    all_items = {}
    for key, item in open_items.get("prs", {}).items():
        all_items[(item["repo"], item["number"])] = item
    for key, item in open_items.get("issues", {}).items():
        all_items[(item["repo"], item["number"])] = item

    for repo in repos:
        events = gh_api(f"/repos/{repo}/events?per_page=100")
        if not events:
            continue

        for event in events:
            event_id = str(event.get("id", ""))
            if event_id in seen:
                continue

            created_at = event.get("created_at", "")
            if created_at < cutoff:
                seen[event_id] = True
                continue

            actor = event.get("actor", {}).get("login", "")
            event_type_gh = event.get("type", "")
            payload = event.get("payload", {})

            # ── PR Comments ──
            if event_type_gh == "IssueCommentEvent":
                issue = payload.get("issue", {})
                number = issue.get("number", 0)
                is_pr = "pull_request" in issue
                category = "pr" if is_pr else "issue"
                evt_type = "new_comment" if is_pr else "issue_comment"
                title = issue.get("title", "")
                html_url = issue.get("html_url", "")
                body = payload.get("comment", {}).get("body", "")[:120].replace("\n", " ")
                details = f"{actor}: {body}" if body else f"{actor} commented"

            # ── PR Reviews ──
            elif event_type_gh == "PullRequestReviewEvent":
                pr = payload.get("pull_request", {})
                number = pr.get("number", 0)
                title = pr.get("title", "")
                category = "pr"
                evt_type = "new_comment"
                html_url = pr.get("html_url", "")
                review = payload.get("review", {})
                review_body = review.get("body", "")
                review_state = review.get("state", "")
                if review_body:
                    details = f"{actor}: {review_body[:120].replace(chr(10), ' ')}"
                else:
                    labels = {"approved": "Approved", "changes_requested": "Changes requested",
                              "commented": "Reviewed", "dismissed": "Review dismissed"}
                    details = f"{actor}: {labels.get(review_state, 'Reviewed')}"

            # ── PR Review Comments (inline code comments) ──
            elif event_type_gh == "PullRequestReviewCommentEvent":
                pr = payload.get("pull_request", {})
                number = pr.get("number", 0)
                title = pr.get("title", "")
                category = "pr"
                evt_type = "new_comment"
                html_url = pr.get("html_url", "")
                body = payload.get("comment", {}).get("body", "")[:120].replace("\n", " ")
                details = f"{actor}: {body}" if body else f"{actor} commented on code"

            # ── Issue Events (opened, closed, reopened, labeled, etc.) ──
            elif event_type_gh == "IssuesEvent":
                issue = payload.get("issue", {})
                number = issue.get("number", 0)
                title = issue.get("title", "")
                category = "issue"
                action = payload.get("action", "")
                if action in ("opened", "closed", "reopened"):
                    evt_type = "new_issue"
                    html_url = issue.get("html_url", "")
                    details = f"{actor}: Issue {action}"
                else:
                    seen[event_id] = True
                    continue

            # ── PR Events (opened, closed, merged, review_requested, etc.) ──
            elif event_type_gh == "PullRequestEvent":
                pr = payload.get("pull_request", {})
                number = pr.get("number", 0)
                title = pr.get("title", "")
                category = "pr"
                action = payload.get("action", "")
                html_url = pr.get("html_url", "")
                if action == "review_requested":
                    evt_type = "new_comment"
                    requested = payload.get("requested_reviewer", {}).get("login", "")
                    details = f"Review requested from {requested}" if requested else "Review requested"
                elif action in ("closed", "reopened"):
                    merged = pr.get("merged", False)
                    evt_type = "new_pr"
                    details = f"{actor}: PR {'merged' if merged else action}"
                else:
                    seen[event_id] = True
                    continue

            else:
                seen[event_id] = True
                continue

            # Look up title from open items if missing
            if not title:
                item = all_items.get((repo, number))
                if item:
                    title = item["title"]

            desktop_notify(
                f"Activity in {repo}",
                f"#{number}: {title}\n{details}",
            )
            log_event(evt_type, category, repo, number, title, actor,
                      html_url or f"https://github.com/{repo}", details,
                      timestamp=created_at)

            seen[event_id] = True

    # Prune seen IDs (keep last 3000)
    if len(seen) > 4000:
        items = list(seen.items())
        state["seen_event_ids"] = dict(items[-3000:])
    else:
        state["seen_event_ids"] = seen


# ── Events Pruning ───────────────────────────────────────────────────────────

def prune_events():
    if not EVENTS_FILE.exists():
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = EVENTS_FILE.read_text().splitlines()
    kept = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            ts = event.get("timestamp", "")
            if not ts or ts >= cutoff:
                kept.append(line)
        except (json.JSONDecodeError, KeyError):
            continue
    EVENTS_FILE.write_text("\n".join(kept) + "\n" if kept else "")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    repos = load_repos()
    if not repos:
        return

    state = load_state()

    # 1. Sync all open PRs/issues
    open_items = sync_open_items(repos)

    # 2. Fetch activity from repo events API (all users, correct timestamps)
    process_repo_events(repos, open_items, state)
    save_state(state)

    # 3. Prune old events
    prune_events()


if __name__ == "__main__":
    main()
