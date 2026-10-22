#!/usr/bin/env python3
"""Posts an Antigravity answer or updated draft comment directly back into an inline
comment thread inside ~/.gemini/antigravity-code-review/active_session.json.

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

from collect_pr_context import ACTIVE_SESSION_PATH, CLIFFS_NOTES_MD_PATH, render_cliffs_notes_markdown


def main() -> None:
    parser = argparse.ArgumentParser(description="Answer an in-code comment question in Antigravity Code Review.")
    parser.add_argument("--thread-id", required=True, help="Target comment thread ID")
    parser.add_argument("--answer", required=True, help="Markdown response to append to the comment thread")
    parser.add_argument("--updated-draft", help="Optional updated GitHub draft comment text")
    parser.add_argument("--stage", action="store_true", help="Mark the thread as staged for GitHub Pending Review")
    args = parser.parse_args()

    if not os.path.isfile(ACTIVE_SESSION_PATH):
        sys.stderr.write(f"No active session found at {ACTIVE_SESSION_PATH}\n")
        sys.exit(1)

    with open(ACTIVE_SESSION_PATH, "r", encoding="utf-8") as f:
        session: Dict[str, Any] = json.load(f)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    matched = False
    for t in session.get("threads") or []:
        if t.get("id") == args.thread_id:
            matched = True
            msgs = [
                m
                for m in (t.get("messages") or [])
                if not m.get("pending") and "⏳ Antigravity is analyzing" not in (m.get("body") or "")
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
                    "author": "Antigravity",
                    "body": body_text,
                    "timestamp": now_iso,
                }
            )
            t["messages"] = msgs
            break

    if not matched:
        sys.stderr.write(f"Thread ID '{args.thread_id}' not found in active_session.json\n")
        sys.exit(1)

    session["updatedAt"] = now_iso
    with open(ACTIVE_SESSION_PATH, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)
    with open(CLIFFS_NOTES_MD_PATH, "w", encoding="utf-8") as f:
        f.write(render_cliffs_notes_markdown(session))

    print(
        json.dumps(
            {
                "status": "answered",
                "threadId": args.thread_id,
                "activeSessionPath": ACTIVE_SESSION_PATH,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
