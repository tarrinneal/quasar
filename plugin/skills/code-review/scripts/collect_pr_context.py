#!/usr/bin/env python3
"""Collects GitHub PR or local branch diff context, materializes Base/Head snapshots
for native side-by-side diffs, and initializes the Antigravity Code Review session.

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

REVIEW_STATE_DIR = os.path.expanduser("~/.gemini/antigravity-code-review")
ACTIVE_SESSION_PATH = os.path.join(REVIEW_STATE_DIR, "active_session.json")
CLIFFS_NOTES_MD_PATH = os.path.join(REVIEW_STATE_DIR, "cliffs_notes.md")

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


def get_github_token() -> Optional[str]:
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


def auto_detect_repo_dir(owner: str, repo: str, provided_dir: Optional[str]) -> str:
    if provided_dir and os.path.isdir(os.path.join(provided_dir, ".git")):
        return os.path.abspath(provided_dir)

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
        if os.path.isdir(os.path.join(cand, ".git")):
            try:
                remotes = run_cmd(["git", "remote", "-v"], cwd=cand, check=False).stdout
                if f"/{repo}" in remotes or f":{owner}/{repo}" in remotes:
                    return os.path.abspath(cand)
            except Exception:
                pass
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
        "User-Agent": "antigravity-code-review",
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
        res = run_cmd(["git", "cat-file", "-e", f"{sha}:{rel_path}"], cwd=repo_dir, check=False)
        if res.returncode == 0:
            show_res = run_cmd(["git", "show", f"{sha}:{rel_path}"], cwd=repo_dir, check=False)
            if show_res.returncode == 0:
                return show_res.stdout

    # 2. Fallback to GitHub raw URL
    quoted_path = urllib.parse.quote(rel_path, safe="/")
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{quoted_path}"
    headers = {"User-Agent": "antigravity-code-review"}
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
) -> Tuple[str, str]:
    snapshot_root = os.path.join(REVIEW_STATE_DIR, "snapshots", session_id)
    base_root = os.path.join(snapshot_root, "base")
    head_root = os.path.join(snapshot_root, "head")
    os.makedirs(base_root, exist_ok=True)
    os.makedirs(head_root, exist_ok=True)

    # Try fetching the base/head refs in local git repo if missing, best-effort
    if os.path.isdir(os.path.join(repo_dir, ".git")) and base_sha:
        has_base = run_cmd(["git", "cat-file", "-t", base_sha], cwd=repo_dir, check=False).returncode == 0
        if not has_base:
            run_cmd(["git", "fetch", "--quiet", "origin"], cwd=repo_dir, check=False)

    def process_one(f_item: Dict[str, Any]) -> None:
        rel_path = f_item["path"]
        status = f_item.get("status", "modified")
        base_target = os.path.join(base_root, rel_path)
        head_target = os.path.join(head_root, rel_path)
        os.makedirs(os.path.dirname(base_target), exist_ok=True)
        os.makedirs(os.path.dirname(head_target), exist_ok=True)

        if status == "added":
            base_content = ""
        else:
            base_content = fetch_raw_file_content(owner, repo, base_sha, rel_path, repo_dir)
            if base_content is None:
                local_file = os.path.join(repo_dir, rel_path)
                base_content = open(local_file, "r", encoding="utf-8", errors="replace").read() if os.path.isfile(local_file) else ""

        if status == "removed":
            head_content = ""
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
    for rc in review_comments:
        comments.append(
            {
                "id": f"gh-inline-{rc.get('id')}",
                "type": "inline_comment",
                "author": (rc.get("user") or {}).get("login"),
                "path": rc.get("path"),
                "line": rc.get("line") or rc.get("original_line") or 1,
                "side": rc.get("side", "RIGHT"),
                "created_at": rc.get("created_at"),
                "body": (rc.get("body") or "")[:1000],
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


def build_initial_cliffs_notes_session(
    session_id: str,
    repo_dir: str,
    metadata: Dict[str, Any],
    categorized_files: Dict[str, List[Dict[str, Any]]],
    existing_comments: List[Dict[str, Any]],
    base_root: str,
    head_root: str,
    output_dir: Optional[str],
) -> Dict[str, Any]:
    """Builds an immediate CliffsNotes session structure from the categorized files and hunks
    so the native Antigravity extension renders the tour right away, before or during deep agent synthesis.
    """
    chapters: List[Dict[str, Any]] = []

    chapter_defs = [
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
                    "callersAndInvariants": "Hover or click 'Ask Antigravity' on any line to trace callers or invariants.",
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

    inline_threads: List[Dict[str, Any]] = []
    for c in existing_comments:
        if c.get("type") == "inline_comment" and c.get("path"):
            inline_threads.append(
                {
                    "id": c["id"],
                    "kind": "github_existing",
                    "severity": "EXISTING",
                    "status": "existing",
                    "path": c["path"],
                    "startLine": int(c.get("line") or 1),
                    "endLine": int(c.get("line") or 1),
                    "title": f"@{c.get('author', 'reviewer')} on GitHub",
                    "whyItMatters": "Existing inline comment on the GitHub PR.",
                    "draftComment": c.get("body", ""),
                    "messages": [
                        {
                            "author": f"@{c.get('author', 'reviewer')} (GitHub)",
                            "body": c.get("body", ""),
                            "timestamp": c.get("created_at") or "",
                        }
                    ],
                }
            )

    pr_body_first_para = ""
    if metadata.get("body"):
        cleaned = re.sub(r"<!--.*?-->", "", metadata["body"], flags=re.DOTALL).strip()
        paras = [p.strip() for p in cleaned.split("\n\n") if p.strip() and not p.strip().startswith("#")]
        if paras:
            pr_body_first_para = paras[0][:400]

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
        "cliffsNotes": {
            "tldr": pr_body_first_para or f"Reviewing PR #{metadata.get('number')}: {metadata.get('title')}",
            "whatAndWhy": pr_body_first_para or "Antigravity is synthesizing the executive CliffsNotes for this PR...",
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


def render_cliffs_notes_markdown(session: Dict[str, Any]) -> str:
    pr = session.get("pr") or {}
    cn = session.get("cliffsNotes") or {}
    lines: List[str] = []
    pr_num = pr.get("number")
    title = pr.get("title") or "Local Branch Diff"
    url = pr.get("url") or ""
    author = pr.get("author") or "local"

    lines.append(f"# 📖 Antigravity CliffsNotes: PR #{pr_num} — {title}" if pr_num else f"# 📖 Antigravity CliffsNotes: {title}")
    if url:
        lines.append(f"**PR**: [#{pr_num}]({url}) • **Author**: `@{author}` • **Diff**: `+{pr.get('additions', 0)} / -{pr.get('deletions', 0)}` across `{pr.get('changedFilesCount', 0)}` files")
    lines.append("")

    if session.get("analysisStatus") == "analyzing":
        lines.append("> [!NOTE]")
        lines.append("> **Deep Analysis in Progress**: Initial file snapshots and diff tour are ready now. Antigravity's specialist review lenses (`review-architect`, `review-bug-hunter`, `review-test-critic`, `review-advocate`) are synthesizing deep CliffsNotes and candidate findings...")
        lines.append("")

    lines.append("## ⚡ At-a-Glance Summary")
    lines.append(cn.get("whatAndWhy") or cn.get("tldr") or "")
    lines.append("")

    if cn.get("architectureDiagram"):
        lines.append("## 🧭 Architecture & Data Flow")
        diag = cn["architectureDiagram"].strip()
        if not diag.startswith("```"):
            lines.append("```mermaid")
            lines.append(diag)
            lines.append("```")
        else:
            lines.append(diag)
        lines.append("")

    if cn.get("keySymbols"):
        lines.append("## 🗂️ Key Symbols & Files (Character List)")
        lines.append("| Symbol / File | Role in This PR |")
        lines.append("| :--- | :--- |")
        for sym in cn["keySymbols"]:
            lines.append(f"| `{sym.get('name', '')}` | {sym.get('role', '')} |")
        lines.append("")

    if cn.get("designTradeoffs"):
        lines.append("## ⚖️ Key Design Decisions & Trade-offs")
        for td in cn["designTradeoffs"]:
            lines.append(f"- {td}")
        lines.append("")

    lines.append("## 📚 Chapter-by-Chapter Reading Guide")
    for ch in session.get("chapters") or []:
        lines.append(f"### {ch.get('title')}")
        if ch.get("summary"):
            lines.append(f"*{ch.get('summary')}*")
            lines.append("")
        for b in ch.get("cliffsNotesBullets") or []:
            lines.append(f"- {b}")
        lines.append("")
        for stop in ch.get("stops") or []:
            head_path = stop.get("headSnapshotPath") or stop.get("localWorkspacePath") or stop.get("path")
            start_l = stop.get("startLine", 1)
            end_l = stop.get("endLine", start_l)
            lines.append(
                f"1. **[{stop.get('stopNumber')}] [{stop.get('title')}](file://{head_path}#L{start_l}-L{end_l})** — {stop.get('cliffsNotes') or stop.get('description')}"
            )
            if stop.get("whyItChanged"):
                lines.append(f"   - *Why it changed*: {stop['whyItChanged']}")
            if stop.get("callersAndInvariants"):
                lines.append(f"   - *Context & Invariants*: {stop['callersAndInvariants']}")
        lines.append("")

    if session.get("coverageMatrix"):
        lines.append("## 🧪 Test Coverage & Hygiene Matrix")
        lines.append("| Behavior / Change | Verification Status | Notes |")
        lines.append("| :--- | :--- | :--- |")
        for row in session["coverageMatrix"]:
            lines.append(f"| {row.get('behavior', '')} | {row.get('status', '')} | {row.get('notes', '')} |")
        lines.append("")

    candidate_threads = [
        t for t in (session.get("threads") or []) if t.get("kind") in ("ai_candidate", "user_staged")
    ]
    if candidate_threads:
        lines.append("## 🎯 Curated Review Comments")
        for idx, t in enumerate(candidate_threads, 1):
            head_path = os.path.join(session.get("headSnapshotRoot", ""), t.get("path", ""))
            start_l = t.get("startLine", 1)
            end_l = t.get("endLine", start_l)
            sev = t.get("severity", "DESIGN")
            badge = "🔴 Blocking" if "BLOCK" in sev.upper() else ("🟡 Design" if "DESIGN" in sev.upper() else "🟢 Nit")
            lines.append(f"### #{idx} [{badge}] {t.get('title', 'Review Finding')}")
            lines.append(f"- **Location**: [{t.get('path')}:L{start_l}-{end_l}](file://{head_path}#L{start_l}-L{end_l})")
            if t.get("whyItMatters"):
                lines.append(f"- **CliffsNotes Context (For You)**: {t['whyItMatters']}")
            lines.append("- **Draft GitHub Comment**:")
            lines.append(f"  > {t.get('draftComment', '')}")
            lines.append("")

    if session.get("dismissedChecks"):
        lines.append("## ✅ Checked & Dismissed (Verified Safe)")
        for item in session["dismissedChecks"]:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect PR or branch context and initialize Antigravity Code Review.")
    parser.add_argument("--pr", help="GitHub PR number or URL")
    parser.add_argument("--base", default="origin/main", help="Base git ref when --pr is not used")
    parser.add_argument("--head", default="HEAD", help="Head git ref when --pr is not used")
    parser.add_argument("--repo-dir", default=None, help="Repository root directory (auto-detected if omitted)")
    parser.add_argument("--output-dir", help="Optional directory to write diff and metadata files")
    args = parser.parse_args()

    os.makedirs(REVIEW_STATE_DIR, exist_ok=True)

    if args.pr:
        owner, repo, pr_num = infer_owner_repo(args.pr, args.repo_dir or os.getcwd())
        repo_dir = auto_detect_repo_dir(owner, repo, args.repo_dir)
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
        repo_dir = os.path.abspath(args.repo_dir or os.getcwd())
        session_id = f"local-diff-{os.path.basename(repo_dir)}"
        base_sha = run_cmd(["git", "rev-parse", args.base], cwd=repo_dir, check=False).stdout.strip()
        head_sha = run_cmd(["git", "rev-parse", args.head], cwd=repo_dir, check=False).stdout.strip()
        diff_text = run_cmd(["git", "diff", f"{args.base}...{args.head}"], cwd=repo_dir).stdout
        numstat = run_cmd(["git", "diff", "--numstat", f"{args.base}...{args.head}"], cwd=repo_dir).stdout
        files_list = []
        for line in numstat.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                rel_p = parts[2]
                file_patch = run_cmd(["git", "diff", f"{args.base}...{args.head}", "--", rel_p], cwd=repo_dir, check=False).stdout
                s_line, e_line = parse_first_changed_line(file_patch)
                files_list.append(
                    {
                        "path": rel_p,
                        "status": "modified",
                        "added": int(parts[0]) if parts[0].isdigit() else 0,
                        "deleted": int(parts[1]) if parts[1].isdigit() else 0,
                        "firstChangedStartLine": s_line,
                        "firstChangedEndLine": e_line,
                        "hunks": parse_all_hunks(file_patch),
                    }
                )
        metadata = {
            "owner": "local",
            "repo": os.path.basename(repo_dir),
            "number": 0,
            "title": f"Local Diff ({args.base}...{args.head})",
            "url": "",
            "state": "open",
            "author": "local",
            "baseRefName": args.base,
            "baseSha": base_sha,
            "headRefName": args.head,
            "headSha": head_sha,
            "additions": sum(f["added"] for f in files_list),
            "deletions": sum(f["deleted"] for f in files_list),
            "changedFilesCount": len(files_list),
            "body": "",
        }
        comments = []
        base_root, head_root = materialize_snapshots(
            session_id=session_id,
            owner="local",
            repo=os.path.basename(repo_dir),
            base_sha=base_sha,
            head_sha=head_sha,
            files_list=files_list,
            repo_dir=repo_dir,
        )

    categorized_files: Dict[str, List[Dict[str, Any]]] = {
        "core_source": [],
        "test": [],
        "generated_or_fixture": [],
        "docs_or_config": [],
    }
    for f in files_list:
        cat = classify_file(f["path"])
        categorized_files[cat].append(f)

    summary: Dict[str, Any] = {
        "mode": "pr" if args.pr else "git_diff",
        "session_id": session_id,
        "repo_dir": repo_dir,
        "base_snapshot_root": base_root,
        "head_snapshot_root": head_root,
        "pr_metadata": metadata,
        "existing_comments": comments,
        "categorized_files": categorized_files,
    }

    out_dir = os.path.abspath(args.output_dir) if args.output_dir else REVIEW_STATE_DIR
    os.makedirs(out_dir, exist_ok=True)
    context_path = os.path.join(out_dir, "pr_context.json")
    diff_path = os.path.join(out_dir, "pr.diff")
    with open(context_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    with open(diff_path, "w", encoding="utf-8") as f:
        f.write(diff_text)
    summary["saved_context_path"] = context_path
    summary["saved_diff_path"] = diff_path

    # Write active_session.json and cliffs_notes.md so the native Antigravity extension updates immediately
    session = build_initial_cliffs_notes_session(
        session_id=session_id,
        repo_dir=repo_dir,
        metadata=metadata,
        categorized_files=categorized_files,
        existing_comments=comments,
        base_root=base_root,
        head_root=head_root,
        output_dir=out_dir,
    )
    with open(ACTIVE_SESSION_PATH, "w", encoding="utf-8") as sf:
        json.dump(session, sf, indent=2)
    with open(CLIFFS_NOTES_MD_PATH, "w", encoding="utf-8") as mf:
        mf.write(render_cliffs_notes_markdown(session))

    summary["active_session_path"] = ACTIVE_SESSION_PATH
    summary["cliffs_notes_md_path"] = CLIFFS_NOTES_MD_PATH
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
