#!/usr/bin/env python3
"""Manages the Quasar Self-Review / Fix Selected Comments workflow.

Allows the PR/branch owner to select any combination of:
- Quasar AI candidate findings (blocking, design, nit)
- Uncertain preference-calibration findings
- Existing GitHub PR review comments from teammates
- Inline user notes/Q&A threads

Responsibilities:
1. `--toggle-select --thread-id <ID> [--selected true|false]`:
   Updates `selectedForFix` on the thread in `active_session.json`.
2. `--prepare-fix --thread-ids <ID1,ID2,...> [--instructions "..."]`:
   - Marks the selected threads as queued/in-progress for fixing in `active_session.json`.
   - Records a positive preference learning signal in `~/.gemini/quasar/preferences.json` for
     any Quasar candidate/uncertain finding the user chose to fix (teaching Quasar that this
     finding was valuable and actionable).
   - Builds a structured fix manifest at `~/.gemini/quasar/fix_request.json` mapping each
     comment to the real editable file path in `session.repoDir`, line range, surrounding code,
     and comment instructions.
3. `--complete-fix --thread-ids <ID1,ID2,...> [--summary "..."]`:
   - Syncs the modified files from `session.repoDir` into `session.headSnapshotRoot` so the
     native side-by-side `vscode.diff` editor immediately reflects the working-tree fixes.
   - Marks the fixed threads as `status = "fixed"` (`✅ Fixed`) in `active_session.json` and
     appends a completion message to each inline comment thread.
"""

import argparse
import datetime
import json
import os
import shutil
import sys
from typing import Any, Dict, List, Optional

from collect_pr_context import (
    REVIEW_STATE_DIR,
    archive_review_session,
    get_session_paths,
    render_cliffs_notes_markdown,
)


def resolve_fix_paths(
    workspace_dir: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> tuple:
    state_dir, active_session_path, cliffs_notes_md_path = get_session_paths(
        workspace_dir, thread_id=thread_id
    )
    os.makedirs(state_dir, exist_ok=True)
    fix_request_path = os.path.join(state_dir, "fix_request.json")
    return state_dir, active_session_path, cliffs_notes_md_path, fix_request_path


def load_session(session_path: str) -> Dict[str, Any]:
    if not os.path.isfile(session_path):
        raise FileNotFoundError(f"No active Quasar session found at {session_path}")
    with open(session_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_session(session: Dict[str, Any], session_path: str, md_path: str) -> None:
    session["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with open(session_path, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)
    md_text = None
    try:
        md_text = render_cliffs_notes_markdown(session)
        with open(md_path, "w", encoding="utf-8") as mf:
            mf.write(md_text)
    except Exception:
        pass
    archive_review_session(session, md_text)


def read_code_snippet(file_path: str, start_line: int, end_line: int, pad: int = 12) -> str:
    if not file_path or not os.path.isfile(file_path):
        return ""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.splitlines()
        s_idx = max(0, start_line - 1 - pad)
        e_idx = min(len(lines), end_line + pad)
        out = []
        for idx in range(s_idx, e_idx):
            line_no = idx + 1
            marker = ">>" if start_line <= line_no <= end_line else "  "
            out.append(f"{marker} {line_no:4d}: {lines[idx]}")
        return "\n".join(out)
    except Exception:
        return ""


def record_positive_preference_for_fixed_threads(
    session: Dict[str, Any], fixed_threads: List[Dict[str, Any]]
) -> int:
    """Records a positive preference signal for Quasar findings that the user chose to fix."""
    try:
        from manage_preferences import (
            add_or_update_rule,
            build_prompt_summary,
            load_preferences,
            normalize_sev_bucket,
            save_preferences,
        )
    except Exception:
        return 0

    prefs = load_preferences()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    repo_name = (session.get("pr") or {}).get("repo")
    recorded = 0

    for t in fixed_threads:
        if t.get("kind") not in ("ai_candidate", "user_staged") and t.get("status") != "uncertain":
            continue
        if t.get("feedbackRating") in ("up", "calibrated"):
            continue

        title = (t.get("title") or "").strip()
        if not title:
            continue

        was_uncertain = t.get("status") == "uncertain" or bool(t.get("uncertain"))
        t["feedbackRating"] = "calibrated" if was_uncertain else "up"
        cat = t.get("preferenceCategory") or "general"
        rule_text = f"Flag and catch issues similar to: {title}"

        add_or_update_rule(
            prefs,
            rule_text=rule_text,
            sentiment="prefer",
            category=cat,
            repo=None,
            source_title=title,
            origin="learned",
        )

        sev_bucket = normalize_sev_bucket(t.get("severity", "DESIGN"))
        cat_stats = prefs.setdefault("categoryStats", {})
        bucket_stats = cat_stats.setdefault(
            sev_bucket, {"upvotes": 0, "downvotes": 0, "staged": 0, "dismissed": 0}
        )
        bucket_stats["upvotes"] = bucket_stats.get("upvotes", 0) + 1
        if was_uncertain:
            unc_stats = cat_stats.setdefault(
                "UNCERTAIN", {"calibrated_keep": 0, "calibrated_skip": 0}
            )
            unc_stats["calibrated_keep"] = unc_stats.get("calibrated_keep", 0) + 1

        history = prefs.setdefault("feedbackHistory", [])
        history.append(
            {
                "id": f"fb-fix-{int(datetime.datetime.now().timestamp())}-{recorded + 1}",
                "timestamp": now_iso,
                "repo": repo_name,
                "threadId": t.get("id"),
                "path": t.get("path") or "",
                "findingTitle": title,
                "action": "fix_selected",
                "userFeedback": "Author selected this finding to be fixed in the branch.",
                "learnedRule": rule_text,
            }
        )
        prefs["feedbackHistory"] = history[-100:]
        recorded += 1

    if recorded > 0:
        save_preferences(prefs)
        session["reviewerPreferences"] = build_prompt_summary(prefs, repo_name)

    return recorded


def parse_thread_ids(raw_ids: Optional[str], session: Dict[str, Any]) -> List[str]:
    if raw_ids:
        return [x.strip() for x in raw_ids.split(",") if x.strip()]
    return [
        str(t.get("id"))
        for t in (session.get("threads") or [])
        if t.get("selectedForFix") and t.get("status") != "fixed"
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Quasar Self-Review Comment Selection & Agent Fixes.")
    parser.add_argument("--toggle-select", action="store_true", help="Toggle or set selectedForFix on one or more threads")
    parser.add_argument("--thread-id", help="Single thread ID")
    parser.add_argument("--thread-ids", help="Comma-separated thread IDs")
    parser.add_argument("--selected", choices=["true", "false", "toggle"], default="toggle", help="Desired selection state")
    parser.add_argument("--prepare-fix", action="store_true", help="Prepare fix_request.json, record preference signal, and output agent prompt")
    parser.add_argument("--instructions", default="", help="Optional custom instructions from the user for how to fix the selected comments")
    parser.add_argument("--complete-fix", action="store_true", help="Mark threads as fixed in active_session.json and sync head snapshots from working tree")
    parser.add_argument("--summary", default="Applied fix in local working tree.", help="Summary of the fix applied by the agent")
    parser.add_argument("--workspace-dir", default=None, help="Window workspace directory")
    args = parser.parse_args()

    target_ids: List[str] = []
    if args.thread_id:
        target_ids.append(args.thread_id.strip())
    if args.thread_ids:
        for tid in args.thread_ids.split(","):
            if tid.strip() and tid.strip() not in target_ids:
                target_ids.append(tid.strip())

    first_tid = target_ids[0] if target_ids else None
    state_dir, active_session_path, cliffs_notes_md_path, fix_request_path = resolve_fix_paths(
        args.workspace_dir, thread_id=first_tid
    )

    try:
        session = load_session(active_session_path)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return

    threads: List[Dict[str, Any]] = session.get("threads") or []

    if args.toggle_select:
        updated = []
        for t in threads:
            if str(t.get("id")) in target_ids:
                if args.selected == "true":
                    t["selectedForFix"] = True
                elif args.selected == "false":
                    t["selectedForFix"] = False
                else:
                    t["selectedForFix"] = not bool(t.get("selectedForFix", False))
                updated.append({"id": t.get("id"), "selectedForFix": t["selectedForFix"]})
        save_session(session, active_session_path, cliffs_notes_md_path)
        selected_count = sum(1 for t in threads if t.get("selectedForFix") and t.get("status") != "fixed")
        print(
            json.dumps(
                {
                    "ok": True,
                    "updated": updated,
                    "selectedCount": selected_count,
                },
                indent=2,
            )
        )
        return

    if args.prepare_fix:
        if not target_ids:
            target_ids = parse_thread_ids(None, session)
        selected_threads = [t for t in threads if str(t.get("id")) in target_ids]
        if not selected_threads:
            print(json.dumps({"ok": False, "error": "No comments selected to fix."}))
            return

        repo_dir = session.get("repoDir") or os.getcwd()
        head_root = session.get("headSnapshotRoot") or ""
        pr = session.get("pr") or {}

        for t in selected_threads:
            t["selectedForFix"] = True
            t["fixStatus"] = "in_progress"
            if args.instructions.strip():
                t["fixUserInstructions"] = args.instructions.strip()

        prefs_recorded = record_positive_preference_for_fixed_threads(session, selected_threads)
        save_session(session, active_session_path, cliffs_notes_md_path)

        fix_items: List[Dict[str, Any]] = []
        for idx, t in enumerate(selected_threads, 1):
            rel_path = t.get("path") or ""
            abs_repo_path = os.path.join(repo_dir, rel_path) if rel_path else ""
            snap_path = os.path.join(head_root, rel_path) if (head_root and rel_path) else ""
            start_l = int(t.get("startLine") or 1)
            end_l = int(t.get("endLine") or start_l)

            all_messages = [
                f"{m.get('author', 'Comment')}: {m.get('body', '')}"
                for m in (t.get("messages") or [])
                if m.get("body")
            ]
            fix_items.append(
                {
                    "index": idx,
                    "threadId": str(t.get("id")),
                    "kind": t.get("kind", "ai_candidate"),
                    "severity": t.get("severity", "DESIGN"),
                    "title": t.get("title") or f"Comment #{idx}",
                    "relativePath": rel_path,
                    "editableFilePath": abs_repo_path,
                    "headSnapshotPath": snap_path,
                    "startLine": start_l,
                    "endLine": end_l,
                    "whyItMatters": t.get("whyItMatters") or "",
                    "commentToAddress": t.get("draftComment") or ("\n\n".join(all_messages)),
                    "threadDiscussion": all_messages,
                    "codeSnippet": read_code_snippet(
                        abs_repo_path if os.path.isfile(abs_repo_path) else snap_path,
                        start_l,
                        end_l,
                    ),
                }
            )

        manifest = {
            "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "sessionId": session.get("sessionId"),
            "stateDir": state_dir,
            "repoDir": repo_dir,
            "headSnapshotRoot": head_root,
            "pr": {
                "number": pr.get("number"),
                "title": pr.get("title"),
                "headRefName": pr.get("headRefName"),
                "baseRefName": pr.get("baseRefName"),
                "isOwnedByUser": pr.get("isOwnedByUser", True),
            },
            "userInstructions": args.instructions.strip(),
            "threadIds": [item["threadId"] for item in fix_items],
            "items": fix_items,
        }
        with open(fix_request_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        script_path = os.path.abspath(__file__)
        ids_csv = ",".join(manifest["threadIds"])
        item_lines = []
        for item in fix_items:
            item_lines.append(
                f"{item['index']}. **[{item['severity']}] {item['title']}** (Thread ID: `{item['threadId']}`)\n"
                f"   - **Editable File**: `{item['editableFilePath']}` (Lines {item['startLine']}–{item['endLine']})\n"
                + (f"   - **Context / Why It Matters**: {item['whyItMatters']}\n" if item["whyItMatters"] else "")
                + f"   - **Comment / Issue to Fix**: {item['commentToAddress']}"
            )

        custom_instr_block = (
            f"\n**Additional User Instructions**:\n> {args.instructions.strip()}\n"
            if args.instructions.strip()
            else ""
        )

        ws_arg = f' --workspace-dir "{session.get("workspaceDir") or repo_dir}"' if (session.get("workspaceDir") or repo_dir) else ""
        agent_prompt = (
            f"Please fix the following {len(fix_items)} selected review comment(s) in `{repo_dir}` "
            f"(full structured context saved at `{fix_request_path}`):\n\n"
            + "\n\n".join(item_lines)
            + "\n"
            + custom_instr_block
            + "\n### Instructions for Applying Fixes:\n"
            f"1. Edit the real working-tree files under `{repo_dir}` (do **not** edit the snapshot copies directly, and do **not** create a git commit unless asked).\n"
            f"2. Run any relevant analyzer or unit tests to verify your fix compiles and passes.\n"
            f"3. Once fixed, sync the Quasar review session and mark the comment thread(s) as `✅ Fixed` by running:\n"
            f"   ```bash\n"
            f'   python3 "{script_path}" --complete-fix --thread-ids "{ids_csv}"{ws_arg} --summary "<CONCISE_SUMMARY_OF_FIXES>"\n'
            f"   ```"
        )

        print(
            json.dumps(
                {
                    "ok": True,
                    "fixRequestPath": fix_request_path,
                    "repoDir": repo_dir,
                    "selectedCount": len(fix_items),
                    "preferencesRecorded": prefs_recorded,
                    "threadIds": manifest["threadIds"],
                    "agentPrompt": agent_prompt,
                },
                indent=2,
            )
        )
        return

    if args.complete_fix:
        if not target_ids:
            if os.path.isfile(fix_request_path):
                try:
                    with open(fix_request_path, "r", encoding="utf-8") as f:
                        req = json.load(f)
                    target_ids = [str(x) for x in (req.get("threadIds") or [])]
                except Exception:
                    pass
        if not target_ids:
            target_ids = [
                str(t.get("id"))
                for t in threads
                if t.get("fixStatus") == "in_progress" or t.get("selectedForFix")
            ]

        repo_dir = session.get("repoDir") or ""
        head_root = session.get("headSnapshotRoot") or ""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        synced_files: List[str] = []
        completed_ids: List[str] = []

        for t in threads:
            if str(t.get("id")) not in target_ids:
                continue
            rel_path = t.get("path") or ""
            if repo_dir and head_root and rel_path:
                src_file = os.path.join(repo_dir, rel_path)
                dst_file = os.path.join(head_root, rel_path)
                if os.path.isfile(src_file):
                    os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                    shutil.copy2(src_file, dst_file)
                    if rel_path not in synced_files:
                        synced_files.append(rel_path)

            t["status"] = "fixed"
            t["fixStatus"] = "fixed"
            t["selectedForFix"] = False
            t["fixedAt"] = now_iso
            t["fixedSummary"] = args.summary.strip()
            msgs = t.setdefault("messages", [])
            msgs.append(
                {
                    "author": "Quasar Fix Agent",
                    "body": f"✅ **Fixed in working tree** (`{rel_path}`):\n\n{args.summary.strip()}",
                    "timestamp": now_iso,
                }
            )
            completed_ids.append(str(t.get("id")))

        save_session(session, active_session_path, cliffs_notes_md_path)
        print(
            json.dumps(
                {
                    "ok": True,
                    "completedThreadIds": completed_ids,
                    "syncedSnapshotFiles": synced_files,
                    "summary": args.summary.strip(),
                },
                indent=2,
            )
        )
        return

    # Default: print current selection status
    queued = [
        {"id": t.get("id"), "title": t.get("title"), "path": t.get("path"), "status": t.get("status")}
        for t in threads
        if t.get("selectedForFix") and t.get("status") != "fixed"
    ]
    fixed = [
        {"id": t.get("id"), "title": t.get("title"), "path": t.get("path"), "fixedSummary": t.get("fixedSummary")}
        for t in threads
        if t.get("status") == "fixed"
    ]
    print(
        json.dumps(
            {
                "ok": True,
                "isOwnedByUser": (session.get("pr") or {}).get("isOwnedByUser", True),
                "queuedCount": len(queued),
                "queuedThreads": queued,
                "fixedCount": len(fixed),
                "fixedThreads": fixed,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
