#!/usr/bin/env python3
"""Posts a Quasar answer or updates a Quasar review comment directly inside an
inline comment thread in active_session.json.

When the user comments on a Quasar comment to ask the agent to change/revise it
(or asks a question inside a native comment thread in the diff editor),
Antigravity runs this script so the editor thread, sidebar, and study guide
update live in place.

Usage:
  python3 answer_comment_question.py \
    --thread-id finding-1 \
    --updated-draft "Updated GitHub comment draft..." \
    [--answer "Brief note on what was changed..."] \
    [--updated-title "Optional updated title"] \
    [--updated-why "Optional updated rationale"] \
    [--updated-severity "NIT|DESIGN|BLOCKING"] \
    [--start-line 142] [--end-line 148] \
    [--stage | --unstage | --dismiss] \
    [--learned-rule "Optional generalized preference rule"] \
    [--sentiment "prefer|avoid|custom"] \
    [--workspace-dir /path/to/workspace]
"""

import argparse
import datetime
import json
import os
import sys
from typing import Any, Dict

from collect_pr_context import (
    archive_review_session,
    get_session_paths,
    render_cliffs_notes_markdown,
)


def rebuild_primary_finding_message_body(t: Dict[str, Any]) -> str:
    """Rebuilds the primary Quasar message body for an AI finding thread so the
    top message in the inline VS Code CommentThread always reflects the latest
    title, rationale, and draftComment.
    """
    title = t.get("title") or "Review Finding"
    why = t.get("whyItMatters") or ""
    draft = t.get("draftComment") or ""
    if t.get("status") == "uncertain":
        uncertainty_q = (
            t.get("uncertaintyQuestion")
            or f'Do you want Quasar to flag issues like "{title}" in future reviews, or skip this pattern?'
        )
        return (
            f"### 🤔 Preference Check: {title}\n\n"
            f"**Question for You**: *{uncertainty_q}*\n\n"
            f"*Context & Trade-off*: {why}\n\n"
            f"---\n"
            f"**Candidate GitHub Comment (if kept)**:\n> {draft}\n\n"
            f"*(Use the **🤔 Calibrate Preference** button above or 👍/👎 to teach Quasar how to handle this in the future.)*"
        )
    parts = [f"**{title}**"]
    if why:
        parts.append(f"*CliffsNotes Context*: {why}")
    if draft:
        parts.append(f"---\n**Draft GitHub Comment**:\n> {draft}")
    return "\n\n".join(parts)


def sync_primary_message_if_applicable(t: Dict[str, Any], msgs: list) -> None:
    """Updates msgs[0]['body'] in place if msgs[0] is the initial Quasar finding card."""
    if not msgs:
        return
    first = msgs[0]
    first_author = str(first.get("author") or "")
    first_body = str(first.get("body") or "")
    if first_author == "Quasar" and (
        "**Draft GitHub Comment**:" in first_body
        or "**Candidate GitHub Comment (if kept)**:" in first_body
        or t.get("kind") in ("ai_candidate", "user_staged")
    ):
        first["body"] = rebuild_primary_finding_message_body(t)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Answer an inline comment question or update a Quasar comment in place."
    )
    parser.add_argument("--thread-id", required=True, help="Target comment thread ID")
    parser.add_argument(
        "--answer",
        default="",
        help="Markdown response or explanation of the change to append to the comment thread",
    )
    parser.add_argument("--updated-draft", help="Updated GitHub draft comment text")
    parser.add_argument("--updated-title", help="Optional updated finding title")
    parser.add_argument("--updated-why", help="Optional updated CliffsNotes rationale (whyItMatters)")
    parser.add_argument(
        "--updated-severity",
        help="Optional updated severity (e.g. BLOCKING, DESIGN, NIT)",
    )
    parser.add_argument("--start-line", type=int, help="Optional updated start line (1-based)")
    parser.add_argument("--end-line", type=int, help="Optional updated end line (1-based)")
    parser.add_argument(
        "--stage",
        action="store_true",
        help="Mark the thread as staged for GitHub Review",
    )
    parser.add_argument(
        "--unstage",
        action="store_true",
        help="Move the thread to unstaged candidate draft status",
    )
    parser.add_argument(
        "--dismiss",
        action="store_true",
        help="Dismiss/remove the comment thread",
    )
    parser.add_argument(
        "--learned-rule",
        help="Optional generalized preference rule learned from the user's comment",
    )
    parser.add_argument(
        "--sentiment",
        choices=["prefer", "avoid", "custom"],
        help="Optional sentiment for --learned-rule",
    )
    parser.add_argument("--workspace-dir", default=None, help="Window workspace directory")
    args = parser.parse_args()

    if not args.answer.strip() and args.updated_draft is None and not args.updated_title and not args.updated_why and not args.updated_severity and not args.dismiss and not args.stage and not args.unstage:
        sys.stderr.write("Provide at least --answer or --updated-draft (or another update flag).\n")
        sys.exit(1)

    _state_dir, active_session_path, cliffs_notes_md_path = get_session_paths(
        args.workspace_dir, thread_id=args.thread_id
    )

    if not os.path.isfile(active_session_path):
        sys.stderr.write(f"No active session found at {active_session_path}\n")
        sys.exit(1)

    with open(active_session_path, "r", encoding="utf-8") as f:
        session: Dict[str, Any] = json.load(f)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    matched = False
    original_draft = ""
    finding_path = ""
    finding_title = ""

    for t in session.get("threads") or []:
        if t.get("id") == args.thread_id:
            matched = True
            original_draft = (t.get("draftComment") or "").strip()
            finding_path = t.get("path") or ""
            finding_title = t.get("title") or ""

            msgs = [
                m
                for m in (t.get("messages") or [])
                if not m.get("pending")
                and "⏳ Quasar is analyzing" not in (m.get("body") or "")
                and "⏳ Quasar is updating" not in (m.get("body") or "")
            ]

            if args.updated_title and args.updated_title.strip():
                t["title"] = args.updated_title.strip()
                finding_title = t["title"]
            if args.updated_why and args.updated_why.strip():
                t["whyItMatters"] = args.updated_why.strip()
            if args.updated_severity and args.updated_severity.strip():
                t["severity"] = args.updated_severity.strip().upper()
            if args.start_line is not None and args.start_line >= 1:
                t["startLine"] = int(args.start_line)
                if args.end_line is None and int(t.get("endLine") or 1) < int(args.start_line):
                    t["endLine"] = int(args.start_line)
            if args.end_line is not None and args.end_line >= 1:
                t["endLine"] = max(int(t.get("startLine") or 1), int(args.end_line))

            body_parts = []
            if args.answer and args.answer.strip():
                body_parts.append(args.answer.strip())

            if args.updated_draft is not None:
                t["draftComment"] = args.updated_draft.strip()
                body_parts.append(
                    f"✏️ **Updated GitHub Comment**:\n> {t['draftComment']}"
                )

            if args.dismiss:
                t["status"] = "dismissed"
                t["unstagedByUser"] = True
                if not body_parts:
                    body_parts.append("🗑️ **Dismissed comment per your feedback.**")
            elif args.unstage:
                t["status"] = "candidate"
                t["unstagedByUser"] = True
                if not body_parts:
                    body_parts.append("📝 **Moved comment to unstaged drafts.**")
            elif args.stage:
                t["status"] = "staged"
                t.pop("unstagedByUser", None)
                if t.get("status") == "uncertain":
                    t["uncertaintyResolved"] = True

            sync_primary_message_if_applicable(t, msgs)

            if body_parts:
                msgs.append(
                    {
                        "author": "Quasar",
                        "body": "\n\n---\n".join(body_parts)
                        if len(body_parts) > 1
                        else body_parts[0],
                        "timestamp": now_iso,
                    }
                )
            t["messages"] = msgs
            break

    if not matched:
        sys.stderr.write(f"Thread ID '{args.thread_id}' not found in {active_session_path}\n")
        sys.exit(1)

    # Record phrasing example and/or learned rule in preferences.json if applicable
    try:
        from manage_preferences import (
            add_or_update_rule,
            build_prompt_summary,
            load_preferences,
            save_preferences,
        )

        prefs = load_preferences()
        prefs_changed = False
        if (
            args.updated_draft is not None
            and original_draft
            and args.updated_draft.strip()
            and original_draft != args.updated_draft.strip()
        ):
            edits = prefs.setdefault("editedDraftExamples", [])
            edits.append(
                {
                    "timestamp": now_iso,
                    "path": finding_path,
                    "title": finding_title,
                    "originalDraft": original_draft,
                    "editedDraft": args.updated_draft.strip(),
                }
            )
            prefs["editedDraftExamples"] = edits[-20:]
            prefs_changed = True

        if args.learned_rule and args.learned_rule.strip():
            add_or_update_rule(
                prefs,
                rule_text=args.learned_rule.strip(),
                sentiment=args.sentiment or "custom",
                category="comment_tone",
                source_title=finding_title,
                origin="learned",
            )
            prefs_changed = True

        if prefs_changed:
            save_preferences(prefs)
            repo_name = (session.get("pr") or {}).get("repo")
            session["reviewerPreferences"] = build_prompt_summary(prefs, repo_name)
    except Exception:
        pass

    session["updatedAt"] = now_iso
    with open(active_session_path, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)
    md_content = render_cliffs_notes_markdown(session)
    with open(cliffs_notes_md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    try:
        archive_review_session(session, md_content)
    except Exception:
        pass

    print(
        json.dumps(
            {
                "status": "updated" if args.updated_draft is not None else "answered",
                "threadId": args.thread_id,
                "updatedDraft": args.updated_draft.strip() if args.updated_draft is not None else None,
                "activeSessionPath": active_session_path,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

