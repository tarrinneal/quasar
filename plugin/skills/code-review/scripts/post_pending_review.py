#!/usr/bin/env python3
"""Creates a PENDING (draft) GitHub PR review from staged comments in the active
Antigravity Code Review session (or from an explicit JSON file).

Because the 'event' field is omitted, the review is saved as PENDING on GitHub,
allowing the human reviewer to inspect, edit, or submit it in the GitHub UI.

Usage:
  # Export staged comments directly from active_session.json:
  python3 post_pending_review.py --from-session

  # Or export from an explicit comments JSON file:
  python3 post_pending_review.py --pr 13265 --comments-file /path/to/comments.json [--repo-dir /path/to/repo]
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from typing import Any, Dict, List, Optional

from collect_pr_context import ACTIVE_SESSION_PATH, find_gh_binary, get_github_token, infer_owner_repo


def post_via_gh_or_rest(owner: str, repo: str, pr_num: int, payload: Dict[str, Any], repo_dir: str) -> Dict[str, Any]:
    payload.pop("event", None)
    gh = find_gh_binary()
    if gh:
        cmd = [
            gh,
            "api",
            "--method",
            "POST",
            f"repos/{owner}/{repo}/pulls/{pr_num}/reviews",
            "--input",
            "-",
        ]
        res = subprocess.run(
            cmd,
            cwd=repo_dir if os.path.isdir(repo_dir) else os.getcwd(),
            input=json.dumps(payload),
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            return json.loads(res.stdout)

    token = get_github_token()
    if not token:
        raise RuntimeError(
            "GitHub authentication required to post a Pending Review. Run `gh auth login` or set GITHUB_TOKEN."
        )
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_num}/reviews"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "antigravity-code-review",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Post a PENDING review to a GitHub PR.")
    parser.add_argument("--from-session", action="store_true", help="Export staged comments from active_session.json")
    parser.add_argument("--pr", help="GitHub PR number or URL (optional if --from-session is used)")
    parser.add_argument("--comments-file", help="Path to JSON file with draft comments")
    parser.add_argument("--repo-dir", default=os.getcwd(), help="Repository directory")
    parser.add_argument("--summary-body", default="", help="Optional overall review body")
    args = parser.parse_args()

    if args.from_session or not args.comments_file:
        if not os.path.isfile(ACTIVE_SESSION_PATH):
            sys.stderr.write(f"No active review session found at {ACTIVE_SESSION_PATH}\n")
            sys.exit(1)
        with open(ACTIVE_SESSION_PATH, "r", encoding="utf-8") as f:
            session = json.load(f)
        pr_meta = session.get("pr") or {}
        owner = pr_meta.get("owner")
        repo = pr_meta.get("repo")
        pr_num = int(pr_meta.get("number") or 0)
        repo_dir = session.get("repoDir") or args.repo_dir
        if args.pr and (not owner or not pr_num):
            owner, repo, pr_num = infer_owner_repo(args.pr, repo_dir)

        staged = [
            t
            for t in (session.get("threads") or [])
            if t.get("status") == "staged" and t.get("path") and t.get("draftComment")
        ]
        if not staged:
            sys.stderr.write("No staged comments found in active session. Stage one or more comments first.\n")
            sys.exit(1)

        comments_list: List[Dict[str, Any]] = []
        for t in staged:
            comments_list.append(
                {
                    "path": t["path"],
                    "line": int(t.get("endLine") or t.get("startLine") or 1),
                    "side": "RIGHT",
                    "body": t["draftComment"],
                }
            )
        payload: Dict[str, Any] = {"comments": comments_list}
        if args.summary_body:
            payload["body"] = args.summary_body
    else:
        with open(args.comments_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not args.pr:
            sys.stderr.write("--pr is required when using --comments-file\n")
            sys.exit(1)
        owner, repo, pr_num = infer_owner_repo(args.pr, args.repo_dir)
        repo_dir = args.repo_dir

    try:
        resp = post_via_gh_or_rest(owner, repo, pr_num, payload, repo_dir)
    except Exception as exc:
        sys.stderr.write(f"Failed to create pending review: {exc}\n")
        sys.exit(1)

    print(
        json.dumps(
            {
                "status": "pending_review_created",
                "review_id": resp.get("id"),
                "state": resp.get("state"),
                "html_url": resp.get("html_url"),
                "comments_exported": len(payload.get("comments") or []),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
