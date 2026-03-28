"""Read Claude Code's native session history for display in the web app."""

import json
from pathlib import Path

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def encode_cwd(path):
    """Encode a CWD path to Claude's project directory name format.

    Claude replaces both / and _ with - in project directory names.
    """
    return str(path).replace("/", "-").replace("_", "-")


def get_sessions(cwd):
    """
    List past Claude Code sessions for a given working directory.

    Returns list of dicts sorted by most recent first:
        {session_id, slug, first_prompt, started_at, last_activity, git_branch}
    """
    encoded = encode_cwd(cwd)
    project_dir = CLAUDE_PROJECTS_DIR / encoded

    if not project_dir.exists():
        return []

    sessions = []
    for jsonl_file in project_dir.glob("*.jsonl"):
        session_id = jsonl_file.stem
        info = _parse_session_file(jsonl_file)
        if info:
            info["session_id"] = session_id
            sessions.append(info)

    sessions.sort(key=lambda s: s.get("started_at", ""), reverse=True)
    return sessions


def _parse_session_file(path):
    """Extract metadata from a session JSONL file efficiently."""
    first_prompt = ""
    started_at = ""
    git_branch = ""
    slug = ""
    last_activity = ""

    try:
        with open(path, "r") as f:
            # Read first ~30 lines to find the first user message and slug
            for i, line in enumerate(f):
                if i > 30:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Grab slug from any message that has it
                if not slug and msg.get("slug"):
                    slug = msg["slug"]

                if msg.get("type") == "user" and not first_prompt:
                    started_at = msg.get("timestamp", "")
                    git_branch = msg.get("gitBranch", "")
                    content = msg.get("message", {}).get("content", "")
                    if isinstance(content, str):
                        first_prompt = content[:150]
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                first_prompt = block.get("text", "")[:150]
                                break

                if first_prompt and slug:
                    break

            # Read last line for last_activity timestamp
            f.seek(0, 2)  # seek to end
            file_size = f.tell()
            if file_size > 0:
                # Read last ~2KB to find the last complete line
                read_size = min(2048, file_size)
                f.seek(file_size - read_size)
                chunk = f.read()
                lines = chunk.strip().split("\n")
                for last_line in reversed(lines):
                    last_line = last_line.strip()
                    if not last_line:
                        continue
                    try:
                        last_msg = json.loads(last_line)
                        last_activity = last_msg.get("timestamp", "")
                        if last_activity:
                            break
                    except json.JSONDecodeError:
                        continue

    except (OSError, IOError):
        return None

    if not first_prompt:
        return None

    return {
        "slug": slug,
        "first_prompt": first_prompt,
        "started_at": started_at,
        "last_activity": last_activity or started_at,
        "git_branch": git_branch,
    }
