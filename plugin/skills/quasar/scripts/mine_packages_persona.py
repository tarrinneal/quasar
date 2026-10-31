#!/usr/bin/env python3
"""Incremental GitHub PR & Human Review Comment Harvester for `flutter/packages`.

Mines merged PRs in `flutter/packages` authored by humans, extracts all review comments
(inline diff threads, review summaries, and top-level PR comments) left by other humans,
with special focus on reviewers listed in `SUGGESTED_REVIEWERS.md`.

Supports chunked, resumable execution across multiple runs/days:
  - Saves pagination cursor and deduplicated PR corpus in `~/.gemini/quasar/personas/flutter_packages/`
  - Can harvest N pages/PRs per chunk (`--max-pages`, `--max-prs`) and resume seamlessly next time
  - Generates rich statistical & qualitative analysis + per-reviewer and per-category dossiers
"""

import argparse
import collections
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Set, Tuple

PERSONA_DATA_DIR = os.path.expanduser("~/.gemini/quasar/personas/flutter_packages")
STATE_FILE = os.path.join(PERSONA_DATA_DIR, "harvest_state.json")
CORPUS_JSONL = os.path.join(PERSONA_DATA_DIR, "prs_and_comments.jsonl")
SUMMARY_JSON = os.path.join(PERSONA_DATA_DIR, "corpus_summary.json")
DOSSIER_MD = os.path.join(PERSONA_DATA_DIR, "REVIEWER_DOSSIERS.md")
GITHUB_AUTH_PATH = os.path.expanduser("~/.gemini/quasar/github_auth.json")
DEFAULT_SUGGESTED_REVIEWERS_MD = "/Users/tarrinneal/work/packages/SUGGESTED_REVIEWERS.md"

BOT_LOGINS = {
    "dependabot",
    "dependabot[bot]",
    "dependabot-preview[bot]",
    "flutter-pub-roller-bot",
    "engine-flutter-autoroll",
    "skia-flutter-autoroll",
    "fluttergithubbot",
    "auto-submit",
    "auto-submit[bot]",
    "google-cla",
    "google-cla[bot]",
    "github-actions",
    "github-actions[bot]",
    "gemini-code-assist",
    "gemini-code-assist[bot]",
    "copilot",
    "copilot-pull-request-reviewer",
    "copilot[bot]",
    "codecov",
    "codecov[bot]",
    "codecov-commenter",
    "renovate",
    "renovate[bot]",
    "dart-pub-roller-bot",
    "flutter-roller-bot",
}

NOISE_PATTERNS = [
    r"^\s*lgtm\.?!?\s*$",
    r"^\s*sgtm\.?!?\s*$",
    r"^\s*lgtm[,!\s]+(thank|thanks|with|except|overall|just|modulo|pending|once|after).*$",
    r"^\s*(overall\s+)?looks\s+(really\s+)?(good|great)[,!\.\s]+(thank|thanks|with|left|just|overall|to\s+me).*$",
    r"^\s*thanks(\s+so\s+much)?(\s+for\s+[\w\s]+)?[!.]?\s*$",
    r"^\s*thank\s+you(\s+for\s+[\w\s]+)?[!.]?\s*$",
    r"^\s*\+1\s*$",
    r"^\s*/gemini\b",
    r"^\s*!build\b",
    r"cla:\s*yes",
    r"googlebot\s+here",
    r"because of the volume of prs we receive,\s+we require that new contributors use",
]


def get_github_token() -> str:
    if os.path.isfile(GITHUB_AUTH_PATH):
        try:
            with open(GITHUB_AUTH_PATH, "r", encoding="utf-8") as f:
                tok = json.load(f).get("token")
                if tok and str(tok).strip():
                    return str(tok).strip()
        except Exception:
            pass
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    raise RuntimeError("No GitHub token found in ~/.gemini/quasar/github_auth.json or env")


def is_human_user(author_obj: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(author_obj, dict):
        return False
    login = str(author_obj.get("login") or "").strip()
    if not login:
        return False
    typename = str(author_obj.get("__typename") or "")
    if typename and typename != "User":
        return False
    low = login.lower()
    if low in BOT_LOGINS:
        return False
    if low.endswith("[bot]") or low.endswith("-bot") or "autoroll" in low or "roller" in low:
        return False
    return True


def is_substantive_comment(body: str) -> bool:
    text = (body or "").strip()
    if len(text) < 8:
        return False
    single_line = re.sub(r"\s+", " ", text).strip()
    if len(single_line) < 90:
        for pat in NOISE_PATTERNS:
            if re.search(pat, single_line, flags=re.IGNORECASE):
                return False
    if re.search(r"because of the volume of prs we receive,\s+we require that new contributors use", single_line, flags=re.IGNORECASE):
        return False
    return True


def parse_suggested_reviewers(md_path: str) -> Dict[str, Dict[str, Any]]:
    """Parses SUGGESTED_REVIEWERS.md into a mapping of handle_lower -> {handle, packages, roles}."""
    reviewers: Dict[str, Dict[str, Any]] = {}
    if not os.path.isfile(md_path):
        return reviewers

    current_pkg = "general"
    with open(md_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip()
            pkg_match = re.match(r"^`([^`]+)`\s*:", line.strip())
            if pkg_match:
                current_pkg = pkg_match.group(1).strip()
                continue
            if "@" in line:
                role_match = re.search(r"\*\*([^*]+)\*\*\s*:", line)
                role = role_match.group(1).strip() if role_match else "Package Reviewer"
                # Ignore org/team handles like @flutter/windows-reviewers
                cleaned_line = re.sub(r"@[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", "", line)
                handles = re.findall(r"@([A-Za-z0-9_-]+)", cleaned_line)
                for h in handles:
                    if h.lower() == "flutter":
                        continue
                    key = h.lower()
                    entry = reviewers.setdefault(
                        key,
                        {
                            "handle": h,
                            "packages": [],
                            "roles": [],
                            "package_roles": [],
                        },
                    )
                    if current_pkg not in entry["packages"]:
                        entry["packages"].append(current_pkg)
                    if role not in entry["roles"]:
                        entry["roles"].append(role)
                    pr_str = f"{current_pkg} ({role})"
                    if pr_str not in entry["package_roles"]:
                        entry["package_roles"].append(pr_str)
    return reviewers


GRAPHQL_QUERY = """
query($cursor: String) {
  repository(owner: "flutter", name: "packages") {
    pullRequests(states: MERGED, first: 35, orderBy: {field: UPDATED_AT, direction: DESC}, after: $cursor) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        number
        title
        url
        createdAt
        mergedAt
        additions
        deletions
        changedFiles
        author {
          __typename
          login
        }
        files(first: 50) {
          nodes {
            path
          }
        }
        reviews(first: 40) {
          nodes {
            author {
              __typename
              login
            }
            state
            body
            submittedAt
          }
        }
        comments(first: 30) {
          nodes {
            author {
              __typename
              login
            }
            body
            createdAt
          }
        }
        reviewThreads(first: 50) {
          nodes {
            path
            line
            originalLine
            isResolved
            isOutdated
            comments(first: 15) {
              nodes {
                author {
                  __typename
                  login
                }
                body
                diffHunk
                createdAt
                url
              }
            }
          }
        }
      }
    }
  }
  rateLimit {
    cost
    remaining
    resetAt
  }
}
"""


def graphql_request(token: str, cursor: Optional[str]) -> Dict[str, Any]:
    payload = json.dumps({"query": GRAPHQL_QUERY, "variables": {"cursor": cursor}}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "quasar-persona-miner",
        },
        method="POST",
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.load(resp)
            if "errors" in data and not data.get("data"):
                raise RuntimeError(f"GraphQL errors: {data['errors']}")
            return data
        except Exception as e:
            if attempt == 3:
                raise
            time.sleep(2.0 * (attempt + 1))
    return {}


def classify_comment_categories(body: str, path: str = "") -> List[str]:
    low = (body or "").lower()
    p_low = (path or "").lower()
    cats: List[str] = []

    if "```suggestion" in low:
        cats.append("has_code_suggestion")
    if re.search(r"\b(nit|nitpick|super nit|minor|tiny nit)\b", low):
        cats.append("nit_or_polish")
    if re.search(r"\b(test|tests|testing|unittest|xctest|mockito|mock|fake|integration_test|assert|expect|coverage)\b", low) or "test" in p_low:
        cats.append("testing_and_verification")
    if re.search(r"\b(api|breaking|public|private|visiblefortesting|experimental|deprecated|interface|platform_interface|federated|abstract|subclass)\b", low):
        cats.append("api_and_architecture")
    if re.search(r"\b(null|nullable|non-null|nonnull|bang|cast|leak|dispose|detach|cancel|memory|thread|async|await|future|race|lifecycle| weak|strong|retain)\b", low):
        cats.append("nullability_lifecycle_safety")
    if re.search(r"\b(pigeon|jni|ffigen|jnigen|ffi|swift|objc|objective-c|kotlin|java|c\+\+|channel|methodchannel|eventchannel| pigeon)\b", low) or p_low.endswith((".kt", ".java", ".swift", ".m", ".mm", ".h", ".cc")):
        cats.append("native_platform_and_pigeon")
    if re.search(r"\b(changelog|pubspec|version|semver|readme|doc|dartdoc|comment|typo|spelling|license|label|override)\b", low) or p_low.endswith(("changelog.md", "pubspec.yaml", "readme.md")):
        cats.append("repo_hygiene_and_docs")
    if re.search(r"\b(why|could we|should we|what happens if|is there a reason|wondering if|can we|would it make sense)\b", low) or "?" in body:
        cats.append("collaborative_question")

    if not cats:
        cats.append("general_code_quality")
    return cats


def extract_packages_from_files(files: List[str]) -> List[str]:
    pkgs: Set[str] = set()
    for f in files:
        parts = f.split("/")
        if len(parts) >= 2 and parts[0] == "packages":
            pkgs.add(parts[1])
        elif parts and parts[0] in ("script", ".ci", ".github"):
            pkgs.add("repo_tooling")
    return sorted(pkgs)


def load_existing_corpus() -> Tuple[Dict[int, Dict[str, Any]], Dict[str, Any]]:
    os.makedirs(PERSONA_DATA_DIR, exist_ok=True)
    prs_by_num: Dict[int, Dict[str, Any]] = {}
    if os.path.isfile(CORPUS_JSONL):
        with open(CORPUS_JSONL, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    num = int(obj["number"])
                    prs_by_num[num] = obj
                except Exception:
                    continue
    state: Dict[str, Any] = {}
    if os.path.isfile(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
    return prs_by_num, state


def process_pr_node(
    node: Dict[str, Any],
    suggested_map: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(node, dict):
        return None
    author = node.get("author")
    if not is_human_user(author):
        return None

    pr_author_login = str(author.get("login"))
    pr_author_lower = pr_author_login.lower()
    pr_number = int(node.get("number") or 0)
    if not pr_number:
        return None

    file_nodes = ((node.get("files") or {}).get("nodes")) or []
    file_paths = [str(f.get("path")) for f in file_nodes if isinstance(f, dict) and f.get("path")]
    packages = extract_packages_from_files(file_paths)

    human_comments: List[Dict[str, Any]] = []
    approvers: Set[str] = set()
    reviewer_set: Set[str] = set()

    # 1. Reviews (summaries + approval state)
    review_nodes = ((node.get("reviews") or {}).get("nodes")) or []
    for rev in review_nodes:
        if not isinstance(rev, dict):
            continue
        r_author = rev.get("author")
        if not is_human_user(r_author):
            continue
        r_login = str(r_author.get("login"))
        if r_login.lower() == pr_author_lower:
            continue
        r_state = str(rev.get("state") or "")
        if r_state == "APPROVED":
            approvers.add(r_login)
        r_body = str(rev.get("body") or "").strip()
        if is_substantive_comment(r_body):
            is_sugg = r_login.lower() in suggested_map
            reviewer_set.add(r_login)
            human_comments.append(
                {
                    "type": "review_summary",
                    "reviewer": r_login,
                    "is_suggested_reviewer": is_sugg,
                    "review_state": r_state,
                    "path": "",
                    "line": None,
                    "diff_hunk": "",
                    "body": r_body,
                    "created_at": rev.get("submittedAt"),
                    "categories": classify_comment_categories(r_body, ""),
                }
            )

    # 2. Inline review threads (diff comments + author/reviewer back-and-forth)
    thread_nodes = ((node.get("reviewThreads") or {}).get("nodes")) or []
    for th in thread_nodes:
        if not isinstance(th, dict):
            continue
        t_path = str(th.get("path") or "")
        t_line = th.get("line") or th.get("originalLine")
        is_resolved = bool(th.get("isResolved"))
        c_nodes = ((th.get("comments") or {}).get("nodes")) or []
        if not c_nodes:
            continue

        thread_replies: List[Dict[str, str]] = []
        for idx, c in enumerate(c_nodes):
            if not isinstance(c, dict):
                continue
            c_author = c.get("author")
            c_login = str((c_author or {}).get("login") or "")
            c_body = str(c.get("body") or "").strip()
            if idx > 0 and c_login and c_body:
                thread_replies.append({"author": c_login, "body": c_body[:600]})

        # Extract all comments in the thread left by other humans
        for idx, c in enumerate(c_nodes):
            if not isinstance(c, dict):
                continue
            c_author = c.get("author")
            if not is_human_user(c_author):
                continue
            c_login = str(c_author.get("login"))
            if c_login.lower() == pr_author_lower:
                continue
            c_body = str(c.get("body") or "").strip()
            if not is_substantive_comment(c_body):
                continue
            is_sugg = c_login.lower() in suggested_map
            reviewer_set.add(c_login)
            diff_hunk = str(c.get("diffHunk") or "")
            # Keep last 12 lines of diffHunk to save space while preserving exact code context
            hunk_lines = diff_hunk.splitlines()
            if len(hunk_lines) > 14:
                diff_hunk = "\n".join(hunk_lines[-14:])
            human_comments.append(
                {
                    "type": "inline_diff_comment" if idx == 0 else "inline_thread_followup",
                    "reviewer": c_login,
                    "is_suggested_reviewer": is_sugg,
                    "path": t_path,
                    "line": t_line,
                    "is_resolved": is_resolved,
                    "diff_hunk": diff_hunk,
                    "body": c_body,
                    "created_at": c.get("createdAt"),
                    "url": c.get("url"),
                    "categories": classify_comment_categories(c_body, t_path),
                    "thread_replies": thread_replies if idx == 0 else [],
                }
            )

    # 3. Top-level PR issue comments by other humans
    issue_comment_nodes = ((node.get("comments") or {}).get("nodes")) or []
    for ic in issue_comment_nodes:
        if not isinstance(ic, dict):
            continue
        ic_author = ic.get("author")
        if not is_human_user(ic_author):
            continue
        ic_login = str(ic_author.get("login"))
        if ic_login.lower() == pr_author_lower:
            continue
        ic_body = str(ic.get("body") or "").strip()
        if not is_substantive_comment(ic_body):
            continue
        is_sugg = ic_login.lower() in suggested_map
        reviewer_set.add(ic_login)
        human_comments.append(
            {
                "type": "top_level_comment",
                "reviewer": ic_login,
                "is_suggested_reviewer": is_sugg,
                "path": "",
                "line": None,
                "diff_hunk": "",
                "body": ic_body,
                "created_at": ic.get("createdAt"),
                "categories": classify_comment_categories(ic_body, ""),
            }
        )

    return {
        "number": pr_number,
        "title": node.get("title"),
        "url": node.get("url"),
        "author": pr_author_login,
        "author_is_suggested_reviewer": pr_author_lower in suggested_map,
        "created_at": node.get("createdAt"),
        "merged_at": node.get("mergedAt"),
        "additions": node.get("additions"),
        "deletions": node.get("deletions"),
        "changed_files": node.get("changedFiles"),
        "packages": packages,
        "approvers": sorted(approvers),
        "commenting_reviewers": sorted(reviewer_set),
        "human_comments_count": len(human_comments),
        "suggested_reviewer_comments_count": sum(1 for c in human_comments if c.get("is_suggested_reviewer")),
        "comments": human_comments,
    }


def build_summary_and_dossiers(
    prs_by_num: Dict[int, Dict[str, Any]],
    suggested_map: Dict[str, Dict[str, Any]],
    state: Dict[str, Any],
) -> Dict[str, Any]:
    total_prs = len(prs_by_num)
    prs_with_comments = 0
    total_comments = 0
    suggested_comments = 0

    reviewer_stats: Dict[str, Dict[str, Any]] = {}
    category_counts: collections.Counter = collections.Counter()
    package_comment_counts: collections.Counter = collections.Counter()
    file_ext_counts: collections.Counter = collections.Counter()

    # Initialize all suggested reviewers so we see full coverage
    for key, info in suggested_map.items():
        h = info["handle"]
        reviewer_stats[key] = {
            "handle": h,
            "is_suggested_reviewer": True,
            "packages_owned": info["packages"],
            "roles": info["roles"],
            "package_roles": info["package_roles"],
            "comments_count": 0,
            "inline_comments_count": 0,
            "suggestions_count": 0,
            "questions_count": 0,
            "prs_reviewed_count": 0,
            "prs_approved_count": 0,
            "prs_authored_count": 0,
            "categories": collections.Counter(),
            "packages_reviewed": collections.Counter(),
            "sample_comments": [],
        }

    for pr_num, pr in sorted(prs_by_num.items(), key=lambda x: x[0], reverse=True):
        author_low = str(pr.get("author") or "").lower()
        if author_low in reviewer_stats:
            reviewer_stats[author_low]["prs_authored_count"] += 1

        for app in pr.get("approvers") or []:
            app_low = app.lower()
            if app_low not in reviewer_stats:
                reviewer_stats[app_low] = {
                    "handle": app,
                    "is_suggested_reviewer": app_low in suggested_map,
                    "packages_owned": [],
                    "roles": [],
                    "package_roles": [],
                    "comments_count": 0,
                    "inline_comments_count": 0,
                    "suggestions_count": 0,
                    "questions_count": 0,
                    "prs_reviewed_count": 0,
                    "prs_approved_count": 0,
                    "prs_authored_count": 0,
                    "categories": collections.Counter(),
                    "packages_reviewed": collections.Counter(),
                    "sample_comments": [],
                }
            reviewer_stats[app_low]["prs_approved_count"] += 1

        comments = [
            c for c in (pr.get("comments") or [])
            if is_substantive_comment(str(c.get("body") or ""))
        ]
        if comments:
            prs_with_comments += 1

        seen_reviewers_this_pr: Set[str] = set()
        for c in comments:
            total_comments += 1
            rev = str(c.get("reviewer") or "")
            rev_low = rev.lower()
            is_sugg = rev_low in suggested_map
            c["is_suggested_reviewer"] = is_sugg
            if is_sugg:
                suggested_comments += 1

            if rev_low not in reviewer_stats:
                reviewer_stats[rev_low] = {
                    "handle": rev,
                    "is_suggested_reviewer": is_sugg,
                    "packages_owned": [],
                    "roles": [],
                    "package_roles": [],
                    "comments_count": 0,
                    "inline_comments_count": 0,
                    "suggestions_count": 0,
                    "questions_count": 0,
                    "prs_reviewed_count": 0,
                    "prs_approved_count": 0,
                    "prs_authored_count": 0,
                    "categories": collections.Counter(),
                    "packages_reviewed": collections.Counter(),
                    "sample_comments": [],
                }

            st = reviewer_stats[rev_low]
            st["comments_count"] += 1
            if c.get("type") in ("inline_diff_comment", "inline_thread_followup"):
                st["inline_comments_count"] += 1
            cats = c.get("categories") or []
            for cat in cats:
                category_counts[cat] += 1
                st["categories"][cat] += 1
            if "has_code_suggestion" in cats:
                st["suggestions_count"] += 1
            if "collaborative_question" in cats:
                st["questions_count"] += 1

            for pkg in pr.get("packages") or []:
                package_comment_counts[pkg] += 1
                st["packages_reviewed"][pkg] += 1

            path = str(c.get("path") or "")
            if path:
                ext = os.path.splitext(path)[1].lower() or os.path.basename(path)
                file_ext_counts[ext] += 1

            seen_reviewers_this_pr.add(rev_low)

            # Save representative high-signal sample comments per reviewer (prefer inline with substantive length)
            body_text = str(c.get("body") or "").strip()
            if 25 <= len(body_text) <= 950 and len(st["sample_comments"]) < 60:
                st["sample_comments"].append(
                    {
                        "pr": pr_num,
                        "pr_title": pr.get("title"),
                        "type": c.get("type"),
                        "path": path,
                        "line": c.get("line"),
                        "categories": cats,
                        "body": body_text,
                        "diff_hunk": (c.get("diff_hunk") or "")[-400:],
                    }
                )

        for r_low in seen_reviewers_this_pr:
            reviewer_stats[r_low]["prs_reviewed_count"] += 1

    # Convert Counters to dicts for JSON serialization and sort sample_comments to prefer inline diff comments
    serializable_reviewers: List[Dict[str, Any]] = []
    for k, v in reviewer_stats.items():
        v_copy = dict(v)
        v_copy["categories"] = dict(v["categories"].most_common())
        v_copy["packages_reviewed"] = dict(v["packages_reviewed"].most_common(15))
        v_copy["sample_comments"] = sorted(
            v["sample_comments"],
            key=lambda sc: (
                1 if sc.get("type") == "inline_diff_comment" else (0.5 if sc.get("type") == "inline_thread_followup" else 0),
                1 if len(sc.get("body") or "") >= 55 else 0,
            ),
            reverse=True,
        )[:25]
        serializable_reviewers.append(v_copy)

    serializable_reviewers.sort(
        key=lambda r: (1 if r["is_suggested_reviewer"] else 0, r["comments_count"], r["prs_approved_count"]),
        reverse=True,
    )

    summary = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "harvest_state": state,
        "total_human_authored_prs": total_prs,
        "prs_with_human_comments": prs_with_comments,
        "total_human_review_comments": total_comments,
        "suggested_reviewer_comments": suggested_comments,
        "suggested_reviewer_share_pct": round((suggested_comments / total_comments * 100.0), 1) if total_comments else 0.0,
        "category_breakdown": dict(category_counts.most_common()),
        "top_packages_by_comments": dict(package_comment_counts.most_common(30)),
        "top_file_extensions_commented": dict(file_ext_counts.most_common(20)),
        "reviewers": serializable_reviewers,
    }

    with open(SUMMARY_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    # Render markdown dossier
    md_lines = [
        "# `flutter/packages` Human Reviewer Dossiers & Empirical Corpus Analysis",
        "",
        f"- **Updated At**: `{summary['generated_at']}`",
        f"- **Human-Authored Merged PRs Analyzed**: **{total_prs}** (across `{state.get('total_raw_prs_scanned', total_prs)}` raw merged PRs including bots)",
        f"- **PRs With Substantive Human Review Comments**: **{prs_with_comments}**",
        f"- **Total Substantive Human Review Comments Mined**: **{total_comments}**",
        f"- **Comments by `SUGGESTED_REVIEWERS.md` Maintainers**: **{suggested_comments}** (**{summary['suggested_reviewer_share_pct']}%** of all human comments)",
        f"- **Resumable Pagination Cursor**: `{state.get('end_cursor')}` (`has_next_page={state.get('has_next_page')}`)",
        "",
        "## 1. Review Comment Category Distribution",
        "| Category | Count |",
        "| :--- | ---: |",
    ]
    for cat, cnt in category_counts.most_common():
        md_lines.append(f"| `{cat}` | {cnt} |")

    md_lines.extend([
        "",
        "## 2. Suggested Reviewers Leaderboard (`SUGGESTED_REVIEWERS.md`)",
        "| Reviewer | Suggested Packages & Roles | Comments Left | Inline Diff Comments | PRs Reviewed (Commented) | PRs Approved | Top Focus Areas |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | :--- |",
    ])
    for r in serializable_reviewers:
        if not r["is_suggested_reviewer"]:
            continue
        top_cats = ", ".join(f"`{k}` ({v})" for k, v in list(r["categories"].items())[:3])
        roles_str = "; ".join(r["package_roles"][:4])
        if len(r["package_roles"]) > 4:
            roles_str += f" (+{len(r['package_roles']) - 4} more)"
        md_lines.append(
            f"| **@{r['handle']}** | {roles_str} | **{r['comments_count']}** | {r['inline_comments_count']} | {r['prs_reviewed_count']} | {r['prs_approved_count']} | {top_cats} |"
        )

    md_lines.extend([
        "",
        "## 3. Other Active Human Reviewers in `flutter/packages`",
        "| Reviewer | Comments Left | Inline Diff Comments | PRs Reviewed | PRs Approved | Top Packages |",
        "| :--- | ---: | ---: | ---: | ---: | :--- |",
    ])
    other_shown = 0
    for r in serializable_reviewers:
        if r["is_suggested_reviewer"] or r["comments_count"] < 3:
            continue
        top_pkgs = ", ".join(f"`{k}` ({v})" for k, v in list(r["packages_reviewed"].items())[:4])
        md_lines.append(
            f"| **@{r['handle']}** | **{r['comments_count']}** | {r['inline_comments_count']} | {r['prs_reviewed_count']} | {r['prs_approved_count']} | {top_pkgs} |"
        )
        other_shown += 1
        if other_shown >= 20:
            break

    md_lines.extend([
        "",
        "## 4. Detailed Reviewer Signatures & Representative Real Comments",
    ])
    for r in serializable_reviewers:
        if not r["is_suggested_reviewer"] and r["comments_count"] < 25:
            continue
        if r["comments_count"] == 0:
            continue
        badge = "⭐ Suggested Reviewer" if r["is_suggested_reviewer"] else "👤 Active Reviewer"
        md_lines.append(f"\n### @{r['handle']} ({badge})")
        if r["package_roles"]:
            md_lines.append(f"- **Listed Ownership (`SUGGESTED_REVIEWERS.md`)**: {', '.join(r['package_roles'])}")
        md_lines.append(
            f"- **Activity in Sample**: {r['comments_count']} substantive comments ({r['inline_comments_count']} inline, {r['suggestions_count']} with `suggestion` blocks, {r['questions_count']} framed as questions) across {r['prs_reviewed_count']} PRs; {r['prs_approved_count']} approvals."
        )
        top_cats = ", ".join(f"`{k}`: {v}" for k, v in list(r["categories"].items())[:5])
        md_lines.append(f"- **Comment Breakdown**: {top_cats}")
        md_lines.append("- **Representative Real Review Comments**:")
        for sc in r["sample_comments"][:10]:
            loc = f"`{sc['path']}:{sc['line']}`" if sc.get("path") else "*(PR Review Summary / Top-Level)*"
            body_one_line = re.sub(r"\s+", " ", sc["body"]).strip()
            if len(body_one_line) > 340:
                body_one_line = body_one_line[:337] + "..."
            md_lines.append(f"  - **[PR #{sc['pr']}]** ({loc}): > {body_one_line}")

    with open(DOSSIER_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")

    return summary


TARGETED_SEARCH_QUERY = """
query($searchQuery: String!) {
  search(query: $searchQuery, type: ISSUE, first: 35) {
    nodes {
      ... on PullRequest {
        number
        title
        url
        createdAt
        mergedAt
        additions
        deletions
        changedFiles
        author {
          __typename
          login
        }
        files(first: 50) {
          nodes {
            path
          }
        }
        reviews(first: 40) {
          nodes {
            author {
              __typename
              login
            }
            state
            body
            submittedAt
          }
        }
        comments(first: 30) {
          nodes {
            author {
              __typename
              login
            }
            body
            createdAt
          }
        }
        reviewThreads(first: 50) {
          nodes {
            path
            line
            originalLine
            isResolved
            isOutdated
            comments(first: 15) {
              nodes {
                author {
                  __typename
                  login
                }
                body
                diffHunk
                createdAt
                url
              }
            }
          }
        }
      }
    }
  }
  rateLimit {
    cost
    remaining
    resetAt
  }
}
"""


def graphql_search_prs(token: str, search_query: str) -> Dict[str, Any]:
    payload = json.dumps({"query": TARGETED_SEARCH_QUERY, "variables": {"searchQuery": search_query}}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "quasar-persona-miner",
        },
        method="POST",
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.load(resp)
            if "errors" in data and not data.get("data"):
                raise RuntimeError(f"GraphQL search errors: {data['errors']}")
            return data
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2.0 * (attempt + 1))
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Harvest merged human PRs and human review comments from flutter/packages.")
    parser.add_argument("--max-pages", type=int, default=15, help="Number of GraphQL pages (35 PRs/page) to fetch in this chunk.")
    parser.add_argument("--target-suggested", action="store_true", help="Also run targeted search queries for SUGGESTED_REVIEWERS.md maintainers with < 50 comments.")
    parser.add_argument("--reset", action="store_true", help="Reset pagination cursor to start from newest merged PRs.")
    parser.add_argument("--analyze-only", action="store_true", help="Skip network fetching and rebuild summary/dossiers from local corpus.")
    parser.add_argument("--suggested-reviewers-md", default=DEFAULT_SUGGESTED_REVIEWERS_MD, help="Path to SUGGESTED_REVIEWERS.md")
    args = parser.parse_args()

    suggested_map = parse_suggested_reviewers(args.suggested_reviewers_md)
    prs_by_num, state = load_existing_corpus()

    if not args.analyze_only:
        token = get_github_token()
        cursor = None if args.reset else state.get("end_cursor")
        has_next = True if args.reset else bool(state.get("has_next_page", True))
        total_raw_scanned = int(state.get("total_raw_prs_scanned") or 0)

        pages_fetched = 0
        new_human_prs = 0
        new_comments = 0

        while pages_fetched < args.max_pages and has_next:
            t0 = time.time()
            resp = graphql_request(token, cursor)
            repo_data = (resp.get("data") or {}).get("repository") or {}
            pr_conn = repo_data.get("pullRequests") or {}
            rate_info = (resp.get("data") or {}).get("rateLimit") or {}

            nodes = pr_conn.get("nodes") or []
            page_info = pr_conn.get("pageInfo") or {}
            has_next = bool(page_info.get("hasNextPage"))
            cursor = page_info.get("endCursor")
            pages_fetched += 1
            total_raw_scanned += len(nodes)

            for node in nodes:
                rec = process_pr_node(node, suggested_map)
                if rec is None:
                    continue
                pr_num = rec["number"]
                if pr_num not in prs_by_num:
                    new_human_prs += 1
                    new_comments += rec["human_comments_count"]
                prs_by_num[pr_num] = rec

            # Save corpus and state after every page so progress is crash-safe
            with open(CORPUS_JSONL, "w", encoding="utf-8") as f:
                for _, pr_obj in sorted(prs_by_num.items(), key=lambda x: x[0], reverse=True):
                    f.write(json.dumps(pr_obj) + "\n")

            state = {
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "end_cursor": cursor,
                "has_next_page": has_next,
                "total_raw_prs_scanned": total_raw_scanned,
                "total_human_prs_saved": len(prs_by_num),
                "rate_limit_remaining": rate_info.get("remaining"),
            }
            with open(STATE_FILE, "w", encoding="utf-8") as sf:
                json.dump(state, sf, indent=2)

            dt = time.time() - t0
            print(
                f"[Page {pages_fetched}/{args.max_pages}] Scanned {len(nodes)} raw PRs ({dt:.1f}s) | "
                f"Corpus: {len(prs_by_num)} human PRs (+{new_human_prs} new), +{new_comments} human comments | "
                f"GraphQL remaining: {rate_info.get('remaining')}",
                flush=True,
            )

        if args.target_suggested:
            # Count current comments per suggested reviewer
            rev_counts: collections.Counter = collections.Counter()
            for pr_obj in prs_by_num.values():
                for c in pr_obj.get("comments") or []:
                    if is_substantive_comment(str(c.get("body") or "")):
                        rev_counts[str(c.get("reviewer") or "").lower()] += 1

            for rev_low, info in suggested_map.items():
                handle = info["handle"]
                if rev_counts[rev_low] >= 50:
                    continue
                t0 = time.time()
                q = f"repo:flutter/packages is:pr is:merged reviewed-by:{handle}"
                try:
                    resp = graphql_search_prs(token, q)
                    nodes = (((resp.get("data") or {}).get("search")) or {}).get("nodes") or []
                    rate_info = (resp.get("data") or {}).get("rateLimit") or {}
                    added_prs = 0
                    added_rev_comments = 0
                    for node in nodes:
                        rec = process_pr_node(node, suggested_map)
                        if rec is None:
                            continue
                        pr_num = rec["number"]
                        if pr_num not in prs_by_num:
                            added_prs += 1
                            added_rev_comments += sum(
                                1 for c in rec["comments"] if str(c.get("reviewer") or "").lower() == rev_low
                            )
                            prs_by_num[pr_num] = rec
                    dt = time.time() - t0
                    print(
                        f"[Targeted @{handle}] Fetched {len(nodes)} PRs ({dt:.1f}s) -> +{added_prs} new PRs, "
                        f"+{added_rev_comments} comments by @{handle} | GraphQL remaining: {rate_info.get('remaining')}",
                        flush=True,
                    )
                except Exception as e:
                    print(f"[Targeted @{handle}] Warning: {e}", flush=True)

            with open(CORPUS_JSONL, "w", encoding="utf-8") as f:
                for _, pr_obj in sorted(prs_by_num.items(), key=lambda x: x[0], reverse=True):
                    f.write(json.dumps(pr_obj) + "\n")

    summary = build_summary_and_dossiers(prs_by_num, suggested_map, state)
    print(
        f"\nDone! Total Human PRs: {summary['total_human_authored_prs']} | "
        f"PRs w/ Human Comments: {summary['prs_with_human_comments']} | "
        f"Total Human Comments: {summary['total_human_review_comments']} "
        f"(Suggested Reviewers: {summary['suggested_reviewer_comments']} / {summary['suggested_reviewer_share_pct']}%)"
    )


if __name__ == "__main__":
    main()
