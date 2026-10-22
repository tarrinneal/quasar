#!/usr/bin/env python3
"""Publishes the synthesized Antigravity CliffsNotes, Guided Reading Tour, Hover Context,
and Verified Review Findings into ~/.gemini/antigravity-code-review/active_session.json.

This immediately updates the native Antigravity Code Review extension (TreeViews, Hover Cards,
CodeLenses, Inline Comment Threads, and CliffsNotes Markdown guide).

Usage:
  python3 publish_review_session.py --input /path/to/review_synthesis.json
"""

import argparse
import datetime
import json
import os
import sys
from typing import Any, Dict, List

from collect_pr_context import (
    ACTIVE_SESSION_PATH,
    CLIFFS_NOTES_MD_PATH,
    REVIEW_STATE_DIR,
    render_cliffs_notes_markdown,
)


def merge_session(existing: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    session = dict(existing)
    session["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    session["analysisStatus"] = payload.get("analysisStatus", "ready")

    if "cliffsNotes" in payload:
        cn = dict(session.get("cliffsNotes") or {})
        cn.update(payload["cliffsNotes"])
        session["cliffsNotes"] = cn

    base_root = session.get("baseSnapshotRoot", "")
    head_root = session.get("headSnapshotRoot", "")
    repo_dir = session.get("repoDir", "")

    if "chapters" in payload and payload["chapters"]:
        normalized_chapters: List[Dict[str, Any]] = []
        for c_idx, ch in enumerate(payload["chapters"], 1):
            stops: List[Dict[str, Any]] = []
            for s_idx, stop in enumerate(ch.get("stops") or [], 1):
                rel_path = stop.get("path", "")
                start_l = int(stop.get("startLine") or 1)
                end_l = int(stop.get("endLine") or start_l)
                local_cand = os.path.join(repo_dir, rel_path) if repo_dir and rel_path else None
                stops.append(
                    {
                        "id": stop.get("id") or f"stop-{c_idx}-{s_idx}",
                        "stopNumber": stop.get("stopNumber") or f"{c_idx}.{s_idx}",
                        "title": stop.get("title") or f"{os.path.basename(rel_path)}:{start_l}-{end_l}",
                        "description": stop.get("description") or stop.get("cliffsNotes") or "",
                        "path": rel_path,
                        "startLine": start_l,
                        "endLine": end_l,
                        "baseSnapshotPath": stop.get("baseSnapshotPath") or os.path.join(base_root, rel_path),
                        "headSnapshotPath": stop.get("headSnapshotPath") or os.path.join(head_root, rel_path),
                        "localWorkspacePath": local_cand if (local_cand and os.path.isfile(local_cand)) else None,
                        "cliffsNotes": stop.get("cliffsNotes") or stop.get("description") or "",
                        "whyItChanged": stop.get("whyItChanged") or "",
                        "callersAndInvariants": stop.get("callersAndInvariants") or "",
                        "testReference": stop.get("testReference") or "",
                        "read": bool(stop.get("read", False)),
                    }
                )
            normalized_chapters.append(
                {
                    "id": ch.get("id") or f"ch-{c_idx}",
                    "title": ch.get("title") or f"Chapter {c_idx}",
                    "summary": ch.get("summary") or "",
                    "cliffsNotesBullets": ch.get("cliffsNotesBullets") or [],
                    "stops": stops,
                }
            )
        session["chapters"] = normalized_chapters

    # Preserve existing GitHub comments and any user Q&A threads while adding AI candidate findings
    existing_threads = [
        t
        for t in (session.get("threads") or [])
        if t.get("kind") in ("github_existing", "user_qa", "user_staged")
    ]
    new_threads: List[Dict[str, Any]] = []
    for idx, finding in enumerate(payload.get("findings") or payload.get("threads") or [], 1):
        if finding.get("kind") in ("github_existing", "user_qa"):
            continue
        t_id = finding.get("id") or f"finding-{idx}"
        sev = finding.get("severity") or "DESIGN"
        draft = finding.get("draftComment") or finding.get("body") or ""
        why = finding.get("whyItMatters") or finding.get("rationale") or ""
        title = finding.get("title") or f"Finding #{idx}"
        start_l = int(finding.get("startLine") or finding.get("line") or 1)
        end_l = int(finding.get("endLine") or start_l)
        status = finding.get("status") or "candidate"
        kind = "user_staged" if status == "staged" else "ai_candidate"
        new_threads.append(
            {
                "id": t_id,
                "kind": kind,
                "severity": sev,
                "status": status,
                "path": finding.get("path", ""),
                "startLine": start_l,
                "endLine": end_l,
                "title": title,
                "whyItMatters": why,
                "draftComment": draft,
                "messages": finding.get("messages")
                or [
                    {
                        "author": "Antigravity Review",
                        "body": f"**{title}**\n\n*CliffsNotes Context*: {why}\n\n---\n**Draft GitHub Comment**:\n> {draft}",
                        "timestamp": session["updatedAt"],
                    }
                ],
            }
        )

    session["threads"] = existing_threads + new_threads

    if "coverageMatrix" in payload:
        session["coverageMatrix"] = payload["coverageMatrix"]
    if "repoChecks" in payload:
        session["repoChecks"] = payload["repoChecks"]
    if "dismissedChecks" in payload:
        session["dismissedChecks"] = payload["dismissedChecks"]

    return session


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish synthesized Antigravity Code Review session.")
    parser.add_argument("--input", required=True, help="Path to JSON file with synthesized CliffsNotes & findings")
    parser.add_argument("--artifact-out", help="Optional path to also copy the generated cliffs_notes.md")
    args = parser.parse_args()

    os.makedirs(REVIEW_STATE_DIR, exist_ok=True)
    existing: Dict[str, Any] = {}
    if os.path.isfile(ACTIVE_SESSION_PATH):
        try:
            with open(ACTIVE_SESSION_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {}

    with open(args.input, "r", encoding="utf-8") as f:
        payload = json.load(f)

    merged = merge_session(existing, payload)
    with open(ACTIVE_SESSION_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)

    md_content = render_cliffs_notes_markdown(merged)
    with open(CLIFFS_NOTES_MD_PATH, "w", encoding="utf-8") as f:
        f.write(md_content)

    if args.artifact_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.artifact_out)), exist_ok=True)
        with open(args.artifact_out, "w", encoding="utf-8") as f:
            f.write(md_content)

    print(
        json.dumps(
            {
                "status": "published",
                "sessionId": merged.get("sessionId"),
                "activeSessionPath": ACTIVE_SESSION_PATH,
                "cliffsNotesMarkdownPath": CLIFFS_NOTES_MD_PATH,
                "chaptersCount": len(merged.get("chapters") or []),
                "threadsCount": len(merged.get("threads") or []),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
