#!/usr/bin/env python3
"""Collects GitHub PR or local branch diff context, materializes Base/Head snapshots
for native side-by-side diffs, and initializes the Quasar session.

Supports both `gh` CLI (if installed/authenticated) and direct GitHub REST API.

Usage:
  # Review a GitHub PR by URL or number:
  python3 collect_pr_context.py --pr https://github.com/flutter/packages/pull/13265

  # Review local branch against a base ref:
  python3 collect_pr_context.py --base origin/main --repo-dir /path/to/repo
"""

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

REVIEW_STATE_DIR = os.path.expanduser("~/.gemini/quasar")
WORKSPACES_STATE_DIR = os.path.join(REVIEW_STATE_DIR, "workspaces")
SNAPSHOTS_DIR = os.path.join(REVIEW_STATE_DIR, "snapshots")
HISTORY_STATE_DIR = os.path.join(REVIEW_STATE_DIR, "history")
GITHUB_AUTH_PATH = os.path.join(REVIEW_STATE_DIR, "github_auth.json")


def archive_review_session(
    session: Dict[str, Any], md_content: Optional[str] = None
) -> Optional[str]:
    """Archives a review session and its CliffsNotes markdown under ~/.gemini/quasar/history/<sessionId>/."""
    if not isinstance(session, dict):
        return None
    raw_id = str(session.get("sessionId") or "").strip()
    if not raw_id:
        pr = session.get("pr") or {}
        if pr.get("number"):
            raw_id = f"{pr.get('owner', 'gh')}-{pr.get('repo', 'repo')}-pr-{pr.get('number')}"
        elif pr.get("headRefName"):
            raw_id = f"local-{pr.get('repo', 'repo')}-{pr.get('headRefName')}"
    safe_id = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw_id).strip("-")
    if not safe_id:
        return None
    hist_dir = os.path.join(HISTORY_STATE_DIR, safe_id)
    try:
        os.makedirs(hist_dir, exist_ok=True)
        hist_session_path = os.path.join(hist_dir, "session.json")
        hist_md_path = os.path.join(hist_dir, "cliffs_notes.md")
        with open(hist_session_path, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2)
        if md_content is None:
            try:
                md_content = render_cliffs_notes_markdown(session)
            except Exception:
                md_content = None
        if md_content:
            with open(hist_md_path, "w", encoding="utf-8") as mf:
                mf.write(md_content)
        return hist_dir
    except Exception:
        return None


def resolve_workspace_root(workspace_dir: Optional[str] = None) -> str:
    """Resolves the canonical workspace folder path for the active window (without collapsing monorepo subfolders)."""
    cand = workspace_dir or os.environ.get("QUASAR_WORKSPACE_DIR") or os.getcwd()
    cand = os.path.realpath(os.path.expanduser(cand))
    if os.path.isfile(cand):
        cand = os.path.dirname(cand)
    return cand


def compute_workspace_key(workspace_dir: str) -> str:
    """Computes a deterministic per-workspace directory key matching extension.js."""
    norm = os.path.realpath(os.path.expanduser(workspace_dir))
    base = re.sub(r"[^a-zA-Z0-9._-]+", "-", os.path.basename(norm)).strip("-") or "workspace"
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:10]
    return f"{base}-{digest}"


def get_workspace_state_dir(workspace_dir: Optional[str] = None) -> str:
    """Returns the isolated Quasar state directory for the given window/workspace."""
    explicit_state = os.environ.get("QUASAR_STATE_DIR")
    if explicit_state and not workspace_dir:
        return os.path.abspath(os.path.expanduser(explicit_state))
    ws_root = resolve_workspace_root(workspace_dir)
    ws_key = compute_workspace_key(ws_root)
    return os.path.join(WORKSPACES_STATE_DIR, ws_key)


def get_session_paths(
    workspace_dir: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Tuple[str, str, str]:
    """Returns (state_dir, active_session_path, cliffs_notes_md_path) for the target workspace.
    If thread_id is provided and not found in the default workspace session, searches all workspace sessions.
    """
    state_dir = get_workspace_state_dir(workspace_dir)
    session_path = os.path.join(state_dir, "active_session.json")
    md_path = os.path.join(state_dir, "cliffs_notes.md")

    if thread_id:
        # Check if the current workspace session already contains this thread_id
        found_in_current = False
        if os.path.isfile(session_path):
            try:
                with open(session_path, "r", encoding="utf-8") as f:
                    sess = json.load(f)
                if any(t.get("id") == thread_id for t in (sess.get("threads") or [])):
                    found_in_current = True
            except Exception:
                pass
        if not found_in_current and os.path.isdir(WORKSPACES_STATE_DIR):
            for entry in os.listdir(WORKSPACES_STATE_DIR):
                cand_dir = os.path.join(WORKSPACES_STATE_DIR, entry)
                cand_sess = os.path.join(cand_dir, "active_session.json")
                if not os.path.isfile(cand_sess):
                    continue
                try:
                    with open(cand_sess, "r", encoding="utf-8") as f:
                        sess = json.load(f)
                    if any(t.get("id") == thread_id for t in (sess.get("threads") or [])):
                        return cand_dir, cand_sess, os.path.join(cand_dir, "cliffs_notes.md")
                except Exception:
                    continue

    return state_dir, session_path, md_path


def is_pid_alive(pid: Any) -> bool:
    try:
        num = int(pid or 0)
        if num <= 0:
            return False
        os.kill(num, 0)
        return True
    except PermissionError:
        return True
    except Exception:
        return False


def resolve_target_window(
    state_dir: str,
    existing: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], Optional[int]]:
    """Resolves the owning (windowId, windowPid) for a session so multi-window workspaces never collide."""
    env_wid = (os.environ.get("QUASAR_WINDOW_ID") or "").strip()
    env_pid_raw = (os.environ.get("QUASAR_WINDOW_PID") or "").strip()
    env_pid = int(env_pid_raw) if env_pid_raw.isdigit() else None
    if env_wid:
        return env_wid, env_pid

    if isinstance(existing, dict) and existing.get("windowId"):
        ex_pid = existing.get("windowPid")
        if not ex_pid or is_pid_alive(ex_pid):
            return str(existing["windowId"]), int(ex_pid) if ex_pid else None

    reg_path = os.path.join(state_dir, "windows.json")
    if os.path.isfile(reg_path):
        try:
            with open(reg_path, "r", encoding="utf-8") as f:
                entries = json.load(f)
            if isinstance(entries, list):
                alive = [
                    w
                    for w in entries
                    if isinstance(w, dict) and w.get("windowId") and is_pid_alive(w.get("pid"))
                ]
                if alive:
                    alive.sort(key=lambda w: int(w.get("lastFocusedAt") or 0), reverse=True)
                    top = alive[0]
                    return str(top["windowId"]), int(top.get("pid") or 0) or None
        except Exception:
            pass

    return None, None


ACTIVE_SESSION_PATH = os.path.join(get_workspace_state_dir(), "active_session.json")
CLIFFS_NOTES_MD_PATH = os.path.join(get_workspace_state_dir(), "cliffs_notes.md")

GENERATED_PATTERNS = [
    r"\.g\.dart$",
    r"\.mocks\.dart$",
    r"\.freezed\.dart$",
    r"\.pigeon\.dart$",
    r"/generated/",
    r"platform_tests/.*\.(dart|kt|java|swift|mm|m|h|cc|cpp)$",
]

TEST_PATTERNS = [
    r"_test\.(dart|kt|java|swift|cc|cpp|py|ts|js)$",
    r"(^|/)test/",
    r"(^|/)tests/",
    r"(^|/)integration_test/",
]

DOC_CONFIG_PATTERNS = [
    r"(^|/)CHANGELOG\.md$",
    r"(^|/)pubspec\.yaml$",
    r"(^|/)package\.json$",
    r"(^|/)README\.md$",
    r"(^|/)AGENTS\.md$",
    r"\.md$",
]


def run_cmd(cmd: List[str], cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed ({' '.join(cmd)}): {res.stderr.strip()}")
    return res


def find_gh_binary() -> Optional[str]:
    for candidate in [
        shutil.which("gh"),
        "/opt/homebrew/bin/gh",
        "/usr/local/bin/gh",
    ]:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def load_stored_github_auth() -> Dict[str, Any]:
    if os.path.isfile(GITHUB_AUTH_PATH):
        try:
            with open(GITHUB_AUTH_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def get_github_token() -> Optional[str]:
    stored = load_stored_github_auth().get("token")
    if stored and str(stored).strip():
        return str(stored).strip()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token.strip()
    gh = find_gh_binary()
    if gh:
        try:
            res = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
    return None


def repo_matches_remote(cand_dir: str, owner: str, repo: str) -> bool:
    if not cand_dir or not os.path.isdir(os.path.join(cand_dir, ".git")):
        return False
    try:
        remotes = run_cmd(["git", "remote", "-v"], cwd=cand_dir, check=False).stdout
        if re.search(rf"[:/]{re.escape(repo)}(?:\.git)?(?:\s|$)", remotes, flags=re.IGNORECASE):
            return True
    except Exception:
        pass
    return False


def auto_detect_repo_dir(owner: str, repo: str, provided_dir: Optional[str]) -> str:
    if provided_dir:
        abs_prov = os.path.abspath(os.path.expanduser(provided_dir))
        if repo_matches_remote(abs_prov, owner, repo):
            return abs_prov

    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "work", repo),
        os.path.join(home, "work", owner, repo),
        os.path.join(home, "dev", repo),
        os.path.join(home, "src", repo),
        os.path.join(home, "projects", repo),
        os.path.join(home, repo),
        os.path.join(home, "work", "packages"),
    ]
    for cand in candidates:
        if repo_matches_remote(cand, owner, repo):
            return os.path.abspath(cand)
    if provided_dir and os.path.isdir(os.path.join(os.path.expanduser(provided_dir), ".git")):
        return os.path.abspath(os.path.expanduser(provided_dir))
    for cand in candidates:
        if os.path.isdir(os.path.join(cand, ".git")):
            return os.path.abspath(cand)
    return os.path.abspath(provided_dir or os.getcwd())


def infer_owner_repo(pr_arg: str, cwd: str) -> Tuple[str, str, int]:
    m = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_arg)
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    pr_num = int(pr_arg.strip().lstrip("#"))
    try:
        remotes = run_cmd(["git", "remote", "-v"], cwd=cwd, check=False).stdout
        for remote_pref in ["upstream", "origin"]:
            for line in remotes.splitlines():
                if line.startswith(remote_pref):
                    rm = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?\s", line)
                    if rm:
                        return rm.group(1), rm.group(2), pr_num
        rm = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?\s", remotes)
        if rm:
            return rm.group(1), rm.group(2), pr_num
    except Exception:
        pass
    return "flutter", "packages", pr_num


def github_api_get(url: str, accept: str = "application/vnd.github.v3+json") -> Any:
    headers = {
        "Accept": accept,
        "User-Agent": "quasar",
    }
    token = get_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        if "json" in accept:
            return json.loads(raw)
        return raw


def classify_file(path: str) -> str:
    for pat in DOC_CONFIG_PATTERNS:
        if re.search(pat, path):
            return "docs_or_config"
    for pat in GENERATED_PATTERNS:
        if re.search(pat, path):
            return "generated_or_fixture"
    for pat in TEST_PATTERNS:
        if re.search(pat, path):
            return "test"
    return "core_source"


def parse_first_changed_line(patch: Optional[str]) -> Tuple[int, int]:
    """Extracts (start_line, end_line) of the first hunk in the new file from a unified patch."""
    if not patch:
        return (1, 20)
    m = re.search(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", patch)
    if not m:
        return (1, 20)
    start = max(1, int(m.group(1)))
    count = int(m.group(2)) if m.group(2) else 15
    end = max(start, start + min(count, 40) - 1)
    return (start, end)


def parse_all_hunks(patch: Optional[str]) -> List[Dict[str, Any]]:
    if not patch:
        return []
    hunks: List[Dict[str, Any]] = []
    lines = patch.splitlines()
    current_hunk: Optional[Dict[str, Any]] = None
    hunk_lines: List[str] = []

    for line in lines:
        m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$", line)
        if m:
            if current_hunk is not None:
                current_hunk["snippet"] = "\n".join(hunk_lines[:35])
                hunks.append(current_hunk)
            new_start = max(1, int(m.group(3)))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            header_ctx = (m.group(5) or "").strip()
            current_hunk = {
                "oldStart": int(m.group(1)),
                "oldLines": int(m.group(2)) if m.group(2) is not None else 1,
                "startLine": new_start,
                "endLine": max(new_start, new_start + max(new_count, 1) - 1),
                "headerContext": header_ctx,
            }
            hunk_lines = [line]
        elif current_hunk is not None:
            hunk_lines.append(line)

    if current_hunk is not None:
        current_hunk["snippet"] = "\n".join(hunk_lines[:35])
        hunks.append(current_hunk)
    return hunks


def fetch_raw_file_content(
    owner: str,
    repo: str,
    sha: str,
    rel_path: str,
    repo_dir: str,
) -> Optional[str]:
    if not sha:
        return None
    # 1. Try local git object database first (fast, offline)
    if os.path.isdir(os.path.join(repo_dir, ".git")):
        commit_exists = run_cmd(["git", "cat-file", "-t", sha], cwd=repo_dir, check=False).returncode == 0
        res = run_cmd(["git", "cat-file", "-e", f"{sha}:{rel_path}"], cwd=repo_dir, check=False)
        if res.returncode == 0:
            show_res = run_cmd(["git", "show", f"{sha}:{rel_path}"], cwd=repo_dir, check=False)
            if show_res.returncode == 0:
                return show_res.stdout
        elif commit_exists:
            # The commit exists locally, so the file genuinely did not exist at this revision.
            return ""

    if owner == "local":
        return None

    # 2. Fallback to GitHub raw URL
    quoted_path = urllib.parse.quote(rel_path, safe="/")
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{quoted_path}"
    headers = {"User-Agent": "quasar"}
    token = get_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(raw_url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ""
        return None
    except Exception:
        return None


def materialize_snapshots(
    session_id: str,
    owner: str,
    repo: str,
    base_sha: str,
    head_sha: str,
    files_list: List[Dict[str, Any]],
    repo_dir: str,
    use_working_tree_head: bool = False,
) -> Tuple[str, str]:
    snapshot_root = os.path.join(REVIEW_STATE_DIR, "snapshots", session_id)
    base_root = os.path.join(snapshot_root, "base")
    head_root = os.path.join(snapshot_root, "head")
    os.makedirs(base_root, exist_ok=True)
    os.makedirs(head_root, exist_ok=True)

    # Try fetching the base/head refs in local git repo if missing, best-effort
    if os.path.isdir(os.path.join(repo_dir, ".git")) and base_sha and owner != "local":
        has_base = run_cmd(["git", "cat-file", "-t", base_sha], cwd=repo_dir, check=False).returncode == 0
        if not has_base:
            run_cmd(["git", "fetch", "--quiet", "origin"], cwd=repo_dir, check=False)

    def process_one(f_item: Dict[str, Any]) -> None:
        rel_path = f_item["path"]
        prev_rel_path = f_item.get("previousPath") or rel_path
        status = f_item.get("status", "modified")
        base_target = os.path.join(base_root, rel_path)
        head_target = os.path.join(head_root, rel_path)
        os.makedirs(os.path.dirname(base_target), exist_ok=True)
        os.makedirs(os.path.dirname(head_target), exist_ok=True)

        if status == "added":
            base_content = ""
        else:
            base_content = fetch_raw_file_content(owner, repo, base_sha, prev_rel_path, repo_dir)
            if base_content is None:
                base_content = ""

        if status == "removed":
            head_content = ""
        elif use_working_tree_head:
            local_file = os.path.join(repo_dir, rel_path)
            if os.path.isfile(local_file):
                head_content = open(local_file, "r", encoding="utf-8", errors="replace").read()
            else:
                head_content = fetch_raw_file_content(owner, repo, head_sha, rel_path, repo_dir) or ""
        else:
            head_content = fetch_raw_file_content(owner, repo, head_sha, rel_path, repo_dir)
            if head_content is None:
                local_file = os.path.join(repo_dir, rel_path)
                head_content = open(local_file, "r", encoding="utf-8", errors="replace").read() if os.path.isfile(local_file) else ""

        with open(base_target, "w", encoding="utf-8") as bf:
            bf.write(base_content or "")
        with open(head_target, "w", encoding="utf-8") as hf:
            hf.write(head_content or "")

        f_item["baseSnapshotPath"] = base_target
        f_item["headSnapshotPath"] = head_target
        local_candidate = os.path.join(repo_dir, rel_path)
        if os.path.isfile(local_candidate):
            f_item["localWorkspacePath"] = local_candidate

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(process_one, files_list))

    return base_root, head_root


def fetch_pr_via_rest(
    owner: str, repo: str, pr_num: int
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], str]:
    base_url = f"https://api.github.com/repos/{owner}/{repo}"
    pr_data = github_api_get(f"{base_url}/pulls/{pr_num}")
    files_data = github_api_get(f"{base_url}/pulls/{pr_num}/files?per_page=100")
    issue_comments = github_api_get(f"{base_url}/issues/{pr_num}/comments?per_page=100")
    review_comments = github_api_get(f"{base_url}/pulls/{pr_num}/comments?per_page=100")
    reviews = github_api_get(f"{base_url}/pulls/{pr_num}/reviews?per_page=100")
    diff_text = github_api_get(f"{base_url}/pulls/{pr_num}", accept="application/vnd.github.v3.diff")

    metadata = {
        "owner": owner,
        "repo": repo,
        "number": pr_data.get("number"),
        "title": pr_data.get("title"),
        "url": pr_data.get("html_url"),
        "state": pr_data.get("state"),
        "author": (pr_data.get("user") or {}).get("login"),
        "baseRefName": (pr_data.get("base") or {}).get("ref"),
        "baseSha": (pr_data.get("base") or {}).get("sha"),
        "headRefName": (pr_data.get("head") or {}).get("ref"),
        "headSha": (pr_data.get("head") or {}).get("sha"),
        "additions": pr_data.get("additions", 0),
        "deletions": pr_data.get("deletions", 0),
        "changedFilesCount": pr_data.get("changed_files", len(files_data)),
        "body": pr_data.get("body") or "",
    }

    comments: List[Dict[str, Any]] = []
    for c in issue_comments:
        comments.append(
            {
                "id": f"gh-issue-{c.get('id')}",
                "type": "issue_comment",
                "author": (c.get("user") or {}).get("login"),
                "created_at": c.get("created_at"),
                "body": (c.get("body") or "")[:1000],
            }
        )
    for r in reviews:
        if r.get("body"):
            comments.append(
                {
                    "id": f"gh-review-{r.get('id')}",
                    "type": "review",
                    "author": (r.get("user") or {}).get("login"),
                    "state": r.get("state"),
                    "body": (r.get("body") or "")[:1000],
                }
            )
    inline_by_raw_id: Dict[Any, Dict[str, Any]] = {}
    orphan_replies: List[Dict[str, Any]] = []
    for rc in review_comments:
        raw_id = rc.get("id")
        reply_to = rc.get("in_reply_to_id")
        rc_author = (rc.get("user") or {}).get("login")
        rc_body = (rc.get("body") or "")[:2000]
        if reply_to:
            parent = inline_by_raw_id.get(reply_to)
            reply_entry = {
                "id": f"gh-inline-{raw_id}",
                "raw_id": raw_id,
                "in_reply_to_id": reply_to,
                "author": rc_author,
                "created_at": rc.get("created_at"),
                "body": rc_body,
            }
            if parent is not None:
                parent.setdefault("replies", []).append(reply_entry)
            else:
                orphan_replies.append((reply_to, reply_entry, rc))
        else:
            entry = {
                "id": f"gh-inline-{raw_id}",
                "raw_id": raw_id,
                "type": "inline_comment",
                "author": rc_author,
                "path": rc.get("path"),
                "line": rc.get("line") or rc.get("original_line") or 1,
                "side": rc.get("side", "RIGHT"),
                "created_at": rc.get("created_at"),
                "body": rc_body,
                "replies": [],
            }
            inline_by_raw_id[raw_id] = entry
            comments.append(entry)

    for reply_to, reply_entry, rc in orphan_replies:
        parent = inline_by_raw_id.get(reply_to)
        if parent is not None:
            parent.setdefault("replies", []).append(reply_entry)
        else:
            comments.append(
                {
                    "id": reply_entry["id"],
                    "raw_id": reply_entry["raw_id"],
                    "in_reply_to_id": reply_to,
                    "type": "inline_comment",
                    "author": reply_entry["author"],
                    "path": rc.get("path"),
                    "line": rc.get("line") or rc.get("original_line") or 1,
                    "side": rc.get("side", "RIGHT"),
                    "created_at": reply_entry["created_at"],
                    "body": reply_entry["body"],
                    "replies": [],
                }
            )

    files_list = []
    for f in files_data:
        patch_str = f.get("patch") or ""
        start_line, end_line = parse_first_changed_line(patch_str)
        files_list.append(
            {
                "path": f.get("filename", ""),
                "status": f.get("status", "modified"),
                "added": f.get("additions", 0),
                "deleted": f.get("deletions", 0),
                "firstChangedStartLine": start_line,
                "firstChangedEndLine": end_line,
                "hunks": parse_all_hunks(patch_str),
            }
        )

    return metadata, comments, files_list, diff_text


def assess_existing_github_comment(
    author: str,
    body: str,
    replies: Optional[List[Dict[str, Any]]] = None,
    pr_author: str = "",
    rel_path: str = "",
    line: int = 1,
) -> Dict[str, Any]:
    """Classifies an existing GitHub PR inline comment so the user immediately sees whether:
    - `recommended_fix` (`🛠️ Should Fix`): Quasar thinks the comment is good and the suggested fix should be made.
    - `needs_attention` (`❓ Question` / `👀 Needs Look`): The comment asks a question that needs answering or raises a design discussion requiring human attention.
    - `addressed_or_refuted` (`⚠️ Disputed` / `💬 Replied`): The PR author already replied or refuted the premise.
    """
    replies = replies or []
    text = (body or "").strip()
    lower = text.lower()
    pr_author_norm = (pr_author or "").strip().lower()

    # 1. Check if PR author already replied or refuted the comment in the thread
    author_replies = [
        r
        for r in replies
        if pr_author_norm and (r.get("author") or "").strip().lower() == pr_author_norm
    ]
    if author_replies:
        last_reply_body = (author_replies[-1].get("body") or "").strip()
        last_reply_lower = last_reply_body.lower()
        first_reply_line = last_reply_body.splitlines()[0][:90] if last_reply_body else ""
        if any(
            kw in last_reply_lower
            for kw in (
                "mistaken",
                "premise",
                "intentionally",
                "by design",
                "does not",
                "doesn't",
                "not needed",
                "false positive",
                "incorrect",
            )
        ):
            return {
                "triageStatus": "addressed_or_refuted",
                "triageCategory": "refuted",
                "triageBadge": "⚠️ Disputed / No Fix",
                "triageLabel": "⚠️ Premise Disputed by Author — Do Not Apply",
                "triageSummary": (
                    f"@{pr_author} replied explaining why this comment's premise does not apply"
                    + (f' ("{first_reply_line}").' if first_reply_line else ".")
                ),
                "recommendedForFix": False,
            }
        if any(
            kw in last_reply_lower
            for kw in ("done", "fixed", "updated", "removed", "renamed", "addressed", "added")
        ):
            return {
                "triageStatus": "addressed_or_refuted",
                "triageCategory": "addressed",
                "triageBadge": "✅ Addressed in Thread",
                "triageLabel": "✅ Already Addressed by Author",
                "triageSummary": f"@{pr_author} replied indicating this comment was already addressed.",
                "recommendedForFix": False,
            }
        return {
            "triageStatus": "addressed_or_refuted",
            "triageCategory": "replied",
            "triageBadge": "💬 Author Replied",
            "triageLabel": "💬 Author Replied in Thread — Check if Resolved",
            "triageSummary": (
                f"@{pr_author} already replied in this thread"
                + (f': "{first_reply_line}"' if first_reply_line else ".")
            ),
            "recommendedForFix": False,
        }

    # 2. Check for explicit GitHub ```suggestion block
    if "```suggestion" in lower:
        return {
            "triageStatus": "recommended_fix",
            "triageCategory": "suggestion",
            "triageBadge": "🛠️ Good Fix (Suggestion)",
            "triageLabel": "🛠️ Good Comment — Suggested Fix Should Be Made",
            "triageSummary": f"@{author} provided a concrete code suggestion block (` ```suggestion `) that is ready to apply.",
            "recommendedForFix": True,
        }

    # 3. Check for high-signal bug / edge-case / compiler warning / shadowing / collision catches
    bug_signals = [
        ("warning:", "compiler/analyzer warning"),
        ("warnings-as-errors", "warnings-as-errors build hazard"),
        ("typecheck", "typecheck warning/error"),
        ("shadows", "variable/property shadowing bug"),
        ("shadowing", "variable/property shadowing bug"),
        ("same scope", "duplicate identifier collision in the same scope"),
        ("emits both", "duplicate code/variable emission bug"),
        ("twice in the same", "duplicate argument/identifier emission"),
        ("never released", "resource/memory leak on error path"),
        ("outside the try/finally", "resource cleanup outside try/finally block"),
        ("out of sync", "binding/type synchronization bug"),
        ("double-free", "memory lifecycle issue"),
        ("null-safe", "null-safety issue"),
        ("completionhandler", "parameter name collision with completionHandler"),
    ]
    matched_bug_reasons = [desc for pat, desc in bug_signals if pat in lower]
    if matched_bug_reasons:
        reason_str = ", ".join(dict.fromkeys(matched_bug_reasons[:2]))
        return {
            "triageStatus": "recommended_fix",
            "triageCategory": "bug_or_edge_case",
            "triageBadge": "🛠️ Good Catch — Should Fix",
            "triageLabel": "🛠️ Good Comment — Suggested Fix Should Be Made",
            "triageSummary": f"High-signal catch by @{author} identifying a valid {reason_str} in `{os.path.basename(rel_path) or rel_path}:L{line}`. Quasar recommends making this fix.",
            "recommendedForFix": True,
        }

    # 4. Check for open architectural / design questions or broader design discussions that require human attention
    is_design_discussion = any(
        phrase in lower
        for phrase in (
            "i don't know if i like",
            "scatters the code",
            "go in there own file",
            "go in their own file",
            "in its own file",
            "what do you think",
            "thoughts on",
            "how do you feel about",
            "architectural",
        )
    )
    has_question_mark = "?" in text
    is_polite_doc_or_nit_request = any(
        phrase in lower
        for phrase in (
            "can we get docs",
            "can we add a comment",
            "could we add a comment",
            "needs a comment",
            "more descriptive name",
            "prefer this be an if",
            "prefer an if",
            "small nit",
        )
    ) or lower.startswith("nit:") or lower.startswith("nit ")

    if is_design_discussion:
        if has_question_mark:
            return {
                "triageStatus": "needs_attention",
                "triageCategory": "question",
                "triageBadge": "❓ Question — Needs Answer",
                "triageLabel": "❓ Question — Requires Your Answer or Decision",
                "triageSummary": f"@{author} asked an architectural/design question on `{os.path.basename(rel_path) or rel_path}` that needs an answer or design decision.",
                "recommendedForFix": False,
            }
        return {
            "triageStatus": "needs_attention",
            "triageCategory": "discussion",
            "triageBadge": "👀 Needs Look (Design)",
            "triageLabel": "👀 Needs Attention — Design Discussion",
            "triageSummary": f"@{author} raised a design/structure concern on `{os.path.basename(rel_path) or rel_path}` that requires looking at to decide whether to refactor or reply.",
            "recommendedForFix": False,
        }

    # 5. Check for questions that ask whether something is necessary or how it works
    if has_question_mark and not is_polite_doc_or_nit_request:
        if "is this " in lower and "necessary" in lower:
            return {
                "triageStatus": "recommended_fix",
                "triageCategory": "cleanup_question",
                "triageBadge": "🛠️ Good Catch / ❓ Question",
                "triageLabel": "🛠️ Good Comment — Suggested Simplification (or Reply)",
                "triageSummary": f"@{author} asked whether this code is redundant; inspecting the context shows it can be simplified directly as suggested.",
                "recommendedForFix": True,
            }
        return {
            "triageStatus": "needs_attention",
            "triageCategory": "question",
            "triageBadge": "❓ Question — Needs Answer",
            "triageLabel": "❓ Question — Requires Your Attention / Answer",
            "triageSummary": f"@{author} asked a question on `{os.path.basename(rel_path) or rel_path}:L{line}` that needs to be looked at and answered.",
            "recommendedForFix": False,
        }

    # 6. Actionable nits, readability improvements, and documentation/comment requests
    if is_polite_doc_or_nit_request or any(
        kw in lower
        for kw in (
            "i think this",
            "should be",
            "prefer ",
            "consider ",
            "nit",
            "readability",
            "comment",
            "docs",
            "rename",
        )
    ):
        return {
            "triageStatus": "recommended_fix",
            "triageCategory": "actionable_improvement",
            "triageBadge": "🛠️ Good Comment — Should Fix",
            "triageLabel": "🛠️ Good Comment — Suggested Fix Should Be Made",
            "triageSummary": f"Clear, actionable improvement suggested by @{author} on `{os.path.basename(rel_path) or rel_path}:L{line}`; Quasar recommends applying this fix.",
            "recommendedForFix": True,
        }

    # Default fallback: requires looking at
    return {
        "triageStatus": "needs_attention",
        "triageCategory": "review_comment",
        "triageBadge": "👀 Needs Attention",
        "triageLabel": "👀 Needs Attention — Review Comment",
        "triageSummary": f"Existing review comment from @{author} that requires looking at.",
        "recommendedForFix": False,
    }


def build_initial_cliffs_notes_session(
    session_id: str,
    repo_dir: str,
    metadata: Dict[str, Any],
    categorized_files: Dict[str, List[Dict[str, Any]]],
    existing_comments: List[Dict[str, Any]],
    base_root: str,
    head_root: str,
    output_dir: Optional[str],
    comments_only: bool = False,
) -> Dict[str, Any]:
    """Builds an immediate CliffsNotes session structure from the categorized files and hunks
    so the native Quasar extension renders the tour right away, before or during deep agent synthesis.
    """
    chapters: List[Dict[str, Any]] = []

    chapter_defs = (
        []
        if comments_only
        else [
            (
                "ch-core",
                "Chapter 1: Core Source & Logic",
                "core_source",
                "Primary implementation and API changes that drive this PR's behavior.",
            ),
            (
                "ch-tests",
                "Chapter 2: Verification & Unit Tests",
                "test",
                "Unit and integration tests verifying the new behavior and edge cases.",
            ),
            (
                "ch-docs",
                "Chapter 3: Documentation, Config & Changelog",
                "docs_or_config",
                "Version bumps, CHANGELOG entries, and documentation updates.",
            ),
            (
                "ch-generated",
                "Chapter 4: Generated Code & Test Fixtures (Skim)",
                "generated_or_fixture",
                "Auto-generated bindings or platform test outputs derived from the generator changes.",
            ),
        ]
    )

    stop_counter = 1
    for ch_id, ch_title, cat_key, ch_desc in chapter_defs:
        cat_files = categorized_files.get(cat_key, [])
        if not cat_files:
            continue
        stops = []
        cliffs_bullets = [ch_desc]
        for f_item in cat_files:
            rel_path = f_item["path"]
            basename = os.path.basename(rel_path)
            hunks = f_item.get("hunks") or []
            start_line = f_item.get("firstChangedStartLine", 1)
            end_line = f_item.get("firstChangedEndLine", start_line + 15)
            header_ctx = hunks[0].get("headerContext") if hunks else ""
            stop_summary = (
                f"{basename} ({header_ctx})"
                if header_ctx
                else f"{basename} (+{f_item.get('added', 0)} / -{f_item.get('deleted', 0)})"
            )
            cliffs_bullets.append(
                f"`{basename}`: +{f_item.get('added', 0)}/-{f_item.get('deleted', 0)} lines ({f_item.get('status', 'modified')})"
            )
            stops.append(
                {
                    "id": f"stop-{stop_counter}",
                    "stopNumber": f"{len(chapters) + 1}.{len(stops) + 1}",
                    "title": f"{basename}:{start_line}-{end_line}",
                    "description": stop_summary,
                    "path": rel_path,
                    "startLine": start_line,
                    "endLine": end_line,
                    "added": f_item.get("added", 0),
                    "deleted": f_item.get("deleted", 0),
                    "status": f_item.get("status", "modified"),
                    "baseSnapshotPath": f_item.get("baseSnapshotPath", os.path.join(base_root, rel_path)),
                    "headSnapshotPath": f_item.get("headSnapshotPath", os.path.join(head_root, rel_path)),
                    "localWorkspacePath": f_item.get("localWorkspacePath"),
                    "cliffsNotes": (
                        f"Changed `{rel_path}` (+{f_item.get('added', 0)} / -{f_item.get('deleted', 0)}). "
                        + (f"Context: `{header_ctx}`." if header_ctx else "Inspect diff hunks for behavioral changes.")
                    ),
                    "whyItChanged": "Part of PR #" + str(metadata.get("number") or "local"),
                    "callersAndInvariants": "Hover or click 'Ask Quasar' on any line to trace callers or invariants.",
                    "hunks": hunks,
                    "read": False,
                }
            )
            stop_counter += 1

        chapters.append(
            {
                "id": ch_id,
                "title": ch_title,
                "category": cat_key,
                "summary": ch_desc,
                "cliffsNotesBullets": cliffs_bullets,
                "stops": stops,
            }
        )

    # Also merge consecutive same-path/same-line comments where a reply was fetched without in_reply_to_id
    pr_author_login = (metadata.get("author") or "").strip()
    inline_comments_raw = [
        c for c in existing_comments if c.get("type") == "inline_comment" and c.get("path")
    ]
    merged_inline_comments: List[Dict[str, Any]] = []
    for c in inline_comments_raw:
        c_copy = dict(c)
        c_copy["replies"] = list(c.get("replies") or [])
        if (
            merged_inline_comments
            and not c_copy.get("replies")
            and pr_author_login
            and (c_copy.get("author") or "").strip().lower() == pr_author_login.lower()
            and merged_inline_comments[-1].get("path") == c_copy.get("path")
            and int(merged_inline_comments[-1].get("line") or 1) == int(c_copy.get("line") or 1)
        ):
            merged_inline_comments[-1]["replies"].append(
                {
                    "id": c_copy.get("id"),
                    "author": c_copy.get("author"),
                    "created_at": c_copy.get("created_at"),
                    "body": c_copy.get("body") or "",
                }
            )
            continue
        merged_inline_comments.append(c_copy)

    inline_threads: List[Dict[str, Any]] = []
    for c in merged_inline_comments:
        raw_body = (c.get("body") or "").strip()
        # Strip leading markdown badge images (e.g. ![critical](...)) when building short title
        clean_lines = [
            re.sub(r"^!\[[^\]]*\]\([^)]+\)\s*", "", ln).strip()
            for ln in raw_body.splitlines()
        ]
        non_empty_lines = [ln for ln in clean_lines if ln]
        first_line = (non_empty_lines[0][:72] if non_empty_lines else "Review comment")
        author = c.get("author") or "reviewer"
        replies = c.get("replies") or []

        triage = assess_existing_github_comment(
            author=author,
            body=raw_body,
            replies=replies,
            pr_author=pr_author_login,
            rel_path=c.get("path") or "",
            line=int(c.get("line") or 1),
        )
        cat = triage.get("triageCategory") or ""
        status = triage.get("triageStatus") or "needs_attention"
        if status == "recommended_fix":
            left_tag = "🛠️ [Suggestion]" if cat == "suggestion" else "🛠️ [Should Fix]"
        elif status == "addressed_or_refuted":
            left_tag = (
                "⚠️ [Disputed]"
                if cat == "refuted"
                else ("✅ [Addressed]" if cat == "addressed" else "💬 [Replied]")
            )
        else:
            left_tag = "❓ [Question]" if "question" in cat else "👀 [Needs Look]"

        msgs: List[Dict[str, Any]] = [
            {
                "author": f"@{author} (GitHub)",
                "body": raw_body,
                "timestamp": c.get("created_at") or "",
            }
        ]
        for rep in replies:
            rep_author = rep.get("author") or "reviewer"
            msgs.append(
                {
                    "author": f"@{rep_author} (GitHub Reply)",
                    "body": (rep.get("body") or "").strip(),
                    "timestamp": rep.get("created_at") or "",
                }
            )
        msgs.append(
            {
                "author": f"Quasar Triage ({triage['triageBadge']})",
                "body": f"**{triage['triageLabel']}**\n\n{triage['triageSummary']}",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "isTriageNote": True,
            }
        )

        inline_threads.append(
            {
                "id": str(c["id"]),
                "kind": "github_existing",
                "severity": "EXISTING",
                "status": "existing",
                "selectedForFix": False,
                "author": author,
                "triageLeftTag": left_tag,
                "triageStatus": triage["triageStatus"],
                "triageCategory": triage["triageCategory"],
                "triageBadge": triage["triageBadge"],
                "triageLabel": triage["triageLabel"],
                "triageSummary": triage["triageSummary"],
                "recommendedForFix": triage["recommendedForFix"],
                "path": c["path"],
                "startLine": int(c.get("line") or 1),
                "endLine": int(c.get("line") or 1),
                "title": f"{left_tag} {first_line}",
                "whyItMatters": f"{triage['triageLabel']} — {triage['triageSummary']}",
                "draftComment": raw_body,
                "messages": msgs,
            }
        )

    pr_body_first_para = ""
    if metadata.get("body"):
        cleaned = re.sub(r"<!--.*?-->", "", metadata["body"], flags=re.DOTALL).strip()
        paras = [p.strip() for p in cleaned.split("\n\n") if p.strip() and not p.strip().startswith("#")]
        if paras:
            pr_body_first_para = paras[0][:400]

    is_local = not metadata.get("number")
    default_tldr = (
        pr_body_first_para
        or (
            f"Reviewing local branch `{metadata.get('headRefName', 'HEAD')}` against `{metadata.get('baseRefName', 'main')}` in `{metadata.get('repo', '')}`"
            if is_local
            else f"Reviewing PR #{metadata.get('number')}: {metadata.get('title')}"
        )
    )

    prefs_summary: Dict[str, Any] = {}
    try:
        from manage_preferences import build_prompt_summary, load_preferences

        prefs_summary = build_prompt_summary(load_preferences(), metadata.get("repo"))
    except Exception:
        prefs_summary = {}

    session: Dict[str, Any] = {
        "sessionId": session_id,
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "analysisStatus": "analyzing",
        "repoDir": repo_dir,
        "baseSnapshotRoot": base_root,
        "headSnapshotRoot": head_root,
        "scratchDir": output_dir or REVIEW_STATE_DIR,
        "cliffsNotesMarkdownPath": CLIFFS_NOTES_MD_PATH,
        "pr": metadata,
        "reviewerPreferences": prefs_summary,
        "cliffsNotes": {
            "tldr": default_tldr,
            "whatAndWhy": pr_body_first_para or "Quasar is synthesizing the executive CliffsNotes for this review...",
            "architectureDiagram": "",
            "keySymbols": [],
            "designTradeoffs": [],
        },
        "chapters": chapters,
        "threads": inline_threads,
        "coverageMatrix": [],
        "repoChecks": [],
        "dismissedChecks": [],
    }
    return session


_MERMAID_RESERVED_IDS = {
    "end",
    "subgraph",
    "graph",
    "flowchart",
    "class",
    "classdef",
    "style",
    "linkstyle",
    "click",
    "call",
    "default",
    "direction",
}


def _safe_mermaid_id(raw_id: str, prefix: str = "node_") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", (raw_id or "").strip())
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"{prefix}{cleaned}"
    if cleaned.lower() in _MERMAID_RESERVED_IDS:
        cleaned = f"{prefix}{cleaned}"
    return cleaned


def _clean_mermaid_label(label: str, is_edge: bool = False) -> str:
    txt = str(label or "")
    # Replace literal \n sequences, actual newlines, and HTML <br> tags with a clean separator
    txt = re.sub(r"\\n|\r?\n", " - ", txt)
    txt = re.sub(r"<\s*br\s*/?\s*>", " - ", txt, flags=re.IGNORECASE)
    # Replace angle-bracket type/placeholder tokens like <Prefix> or <T> without leaving HTML tags
    txt = re.sub(r"<([A-Za-z0-9_., /+-]+)>", r"\1", txt)
    txt = txt.replace("<", "").replace(">", "")
    txt = txt.replace('"', "'").replace("`", "'")
    if is_edge:
        # Edge labels |...| in Mermaid can fail when containing parentheses, brackets, or colons
        txt = txt.replace("(", "- ").replace(")", "").replace("[", "").replace("]", "")
        txt = txt.replace(":", " -").replace(";", ",")
    txt = re.sub(r"\s+", " ", txt).strip()
    txt = re.sub(r"(?:\s*-\s*){2,}", " - ", txt).strip(" -")
    return txt or "Step"


def sanitize_mermaid_diagram(diag: str) -> str:
    """Sanitizes LLM-generated Mermaid flowchart code to prevent parser and renderer errors."""
    if not diag or not str(diag).strip():
        return ""
    raw = str(diag).strip()
    # Strip outer markdown code fences if present
    raw = re.sub(r"^```(?:mermaid)?\s*\r?\n?", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\r?\n?```\s*$", "", raw).strip()

    # Collapse multi-line double-quoted strings into single lines before line splitting
    raw = re.sub(
        r'"([^"]*)"',
        lambda m: '"' + re.sub(r"\s*\r?\n\s*", " - ", m.group(1)).strip() + '"',
        raw,
    )

    raw_lines = [ln.rstrip() for ln in raw.splitlines() if ln.strip()]
    if not raw_lines:
        return ""

    out_lines: List[str] = []
    first = raw_lines[0].strip()
    if re.match(r"^(?:flowchart|graph)\b", first, flags=re.IGNORECASE):
        parts = first.split()
        direction = parts[1].upper() if len(parts) > 1 and parts[1].upper() in ("TD", "TB", "BT", "RL", "LR") else "LR"
        out_lines.append(f"flowchart {direction}")
        body_lines = raw_lines[1:]
    else:
        out_lines.append("flowchart LR")
        body_lines = raw_lines

    for line in body_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        indent_Match = re.match(r"^(\s*)", line)
        indent = indent_Match.group(1) if indent_Match else "  "

        if stripped.lower() == "end":
            out_lines.append(f"{indent}end")
            continue

        if re.match(r"^direction\s+(?:TD|TB|BT|RL|LR)\b", stripped, flags=re.IGNORECASE):
            dir_tok = stripped.split()[1].upper()
            out_lines.append(f"{indent}direction {dir_tok}")
            continue

        # Handle subgraph declarations: subgraph ID ["Title"] or subgraph "Title" or subgraph Title
        sg_match = re.match(r"^subgraph\s+(.+)$", stripped, flags=re.IGNORECASE)
        if sg_match:
            sg_rest = sg_match.group(1).strip()
            m_bracket = re.match(r'^([A-Za-z0-9_]+)\s*\[\s*"?([^"\]]*)"?\s*\]$', sg_rest)
            m_quoted = re.match(r'^([A-Za-z0-9_]+)\s+"([^"]+)"$', sg_rest)
            m_only_quoted = re.match(r'^"([^"]+)"$', sg_rest)
            if m_bracket:
                sg_id = _safe_mermaid_id(m_bracket.group(1), prefix="sg_")
                sg_label = _clean_mermaid_label(m_bracket.group(2))
            elif m_quoted:
                sg_id = _safe_mermaid_id(m_quoted.group(1), prefix="sg_")
                sg_label = _clean_mermaid_label(m_quoted.group(2))
            elif m_only_quoted:
                sg_label = _clean_mermaid_label(m_only_quoted.group(1))
                sg_id = _safe_mermaid_id(sg_label[:24], prefix="sg_")
            else:
                sg_label = _clean_mermaid_label(sg_rest)
                sg_id = _safe_mermaid_id(sg_rest.split()[0], prefix="sg_")
            out_lines.append(f'{indent}subgraph {sg_id} ["{sg_label}"]')
            continue

        # Normalize inline -- "label" --> or -- label --> into -->|"label"|
        stmt = re.sub(
            r'--\s*"([^"]+)"\s*-->',
            lambda m: f'-->|"{_clean_mermaid_label(m.group(1), is_edge=True)}"|',
            stripped,
        )
        stmt = re.sub(
            r"-\.\s*\"([^\"]+)\"\s*\.->",
            lambda m: f'-.->|"{_clean_mermaid_label(m.group(1), is_edge=True)}"|',
            stmt,
        )

        # Normalize bidirectional arrows (<-->, <-.->, <==>) to standard arrows (Mermaid flowchart chokes on <--> with labels)
        stmt = stmt.replace("<-->", "-->").replace("<-.->", "-.->").replace("<==>", "==>")

        # Clean any |...| edge labels
        stmt = re.sub(
            r'\|\s*"?([^"|]*)"?\s*\|',
            lambda m: f'|"{_clean_mermaid_label(m.group(1), is_edge=True)}"|',
            stmt,
        )

        # Clean any node definitions ID["Label"] or ID[Label]
        def _replace_node_def(m: re.Match) -> str:
            n_id = _safe_mermaid_id(m.group(1), prefix="n_")
            n_lbl = _clean_mermaid_label(m.group(2), is_edge=False)
            return f'{n_id}["{n_lbl}"]'

        stmt = re.sub(r'([A-Za-z0-9_]+)\s*\[\s*"([^"]*)"\s*\]', _replace_node_def, stmt)
        stmt = re.sub(r'([A-Za-z0-9_]+)\s*\[\s*([^"\[\]]+)\s*\]', _replace_node_def, stmt)

        # Attach any |...| edge label directly to its preceding arrow token (Mermaid requires -->|label| with no space before |)
        stmt = re.sub(r'(-\.->|==>|--+>)\s+(\|"[^"]*"\|)', r"\1\2", stmt)

        # Ensure spacing before arrow tokens and after arrow(+label) tokens
        stmt = re.sub(
            r'(?<!\s)((?:-\.->|==>|--+>)(?:\|"[^"]*"\|)?)',
            r" \1",
            stmt,
        )
        stmt = re.sub(
            r'((?:-\.->|==>|--+>)(?:\|"[^"]*"\|)?)(?!\s|\|)',
            r"\1 ",
            stmt,
        )
        stmt = re.sub(r"\s+", " ", stmt).strip()

        # Split chained arrows (e.g. A --> B --> C) into individual lines
        tokens = re.split(
            r'\s+((?:-\.->|==>|--+>)(?:\|"[^"]*"\|)?)\s+',
            stmt,
        )
        if len(tokens) >= 5 and len(tokens) % 2 == 1:
            for idx in range(0, len(tokens) - 2, 2):
                left_tok = tokens[idx].strip()
                arrow_tok = tokens[idx + 1].strip()
                right_tok = tokens[idx + 2].strip()
                if idx > 0:
                    # Use only the node ID on the left of subsequent chained edges if it was already defined
                    m_id = re.match(r"^([A-Za-z0-9_]+)", left_tok)
                    if m_id:
                        left_tok = _safe_mermaid_id(m_id.group(1), prefix="n_")
                out_lines.append(f"{indent}{left_tok} {arrow_tok} {right_tok}")
        else:
            out_lines.append(f"{indent}{stmt}")

    return "\n".join(out_lines)


def _split_concise_and_detail(raw_text: str, max_short_chars: int = 165) -> Tuple[str, str]:
    text = re.sub(r"\s+", " ", str(raw_text or "")).strip()
    if not text:
        return "", ""
    # Split after first sentence boundary (. ! ?) not inside backticks
    in_tick = False
    split_idx = -1
    for i in range(len(text) - 1):
        ch = text[i]
        if ch == "`":
            in_tick = not in_tick
            continue
        if not in_tick and ch in ".!?" and text[i + 1] == " ":
            if i >= 22:
                split_idx = i + 1
                break
    if split_idx != -1 and split_idx < len(text) - 4:
        first = text[:split_idx].strip()
        rest = text[split_idx:].strip()
        cleaned_first = re.sub(r"\s*\([^()]{28,}\)", "", first).strip()
        return cleaned_first or first, rest
    cleaned = re.sub(r"\s*\([^()]{28,}\)", "", text).strip()
    if cleaned != text and len(cleaned) >= 24:
        return cleaned, text
    if len(text) > max_short_chars:
        clause_match = re.match(r"^(.{40,145}?[,;—–])\s+(.+)$", text)
        if clause_match:
            return clause_match.group(1).rstrip(",;—– ").strip() + ".", text
    return text, ""


def _read_stop_snippet_markdown(session: Dict[str, Any], stop: Dict[str, Any], max_lines: int = 12) -> str:
    candidates = [
        stop.get("headSnapshotPath"),
        stop.get("localWorkspacePath"),
        os.path.join(session.get("headSnapshotRoot", ""), stop.get("path", "")) if session.get("headSnapshotRoot") and stop.get("path") else None,
    ]
    file_path = next((p for p in candidates if p and os.path.isfile(p)), None)
    if not file_path:
        return ""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.read().splitlines()
        start_l = max(1, int(stop.get("startLine") or 1))
        end_l = max(start_l, int(stop.get("endLine") or start_l))
        slice_end = min(len(all_lines), end_l, start_l + max_lines - 1)
        snippet_lines = all_lines[start_l - 1 : slice_end]
        if not snippet_lines:
            return ""
        ext = os.path.splitext(file_path)[1].lstrip(".").lower()
        lang_map = {
            "py": "python",
            "js": "javascript",
            "ts": "typescript",
            "tsx": "tsx",
            "jsx": "jsx",
            "dart": "dart",
            "kt": "kotlin",
            "kts": "kotlin",
            "swift": "swift",
            "rs": "rust",
            "go": "go",
            "sh": "bash",
            "md": "markdown",
            "json": "json",
            "yaml": "yaml",
            "yml": "yaml",
        }
        lang = lang_map.get(ext, ext)
        out = [f"```{lang}"]
        for idx, line in enumerate(snippet_lines):
            out.append(f"{start_l + idx:>4} | {line}")
        if end_l > slice_end:
            out.append(f"     | ... ({end_l - slice_end} more lines)")
        out.append("```")
        return "\n".join(out)
    except Exception:
        return ""


TOP_LEVEL_COMMENTS_FILENAME = "TOP_LEVEL_PR_COMMENTS.md"
TOP_LEVEL_SUMMARY_THREAD_ID = "top-level-review"


def build_combined_top_level_comment(session: Dict[str, Any]) -> str:
    """Builds a single ready-to-leave Top-Level PR Review Comment combining any
    explicit `topLevelCommentIntro` / `topLevelComment` and any active top-level
    findings (such as comments on untouched files outside the PR diff).
    """
    intro = (session.get("topLevelCommentIntro") or "").strip()
    threads = session.get("threads") or []
    top_items: List[str] = []
    for t in threads:
        if t.get("id") == TOP_LEVEL_SUMMARY_THREAD_ID:
            continue
        if t.get("status") in ("dismissed", "uncertain"):
            continue
        rel_path = (t.get("path") or "").strip()
        is_top = bool(
            t.get("isTopLevel")
            or not rel_path
            or rel_path == TOP_LEVEL_COMMENTS_FILENAME
        )
        if not is_top:
            continue
        draft = (t.get("draftComment") or t.get("body") or "").strip()
        if not draft:
            continue
        if intro and draft in intro:
            continue
        target_file = (t.get("targetFile") or "").strip()
        if target_file and target_file not in draft and os.path.basename(target_file) not in draft:
            top_items.append(f"**`{target_file}`:** {draft}")
        elif target_file and target_file not in draft:
            top_items.append(f"**`{target_file}`:** {draft}")
        else:
            top_items.append(draft)

    parts: List[str] = []
    if intro:
        parts.append(intro)
    if len(top_items) > 1:
        parts.append("\n".join(f"- {item}" for item in top_items))
    elif len(top_items) == 1:
        parts.append(top_items[0])
    combined = "\n\n".join(p for p in parts if p).strip()
    session["topLevelComment"] = combined
    return combined


def sync_top_level_comments_file(session: Dict[str, Any]) -> None:
    """Generates `<baseSnapshotRoot>/TOP_LEVEL_PR_COMMENTS.md` and
    `<headSnapshotRoot>/TOP_LEVEL_PR_COMMENTS.md` with a ready-to-leave
    Top-Level PR Review Comment at the top plus individual top-level comment
    sections anchored to native VS Code inline comment controls.
    """
    base_root = (session.get("baseSnapshotRoot") or "").strip()
    head_root = (session.get("headSnapshotRoot") or "").strip()
    repo_dir = (session.get("repoDir") or "").strip()
    if not head_root or not os.path.isdir(head_root):
        build_combined_top_level_comment(session)
        return

    threads = session.get("threads") or []
    summary_thread: Optional[Dict[str, Any]] = None
    top_threads: List[Dict[str, Any]] = []
    for t in threads:
        if t.get("id") == TOP_LEVEL_SUMMARY_THREAD_ID:
            summary_thread = t
            continue
        rel_path = (t.get("path") or "").strip()
        is_top = bool(
            t.get("isTopLevel")
            or not rel_path
            or rel_path == TOP_LEVEL_COMMENTS_FILENAME
            or not os.path.isfile(os.path.join(head_root, rel_path))
        )
        if is_top:
            if rel_path and rel_path != TOP_LEVEL_COMMENTS_FILENAME and not t.get("targetFile"):
                t["targetFile"] = rel_path
            t["isTopLevel"] = True
            t["path"] = TOP_LEVEL_COMMENTS_FILENAME
            if t.get("status") != "dismissed":
                top_threads.append(t)

    combined_top_comment = build_combined_top_level_comment(session)

    header_lines = [
        "# 📌 Top-Level PR Review Comment (Ready to Leave on PR)",
        "",
        "This file holds the overall **top-level review comment** to leave on the PR (including comments about untouched files not in the PR diff).",
        "When you publish your review to GitHub, staged comments here are automatically combined into the **top-level PR review comment body**.",
    ]

    if base_root and os.path.isdir(base_root):
        base_file = os.path.join(base_root, TOP_LEVEL_COMMENTS_FILENAME)
        try:
            with open(base_file, "w", encoding="utf-8") as bf:
                bf.write("\n".join(header_lines) + "\n")
        except Exception:
            pass

    head_lines = list(header_lines)
    head_lines.append("")
    summary_start_line = len(head_lines) + 1
    head_lines.append("## 📝 Combined Top-Level Comment to Leave on Review")
    effective_summary_draft = (
        (summary_thread.get("draftComment") or "").strip()
        if summary_thread and summary_thread.get("editedByUser")
        else combined_top_comment
    )
    if effective_summary_draft:
        head_lines.append("```markdown")
        for c_ln in effective_summary_draft.splitlines():
            head_lines.append(c_ln)
        head_lines.append("```")
    else:
        head_lines.append("_No top-level PR review comment staged._")
    summary_end_line = len(head_lines)
    head_lines.append("")
    head_lines.append("---")
    head_lines.append("")

    if effective_summary_draft:
        now_ts = session.get("updatedAt") or datetime.datetime.now(datetime.timezone.utc).isoformat()
        summary_msg_body = (
            f"**📌 Top-Level PR Review Comment (Overall Summary & Untouched Files)**\n\n"
            f"*CliffsNotes Context*: Ready-to-leave top-level comment for the PR review (automatically used as the GitHub review summary `body`).\n\n"
            f"---\n"
            f"**Draft GitHub Comment**:\n> "
            + "\n> ".join(effective_summary_draft.splitlines())
        )
        if summary_thread is None:
            summary_thread = {
                "id": TOP_LEVEL_SUMMARY_THREAD_ID,
                "kind": "ai_candidate",
                "severity": "DESIGN",
                "status": "staged",
                "isTopLevel": True,
                "isTopLevelSummary": True,
                "targetFile": "",
                "selectedForFix": False,
                "uncertain": False,
                "uncertaintyQuestion": "",
                "preferenceCategory": "comment_placement",
                "preferenceOptions": [],
                "path": TOP_LEVEL_COMMENTS_FILENAME,
                "startLine": summary_start_line,
                "endLine": summary_end_line,
                "title": "📌 Top-Level PR Review Comment (Ready to Leave on Review)",
                "whyItMatters": "Overall top-level review comment combining general PR feedback and comments on untouched files outside the PR diff.",
                "draftComment": effective_summary_draft,
                "messages": [
                    {
                        "author": "Quasar",
                        "body": summary_msg_body,
                        "timestamp": now_ts,
                    }
                ],
            }
            session["threads"] = [summary_thread] + threads
        else:
            summary_thread["isTopLevel"] = True
            summary_thread["isTopLevelSummary"] = True
            summary_thread["path"] = TOP_LEVEL_COMMENTS_FILENAME
            summary_thread["startLine"] = summary_start_line
            summary_thread["endLine"] = summary_end_line
            if not summary_thread.get("editedByUser"):
                summary_thread["draftComment"] = effective_summary_draft
                if summary_thread.get("messages"):
                    summary_thread["messages"][0]["body"] = summary_msg_body

    if not top_threads:
        head_lines.append("_No individual untouched-file comments in this session._")
    else:
        for idx, t in enumerate(top_threads, 1):
            start_line = len(head_lines) + 1
            title = (t.get("title") or f"Top-Level Comment #{idx}").strip()
            target_file = (t.get("targetFile") or "").strip()
            sev = (t.get("severity") or "DESIGN").upper()
            why = (t.get("whyItMatters") or "").strip()
            draft = (t.get("draftComment") or t.get("body") or "").strip()

            head_lines.append(f"## 📌 #{idx} [{sev}] {title}")
            if target_file:
                head_lines.append(f"- **Untouched File (Not in PR Diff)**: `{target_file}`")
            if why:
                head_lines.append(f"- **CliffsNotes Context**: {why}")
            head_lines.append("")
            head_lines.append("### 💬 Top-Level GitHub Review Comment Item")
            if target_file and target_file not in draft and os.path.basename(target_file) not in draft:
                head_lines.append(f"> **`{target_file}`:** {draft}")
            elif target_file and target_file not in draft:
                head_lines.append(f"> **`{target_file}`:** {draft}")
            else:
                head_lines.append(f"> {draft}")

            end_line = len(head_lines)
            t["startLine"] = start_line
            t["endLine"] = end_line

            if target_file and repo_dir:
                local_target = os.path.join(repo_dir, target_file)
                if os.path.isfile(local_target):
                    try:
                        with open(local_target, "r", encoding="utf-8", errors="replace") as lf:
                            raw_file_lines = lf.read().splitlines()
                        preview_count = min(35, len(raw_file_lines))
                        ext = os.path.splitext(target_file)[1].lstrip(".") or "text"
                        head_lines.append("")
                        head_lines.append(
                            f"#### 📄 Reference Preview: `{target_file}` (lines 1–{preview_count} of {len(raw_file_lines)} in local workspace)"
                        )
                        head_lines.append(f"```{ext}")
                        for ln_idx, content_ln in enumerate(raw_file_lines[:preview_count], 1):
                            head_lines.append(f"{ln_idx:3d}: {content_ln}")
                        head_lines.append("```")
                    except Exception:
                        pass

            head_lines.append("")
            head_lines.append("---")
            head_lines.append("")

    head_file = os.path.join(head_root, TOP_LEVEL_COMMENTS_FILENAME)
    try:
        with open(head_file, "w", encoding="utf-8") as hf:
            hf.write("\n".join(head_lines).rstrip() + "\n")
    except Exception:
        pass


def render_cliffs_notes_markdown(session: Dict[str, Any]) -> str:
    sync_top_level_comments_file(session)
    pr = session.get("pr") or {}
    cn = session.get("cliffsNotes") or {}
    lines: List[str] = []
    pr_num = pr.get("number")
    title = pr.get("title") or "Local Branch Diff"
    url = pr.get("url") or ""
    author = pr.get("author") or "local"

    lines.append(f"# 📖 Quasar CliffsNotes: PR #{pr_num} — {title}" if pr_num else f"# 📖 Quasar CliffsNotes: {title}")
    if url:
        lines.append(f"**PR**: [#{pr_num}]({url}) • **Author**: `@{author}` • **Diff**: `+{pr.get('additions', 0)} / -{pr.get('deletions', 0)}` across `{pr.get('changedFilesCount', 0)}` files")
    else:
        head_ref = pr.get("headRefName") or "HEAD"
        base_ref = pr.get("baseRefName") or "main"
        repo_name = pr.get("repo") or ""
        lines.append(
            f"**Branch**: `{head_ref}` → `{base_ref}` • **Repo**: `{repo_name}` • **Author**: `{author}` • **Diff**: `+{pr.get('additions', 0)} / -{pr.get('deletions', 0)}` across `{pr.get('changedFilesCount', 0)}` files"
        )
    lines.append("")

    if session.get("analysisStatus") == "analyzing":
        lines.append("> [!NOTE]")
        lines.append("> **Deep Analysis in Progress**: Initial file snapshots and diff tour are ready now. Quasar's specialist review lenses are synthesizing CliffsNotes and candidate findings...")
        lines.append("")

    raw_tldr = (cn.get("tldr") or "").strip()
    raw_what = (cn.get("whatAndWhy") or "").strip()
    short_summary, extra_summary = _split_concise_and_detail(raw_tldr or raw_what, 190)

    lines.append("## ⚡ TL;DR")
    lines.append(short_summary or raw_tldr or raw_what)
    lines.append("")

    has_extra_overview = bool(
        (raw_what and raw_what != short_summary)
        or extra_summary
        or cn.get("keySymbols")
        or cn.get("designTradeoffs")
    )
    if has_extra_overview:
        lines.append("<details>")
        lines.append("<summary><strong>More context, key symbols & design trade-offs</strong></summary>")
        lines.append("")
        if raw_what and raw_what != short_summary:
            lines.append(raw_what)
            lines.append("")
        elif extra_summary:
            lines.append(extra_summary)
            lines.append("")
        if cn.get("keySymbols"):
            lines.append("| Symbol / File | Role in This Change |")
            lines.append("| :--- | :--- |")
            for sym in cn["keySymbols"]:
                lines.append(f"| `{sym.get('name', '')}` | {sym.get('role', '')} |")
            lines.append("")
        if cn.get("designTradeoffs"):
            lines.append("**Key Design Decisions & Trade-offs:**")
            for td in cn["designTradeoffs"]:
                lines.append(f"- {td}")
            lines.append("")
        lines.append("</details>")
        lines.append("")

    if cn.get("architectureDiagram"):
        lines.append("## 🧭 Architecture & Data Flow")
        diag = sanitize_mermaid_diagram(cn["architectureDiagram"])
        lines.append("```mermaid")
        lines.append(diag)
        lines.append("```")
        lines.append("")

    lines.append("## 📚 Guided Tour")
    for ch in session.get("chapters") or []:
        lines.append(f"### {ch.get('title')}")
        ch_short, ch_extra = _split_concise_and_detail(ch.get("summary") or "", 165)
        if ch_short:
            lines.append(f"*{ch_short}*")
            lines.append("")
        bullets = ch.get("cliffsNotesBullets") or []
        if ch_extra or bullets:
            lines.append("<details>")
            lines.append(f"<summary>Chapter notes ({len(bullets)} key point{'' if len(bullets) == 1 else 's'})</summary>")
            lines.append("")
            if ch_extra:
                lines.append(ch_extra)
                lines.append("")
            for b in bullets:
                lines.append(f"- {b}")
            lines.append("")
            lines.append("</details>")
            lines.append("")
        for stop in ch.get("stops") or []:
            head_path = stop.get("headSnapshotPath") or stop.get("localWorkspacePath") or stop.get("path")
            start_l = stop.get("startLine", 1)
            end_l = stop.get("endLine", start_l)
            raw_stop_note = stop.get("cliffsNotes") or stop.get("description") or ""
            stop_short, stop_extra = _split_concise_and_detail(raw_stop_note, 150)
            lines.append(
                f"- **[{stop.get('stopNumber')}] [{stop.get('title')}](file://{head_path}#L{start_l}-L{end_l})** — {stop_short}"
            )
            snippet_md = _read_stop_snippet_markdown(session, stop)
            if stop_extra or stop.get("whyItChanged") or stop.get("callersAndInvariants") or snippet_md:
                lines.append("  <details>")
                lines.append("  <summary>Context & code snippet</summary>")
                lines.append("")
                if stop_extra:
                    lines.append(f"  - **Details**: {stop_extra}")
                if stop.get("whyItChanged"):
                    lines.append(f"  - **Why it changed**: {stop['whyItChanged']}")
                if stop.get("callersAndInvariants"):
                    lines.append(f"  - **Callers & invariants**: {stop['callersAndInvariants']}")
                if snippet_md:
                    lines.append("")
                    for s_line in snippet_md.splitlines():
                        lines.append(f"  {s_line}")
                lines.append("")
                lines.append("  </details>")
        lines.append("")

    if session.get("coverageMatrix"):
        lines.append("<details>")
        lines.append(f"<summary><strong>🧪 Test Coverage & Hygiene Matrix ({len(session['coverageMatrix'])})</strong></summary>")
        lines.append("")
        lines.append("| Behavior / Change | Verification Status | Notes |")
        lines.append("| :--- | :--- | :--- |")
        for row in session["coverageMatrix"]:
            lines.append(f"| {row.get('behavior', '')} | {row.get('status', '')} | {row.get('notes', '')} |")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    uncertain_threads = [
        t
        for t in (session.get("threads") or [])
        if t.get("status") == "uncertain"
    ]
    if uncertain_threads:
        lines.append("## 🤔 Needs Your Preference")
        for idx, t in enumerate(uncertain_threads, 1):
            start_l = t.get("startLine", 1)
            end_l = t.get("endLine", start_l)
            if t.get("isTopLevel") or not t.get("path"):
                tf = t.get("targetFile") or ""
                repo_file = os.path.join(session.get("repoDir", ""), tf) if tf else ""
                if tf and repo_file and os.path.isfile(repo_file):
                    loc_md = f"[📌 Top-Level • {os.path.basename(tf)}](file://{repo_file}#L{start_l}-L{end_l})"
                elif tf:
                    loc_md = f"`📌 Top-Level • {os.path.basename(tf)}`"
                else:
                    loc_md = "`📌 Top-Level PR Comment`"
            else:
                head_path = os.path.join(session.get("headSnapshotRoot", ""), t.get("path", ""))
                loc_md = f"[{os.path.basename(t.get('path', ''))}:L{start_l}-{end_l}](file://{head_path}#L{start_l}-L{end_l})"
            lines.append(f"- **❓ #{idx} {loc_md} — {t.get('title', 'Uncertain Finding')}**")
            if t.get("uncertaintyQuestion"):
                lines.append(f"  - *Question*: {t['uncertaintyQuestion']}")
            lines.append("  <details>")
            lines.append("  <summary>Context & draft comment</summary>")
            lines.append("")
            if t.get("whyItMatters"):
                lines.append(f"  - **Trade-off Context**: {t['whyItMatters']}")
            lines.append(f"  > {t.get('draftComment', '')}")
            lines.append("")
            lines.append("  </details>")
        lines.append("")

    if session.get("topLevelComment"):
        top_md_path = os.path.join(session.get("headSnapshotRoot", ""), TOP_LEVEL_COMMENTS_FILENAME)
        lines.append(f"## 📌 Top-Level Review Comment ([`{TOP_LEVEL_COMMENTS_FILENAME}`](file://{top_md_path}))")
        lines.append("```markdown")
        lines.append(session["topLevelComment"])
        lines.append("```")
        lines.append("")

    candidate_threads = [
        t
        for t in (session.get("threads") or [])
        if t.get("kind") in ("ai_candidate", "user_staged") and t.get("status") not in ("uncertain", "dismissed")
    ]
    if candidate_threads:
        lines.append("## 🎯 Review Findings")
        for idx, t in enumerate(candidate_threads, 1):
            start_l = t.get("startLine", 1)
            end_l = t.get("endLine", start_l)
            sev = t.get("severity", "DESIGN")
            badge = "🔴 Blocking" if "BLOCK" in sev.upper() else ("🟡 Design" if "DESIGN" in sev.upper() else "🟢 Nit")
            if t.get("isTopLevel") or not t.get("path"):
                tf = t.get("targetFile") or ""
                repo_file = os.path.join(session.get("repoDir", ""), tf) if tf else ""
                if tf and repo_file and os.path.isfile(repo_file):
                    loc_md = f"[📌 Top-Level • {os.path.basename(tf)}](file://{repo_file}#L{start_l}-L{end_l})"
                elif tf:
                    loc_md = f"`📌 Top-Level • {os.path.basename(tf)}`"
                else:
                    loc_md = "`📌 Top-Level PR Comment`"
            else:
                head_path = os.path.join(session.get("headSnapshotRoot", ""), t.get("path", ""))
                loc_md = f"[{os.path.basename(t.get('path', ''))}:L{start_l}-{end_l}](file://{head_path}#L{start_l}-L{end_l})"
            why_short, why_extra = _split_concise_and_detail(t.get("whyItMatters") or "", 140)
            lines.append(f"- **#{idx} [{badge}] {loc_md} — {t.get('title', 'Review Finding')}**")
            if why_short:
                lines.append(f"  - {why_short}")
            lines.append("  <details>")
            lines.append("  <summary>Draft comment & details</summary>")
            lines.append("")
            if why_extra:
                lines.append(f"  - **Context**: {why_extra}")
            lines.append(f"  > {t.get('draftComment', '')}")
            lines.append("")
            lines.append("  </details>")
        lines.append("")

    existing_gh_threads = [
        t
        for t in (session.get("threads") or [])
        if t.get("kind") == "github_existing" and t.get("status") != "dismissed"
    ]
    if existing_gh_threads:
        lines.append("## 💬 Existing GitHub PR Comments (Triage & Recommendations)")
        lines.append("| Assessment | Location | Comment & Quasar Triage |")
        lines.append("| :--- | :--- | :--- |")
        for t in existing_gh_threads:
            head_path = os.path.join(session.get("headSnapshotRoot", ""), t.get("path", ""))
            start_l = t.get("startLine", 1)
            end_l = t.get("endLine", start_l)
            badge = (
                "✅ Fixed"
                if t.get("status") == "fixed"
                else (t.get("triageBadge") or "👀 Needs Attention")
            )
            summary_note = (
                t.get("fixedSummary")
                if t.get("status") == "fixed"
                else (t.get("triageSummary") or t.get("whyItMatters") or "")
            )
            title_clean = (t.get("title") or "GitHub Comment").replace("|", "\\|")
            summary_clean = summary_note.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| **{badge}** | [{os.path.basename(t.get('path', ''))}:L{start_l}](file://{head_path}#L{start_l}-L{end_l}) | **{title_clean}** — {summary_clean} |"
            )
        lines.append("")

    prefs = session.get("reviewerPreferences") or {}
    if prefs.get("activeRulesCount", 0) > 0:
        lines.append("## 🧠 Learned Reviewer Preferences Applied")
        for r in prefs.get("preferRules") or []:
            lines.append(f"- ✅ **Prefer/Flag**: {r}")
        for r in prefs.get("avoidRules") or []:
            lines.append(f"- 🚫 **Avoid/Skip**: {r}")
        for r in prefs.get("customRules") or []:
            lines.append(f"- 📌 **Guideline**: {r}")
        lines.append("")

    if session.get("dismissedChecks"):
        lines.append("## ✅ Checked & Dismissed (Verified Safe)")
        for item in session["dismissedChecks"]:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


def is_github_pr_arg(pr_arg: Optional[str]) -> bool:
    if not pr_arg:
        return False
    s = pr_arg.strip()
    if re.search(r"github\.com/[^/]+/[^/]+/pull/\d+", s):
        return True
    if re.match(r"^#?\d+$", s):
        return True
    return False


def resolve_local_repo_dir(provided_dir: Optional[str], branch_hint: Optional[str] = None) -> str:
    """Finds the git repository root for a local branch review."""
    if provided_dir:
        expanded = os.path.abspath(os.path.expanduser(provided_dir))
        if os.path.isdir(expanded):
            top = run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=expanded, check=False)
            if top.returncode == 0 and top.stdout.strip():
                return top.stdout.strip()

    cwd_top = run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=os.getcwd(), check=False)
    cwd_repo = cwd_top.stdout.strip() if cwd_top.returncode == 0 and cwd_top.stdout.strip() else None

    if cwd_repo and (not branch_hint or branch_hint == "HEAD"):
        return cwd_repo

    if cwd_repo and branch_hint:
        verify = run_cmd(["git", "rev-parse", "--verify", "--quiet", branch_hint], cwd=cwd_repo, check=False)
        if verify.returncode == 0:
            return cwd_repo

    # Search common workspace directories if branch_hint lives in another local repo
    home = os.path.expanduser("~")
    search_roots = [
        os.path.join(home, "work"),
        os.path.join(home, "dev"),
        os.path.join(home, "src"),
        os.path.join(home, "projects"),
    ]
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        try:
            for entry in os.scandir(root):
                if not entry.is_dir():
                    continue
                if os.path.isdir(os.path.join(entry.path, ".git")):
                    if branch_hint and branch_hint != "HEAD":
                        v = run_cmd(
                            ["git", "rev-parse", "--verify", "--quiet", branch_hint],
                            cwd=entry.path,
                            check=False,
                        )
                        if v.returncode == 0:
                            return os.path.abspath(entry.path)
        except Exception:
            pass

    return cwd_repo or os.path.abspath(provided_dir or os.getcwd())


def detect_base_ref(repo_dir: str, explicit_base: Optional[str], head_ref: str) -> str:
    """Detects the best base branch (e.g. upstream/main, origin/main, main) to diff against."""
    if explicit_base and explicit_base != "auto":
        v = run_cmd(["git", "rev-parse", "--verify", "--quiet", explicit_base], cwd=repo_dir, check=False)
        if v.returncode == 0:
            return explicit_base

    candidates: List[str] = []
    for sym in ["refs/remotes/upstream/HEAD", "refs/remotes/origin/HEAD"]:
        res = run_cmd(["git", "symbolic-ref", "--quiet", "--short", sym], cwd=repo_dir, check=False)
        if res.returncode == 0 and res.stdout.strip():
            candidates.append(res.stdout.strip())

    for fallback in [
        "upstream/main",
        "origin/main",
        "main",
        "upstream/master",
        "origin/master",
        "master",
        "origin/develop",
        "develop",
    ]:
        if fallback not in candidates:
            candidates.append(fallback)

    valid_candidates: List[Tuple[int, str]] = []
    for cand in candidates:
        v = run_cmd(["git", "rev-parse", "--verify", "--quiet", cand], cwd=repo_dir, check=False)
        if v.returncode != 0:
            continue
        mb = run_cmd(["git", "merge-base", cand, head_ref], cwd=repo_dir, check=False)
        if mb.returncode == 0 and mb.stdout.strip():
            cnt_res = run_cmd(
                ["git", "rev-list", "--count", f"{mb.stdout.strip()}..{head_ref}"],
                cwd=repo_dir,
                check=False,
            )
            if cnt_res.returncode == 0 and cnt_res.stdout.strip().isdigit():
                cnt = int(cnt_res.stdout.strip())
                valid_candidates.append((cnt, cand))

    # Prefer the base branch with the fewest positive commits since branching (most direct ancestor)
    positive = [ item for item in valid_candidates if item[0] > 0 ]
    if positive:
        positive.sort(key=lambda x: x[0])
        return positive[0][1]

    if valid_candidates:
        return valid_candidates[0][1]

    return explicit_base or "HEAD~1"


def detect_pr_ownership(metadata: Dict[str, Any], repo_dir: str) -> Tuple[bool, str]:
    """Determines whether the active PR or branch is owned by the current user."""
    if metadata.get("mode") == "local_branch" or not metadata.get("number"):
        return True, "local_branch"

    author = (metadata.get("author") or "").strip().lower()
    head_ref = (metadata.get("headRefName") or "").strip()
    head_sha = (metadata.get("headSha") or "").strip()

    if repo_dir and os.path.isdir(os.path.join(repo_dir, ".git")):
        cur_branch = run_cmd(["git", "branch", "--show-current"], cwd=repo_dir, check=False).stdout.strip()
        if cur_branch and head_ref and cur_branch == head_ref:
            return True, f"checked_out_branch ({cur_branch})"
        cur_sha = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=False).stdout.strip()
        if cur_sha and head_sha and cur_sha == head_sha:
            return True, "checked_out_head_sha"

    candidate_identities = set()
    for env_key in ["GITHUB_USER", "GH_USER", "USER", "LOGNAME"]:
        val = (os.environ.get(env_key) or "").strip().lower()
        if val:
            candidate_identities.add(val)

    home_user = os.path.basename(os.path.expanduser("~")).strip().lower()
    if home_user:
        candidate_identities.add(home_user)

    if repo_dir and os.path.isdir(repo_dir):
        for cfg_key in ["github.user", "user.name", "user.email"]:
            val = run_cmd(["git", "config", cfg_key], cwd=repo_dir, check=False).stdout.strip().lower()
            if val:
                candidate_identities.add(val)
                candidate_identities.add(re.sub(r"[^a-z0-9]", "", val))
                if "@" in val:
                    local_part = val.split("@", 1)[0]
                    candidate_identities.add(local_part)
                    candidate_identities.add(re.sub(r"[^a-z0-9]", "", local_part))

    stored_login = (load_stored_github_auth().get("login") or "").strip().lower()
    if stored_login:
        candidate_identities.add(stored_login)

    gh = find_gh_binary()
    if gh:
        try:
            res = subprocess.run([gh, "api", "user", "--jq", ".login"], capture_output=True, text=True, timeout=4)
            if res.returncode == 0 and res.stdout.strip():
                candidate_identities.add(res.stdout.strip().lower())
        except Exception:
            pass

    if author:
        norm_author = re.sub(r"[^a-z0-9]", "", author)
        if author in candidate_identities or (norm_author and norm_author in candidate_identities):
            return True, f"author_match (@{metadata.get('author')})"

    return False, "external_pr"


def inspect_local_repo_branch_state(metadata: Dict[str, Any], repo_dir: str) -> Dict[str, Any]:
    """Checks whether `repo_dir` is a matching local clone for this PR and what branch is currently checked out."""
    owner = str(metadata.get("owner") or "").strip()
    repo = str(metadata.get("repo") or "").strip()
    pr_num = int(metadata.get("number") or 0)
    head_ref = str(metadata.get("headBranch") or metadata.get("headRefName") or "").replace(" + working tree", "").strip()
    is_owned = bool(metadata.get("isOwnedByUser"))

    if not repo_dir or not os.path.isdir(os.path.join(repo_dir, ".git")):
        return {
            "hasMatchingLocalClone": False,
            "localCurrentBranch": "",
            "isOnPrBranch": False,
            "hasUncommittedChanges": False,
            "needsBranchSwitch": False,
        }

    is_matching_clone = True if owner == "local" or not pr_num else repo_matches_remote(repo_dir, owner, repo)
    cur_branch = run_cmd(["git", "branch", "--show-current"], cwd=repo_dir, check=False).stdout.strip()
    has_uncommitted = bool(
        run_cmd(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo_dir, check=False).stdout.strip()
    )
    is_on_pr_branch = bool(head_ref and cur_branch and cur_branch == head_ref)
    needs_switch = bool(
        is_owned
        and pr_num > 0
        and is_matching_clone
        and head_ref
        and not is_on_pr_branch
    )
    return {
        "hasMatchingLocalClone": is_matching_clone,
        "localCurrentBranch": cur_branch,
        "isOnPrBranch": is_on_pr_branch,
        "hasUncommittedChanges": has_uncommitted,
        "needsBranchSwitch": needs_switch,
    }


def checkout_pr_branch_in_repo(
    repo_dir: str,
    owner: str,
    repo: str,
    pr_num: int,
    head_ref: str,
    stash_if_dirty: bool = False,
) -> Dict[str, Any]:
    """Switches `repo_dir` to the PR's head branch (`head_ref`), fetching it if needed."""
    if not repo_dir or not os.path.isdir(os.path.join(repo_dir, ".git")):
        return {"ok": False, "error": f"No valid git repository found at {repo_dir}"}
    if not head_ref:
        return {"ok": False, "error": "PR has no headRefName to check out."}

    prev_branch = run_cmd(["git", "branch", "--show-current"], cwd=repo_dir, check=False).stdout.strip()
    if prev_branch == head_ref:
        return {
            "ok": True,
            "alreadyOnBranch": True,
            "previousBranch": prev_branch,
            "checkedOutBranch": head_ref,
            "repoDir": repo_dir,
        }

    dirty_status = run_cmd(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo_dir, check=False).stdout.strip()
    stashed = False
    if dirty_status and stash_if_dirty:
        stash_res = run_cmd(
            ["git", "stash", "push", "-m", f"Quasar auto-stash before switching to PR #{pr_num} ({head_ref})"],
            cwd=repo_dir,
            check=False,
        )
        stashed = stash_res.returncode == 0

    # 1. Check if local branch already exists
    has_local = run_cmd(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{head_ref}"], cwd=repo_dir, check=False).returncode == 0
    if has_local:
        co = run_cmd(["git", "checkout", head_ref], cwd=repo_dir, check=False)
        if co.returncode == 0:
            return {
                "ok": True,
                "previousBranch": prev_branch,
                "checkedOutBranch": head_ref,
                "stashed": stashed,
                "repoDir": repo_dir,
            }

    # 2. Check remote tracking branches (origin/<head_ref> or upstream/<head_ref>)
    for remote_name in ["origin", "upstream"]:
        has_remote_ref = (
            run_cmd(
                ["git", "show-ref", "--verify", "--quiet", f"refs/remotes/{remote_name}/{head_ref}"],
                cwd=repo_dir,
                check=False,
            ).returncode
            == 0
        )
        if has_remote_ref:
            co = run_cmd(["git", "checkout", "-b", head_ref, "--track", f"{remote_name}/{head_ref}"], cwd=repo_dir, check=False)
            if co.returncode == 0:
                return {
                    "ok": True,
                    "previousBranch": prev_branch,
                    "checkedOutBranch": head_ref,
                    "stashed": stashed,
                    "repoDir": repo_dir,
                }

    # 3. Fetch PR head ref from remote (`pull/<pr_num>/head:<head_ref>`)
    if pr_num > 0:
        for remote_name in ["upstream", "origin"]:
            fetch_res = run_cmd(
                ["git", "fetch", remote_name, f"pull/{pr_num}/head:{head_ref}"],
                cwd=repo_dir,
                check=False,
            )
            if fetch_res.returncode == 0:
                co = run_cmd(["git", "checkout", head_ref], cwd=repo_dir, check=False)
                if co.returncode == 0:
                    return {
                        "ok": True,
                        "previousBranch": prev_branch,
                        "checkedOutBranch": head_ref,
                        "stashed": stashed,
                        "repoDir": repo_dir,
                    }

    # 4. Fallback to `gh pr checkout`
    gh = find_gh_binary()
    if gh and pr_num > 0:
        gh_co = run_cmd([gh, "pr", "checkout", str(pr_num)], cwd=repo_dir, check=False)
        if gh_co.returncode == 0:
            now_branch = run_cmd(["git", "branch", "--show-current"], cwd=repo_dir, check=False).stdout.strip() or head_ref
            return {
                "ok": True,
                "previousBranch": prev_branch,
                "checkedOutBranch": now_branch,
                "stashed": stashed,
                "repoDir": repo_dir,
            }

    err_detail = (
        "Uncommitted changes in working tree prevented branch checkout."
        if dirty_status and not stashed
        else f"Could not check out branch '{head_ref}' in {repo_dir}."
    )
    return {
        "ok": False,
        "hasUncommittedChanges": bool(dirty_status and not stashed),
        "error": err_detail,
        "repoDir": repo_dir,
    }


def create_tutorial_sandbox_session(
    ws_root: str,
    state_dir: str,
    active_session_path: str,
    cliffs_notes_md_path: str,
    comments_only: bool = False,
) -> Dict[str, Any]:
    """Creates an instant, self-contained interactive sandbox PR review session for learning Quasar."""
    session_id = "quasar-tutorial-sandbox"
    sandbox_root = os.path.join(REVIEW_STATE_DIR, "tutorial_sandbox")
    repo_dir = os.path.join(sandbox_root, "repo")
    snap_dir = os.path.join(SNAPSHOTS_DIR, session_id)
    base_root = os.path.join(snap_dir, "base")
    head_root = os.path.join(snap_dir, "head")

    for d in (
        os.path.join(repo_dir, "src"),
        os.path.join(repo_dir, "tests"),
        os.path.join(base_root, "src"),
        os.path.join(base_root, "tests"),
        os.path.join(head_root, "src"),
        os.path.join(head_root, "tests"),
    ):
        os.makedirs(d, exist_ok=True)

    base_checkout_ts = """export interface CartItem {
  sku: string;
  priceCents: number;
  quantity: number;
}

export function calculateSubtotal(items: CartItem[]): number {
  return items.reduce((sum, item) => sum + item.priceCents * item.quantity, 0);
}

export function applyPromoDiscount(
  items: CartItem[]
): { totalCents: number; discountRatio: number } {
  const subtotal = calculateSubtotal(items);
  return {
    totalCents: subtotal,
    discountRatio: 0,
  };
}
"""

    head_checkout_ts = """export interface CartItem {
  sku: string;
  priceCents: number;
  quantity: number;
}

export interface PromoRule {
  code: string;
  discountCents: number;
  expiresIso?: string;
}

const promoCache = new Map<string, PromoRule>();

export function calculateSubtotal(items: CartItem[]): number {
  // Sort items highest-price first before summing
  items.sort((a, b) => b.priceCents - a.priceCents);
  return items.reduce((sum, item) => sum + item.priceCents * item.quantity, 0);
}

export function applyPromoDiscount(
  items: CartItem[],
  promo?: PromoRule
): { totalCents: number; discountRatio: number } {
  const subtotal = calculateSubtotal(items);
  const discount = promo ? Math.min(subtotal, promo.discountCents) : 0;
  const discountRatio = discount / subtotal;
  return {
    totalCents: Math.max(0, subtotal - discount),
    discountRatio,
  };
}

function parseCouponExpiry(expiresIso?: string): number {
  return expiresIso ? Date.parse(expiresIso) : Number.POSITIVE_INFINITY;
}

export function lookupActivePromo(rawCode: string, nowMs = Date.now()): PromoRule | undefined {
  const rule = promoCache.get(rawCode);
  if (!rule) return undefined;
  if (parseCouponExpiry(rule.expiresIso) < nowMs) {
    promoCache.delete(rawCode);
    return undefined;
  }
  return rule;
}

export function registerPromoRule(rule: PromoRule): void {
  promoCache.set(rule.code.trim().toUpperCase(), rule);
}
"""

    base_test_ts = """import { applyPromoDiscount } from '../src/checkout_service';

describe('checkout_service', () => {
  it('computes subtotal without promo', () => {
    const res = applyPromoDiscount([{ sku: 'coffee', priceCents: 500, quantity: 2 }]);
    expect(res.totalCents).toBe(1000);
  });
});
"""

    head_test_ts = """import { applyPromoDiscount, lookupActivePromo, registerPromoRule } from '../src/checkout_service';

describe('checkout_service promo codes', () => {
  it('applies fixed promo discount to cart subtotal', () => {
    registerPromoRule({ code: 'SAVE200', discountCents: 200 });
    const promo = lookupActivePromo('SAVE200');
    const res = applyPromoDiscount(
      [{ sku: 'coffee', priceCents: 500, quantity: 2 }],
      promo
    );
    expect(res.totalCents).toBe(800);
    expect(res.discountRatio).toBeCloseTo(0.2);
  });

  it('evicts expired promo rules on lookup', () => {
    registerPromoRule({
      code: 'EXPIRED10',
      discountCents: 100,
      expiresIso: '2020-01-01T00:00:00Z',
    });
    expect(lookupActivePromo('EXPIRED10')).toBeUndefined();
  });
});
"""

    with open(os.path.join(base_root, "src", "checkout_service.ts"), "w", encoding="utf-8") as f:
        f.write(base_checkout_ts)
    with open(os.path.join(base_root, "tests", "checkout_service.test.ts"), "w", encoding="utf-8") as f:
        f.write(base_test_ts)
    for target_root in (head_root, repo_dir):
        with open(os.path.join(target_root, "src", "checkout_service.ts"), "w", encoding="utf-8") as f:
            f.write(head_checkout_ts)
        with open(os.path.join(target_root, "tests", "checkout_service.test.ts"), "w", encoding="utf-8") as f:
            f.write(head_test_ts)

    # Initialize git repo in sandbox repo_dir if not yet initialized so git status/diff works
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        run_cmd(["git", "init"], cwd=repo_dir, check=False)
        run_cmd(["git", "add", "."], cwd=repo_dir, check=False)
        run_cmd(
            ["git", "-c", "user.name=Quasar Tutorial", "-c", "user.email=tutorial@quasar.local", "commit", "-m", "feat(checkout): promo code discount & cache"],
            cwd=repo_dir,
            check=False,
        )

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    prefs_summary: Dict[str, Any] = {}
    try:
        from manage_preferences import build_prompt_summary, load_preferences

        prefs_summary = build_prompt_summary(load_preferences(), "quasar-sandbox")
    except Exception:
        prefs_summary = {}

    chapters = (
        []
        if comments_only
        else [
            {
                "id": "ch-tutorial-1",
                "title": "Chapter 1: Promo Calculation & Cache Lookup",
                "summary": "Adds promo discount math and an in-memory promo rule cache with expiration.",
                "cliffsNotesBullets": [
                    "Try clicking Stop 1.1 or 1.2 below (or pressing Alt+Down) to open the side-by-side diff.",
                    "Hover over any changed function in the diff editor to see native CliffsNotes hover cards.",
                ],
                "stops": [
                    {
                        "id": "stop-1-1",
                        "stopNumber": "1.1",
                        "title": "checkout_service.ts:15-32",
                        "description": "Subtotal sorting & promo discount ratio math",
                        "path": "src/checkout_service.ts",
                        "startLine": 15,
                        "endLine": 32,
                        "baseSnapshotPath": os.path.join(base_root, "src", "checkout_service.ts"),
                        "headSnapshotPath": os.path.join(head_root, "src", "checkout_service.ts"),
                        "localWorkspacePath": os.path.join(repo_dir, "src", "checkout_service.ts"),
                        "cliffsNotes": "Computes cart subtotal and applies promo discount, returning totalCents and discountRatio.",
                        "whyItChanged": "Supports fixed-amount promotional discounts during checkout.",
                        "callersAndInvariants": "Called by CheckoutController. Contains 2 intentional bugs for you to test in this tutorial!",
                        "read": False,
                    },
                    {
                        "id": "stop-1-2",
                        "stopNumber": "1.2",
                        "title": "checkout_service.ts:34-50",
                        "description": "In-memory promo cache registration & expiry check",
                        "path": "src/checkout_service.ts",
                        "startLine": 34,
                        "endLine": 50,
                        "baseSnapshotPath": os.path.join(base_root, "src", "checkout_service.ts"),
                        "headSnapshotPath": os.path.join(head_root, "src", "checkout_service.ts"),
                        "localWorkspacePath": os.path.join(repo_dir, "src", "checkout_service.ts"),
                        "cliffsNotes": "Registers uppercase promo rules in `promoCache` and evicts expired rules on lookup.",
                        "whyItChanged": "Avoids repeated promo table lookups for active codes.",
                        "callersAndInvariants": "`registerPromoRule` normalizes keys with `.trim().toUpperCase()`, but `lookupActivePromo` does not.",
                        "read": False,
                    },
                ],
            },
            {
                "id": "ch-tutorial-2",
                "title": "Chapter 2: Unit Tests & Verification",
                "summary": "Tests basic promo discount application and expired rule eviction.",
                "cliffsNotesBullets": [
                    "Covers happy-path discount math and expired promo eviction.",
                    "Leaves zero-subtotal carts and lowercase promo codes untested.",
                ],
                "stops": [
                    {
                        "id": "stop-2-1",
                        "stopNumber": "2.1",
                        "title": "checkout_service.test.ts:1-24",
                        "description": "Unit tests for SAVE200 and EXPIRED10 promo codes",
                        "path": "tests/checkout_service.test.ts",
                        "startLine": 1,
                        "endLine": 24,
                        "baseSnapshotPath": os.path.join(base_root, "tests", "checkout_service.test.ts"),
                        "headSnapshotPath": os.path.join(head_root, "tests", "checkout_service.test.ts"),
                        "localWorkspacePath": os.path.join(repo_dir, "tests", "checkout_service.test.ts"),
                        "cliffsNotes": "Adds Jest unit tests for `applyPromoDiscount` and `lookupActivePromo`.",
                        "whyItChanged": "Verifies new promo code behavior.",
                        "callersAndInvariants": "Includes an uncertain preference question on line 14 for you to calibrate!",
                        "read": False,
                    }
                ],
            },
        ]
    )

    threads = [
        {
            "id": "tutorial-finding-1",
            "kind": "ai_candidate",
            "severity": "BLOCKING",
            "status": "staged",
            "selectedForFix": True,
            "uncertain": False,
            "path": "src/checkout_service.ts",
            "startLine": 25,
            "endLine": 31,
            "title": "Division by zero (NaN) when cart subtotal is $0",
            "whyItMatters": "When `subtotal === 0` (e.g. an empty cart or free sample item), `discount / subtotal` on line 27 evaluates to `0 / 0 = NaN`. Try clicking '💬 Ask Agent to Change Comment' to make this comment shorter, or '🔧 Fix with Agent' to patch the file!",
            "draftComment": "If `subtotal` is `0` (for example on an empty cart or 100%-free sample item), `discount / subtotal` on line 27 evaluates to `NaN`. Should we guard with `const discountRatio = subtotal > 0 ? discount / subtotal : 0;`?",
            "messages": [
                {
                    "author": "Quasar",
                    "body": (
                        "**Division by zero (NaN) when cart subtotal is $0**\n\n"
                        "*CliffsNotes Context*: When `subtotal === 0` (e.g. an empty cart or free sample item), `discount / subtotal` on line 27 evaluates to `0 / 0 = NaN`.\n\n"
                        "💡 **Tutorial Tip**: Click **💬 Ask Agent to Change Comment** above (or type in the reply box below) and tell Quasar: *\"Make this shorter and don't include a code snippet\"* — or click **🔧 Fix This Issue with Quasar Now** to watch the agent fix `src/checkout_service.ts`!\n\n"
                        "---\n"
                        "**Draft GitHub Comment**:\n"
                        "> If `subtotal` is `0` (for example on an empty cart or 100%-free sample item), `discount / subtotal` on line 27 evaluates to `NaN`. Should we guard with `const discountRatio = subtotal > 0 ? discount / subtotal : 0;`?"
                    ),
                    "timestamp": now_iso,
                }
            ],
        },
        {
            "id": "tutorial-finding-2",
            "kind": "ai_candidate",
            "severity": "NIT",
            "status": "uncertain",
            "selectedForFix": False,
            "uncertain": True,
            "preferenceCategory": "test_strictness",
            "uncertaintyQuestion": "Do you want Quasar to flag missing direct unit tests for private helper functions (like `parseCouponExpiry`) when the public API is already tested?",
            "preferenceOptions": [
                "Flag this & similar cases in future reviews",
                "Skip this pattern in future reviews",
                "Keep this comment once, don't save a rule",
            ],
            "path": "tests/checkout_service.test.ts",
            "startLine": 14,
            "endLine": 23,
            "title": "No direct unit test for malformed dates in private parseCouponExpiry",
            "whyItMatters": "💡 Tutorial Tip: Click '🤔 Calibrate Preference' (or 👍 / 👎) on this thread to teach Quasar whether you care about unit tests for private helpers.",
            "draftComment": "Consider adding a test case where `expiresIso` is an invalid date string to verify how `parseCouponExpiry` behaves.",
            "messages": [
                {
                    "author": "Quasar",
                    "body": (
                        "### 🤔 Preference Check: No direct unit test for malformed dates in private parseCouponExpiry\n\n"
                        "**Question for You**: *Do you want Quasar to flag missing direct unit tests for private helper functions (like `parseCouponExpiry`) when the public API is already tested?*\n\n"
                        "*Context & Trade-off*: 💡 **Tutorial Tip**: Click **🤔 Calibrate Preference** (or 👍 / 👎) in the toolbar above to teach Quasar your personal preference!\n\n"
                        "---\n"
                        "**Candidate GitHub Comment (if kept)**:\n"
                        "> Consider adding a test case where `expiresIso` is an invalid date string to verify how `parseCouponExpiry` behaves."
                    ),
                    "timestamp": now_iso,
                }
            ],
        },
        {
            "id": "tutorial-finding-3",
            "kind": "github_existing",
            "severity": "EXISTING",
            "status": "existing",
            "selectedForFix": False,
            "author": "teammate_alex",
            "triageLeftTag": "🛠️ [Should Fix]",
            "triageStatus": "recommended_fix",
            "triageCategory": "actionable_improvement",
            "triageBadge": "🛠️ Good Catch — Should Fix",
            "triageLabel": "🛠️ Good Comment — Suggested Fix Should Be Made",
            "triageSummary": "@teammate_alex spotted that `registerPromoRule` uppercases the key while `lookupActivePromo` uses raw `rawCode`.",
            "recommendedForFix": True,
            "path": "src/checkout_service.ts",
            "startLine": 38,
            "endLine": 45,
            "title": "🛠️ [Should Fix] Normalize rawCode.trim().toUpperCase() in lookupActivePromo",
            "whyItMatters": "🛠️ Good Comment — Suggested Fix Should Be Made — @teammate_alex spotted that `registerPromoRule` uppercases the key while `lookupActivePromo` uses raw `rawCode`.",
            "draftComment": "`registerPromoRule` stores keys with `rule.code.trim().toUpperCase()`, so calling `lookupActivePromo('save200')` in lowercase will miss the cache. We should normalize `rawCode.trim().toUpperCase()` here too.",
            "messages": [
                {
                    "author": "@teammate_alex (GitHub)",
                    "body": "`registerPromoRule` stores keys with `rule.code.trim().toUpperCase()`, so calling `lookupActivePromo('save200')` in lowercase will miss the cache. We should normalize `rawCode.trim().toUpperCase()` here too.",
                    "timestamp": now_iso,
                },
                {
                    "author": "Quasar Triage (🛠️ Good Catch — Should Fix)",
                    "body": "**🛠️ Good Comment — Suggested Fix Should Be Made**\n\n@teammate_alex spotted that `registerPromoRule` uppercases the key while `lookupActivePromo` uses raw `rawCode`.",
                    "timestamp": now_iso,
                    "isTriageNote": True,
                },
            ],
        },
        {
            "id": "tutorial-finding-4",
            "kind": "ai_candidate",
            "severity": "DESIGN",
            "status": "staged",
            "selectedForFix": False,
            "uncertain": False,
            "path": "src/checkout_service.ts",
            "startLine": 15,
            "endLine": 19,
            "title": "In-place array sort mutates caller's CartItem[]",
            "whyItMatters": "`items.sort(...)` mutates the input array passed by the caller, which can reorder items in the UI cart unexpectedly.",
            "draftComment": "`items.sort(...)` mutates the caller's `items` array in-place (and sorting isn't needed for addition). Can we remove the sort here?",
            "messages": [
                {
                    "author": "Quasar",
                    "body": (
                        "**In-place array sort mutates caller's CartItem[]**\n\n"
                        "*CliffsNotes Context*: `items.sort(...)` mutates the input array passed by the caller, which can reorder items in the UI cart unexpectedly.\n\n"
                        "---\n"
                        "**Draft GitHub Comment**:\n"
                        "> `items.sort(...)` mutates the caller's `items` array in-place (and sorting isn't needed for addition). Can we remove the sort here?"
                    ),
                    "timestamp": now_iso,
                }
            ],
        },
    ]

    win_id = os.environ.get("QUASAR_WINDOW_ID") or ""
    win_pid = int(os.environ.get("QUASAR_WINDOW_PID") or 0) if (os.environ.get("QUASAR_WINDOW_PID") or "").isdigit() else 0

    session: Dict[str, Any] = {
        "sessionId": session_id,
        "isTutorial": True,
        "windowId": win_id,
        "windowPid": win_pid,
        "updatedAt": now_iso,
        "analysisStatus": "ready",
        "effortLevel": "medium",
        "commentsOnly": comments_only,
        "workspaceDir": ws_root,
        "stateDir": state_dir,
        "repoDir": repo_dir,
        "baseSnapshotRoot": base_root,
        "headSnapshotRoot": head_root,
        "scratchDir": state_dir,
        "cliffsNotesMarkdownPath": cliffs_notes_md_path,
        "pr": {
            "mode": "tutorial",
            "owner": "quasar-demo",
            "repo": "checkout-service",
            "number": 42,
            "title": "🎓 [Interactive Tutorial] feat(checkout): Promo code discounts & cache",
            "url": "",
            "state": "OPEN",
            "author": "you",
            "baseRefName": "main",
            "baseSha": "base-tutorial",
            "headRefName": "feat/promo-codes",
            "headBranch": "feat/promo-codes",
            "headSha": "head-tutorial",
            "isOwnedByUser": True,
            "needsBranchSwitch": False,
            "additions": 48,
            "deletions": 6,
            "changedFilesCount": 2,
            "body": "Interactive Quasar Sandbox Tutorial PR! Use this live playground to try Guided Tour stops, Ask Agent to Change Comment, Calibrate Preferences, Fix with Agent, and Comments-Only Mode.",
        },
        "reviewerPreferences": prefs_summary,
        "cliffsNotes": {
            "tldr": (
                "Comments-Only Tutorial Mode — Study guide skipped; use Review Comments & Cart to revise, fix, or stage comments."
                if comments_only
                else "Adds fixed-amount promo code discounts and an expiring in-memory promo lookup cache to `checkout_service.ts`."
            ),
            "whatAndWhy": (
                "Study guide skipped (Comments-Only mode)."
                if comments_only
                else "Introduces `PromoRule`, updates `applyPromoDiscount` to compute discounted totals and ratios, and adds `registerPromoRule` / `lookupActivePromo` with expiration eviction. Intentionally includes a divide-by-zero bug, an in-place array mutation, an unnormalized cache key, and a preference question so you can test every Quasar feature hands-on."
            ),
            "architectureDiagram": (
                ""
                if comments_only
                else 'flowchart LR\n  A["registerPromoRule ( checkout_service.ts )"] --> B["promoCache ( checkout_service.ts )"]\n  B --> C["lookupActivePromo ( checkout_service.ts )"]\n  C --> D["applyPromoDiscount ( checkout_service.ts )"]\n  D --> E["calculateSubtotal ( checkout_service.ts )"]'
            ),
            "keySymbols": [
                {"name": "applyPromoDiscount", "role": "Computes subtotal, clamps promo discount, and returns totalCents + discountRatio."},
                {"name": "lookupActivePromo", "role": "Fetches a PromoRule from promoCache and evicts expired entries."},
                {"name": "calculateSubtotal", "role": "Sums item prices * quantities across the cart."},
            ],
            "designTradeoffs": [
                "In-memory `promoCache` is fast for single-instance services, though case-sensitivity between `registerPromoRule` and `lookupActivePromo` must stay consistent."
            ],
        },
        "chapters": chapters,
        "threads": threads,
        "coverageMatrix": [
            {"behavior": "Valid promo discount (`SAVE200`)", "status": "✅ Covered", "notes": "Verified in `checkout_service.test.ts:L4-L12`"},
            {"behavior": "Expired promo eviction (`EXPIRED10`)", "status": "✅ Covered", "notes": "Verified in `checkout_service.test.ts:L14-L22`"},
            {"behavior": "Zero-subtotal cart (`subtotal === 0`)", "status": "❌ Missing", "notes": "Triggers `NaN` in `applyPromoDiscount` (Finding #1)"},
        ],
        "repoChecks": [
            {"name": "Sandbox Git Repository", "passed": True, "detail": f"Ready at {repo_dir} for live 'Fix with Agent' testing"}
        ],
        "dismissedChecks": [
            "Checked negative promo discount leading to negative total — guarded by `Math.max(0, subtotal - discount)` on line 29."
        ],
    }

    with open(active_session_path, "w", encoding="utf-8") as sf:
        json.dump(session, sf, indent=2)
    md_text = render_cliffs_notes_markdown(session)
    with open(cliffs_notes_md_path, "w", encoding="utf-8") as mf:
        mf.write(md_text)
    archive_review_session(session, md_text)
    return session


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect PR or branch context and initialize Quasar.")
    parser.add_argument("--pr", help="GitHub PR number or URL (or local branch name/path)")
    parser.add_argument("--branch", help="Local branch name or git ref to review (e.g. feature/foo or HEAD)")
    parser.add_argument("--base", default=None, help="Base git ref when reviewing a local branch (auto-detected if omitted)")
    parser.add_argument("--head", default=None, help="Head git ref when reviewing a local branch (defaults to --branch or HEAD)")
    parser.add_argument("--committed-only", action="store_true", help="Only include committed changes on the branch, ignoring uncommitted working-tree edits")
    parser.add_argument("--checkout-pr-branch", action="store_true", help="Switch the local repository to the active PR's head branch")
    parser.add_argument("--stash-if-dirty", action="store_true", help="Auto-stash uncommitted changes if needed when switching to the PR branch")
    parser.add_argument("--repo-dir", default=None, help="Repository root directory (auto-detected if omitted)")
    parser.add_argument("--workspace-dir", default=None, help="Window workspace directory that owns this Quasar session (defaults to QUASAR_WORKSPACE_DIR or cwd)")
    parser.add_argument("--effort", choices=["low", "medium", "high"], default=None, help="Review effort level: low (fast single-pass), medium (balanced 2-flash-subagents), or high (full 4-lens deep pipeline)")
    parser.add_argument("--comments-only", action="store_true", help="Comments-Only mode: skip CliffsNotes study guide and reading tour generation, and only produce comments to send to GitHub or fix")
    parser.add_argument("--full-guide", action="store_true", help="Full Guide mode: generate full CliffsNotes study guide, reading tour, and comments (overrides defaultCommentsOnly)")
    parser.add_argument("--tutorial", action="store_true", help="Initialize the interactive Quasar Sandbox PR Tutorial session")
    parser.add_argument("--output-dir", help="Optional directory to write diff and metadata files")
    args = parser.parse_args()

    os.makedirs(REVIEW_STATE_DIR, exist_ok=True)
    ws_root = resolve_workspace_root(args.workspace_dir)
    state_dir, active_session_path, cliffs_notes_md_path = get_session_paths(args.workspace_dir)
    os.makedirs(state_dir, exist_ok=True)

    if args.tutorial:
        session = create_tutorial_sandbox_session(
            ws_root=ws_root,
            state_dir=state_dir,
            active_session_path=active_session_path,
            cliffs_notes_md_path=cliffs_notes_md_path,
            comments_only=bool(args.comments_only),
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "tutorial",
                    "session_id": session["sessionId"],
                    "active_session_path": active_session_path,
                    "cliffs_notes_md_path": cliffs_notes_md_path,
                    "repo_dir": session["repoDir"],
                },
                indent=2,
            )
        )
        return

    if args.checkout_pr_branch:
        if not os.path.isfile(active_session_path):
            print(json.dumps({"ok": False, "error": f"No active session found at {active_session_path}"}))
            sys.exit(1)
        with open(active_session_path, "r", encoding="utf-8") as sf:
            sess = json.load(sf)
        pr_meta = sess.get("pr") or {}
        r_dir = args.repo_dir or sess.get("repoDir") or ws_root
        owner = str(pr_meta.get("owner") or "")
        repo = str(pr_meta.get("repo") or "")
        pr_num = int(pr_meta.get("number") or 0)
        head_ref = str(pr_meta.get("headBranch") or pr_meta.get("headRefName") or "").replace(" + working tree", "").strip()
        res = checkout_pr_branch_in_repo(
            repo_dir=r_dir,
            owner=owner,
            repo=repo,
            pr_num=pr_num,
            head_ref=head_ref,
            stash_if_dirty=args.stash_if_dirty,
        )
        if res.get("ok"):
            pr_meta["localCurrentBranch"] = res.get("checkedOutBranch") or head_ref
            pr_meta["isOnPrBranch"] = True
            pr_meta["needsBranchSwitch"] = False
            sess["pr"] = pr_meta
            with open(active_session_path, "w", encoding="utf-8") as sf:
                json.dump(sess, sf, indent=2)
            ctx_path = os.path.join(state_dir, "pr_context.json")
            if os.path.isfile(ctx_path):
                try:
                    with open(ctx_path, "r", encoding="utf-8") as cf:
                        ctx_data = json.load(cf)
                    if isinstance(ctx_data.get("pr_metadata"), dict):
                        ctx_data["pr_metadata"]["localCurrentBranch"] = pr_meta["localCurrentBranch"]
                        ctx_data["pr_metadata"]["isOnPrBranch"] = True
                        ctx_data["pr_metadata"]["needsBranchSwitch"] = False
                        with open(ctx_path, "w", encoding="utf-8") as cf:
                            json.dump(ctx_data, cf, indent=2)
                except Exception:
                    pass
        print(json.dumps(res, indent=2))
        if not res.get("ok"):
            sys.exit(1)
        return

    # Support passing a local branch, range, or repo path via --pr or --branch
    pr_arg = args.pr
    branch_arg = args.branch or args.head
    repo_dir_arg = args.repo_dir
    base_arg = args.base

    if pr_arg and not is_github_pr_arg(pr_arg):
        branch_arg = pr_arg.strip()
        pr_arg = None

    # Support `/path/to/repo#branch` or `/path/to/repo:branch` or `/path/to/repo`
    if branch_arg:
        for sep in ["#", ":"]:
            if sep in branch_arg and os.path.isdir(os.path.expanduser(branch_arg.split(sep, 1)[0])):
                repo_dir_arg, branch_arg = branch_arg.split(sep, 1)
                break
        if os.path.isdir(os.path.expanduser(branch_arg)):
            repo_dir_arg = os.path.expanduser(branch_arg)
            branch_arg = "HEAD"
        # Support range syntax `base...head` or `base..head`
        if "..." in branch_arg:
            b_part, h_part = branch_arg.split("...", 1)
            base_arg = base_arg or b_part.strip()
            branch_arg = h_part.strip() or "HEAD"
        elif ".." in branch_arg:
            b_part, h_part = branch_arg.split("..", 1)
            base_arg = base_arg or b_part.strip()
            branch_arg = h_part.strip() or "HEAD"

    if pr_arg:
        owner, repo, pr_num = infer_owner_repo(pr_arg, repo_dir_arg or os.getcwd())
        repo_dir = auto_detect_repo_dir(owner, repo, repo_dir_arg)
        metadata, comments, files_list, diff_text = fetch_pr_via_rest(owner, repo, pr_num)
        session_id = f"{owner}-{repo}-pr-{pr_num}"
        base_root, head_root = materialize_snapshots(
            session_id=session_id,
            owner=owner,
            repo=repo,
            base_sha=metadata.get("baseSha") or "",
            head_sha=metadata.get("headSha") or "",
            files_list=files_list,
            repo_dir=repo_dir,
        )
    else:
        head_ref = (branch_arg or "HEAD").strip()
        repo_dir = resolve_local_repo_dir(repo_dir_arg, head_ref)
        repo_name = os.path.basename(repo_dir)

        current_branch = run_cmd(["git", "branch", "--show-current"], cwd=repo_dir, check=False).stdout.strip()
        if head_ref == "HEAD":
            head_branch_name = current_branch or "HEAD"
        else:
            abbrev = run_cmd(["git", "rev-parse", "--abbrev-ref", head_ref], cwd=repo_dir, check=False)
            head_branch_name = abbrev.stdout.strip() if abbrev.returncode == 0 and abbrev.stdout.strip() else head_ref

        head_sha = run_cmd(["git", "rev-parse", head_ref], cwd=repo_dir).stdout.strip()
        base_ref = detect_base_ref(repo_dir, base_arg, head_ref)
        mb_res = run_cmd(["git", "merge-base", base_ref, head_sha], cwd=repo_dir, check=False)
        merge_base_sha = (
            mb_res.stdout.strip()
            if mb_res.returncode == 0 and mb_res.stdout.strip()
            else run_cmd(["git", "rev-parse", base_ref], cwd=repo_dir, check=False).stdout.strip()
        )

        has_uncommitted = bool(
            run_cmd(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo_dir, check=False).stdout.strip()
        )
        head_current_sha = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=False).strip() if hasattr(run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=False), "strip") else run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=False).stdout.strip()
        is_checked_out = head_ref == "HEAD" or (current_branch and head_ref == current_branch) or (head_sha == head_current_sha and head_ref == head_branch_name)
        use_working_tree = bool(is_checked_out and has_uncommitted and not args.committed_only)

        # If merge_base equals head_sha and working tree is clean, diff against parent commit so single-commit review works
        if merge_base_sha == head_sha and not use_working_tree:
            parent_res = run_cmd(["git", "rev-parse", "--verify", "--quiet", f"{head_ref}~1"], cwd=repo_dir, check=False)
            if parent_res.returncode == 0 and parent_res.stdout.strip():
                merge_base_sha = parent_res.stdout.strip()
                base_ref = f"{head_branch_name}~1"

        diff_target_args = [merge_base_sha] if use_working_tree else [merge_base_sha, head_sha]
        diff_text = run_cmd(["git", "diff", *diff_target_args], cwd=repo_dir).stdout
        numstat = run_cmd(["git", "diff", "--numstat", *diff_target_args], cwd=repo_dir).stdout
        name_status_out = run_cmd(["git", "diff", "--name-status", *diff_target_args], cwd=repo_dir).stdout

        status_map: Dict[str, Tuple[str, Optional[str]]] = {}
        for ns_line in name_status_out.strip().splitlines():
            ns_parts = ns_line.split("\t")
            if len(ns_parts) >= 2:
                code = ns_parts[0].strip()
                if code.startswith("R") and len(ns_parts) >= 3:
                    status_map[ns_parts[2]] = ("modified", ns_parts[1])
                elif code.startswith("A"):
                    status_map[ns_parts[1]] = ("added", None)
                elif code.startswith("D"):
                    status_map[ns_parts[1]] = ("removed", None)
                else:
                    status_map[ns_parts[1]] = ("modified", None)

        files_list = []
        for line in numstat.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                rel_p = parts[2]
                # Handle rename paths like `old => new` in numstat by matching name-status if needed
                if " => " in rel_p and rel_p not in status_map:
                    rel_p = re.sub(r"\{([^}]*) => ([^}]*)\}", r"\2", rel_p)
                    if " => " in rel_p:
                        rel_p = rel_p.split(" => ")[-1]
                file_status, prev_path = status_map.get(rel_p, ("modified", None))
                file_patch = run_cmd(["git", "diff", *diff_target_args, "--", rel_p], cwd=repo_dir, check=False).stdout
                s_line, e_line = parse_first_changed_line(file_patch)
                f_entry: Dict[str, Any] = {
                    "path": rel_p,
                    "status": file_status,
                    "added": int(parts[0]) if parts[0].isdigit() else 0,
                    "deleted": int(parts[1]) if parts[1].isdigit() else 0,
                    "firstChangedStartLine": s_line,
                    "firstChangedEndLine": e_line,
                    "hunks": parse_all_hunks(file_patch),
                }
                if prev_path:
                    f_entry["previousPath"] = prev_path
                files_list.append(f_entry)

        git_author = (
            run_cmd(["git", "log", "-1", "--format=%an", head_sha], cwd=repo_dir, check=False).stdout.strip()
            or run_cmd(["git", "config", "user.name"], cwd=repo_dir, check=False).stdout.strip()
            or "local"
        )
        commit_log = run_cmd(
            ["git", "log", "--no-merges", "--pretty=format:- %s (%h)", f"{merge_base_sha}..{head_sha}"],
            cwd=repo_dir,
            check=False,
        ).stdout.strip()
        latest_subject = run_cmd(
            ["git", "log", "-1", "--pretty=format:%s", head_sha],
            cwd=repo_dir,
            check=False,
        ).stdout.strip()

        wt_suffix = " + working tree" if use_working_tree else ""
        branch_title = (
            f"{head_branch_name}{wt_suffix}: {latest_subject}"
            if latest_subject and merge_base_sha != head_sha
            else f"Local Branch {head_branch_name}{wt_suffix} (vs {base_ref})"
        )
        body_parts = []
        if commit_log:
            body_parts.append(f"Commits on `{head_branch_name}` (since `{base_ref}`):\n{commit_log}")
        if use_working_tree:
            body_parts.append("Includes uncommitted working-tree changes.")

        safe_branch_slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", head_branch_name).strip("-") or "head"
        session_id = f"local-{repo_name}-{safe_branch_slug}"

        metadata = {
            "mode": "local_branch",
            "owner": "local",
            "repo": repo_name,
            "number": 0,
            "title": branch_title,
            "url": "",
            "state": "local",
            "author": git_author,
            "baseRefName": base_ref,
            "baseSha": merge_base_sha,
            "headRefName": f"{head_branch_name}{wt_suffix}",
            "headBranch": head_branch_name,
            "headSha": head_sha,
            "includesWorkingTree": use_working_tree,
            "additions": sum(f["added"] for f in files_list),
            "deletions": sum(f["deleted"] for f in files_list),
            "changedFilesCount": len(files_list),
            "body": "\n\n".join(body_parts),
        }
        comments = []
        base_root, head_root = materialize_snapshots(
            session_id=session_id,
            owner="local",
            repo=repo_name,
            base_sha=merge_base_sha,
            head_sha=head_sha,
            files_list=files_list,
            repo_dir=repo_dir,
            use_working_tree_head=use_working_tree,
        )

    is_owned, own_reason = detect_pr_ownership(metadata, repo_dir)
    metadata["isOwnedByUser"] = is_owned
    metadata["ownershipReason"] = own_reason
    metadata.update(inspect_local_repo_branch_state(metadata, repo_dir))

    categorized_files: Dict[str, List[Dict[str, Any]]] = {
        "core_source": [],
        "test": [],
        "generated_or_fixture": [],
        "docs_or_config": [],
    }
    for f in files_list:
        cat = classify_file(f["path"])
        categorized_files[cat].append(f)

    prefs_for_context: Dict[str, Any] = {}
    try:
        from manage_preferences import build_prompt_summary, load_preferences

        prefs_for_context = build_prompt_summary(load_preferences(), metadata.get("repo"))
    except Exception:
        prefs_for_context = {}

    effort_level = (args.effort or prefs_for_context.get("defaultEffort") or "medium").lower()
    if effort_level not in ("low", "medium", "high"):
        effort_level = "medium"

    if args.comments_only:
        comments_only = True
    elif args.full_guide:
        comments_only = False
    else:
        comments_only = bool(prefs_for_context.get("defaultCommentsOnly", False))

    win_id, win_pid = resolve_target_window(state_dir)

    summary: Dict[str, Any] = {
        "mode": "pr" if pr_arg else "local_branch",
        "session_id": session_id,
        "effort_level": effort_level,
        "comments_only": comments_only,
        "workspace_dir": ws_root,
        "state_dir": state_dir,
        "window_id": win_id,
        "window_pid": win_pid,
        "repo_dir": repo_dir,
        "base_snapshot_root": base_root,
        "head_snapshot_root": head_root,
        "pr_metadata": metadata,
        "reviewer_preferences": prefs_for_context,
        "existing_comments": comments,
        "categorized_files": categorized_files,
    }

    out_dir = os.path.abspath(args.output_dir) if args.output_dir else state_dir
    os.makedirs(out_dir, exist_ok=True)
    context_path = os.path.join(out_dir, "pr_context.json")
    diff_path = os.path.join(out_dir, "pr.diff")
    with open(context_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    with open(diff_path, "w", encoding="utf-8") as f:
        f.write(diff_text)
    summary["saved_context_path"] = context_path
    summary["saved_diff_path"] = diff_path

    # Write active_session.json and cliffs_notes.md inside this window's isolated workspace state_dir
    session = build_initial_cliffs_notes_session(
        session_id=session_id,
        repo_dir=repo_dir,
        metadata=metadata,
        categorized_files=categorized_files,
        existing_comments=comments,
        base_root=base_root,
        head_root=head_root,
        output_dir=out_dir,
        comments_only=comments_only,
    )
    session["effortLevel"] = effort_level
    session["commentsOnly"] = comments_only
    if win_id:
        session["windowId"] = win_id
    if win_pid:
        session["windowPid"] = win_pid
    m_conv = re.search(
        r"/brain/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:/|$)",
        os.path.abspath(str(out_dir)),
    )
    if m_conv:
        session["conversationId"] = m_conv.group(1)
    if comments_only:
        session["cliffsNotes"] = {
            "tldr": "Comments-Only Review — Study guide skipped; see Review Comments & Cart to stage comments for GitHub or queue them for Agent fixes.",
            "whatAndWhy": "Quasar is analyzing the diff in Comments-Only mode to produce verified review comments...",
            "architectureDiagram": "",
            "keySymbols": [],
            "designTradeoffs": [],
        }
    session["workspaceDir"] = ws_root
    session["stateDir"] = state_dir

    # If an active session already exists in this workspace, archive it to history before overwriting
    if os.path.isfile(active_session_path):
        try:
            with open(active_session_path, "r", encoding="utf-8") as old_sf:
                old_sess = json.load(old_sf)
            old_md = None
            if os.path.isfile(cliffs_notes_md_path):
                with open(cliffs_notes_md_path, "r", encoding="utf-8") as old_mf:
                    old_md = old_mf.read()
            archive_review_session(old_sess, old_md)
        except Exception:
            pass

    with open(active_session_path, "w", encoding="utf-8") as sf:
        json.dump(session, sf, indent=2)
    md_text = render_cliffs_notes_markdown(session)
    with open(cliffs_notes_md_path, "w", encoding="utf-8") as mf:
        mf.write(md_text)
    archive_review_session(session, md_text)

    summary["active_session_path"] = active_session_path
    summary["cliffs_notes_md_path"] = cliffs_notes_md_path
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
