#!/usr/bin/env python3
"""Manages GitHub API authentication (`~/.gemini/quasar/github_auth.json`) and
publishes reviews (as the authenticated user) to a GitHub Pull Request.

Supports publishing in 4 modes (`--event`):
  - `PENDING` (default): Creates a draft Pending Review on GitHub under your account
    so you can inspect/edit it in the GitHub UI before final submission.
  - `COMMENT`: Publishes the review and staged inline comments immediately as a comment review.
  - `APPROVE`: Approves the PR and publishes any staged inline comments + summary body.
  - `REQUEST_CHANGES`: Requests changes on the PR and publishes any staged inline comments + summary body.

Also manages your GitHub Personal Access Token (API Key):
  - `python3 post_pending_review.py --set-token <GITHUB_PAT>`
  - `python3 post_pending_review.py --auth-status`
  - `python3 post_pending_review.py --clear-token`
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from collect_pr_context import (
    ACTIVE_SESSION_PATH,
    GITHUB_AUTH_PATH,
    REVIEW_STATE_DIR,
    find_gh_binary,
    get_github_token,
    get_session_paths,
    infer_owner_repo,
    load_stored_github_auth,
    run_cmd,
)


def verify_github_token(token: str) -> Dict[str, Any]:
    """Verifies a GitHub Personal Access Token against GET https://api.github.com/user."""
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {token.strip()}",
            "User-Agent": "quasar",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            scopes = resp.headers.get("X-OAuth-Scopes", "")
            return {
                "login": data.get("login") or "",
                "name": data.get("name") or "",
                "html_url": data.get("html_url") or "",
                "scopes": scopes,
            }
    except urllib.error.HTTPError:
        raise
    except Exception as exc:
        if "Remote end closed connection" in str(exc) or "RemoteDisconnected" in str(exc):
            return {
                "login": "github-user",
                "name": "",
                "html_url": "https://github.com",
                "scopes": "repo",
            }
        raise


def save_github_auth(token: str, user_info: Dict[str, Any]) -> Dict[str, Any]:
    os.makedirs(REVIEW_STATE_DIR, exist_ok=True)
    record = {
        "token": token.strip(),
        "login": user_info.get("login") or "",
        "name": user_info.get("name") or "",
        "html_url": user_info.get("html_url") or "",
        "scopes": user_info.get("scopes") or "",
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with open(GITHUB_AUTH_PATH, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    try:
        os.chmod(GITHUB_AUTH_PATH, 0o600)
    except Exception:
        pass
    return record


def get_auth_status() -> Dict[str, Any]:
    stored = load_stored_github_auth()
    if stored.get("token"):
        login = stored.get("login") or ""
        name = stored.get("name") or ""
        if not login:
            try:
                info = verify_github_token(stored["token"])
                login = info.get("login") or ""
                name = info.get("name") or ""
                save_github_auth(stored["token"], info)
            except Exception as exc:
                return {
                    "ok": True,
                    "authenticated": False,
                    "source": "quasar_auth_file_invalid",
                    "error": str(exc),
                }
        return {
            "ok": True,
            "authenticated": True,
            "source": "quasar_api_key",
            "login": login,
            "name": name,
            "authFilePath": GITHUB_AUTH_PATH,
        }

    env_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if env_token and env_token.strip():
        try:
            info = verify_github_token(env_token.strip())
            return {
                "ok": True,
                "authenticated": True,
                "source": "environment_variable",
                "login": info.get("login") or "env-user",
                "name": info.get("name") or "",
            }
        except Exception:
            pass

    gh = find_gh_binary()
    if gh:
        try:
            res = subprocess.run([gh, "api", "user", "--jq", ".login"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                return {
                    "ok": True,
                    "authenticated": True,
                    "source": "gh_cli",
                    "login": res.stdout.strip(),
                    "name": "",
                }
        except Exception:
            pass

    return {
        "ok": True,
        "authenticated": False,
        "source": "none",
        "login": "",
        "name": "",
    }


def discover_open_pr_for_branch(repo_dir: str, head_branch: str) -> Optional[Tuple[str, str, int]]:
    """Attempts to find an open GitHub PR for a local branch via `gh` or the GitHub REST API."""
    clean_branch = head_branch.replace(" + working tree", "").strip()
    if not clean_branch or clean_branch == "HEAD":
        cur = run_cmd(["git", "branch", "--show-current"], cwd=repo_dir, check=False).stdout.strip()
        clean_branch = cur or ""
    if not clean_branch:
        return None

    gh = find_gh_binary()
    if gh and os.path.isdir(repo_dir):
        res = subprocess.run(
            [gh, "pr", "view", clean_branch, "--json", "number,url"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and res.stdout.strip():
            try:
                pr_info = json.loads(res.stdout)
                if pr_info.get("url"):
                    return infer_owner_repo(pr_info["url"], repo_dir)
            except Exception:
                pass

    # Fallback: check remotes in repo_dir and query GitHub REST API
    token = get_github_token()
    if os.path.isdir(repo_dir):
        remotes_out = run_cmd(["git", "remote", "-v"], cwd=repo_dir, check=False).stdout
        import re

        pairs = []
        for line in remotes_out.splitlines():
            m = re.search(r"github\.com[:/]([^/]+)/([^\s/]+?)(?:\.git)?\s", line)
            if m:
                pair = (m.group(1), m.group(2))
                if pair not in pairs:
                    pairs.append(pair)
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "quasar",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        for owner_cand, repo_cand in pairs:
            url = f"https://api.github.com/repos/{owner_cand}/{repo_cand}/pulls?state=open&per_page=50"
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    pulls = json.loads(resp.read().decode("utf-8", errors="replace"))
                for pr in pulls:
                    if (pr.get("head") or {}).get("ref") == clean_branch:
                        return owner_cand, repo_cand, int(pr["number"])
            except Exception:
                pass
    return None


def parse_patch_right_lines(patch_text: str) -> Tuple[Dict[int, int], Dict[int, int]]:
    """Parses a unified diff patch and returns (valid_right_lines, modified_right_lines)
    mapping 1-based RIGHT-side line numbers to their 0-based hunk index.
    """
    import re

    valid_lines: Dict[int, int] = {}
    modified_lines: Dict[int, int] = {}
    hunk_idx = -1
    cur_right: Optional[int] = None
    for raw_line in (patch_text or "").splitlines():
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", raw_line)
        if m:
            hunk_idx += 1
            cur_right = int(m.group(1))
            continue
        if cur_right is None:
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            valid_lines[cur_right] = hunk_idx
            modified_lines[cur_right] = hunk_idx
            cur_right += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            pass
        elif raw_line.startswith(" "):
            valid_lines[cur_right] = hunk_idx
            cur_right += 1
    return valid_lines, modified_lines


def align_comment_range_to_head_file(
    path: str,
    start_line: int,
    end_line: int,
    body: str,
    head_root: Optional[str],
) -> Tuple[int, int]:
    """Trims trailing/leading blank lines from [start_line, end_line] and, when a single-line
    ```suggestion block is present inside a multi-line range, narrows the range to the specific
    target line in the head file so GitHub's 'Apply suggestion' replaces only that line.
    """
    import re

    s_line = max(1, int(start_line or end_line or 1))
    e_line = max(s_line, int(end_line or s_line))
    if not head_root:
        return s_line, e_line
    full_path = os.path.join(head_root, path)
    if not os.path.isfile(full_path):
        return s_line, e_line
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            file_lines = f.read().splitlines()
    except Exception:
        return s_line, e_line

    n = len(file_lines)
    if n == 0:
        return 1, 1
    s_line = max(1, min(s_line, n))
    e_line = max(s_line, min(e_line, n))

    while e_line > s_line and not file_lines[e_line - 1].strip():
        e_line -= 1
    while s_line < e_line and not file_lines[s_line - 1].strip():
        s_line += 1

    m = re.search(r"```suggestion\r?\n(.*?)\r?\n```", body or "", flags=re.DOTALL)
    if m and s_line < e_line:
        sugg_lines = [l for l in m.group(1).splitlines() if l.strip()]
        if len(sugg_lines) == 1:
            sugg_tokens = set(re.findall(r"[A-Za-z0-9_.]+", sugg_lines[0].lower()))
            best_ln: Optional[int] = None
            best_score = 0.0
            for ln in range(s_line, e_line + 1):
                cand_tokens = set(re.findall(r"[A-Za-z0-9_.]+", file_lines[ln - 1].lower()))
                if not cand_tokens:
                    continue
                overlap = len(sugg_tokens & cand_tokens) / max(1, min(len(sugg_tokens), len(cand_tokens)))
                if overlap > best_score:
                    best_score = overlap
                    best_ln = ln
            if best_ln is not None and best_score >= 0.3:
                return best_ln, best_ln
    return s_line, e_line


def build_hunk_constrained_comments(
    staged_threads: List[Dict[str, Any]],
    base_root: Optional[str],
    head_root: Optional[str],
    live_patches: Optional[Dict[str, str]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Builds GitHub PR review comment objects constrained to valid diff hunks so GitHub never
    rejects the review with HTTP 422 'Line could not be resolved'.
    Returns (comments_list, overflow_summary_notes).
    """
    import re

    comments_list: List[Dict[str, Any]] = []
    overflow_notes: List[str] = []
    patch_cache: Dict[str, Tuple[Dict[int, int], Dict[int, int]]] = {}

    def get_file_hunk_lines(rel_path: str) -> Tuple[Dict[int, int], Dict[int, int]]:
        if rel_path in patch_cache:
            return patch_cache[rel_path]
        patch_text = ""
        if live_patches is not None and rel_path in live_patches:
            patch_text = live_patches.get(rel_path) or ""
        elif base_root and head_root:
            b_file = os.path.join(base_root, rel_path)
            h_file = os.path.join(head_root, rel_path)
            if os.path.isfile(h_file) and not os.path.isfile(b_file):
                try:
                    with open(h_file, "r", encoding="utf-8", errors="replace") as hf:
                        line_cnt = len(hf.read().splitlines())
                    valid = {i: 0 for i in range(1, max(1, line_cnt) + 1)}
                    patch_cache[rel_path] = (valid, dict(valid))
                    return patch_cache[rel_path]
                except Exception:
                    pass
            elif os.path.isfile(b_file) and os.path.isfile(h_file):
                res = subprocess.run(
                    ["git", "diff", "--no-index", "-U3", b_file, h_file],
                    capture_output=True,
                    text=True,
                )
                patch_text = res.stdout or ""
        parsed = parse_patch_right_lines(patch_text)
        patch_cache[rel_path] = parsed
        return parsed

    for t in staged_threads:
        rel_path = (t.get("path") or "").strip()
        raw_target = (t.get("targetFile") or "").strip()
        target_file = raw_target or ("" if rel_path == "TOP_LEVEL_PR_COMMENTS.md" else rel_path)
        raw_body = (t.get("draftComment") or t.get("body") or "").strip()
        if not raw_body:
            continue

        file_missing_from_diff = bool(
            not rel_path
            or rel_path == "TOP_LEVEL_PR_COMMENTS.md"
            or t.get("isTopLevel")
            or (head_root and not os.path.isfile(os.path.join(head_root, rel_path)))
            or (live_patches is not None and rel_path not in live_patches)
        )
        if file_missing_from_diff:
            if t.get("id") == "top-level-review" or t.get("isTopLevelSummary"):
                formatted_note = raw_body
            elif target_file and target_file not in raw_body and os.path.basename(target_file) not in raw_body:
                formatted_note = f"**`{target_file}`:**\n{raw_body}"
            elif target_file and target_file not in raw_body:
                formatted_note = f"**`{target_file}`:** {raw_body}"
            else:
                formatted_note = raw_body
            if not any(raw_body in existing or formatted_note in existing for existing in overflow_notes):
                overflow_notes.append(formatted_note)
            continue

        orig_s = int(t.get("startLine") or t.get("start_line") or t.get("endLine") or t.get("line") or 1)
        orig_e = int(t.get("endLine") or t.get("line") or orig_s)
        s_line, e_line = align_comment_range_to_head_file(rel_path, orig_s, orig_e, raw_body, head_root)

        valid_lines, modified_lines = get_file_hunk_lines(rel_path)
        if not valid_lines:
            # Otherwise pass through aligned lines
            c_obj: Dict[str, Any] = {
                "path": rel_path,
                "line": e_line,
                "side": "RIGHT",
                "body": raw_body,
            }
            if s_line < e_line:
                c_obj["start_line"] = s_line
                c_obj["start_side"] = "RIGHT"
            comments_list.append(c_obj)
            continue

        if e_line in valid_lines:
            c_obj = {
                "path": rel_path,
                "line": e_line,
                "side": "RIGHT",
                "body": raw_body,
            }
            if s_line < e_line and s_line in valid_lines and valid_lines[s_line] == valid_lines[e_line]:
                c_obj["start_line"] = s_line
                c_obj["start_side"] = "RIGHT"
            comments_list.append(c_obj)
        else:
            # Target line is outside the PR's 3-line unified diff hunks for this file!
            # Snap to the nearest modified/hunk line in this file so GitHub resolves the inline comment cleanly.
            pool = sorted(modified_lines.keys()) if modified_lines else sorted(valid_lines.keys())
            nearest_line = min(pool, key=lambda ln: abs(ln - e_line))
            safe_body = re.sub(r"```suggestion\b", "```", raw_body)
            loc_str = f"lines {orig_s}–{orig_e}" if orig_s != orig_e else f"line {orig_e}"
            if str(orig_s) not in safe_body and str(orig_e) not in safe_body:
                safe_body = f"*(Note regarding {loc_str} of `{rel_path}`)*\n\n{safe_body}"
            comments_list.append(
                {
                    "path": rel_path,
                    "line": nearest_line,
                    "side": "RIGHT",
                    "body": safe_body,
                }
            )

    return comments_list, overflow_notes


def fetch_live_pr_file_patches(owner: str, repo: str, pr_num: int, token: Optional[str]) -> Dict[str, str]:
    """Fetches the live unified diff `patch` per file from GitHub's PR files endpoint."""
    patches: Dict[str, str] = {}
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "quasar",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_num}/files?per_page=100"
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            files_data = json.loads(resp.read().decode("utf-8", errors="replace"))
        if isinstance(files_data, list):
            for fobj in files_data:
                fn = fobj.get("filename")
                pt = fobj.get("patch")
                if fn and pt:
                    patches[fn] = pt
    except Exception:
        pass
    return patches


def delete_existing_pending_reviews(
    owner: str,
    repo: str,
    pr_num: int,
    token: Optional[str],
    gh: Optional[str],
    repo_dir: str,
    known_review_id: Optional[int] = None,
) -> List[int]:
    """Finds and deletes any existing PENDING review owned by the current user on this PR so updated comments can be pushed."""
    pending_ids: List[int] = []
    if known_review_id:
        pending_ids.append(int(known_review_id))

    if token:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "quasar",
        }
        try:
            list_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_num}/reviews?per_page=100"
            req = urllib.request.Request(list_url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=20) as resp:
                reviews = json.loads(resp.read().decode("utf-8", errors="replace"))
            if isinstance(reviews, list):
                for r in reviews:
                    if str(r.get("state") or "").upper() == "PENDING" and r.get("id"):
                        rid = int(r["id"])
                        if rid not in pending_ids:
                            pending_ids.append(rid)
        except Exception:
            pass

        deleted: List[int] = []
        for rid in pending_ids:
            try:
                del_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_num}/reviews/{rid}"
                del_req = urllib.request.Request(del_url, headers=headers, method="DELETE")
                with urllib.request.urlopen(del_req, timeout=20):
                    deleted.append(rid)
            except Exception:
                pass
        return deleted

    if gh:
        cwd = repo_dir if os.path.isdir(repo_dir) else os.getcwd()
        try:
            res = subprocess.run(
                [gh, "api", f"repos/{owner}/{repo}/pulls/{pr_num}/reviews?per_page=100"],
                cwd=cwd,
                capture_output=True,
                text=True,
            )
            if res.returncode == 0:
                reviews = json.loads(res.stdout or "[]")
                if isinstance(reviews, list):
                    for r in reviews:
                        if str(r.get("state") or "").upper() == "PENDING" and r.get("id"):
                            rid = int(r["id"])
                            if rid not in pending_ids:
                                pending_ids.append(rid)
        except Exception:
            pass

        deleted = []
        for rid in pending_ids:
            try:
                del_res = subprocess.run(
                    [gh, "api", "--method", "DELETE", f"repos/{owner}/{repo}/pulls/{pr_num}/reviews/{rid}"],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                )
                if del_res.returncode == 0:
                    deleted.append(rid)
            except Exception:
                pass
        return deleted

    return []


def post_via_rest_or_gh(
    owner: str,
    repo: str,
    pr_num: int,
    payload: Dict[str, Any],
    repo_dir: str,
    review_event: str = "PENDING",
    head_root: Optional[str] = None,
    known_pending_review_id: Optional[int] = None,
) -> Tuple[Dict[str, Any], List[int]]:
    event_upper = (review_event or "PENDING").strip().upper()
    if event_upper == "PENDING":
        payload.pop("event", None)
    else:
        payload["event"] = event_upper

    token = get_github_token()
    gh = find_gh_binary() if not token else None

    # Always check and replace any existing PENDING review on this PR so updates push cleanly
    replaced_ids = delete_existing_pending_reviews(
        owner,
        repo,
        pr_num,
        token,
        gh,
        repo_dir,
        known_review_id=known_pending_review_id,
    )

    def _send_via_token(req_payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_num}/reviews"
        req = urllib.request.Request(
            url,
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "quasar",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    if token:
        try:
            return _send_via_token(payload), replaced_ids
        except urllib.error.HTTPError as http_err:
            err_body = ""
            try:
                err_body = http_err.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if (
                http_err.code == 422
                and "Line could not be resolved" in err_body
                and payload.get("comments")
            ):
                live_patches = fetch_live_pr_file_patches(owner, repo, pr_num, token)
                if live_patches:
                    fixed_comments, overflow = build_hunk_constrained_comments(
                        payload["comments"], None, head_root, live_patches=live_patches
                    )
                    # Strip start_line on retry for maximum compatibility
                    for c in fixed_comments:
                        c.pop("start_line", None)
                        c.pop("start_side", None)
                    retry_payload = dict(payload)
                    retry_payload["comments"] = fixed_comments
                    if overflow:
                        existing_body = (retry_payload.get("body") or "").strip()
                        extra = "\n\n".join(overflow)
                        retry_payload["body"] = f"{existing_body}\n\n{extra}".strip() if existing_body else extra
                    try:
                        return _send_via_token(retry_payload), replaced_ids
                    except urllib.error.HTTPError as retry_err:
                        try:
                            err_body = retry_err.read().decode("utf-8", errors="replace")
                        except Exception:
                            pass
                        http_err = retry_err
            if (
                http_err.code == 403
                and "Resource not accessible by personal access token" in err_body
                and token.startswith("github_pat_")
            ):
                raise RuntimeError(
                    f"GitHub HTTP 403 on {owner}/{repo}: Fine-Grained tokens (github_pat_...) only grant write access to your own personal repos (single Resource Owner) and are read-only on external orgs like '{owner}'. Please generate a Classic Personal Access Token (ghp_...) with the 'public_repo' (or 'repo') scope."
                )
            raise RuntimeError(f"GitHub API HTTP {http_err.code}: {err_body or http_err.reason}")

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
            return json.loads(res.stdout), replaced_ids
        raise RuntimeError(res.stderr.strip() or "gh api call failed")

    raise RuntimeError(
        "NO_GITHUB_AUTH: Please configure your GitHub Personal Access Token in Quasar (Quasar: Configure GitHub API Key / Token...) or run `gh auth login`."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure GitHub API Key or publish a review to a GitHub PR.")
    parser.add_argument("--set-token", help="Verify and save a GitHub Personal Access Token (API Key) for publishing reviews as you")
    parser.add_argument("--auth-status", action="store_true", help="Print current GitHub authentication status as JSON")
    parser.add_argument("--clear-token", action="store_true", help="Remove stored GitHub API Key from ~/.gemini/quasar/github_auth.json")
    parser.add_argument("--from-session", action="store_true", help="Publish/export staged comments from active_session.json")
    parser.add_argument("--pr", help="GitHub PR number or URL (optional if --from-session is used)")
    parser.add_argument("--comments-file", help="Path to JSON file with draft comments")
    parser.add_argument("--repo-dir", default=os.getcwd(), help="Repository directory")
    parser.add_argument(
        "--event",
        choices=["PENDING", "COMMENT", "APPROVE", "REQUEST_CHANGES"],
        default="PENDING",
        help="Review submission mode: PENDING (draft), COMMENT, APPROVE, or REQUEST_CHANGES",
    )
    parser.add_argument("--summary-body", default="", help="Optional overall review summary comment")
    parser.add_argument("--workspace-dir", default=None, help="Window workspace directory")
    args = parser.parse_args()

    if args.auth_status:
        print(json.dumps(get_auth_status(), indent=2))
        return

    if args.clear_token:
        if os.path.isfile(GITHUB_AUTH_PATH):
            try:
                os.unlink(GITHUB_AUTH_PATH)
            except Exception:
                pass
        print(json.dumps({"ok": True, "cleared": True, "status": get_auth_status()}, indent=2))
        return

    if args.set_token:
        raw_tok = args.set_token.strip()
        if not raw_tok:
            print(json.dumps({"ok": False, "error": "Empty token provided."}))
            sys.exit(1)
        try:
            info = verify_github_token(raw_tok)
            saved = save_github_auth(raw_tok, info)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "authenticated": True,
                        "login": saved.get("login"),
                        "name": saved.get("name"),
                        "scopes": saved.get("scopes"),
                        "authFilePath": GITHUB_AUTH_PATH,
                    },
                    indent=2,
                )
            )
            return
        except urllib.error.HTTPError as e:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"GitHub rejected token (HTTP {e.code}). Ensure it is a valid Personal Access Token with 'repo' or 'pull_requests:write' permission.",
                    }
                )
            )
            sys.exit(1)
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            sys.exit(1)

    _state_dir, active_session_path, _cliffs_md_path = get_session_paths(
        getattr(args, "workspace_dir", None)
    )

    if args.from_session or not args.comments_file:
        if not os.path.isfile(active_session_path):
            sys.stderr.write(f"No active review session found at {active_session_path}\n")
            sys.exit(1)
        with open(active_session_path, "r", encoding="utf-8") as f:
            session = json.load(f)
        pr_meta = session.get("pr") or {}
        owner = pr_meta.get("owner")
        repo = pr_meta.get("repo")
        pr_num = int(pr_meta.get("number") or 0)
        repo_dir = session.get("repoDir") or args.repo_dir
        if args.pr and (not owner or owner == "local" or not pr_num):
            owner, repo, pr_num = infer_owner_repo(args.pr, repo_dir)

        staged = [
            t
            for t in (session.get("threads") or [])
            if t.get("status") == "staged" and (t.get("draftComment") or t.get("body"))
        ]
        if not staged and args.event == "PENDING" and not args.summary_body.strip():
            sys.stderr.write("No staged comments found in active session. Stage one or more comments first.\n")
            sys.exit(1)

        if session.get("isTutorial") or pr_meta.get("mode") == "tutorial":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "status": "tutorial_simulated",
                        "event": args.event,
                        "comments_exported": len(staged),
                        "message": f"Tutorial Sandbox: Simulated publishing {len(staged)} staged comment(s) ({args.event}). On a real GitHub PR, this posts directly to GitHub under your account!",
                    },
                    indent=2,
                )
            )
            return

        if not pr_num or owner == "local":
            head_branch = pr_meta.get("headBranch") or pr_meta.get("headRefName") or ""
            discovered = discover_open_pr_for_branch(repo_dir, head_branch)
            if discovered:
                owner, repo, pr_num = discovered

        if not pr_num or owner == "local":
            local_export_path = os.path.join(os.path.dirname(active_session_path), "local_review_comments.md")
            md_lines = [
                f"# Staged Review Comments ({pr_meta.get('title') or 'Local Branch'})",
                "",
            ]
            if args.summary_body.strip():
                md_lines.append(args.summary_body.strip())
                md_lines.append("")
            for idx, t in enumerate(staged, 1):
                if t.get("isTopLevel") or not t.get("path"):
                    tf = t.get("targetFile") or ""
                    header_loc = f"📌 Top-Level PR Comment (`{tf}`)" if tf else "📌 Top-Level PR Comment"
                    md_lines.append(f"## {idx}. {header_loc}")
                else:
                    md_lines.append(f"## {idx}. `{t['path']}` (Line {t.get('endLine') or t.get('startLine') or 1})")
                md_lines.append(f"> {t.get('draftComment') or t.get('body') or ''}")
                md_lines.append("")
            with open(local_export_path, "w", encoding="utf-8") as mf:
                mf.write("\n".join(md_lines))
            print(
                json.dumps(
                    {
                        "ok": True,
                        "status": "local_review_exported",
                        "local_markdown_path": local_export_path,
                        "comments_exported": len(staged),
                    },
                    indent=2,
                )
            )
            return

        base_root = session.get("baseSnapshotRoot")
        head_root = session.get("headSnapshotRoot")
        comments_list, overflow_notes = build_hunk_constrained_comments(
            staged, base_root, head_root
        )
        payload: Dict[str, Any] = {"comments": comments_list}
        summary_parts: List[str] = []
        base_summary = args.summary_body.strip() or (session.get("topLevelCommentIntro") or "").strip()
        if base_summary and not any(base_summary in note for note in overflow_notes):
            summary_parts.append(base_summary)
        for note in overflow_notes:
            if not any(note in existing for existing in summary_parts):
                summary_parts.append(note)
        if summary_parts:
            payload["body"] = "\n\n".join(summary_parts)
        known_pending_id = session.get("githubPendingReviewId")
    else:
        session = None
        known_pending_id = None
        head_root = None
        with open(args.comments_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not args.pr:
            sys.stderr.write("--pr is required when using --comments-file\n")
            sys.exit(1)
        owner, repo, pr_num = infer_owner_repo(args.pr, args.repo_dir)
        repo_dir = args.repo_dir

    try:
        resp, replaced_ids = post_via_rest_or_gh(
            owner,
            repo,
            pr_num,
            payload,
            repo_dir,
            args.event,
            head_root=head_root,
            known_pending_review_id=int(known_pending_id) if known_pending_id else None,
        )
    except Exception as exc:
        err_str = str(exc)
        if (
            "ÜberProxy" in err_str
            or "uberproxy" in err_str.lower()
            or "Denied access to requested resource" in err_str
            or "Remote end closed connection" in err_str
            or "RemoteDisconnected" in err_str
        ):
            event_upper = (args.event or "PENDING").strip().upper()
            browser_payload = dict(payload)
            if event_upper == "PENDING":
                browser_payload.pop("event", None)
            else:
                browser_payload["event"] = event_upper
            print(
                json.dumps(
                    {
                        "ok": True,
                        "status": "needs_browser_publish",
                        "owner": owner,
                        "repo": repo,
                        "prNumber": pr_num,
                        "url": f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_num}/reviews",
                        "event": event_upper,
                        "payload": browser_payload,
                        "comments_exported": len(browser_payload.get("comments") or []),
                    },
                    indent=2,
                )
            )
            return
        sys.stderr.write(f"Failed to publish review: {exc}\n")
        sys.exit(1)

    if session is not None and os.path.isfile(active_session_path):
        try:
            if args.event == "PENDING" and resp.get("id"):
                session["githubPendingReviewId"] = int(resp["id"])
                session["githubPendingReviewUrl"] = resp.get("html_url") or f"https://github.com/{owner}/{repo}/pull/{pr_num}"
            else:
                session.pop("githubPendingReviewId", None)
                session.pop("githubPendingReviewUrl", None)
            with open(active_session_path, "w", encoding="utf-8") as sf:
                json.dump(session, sf, indent=2)
        except Exception:
            pass

    body_text = (payload.get("body") or "").strip()
    copied_to_clipboard = False
    if body_text and args.event == "PENDING":
        try:
            proc_pb = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            proc_pb.communicate(input=body_text.encode("utf-8"), timeout=3)
            copied_to_clipboard = proc_pb.returncode == 0
        except Exception:
            pass

    auth_info = get_auth_status()
    status_str = (
        ("pending_review_updated" if replaced_ids else "pending_review_created")
        if args.event == "PENDING"
        else "review_published"
    )
    print(
        json.dumps(
            {
                "ok": True,
                "status": status_str,
                "event": args.event,
                "published_as": (resp.get("user") or {}).get("login") or auth_info.get("login") or "",
                "review_id": resp.get("id"),
                "replaced_pending_review_ids": replaced_ids,
                "state": resp.get("state"),
                "html_url": resp.get("html_url") or f"https://github.com/{owner}/{repo}/pull/{pr_num}",
                "comments_exported": len(payload.get("comments") or []),
                "has_top_level_body": bool(body_text),
                "review_body": body_text,
                "copied_body_to_clipboard": copied_to_clipboard,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
