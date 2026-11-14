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
import time
from typing import Any, Dict, Optional

from collect_pr_context import (
    archive_review_session,
    format_solution_option_text,
    get_session_paths,
    normalize_solution_options,
    render_cliffs_notes_markdown,
    sync_top_level_comments_file,
)


def rebuild_primary_finding_message_body(t: Dict[str, Any]) -> str:
    """Rebuilds the primary Quasar message body for an AI finding thread so the
    top message in the inline VS Code CommentThread always reflects the latest
    title, rationale, subagent attribution, and draftComment.
    """
    title = t.get("title") or "Review Finding"
    why = t.get("whyItMatters") or ""
    draft = t.get("draftComment") or ""
    src_agents = list(t.get("sourceAgents") or ([] if not t.get("sourceAgent") else [t.get("sourceAgent")]))
    src_md = (
        "\n\n🤖 *Suggested by*: " + ", ".join(f"`{a}`" for a in src_agents if a)
        if src_agents
        else ""
    )
    if t.get("status") == "uncertain":
        uncertainty_q = (
            t.get("uncertaintyQuestion")
            or f'Do you want Quasar to flag issues like "{title}" in future reviews, or skip this pattern?'
        )
        return (
            f"### 🤔 Preference Check: {title}{src_md}\n\n"
            f"**Question for You**: *{uncertainty_q}*\n\n"
            f"*Context & Trade-off*: {why}\n\n"
            f"---\n"
            f"**Candidate GitHub Comment (if kept)**:\n> {draft}\n\n"
            f"*(Use the **🤔 Calibrate Preference** button above or 👍/👎 to teach Quasar how to handle this in the future.)*"
        )
    parts = [f"**{title}**{src_md}"]
    if why:
        parts.append(f"*CliffsNotes Context*: {why}")
    if draft:
        parts.append(f"---\n**Draft GitHub Comment**:\n> {draft}")
    return "\n\n".join(parts)


def sync_primary_message_if_applicable(t: Dict[str, Any], msgs: list) -> None:
    """Updates msgs[0]['body'] in place if msgs[0] is the primary review comment card."""
    if not msgs:
        if t.get("kind") in ("ai_candidate", "user_staged"):
            msgs.append(
                {
                    "author": "You" if t.get("createdBy") == "user" else "Quasar",
                    "body": rebuild_primary_finding_message_body(t),
                }
            )
        return
    first = msgs[0]
    first_author = str(first.get("author") or "")
    first_body = str(first.get("body") or "")
    if (
        t.get("kind") in ("ai_candidate", "user_staged")
        or (
            (first_author.startswith("Quasar") or first_author.startswith("You"))
            and (
                "**Draft GitHub Comment**:" in first_body
                or "**Candidate GitHub Comment (if kept)**:" in first_body
                or "**Staged GitHub Comment**:" in first_body
            )
        )
    ):
        first["body"] = rebuild_primary_finding_message_body(t)


def derive_concise_title(draft_text: str, fallback: str = "Inline Review Comment") -> str:
    """Extracts a crisp single-line title from a comment body."""
    cleaned = " ".join((draft_text or "").strip().splitlines()).strip()
    if not cleaned:
        return fallback
    first_sentence = cleaned.split(". ")[0].strip()
    candidate = first_sentence if 8 <= len(first_sentence) <= 72 else cleaned
    return (candidate[:69].rstrip() + "...") if len(candidate) > 72 else candidate


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Answer an inline comment question, update a Quasar comment, or add a new inline review comment."
    )
    parser.add_argument("--thread-id", default=None, help="Target comment thread ID (optional when --create is used)")
    parser.add_argument(
        "--create",
        action="store_true",
        help="Create a new inline review comment thread if --thread-id is omitted or does not exist yet",
    )
    parser.add_argument(
        "--promote-to-comment",
        action="store_true",
        help="Populate or convert the target thread into a first-class staged Quasar review comment",
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Relative file path in the PR/repo when creating a new inline comment (omit or leave empty for top-level)",
    )
    parser.add_argument(
        "--answer",
        default="",
        help="Markdown response or explanation of the change to append to the comment thread",
    )
    parser.add_argument("--updated-draft", "--draft", dest="updated_draft", help="GitHub draft comment text")
    parser.add_argument("--updated-title", "--title", dest="updated_title", help="Finding/comment title")
    parser.add_argument("--updated-why", "--why", dest="updated_why", help="CliffsNotes rationale (whyItMatters)")
    parser.add_argument(
        "--updated-severity",
        "--severity",
        dest="updated_severity",
        help="Severity (e.g. BLOCKING, DESIGN, NIT)",
    )
    parser.add_argument(
        "--solution-options",
        default=None,
        help="Optional JSON array of solution options ([{\"label\": \"...\", \"description\": \"...\", \"recommended\": true}, ...])",
    )
    parser.add_argument(
        "--select-solution",
        default=None,
        help="Optional selected solution option ID (e.g. opt-1) or custom approach text",
    )
    parser.add_argument("--start-line", type=int, help="Start line (1-based)")
    parser.add_argument("--end-line", type=int, help="End line (1-based)")
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
    parser.add_argument(
        "--subagent",
        default=None,
        help="Optional review-* subagent name to scope --learned-rule or attribution to",
    )
    parser.add_argument("--workspace-dir", default=None, help="Window workspace directory")
    args = parser.parse_args()

    is_creating_mode = bool(args.create or (not args.thread_id and args.path is not None))
    if not args.thread_id and not is_creating_mode:
        sys.stderr.write("Provide --thread-id (to update an existing thread) or --create --path <file> (to add a new inline comment).\n")
        sys.exit(1)

    if (
        not is_creating_mode
        and not args.promote_to_comment
        and not args.answer.strip()
        and args.updated_draft is None
        and not args.updated_title
        and not args.updated_why
        and not args.updated_severity
        and args.solution_options is None
        and args.select_solution is None
        and not args.dismiss
        and not args.stage
        and not args.unstage
    ):
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
    created_new = False
    target_thread: Optional[Dict[str, Any]] = None
    original_draft = ""
    finding_path = ""
    finding_title = ""

    threads_list = session.setdefault("threads", [])
    if args.thread_id:
        for t in threads_list:
            if t.get("id") == args.thread_id:
                target_thread = t
                matched = True
                break

    if not matched and is_creating_mode:
        new_tid = args.thread_id or f"finding-inline-{int(time.time() * 1000)}"
        raw_rel_path = (args.path or "").strip().lstrip("/")
        head_root = session.get("headSnapshotRoot") or ""
        is_top_level = False
        target_file = ""
        resolved_path = raw_rel_path
        if not raw_rel_path:
            is_top_level = True
            resolved_path = "TOP_LEVEL_PR_COMMENTS.md"
        elif head_root and not os.path.isfile(os.path.join(head_root, raw_rel_path)):
            is_top_level = True
            target_file = raw_rel_path
            resolved_path = "TOP_LEVEL_PR_COMMENTS.md"

        s_line = max(1, int(args.start_line or 1))
        e_line = max(s_line, int(args.end_line or s_line))
        draft_val = (args.updated_draft or args.answer or "").strip()
        sev_val = (args.updated_severity or "DESIGN").strip().upper()
        title_val = (args.updated_title or "").strip() or derive_concise_title(draft_val)
        why_val = (args.updated_why or "").strip()
        src_agents = [args.subagent] if args.subagent else ["quasar-inline"]

        target_thread = {
            "id": new_tid,
            "kind": "ai_candidate",
            "createdBy": "quasar_inline",
            "sourceAgent": src_agents[0],
            "sourceAgents": src_agents,
            "severity": sev_val,
            "status": "candidate" if args.unstage else "staged",
            "stagedForGithub": not args.unstage,
            "isTopLevel": is_top_level,
            "targetFile": target_file,
            "path": resolved_path,
            "startLine": 1 if is_top_level else s_line,
            "endLine": 1 if is_top_level else e_line,
            "title": title_val,
            "whyItMatters": why_val,
            "draftComment": draft_val,
            "messages": [],
        }
        if args.solution_options is not None:
            try:
                norm_opts = normalize_solution_options(json.loads(args.solution_options))
                if norm_opts:
                    target_thread["solutionOptions"] = norm_opts
            except Exception:
                pass
        target_thread["messages"] = [
            {
                "author": "Quasar",
                "body": rebuild_primary_finding_message_body(target_thread),
                "timestamp": now_iso,
            }
        ]
        if args.answer and args.updated_draft and args.answer.strip() != args.updated_draft.strip():
            target_thread["messages"].append(
                {
                    "author": "Quasar",
                    "body": args.answer.strip(),
                    "timestamp": now_iso,
                }
            )
        threads_list.append(target_thread)
        args.thread_id = new_tid
        matched = True
        created_new = True
        finding_path = target_file or resolved_path
        finding_title = title_val

    elif matched and target_thread is not None:
        t = target_thread
        was_pending_inline = bool(t.get("pendingInlineDraft"))
        original_draft = "" if was_pending_inline else (t.get("draftComment") or "").strip()
        finding_path = t.get("targetFile") or t.get("path") or ""
        finding_title = t.get("title") or ""

        msgs = [
            m
            for m in (t.get("messages") or [])
            if not m.get("pending")
            and "⏳ Quasar is analyzing" not in (m.get("body") or "")
            and "⏳ Quasar is updating" not in (m.get("body") or "")
            and "⏳ Quasar is drafting" not in (m.get("body") or "")
        ]

        if args.promote_to_comment or was_pending_inline:
            t["kind"] = "ai_candidate"
            t.pop("pendingInlineDraft", None)
            if not t.get("sourceAgents") and not t.get("createdBy") == "user":
                ag_name = args.subagent or "quasar-inline"
                t["sourceAgent"] = ag_name
                t["sourceAgents"] = [ag_name]
            if not args.unstage and not args.dismiss:
                t["status"] = "staged"
                t["stagedForGithub"] = True
                t.pop("unstagedByUser", None)

        if args.updated_title and args.updated_title.strip():
            t["title"] = args.updated_title.strip()
            finding_title = t["title"]
        if args.updated_why is not None and args.updated_why.strip():
            t["whyItMatters"] = args.updated_why.strip()
        if args.updated_severity and args.updated_severity.strip():
            t["severity"] = args.updated_severity.strip().upper()
        if args.solution_options is not None:
            try:
                parsed_opts = json.loads(args.solution_options)
                norm_opts = normalize_solution_options(parsed_opts)
                if norm_opts:
                    t["solutionOptions"] = norm_opts
                else:
                    t.pop("solutionOptions", None)
            except Exception:
                pass
        if args.select_solution is not None:
            sel_raw = args.select_solution.strip()
            if not sel_raw or sel_raw.lower() in ("clear", "none"):
                t.pop("selectedSolutionId", None)
                t.pop("selectedSolution", None)
            else:
                opts = t.get("solutionOptions") or []
                matched_opt = next((o for o in opts if str(o.get("id") or "") == sel_raw), None)
                if matched_opt:
                    t["selectedSolutionId"] = str(matched_opt.get("id"))
                    t["selectedSolution"] = format_solution_option_text(matched_opt)
                else:
                    t["selectedSolutionId"] = "custom"
                    t["selectedSolution"] = sel_raw
                if t.get("status") != "fixed":
                    t["selectedForFix"] = True
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
            if not t.get("title") or was_pending_inline and not (args.updated_title and args.updated_title.strip()):
                t["title"] = derive_concise_title(t["draftComment"], t.get("title") or "Inline Review Comment")
                finding_title = t["title"]
            if not (args.promote_to_comment or was_pending_inline):
                body_parts.append(
                    f"✏️ **Updated GitHub Comment**:\n> {t['draftComment']}"
                )

        if args.dismiss:
            t["status"] = "dismissed"
            t["stagedForGithub"] = False
            t["unstagedByUser"] = True
            if not body_parts:
                body_parts.append("🗑️ **Dismissed comment per your feedback.**")
        elif args.unstage:
            t["status"] = "candidate"
            t["stagedForGithub"] = False
            t["unstagedByUser"] = True
            if not body_parts:
                body_parts.append("📝 **Moved comment to unstaged drafts.**")
        elif args.stage:
            if t.get("kind") in ("user_qa", "user_question"):
                t["kind"] = "ai_candidate"
            t["status"] = "staged"
            t["stagedForGithub"] = True
            t.pop("unstagedByUser", None)
            if t.get("status") == "uncertain":
                t["uncertaintyResolved"] = True

        if args.promote_to_comment or was_pending_inline:
            # Replace the initial card cleanly so the thread is indistinguishable from a native Quasar finding
            msgs = [
                {
                    "author": "Quasar",
                    "body": rebuild_primary_finding_message_body(t),
                    "timestamp": now_iso,
                }
            ]
        else:
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
            src_ag = (
                args.subagent
                or (target_thread.get("sourceAgent") if target_thread else None)
            )
            add_or_update_rule(
                prefs,
                rule_text=args.learned_rule.strip(),
                sentiment=args.sentiment or "custom",
                category="comment_tone",
                source_title=finding_title,
                origin="learned",
                subagent=args.subagent,
                source_subagent=src_ag,
            )
            prefs_changed = True

        if prefs_changed:
            save_preferences(prefs)
            repo_name = (session.get("pr") or {}).get("repo")
            session["reviewerPreferences"] = build_prompt_summary(prefs, repo_name)
    except Exception:
        pass

    session["updatedAt"] = now_iso
    try:
        sync_top_level_comments_file(session)
    except Exception:
        pass
    md_content = render_cliffs_notes_markdown(session)
    with open(active_session_path, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)
    with open(cliffs_notes_md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    try:
        archive_review_session(session, md_content)
    except Exception:
        pass

    print(
        json.dumps(
            {
                "status": "created"
                if created_new
                else ("updated" if args.updated_draft is not None else "answered"),
                "threadId": args.thread_id,
                "updatedDraft": args.updated_draft.strip() if args.updated_draft is not None else None,
                "activeSessionPath": active_session_path,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

