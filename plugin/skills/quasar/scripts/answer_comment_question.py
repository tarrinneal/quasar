#!/usr/bin/env python3
"""Posts an Quasar answer or updated draft comment directly back into an inline
comment thread inside ~/.gemini/quasar/active_session.json.

When the user asks a question inside a native comment thread in the diff editor,
Antigravity runs this script to reply in place so the editor thread updates live.

Usage:
  python3 answer_comment_question.py \
    --thread-id thread-123 \
    --answer "Here is why this method is safe..." \
    [--updated-draft "Optional updated GitHub comment draft"] \
    [--stage]
"""

import argparse
import datetime
import json
import os
import sys
from typing import Any, Dict

from collect_pr_context import get_session_paths, render_cliffs_notes_markdown


def main() -> None:
    parser = argparse.ArgumentParser(description="Answer an in-code comment question in Quasar.")
    parser.add_argument("--thread-id", required=True, help="Target comment thread ID")
    parser.add_argument("--answer", required=True, help="Markdown response to append to the comment thread")
    parser.add_argument("--updated-draft", help="Optional updated GitHub draft comment text")
    parser.add_argument("--stage", action="store_true", help="Mark the thread as staged for GitHub Pending Review")
    parser.add_argument("--workspace-dir", default=None, help="Window workspace directory")
    args = parser.parse_args()

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
    for t in session.get("threads") or []:
        if t.get("id") == args.thread_id:
            matched = True
            msgs = [
                m
                for m in (t.get("messages") or [])
                if not m.get("pending") and "⏳ Quasar is analyzing" not in (m.get("body") or "")
            ]
            body_text = args.answer.strip()
            if args.updated_draft:
                t["draftComment"] = args.updated_draft.strip()
                body_text += f"\n\n---\n**Updated Draft GitHub Comment**:\n> {t['draftComment']}"
            if args.stage:
                t["status"] = "staged"
                t["kind"] = "user_staged"
            msgs.append(
                {
                    "author": "Quasar",
                    "body": body_text,
                    "timestamp": now_iso,
                }
            )
            t["messages"] = msgs
            break

    if not matched:
        sys.stderr.write(f"Thread ID '{args.thread_id}' not found in {active_session_path}\n")
        sys.exit(1)

    session["updatedAt"] = now_iso
    with open(active_session_path, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)
    with open(cliffs_notes_md_path, "w", encoding="utf-8") as f:
        f.write(render_cliffs_notes_markdown(session))

    print(
        json.dumps(
            {
                "status": "answered",
                "threadId": args.thread_id,
                "activeSessionPath": active_session_path,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
