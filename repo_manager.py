"""Git clone and worktree management for watched repos."""

import subprocess
from pathlib import Path

REPOS_DIR = Path(__file__).parent / "repos"


def _run(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def _repo_dir_name(repo):
    """Convert 'owner/repo' to 'owner--repo'."""
    return repo.replace("/", "--")


def ensure_repo(repo):
    """Clone repo if missing, fetch if exists. Returns clone path."""
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    repo_dir = REPOS_DIR / _repo_dir_name(repo)

    if not repo_dir.exists():
        _run(["git", "clone", f"git@github.com:{repo}.git", str(repo_dir)])
    else:
        _run(["git", "-C", str(repo_dir), "fetch", "--all", "--prune"], check=False)

    return repo_dir


def _get_pr_branch(repo, number):
    """Get the head branch name for a PR."""
    result = _run(
        ["gh", "pr", "view", str(number), "--repo", repo,
         "--json", "headRefName", "-q", ".headRefName"],
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def ensure_worktree(repo, number, category):
    """
    Prepare a working directory for Claude Code.

    For PRs: creates a git worktree checked out to the PR branch.
    For issues: uses the main clone on the default branch.

    Returns the working directory path.
    """
    repo_dir = ensure_repo(repo)

    if category != "pr":
        # Issues use the main clone directory
        return repo_dir

    # PRs get their own worktree
    wt_dir = REPOS_DIR / f"{_repo_dir_name(repo)}--pr-{number}"

    branch = _get_pr_branch(repo, number)
    if not branch:
        # Fallback: fetch the PR ref directly
        _run(
            ["git", "-C", str(repo_dir), "fetch", "origin",
             f"pull/{number}/head:pr-{number}"],
            check=False,
        )
        branch = f"pr-{number}"

    if wt_dir.exists():
        # Worktree exists — update it
        _run(["git", "-C", str(wt_dir), "fetch", "origin"], check=False)
        _run(["git", "-C", str(wt_dir), "checkout", branch], check=False)
        _run(["git", "-C", str(wt_dir), "pull", "--ff-only"], check=False)
    else:
        # Fetch the branch in the main clone first
        _run(
            ["git", "-C", str(repo_dir), "fetch", "origin", f"{branch}:{branch}"],
            check=False,
        )
        result = _run(
            ["git", "-C", str(repo_dir), "worktree", "add", str(wt_dir), branch],
            check=False,
        )
        if result.returncode != 0:
            # Branch may already exist locally, try checkout approach
            _run(
                ["git", "-C", str(repo_dir), "worktree", "add", str(wt_dir)],
                check=False,
            )
            _run(["git", "-C", str(wt_dir), "checkout", branch], check=False)

    return wt_dir


def get_worktree_path(repo, number, category):
    """Get the expected working directory path without creating it."""
    if category != "pr":
        return REPOS_DIR / _repo_dir_name(repo)
    return REPOS_DIR / f"{_repo_dir_name(repo)}--pr-{number}"
