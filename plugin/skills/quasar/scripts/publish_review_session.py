#!/usr/bin/env python3
"""Publishes the synthesized Quasar CliffsNotes, Guided Reading Tour, Hover Context,
and Verified Review Findings into ~/.gemini/quasar/active_session.json.

This immediately updates the native Quasar extension (TreeViews, Hover Cards,
CodeLenses, Inline Comment Threads, and CliffsNotes Markdown guide).

Usage:
  python3 publish_review_session.py --input /path/to/review_synthesis.json
"""

import argparse
import datetime
import json
import os
import sys
from typing import Any, Dict, List, Optional

from collect_pr_context import (
    REVIEW_STATE_DIR,
    archive_review_session,
    assess_existing_github_comment,
    get_session_paths,
    render_cliffs_notes_markdown,
    sanitize_mermaid_diagram,
)


def _apply_triage_to_existing_thread(
    t: Dict[str, Any],
    override: Optional[Dict[str, Any]],
    pr_author: str,
    now_iso: str,
) -> Dict[str, Any]:
    thread = dict(t)
    raw_body = (thread.get("draftComment") or "").strip()
    msgs = [
        m for m in (thread.get("messages") or []) if not m.get("isTriageNote")
    ]
    author = "reviewer"
    if msgs and msgs[0].get("author"):
        author = str(msgs[0]["author"]).split(" ")[0].lstrip("@")

    replies = [
        {
            "author": str(m.get("author") or "").split(" ")[0].lstrip("@"),
            "body": m.get("body") or "",
            "created_at": m.get("timestamp") or "",
        }
        for m in msgs[1:]
        if "(GitHub" in str(m.get("author") or "")
    ]

    if override:
        status = override.get("triageStatus") or override.get("verdict") or thread.get("triageStatus") or "needs_attention"
        if status in ("good", "should_fix", "fix", "recommended_fix"):
            status = "recommended_fix"
        elif status in ("question", "needs_look", "needs_attention", "discussion"):
            status = "needs_attention"
        elif status in ("addressed", "refuted", "disputed", "replied", "skip", "addressed_or_refuted"):
            status = "addressed_or_refuted"

        default_badge = (
            "🛠️ Good Catch — Should Fix"
            if status == "recommended_fix"
            else ("❓ Question / Needs Look" if status == "needs_attention" else "💬 Replied / No Fix")
        )
        default_label = (
            "🛠️ Good Comment — Suggested Fix Should Be Made"
            if status == "recommended_fix"
            else (
                "❓ Needs Attention — Question or Design Decision"
                if status == "needs_attention"
                else "💬 Already Replied / No Fix Needed"
            )
        )
        badge = override.get("triageBadge") or default_badge
        label = override.get("triageLabel") or default_label
        summary = override.get("triageSummary") or override.get("reason") or override.get("rationale") or thread.get("triageSummary") or ""
        rec_fix = bool(override.get("recommendedForFix", status == "recommended_fix"))
        triage = {
            "triageStatus": status,
            "triageCategory": override.get("triageCategory") or status,
            "triageBadge": badge,
            "triageLabel": label,
            "triageSummary": summary,
            "recommendedForFix": rec_fix,
        }
    elif thread.get("triageStatus") and thread.get("triageBadge") and thread.get("triageSummary"):
        triage = {
            "triageStatus": thread["triageStatus"],
            "triageCategory": thread.get("triageCategory") or thread["triageStatus"],
            "triageBadge": thread["triageBadge"],
            "triageLabel": thread.get("triageLabel") or thread["triageBadge"],
            "triageSummary": thread["triageSummary"],
            "recommendedForFix": bool(thread.get("recommendedForFix", thread["triageStatus"] == "recommended_fix")),
        }
    else:
        triage = assess_existing_github_comment(
            author=author,
            body=raw_body,
            replies=replies,
            pr_author=pr_author,
            rel_path=thread.get("path") or "",
            line=int(thread.get("startLine") or 1),
        )

    import re

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

    thread.update(triage)
    thread["author"] = author
    thread["triageLeftTag"] = left_tag
    if raw_body:
        clean_lines = [
            re.sub(r"^!\[[^\]]*\]\([^)]+\)\s*", "", ln).strip()
            for ln in raw_body.splitlines()
        ]
        non_empty = [ln for ln in clean_lines if ln]
        if non_empty:
            thread["title"] = f"{left_tag} {non_empty[0][:72]}"
    thread["whyItMatters"] = f"{triage['triageLabel']} — {triage['triageSummary']}"
    msgs.append(
        {
            "author": f"Quasar Triage ({triage['triageBadge']})",
            "body": f"**{triage['triageLabel']}**\n\n{triage['triageSummary']}",
            "timestamp": now_iso,
            "isTriageNote": True,
        }
    )
    thread["messages"] = msgs
    return thread


def merge_session(existing: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    session = dict(existing)
    session["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    session["analysisStatus"] = payload.get("analysisStatus", "ready")
    effort = (
        payload.get("effortLevel")
        or payload.get("effort_level")
        or existing.get("effortLevel")
        or "medium"
    )
    session["effortLevel"] = str(effort).lower() if str(effort).lower() in ("low", "medium", "high") else "medium"

    if "cliffsNotes" in payload:
        cn = dict(session.get("cliffsNotes") or {})
        cn.update(payload["cliffsNotes"])
        if cn.get("architectureDiagram"):
            cn["architectureDiagram"] = sanitize_mermaid_diagram(cn["architectureDiagram"])
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

    # Map any explicit AI assessments for existing GitHub comments (from existingCommentAssessments or findings)
    assessments_by_id: Dict[str, Dict[str, Any]] = {}
    raw_assessments = payload.get("existingCommentAssessments") or payload.get("existing_comment_assessments") or []
    if isinstance(raw_assessments, dict):
        for k, v in raw_assessments.items():
            if isinstance(v, dict):
                assessments_by_id[str(k)] = v
    elif isinstance(raw_assessments, list):
        for item in raw_assessments:
            if isinstance(item, dict) and item.get("id"):
                assessments_by_id[str(item["id"])] = item

    for finding in payload.get("findings") or payload.get("threads") or []:
        if finding.get("kind") == "github_existing" and finding.get("id"):
            assessments_by_id[str(finding["id"])] = finding

    pr_author = ((session.get("pr") or {}).get("author") or "").strip()
    raw_existing_threads = [
        t
        for t in (session.get("threads") or [])
        if t.get("kind") in ("github_existing", "user_qa", "user_staged")
    ]
    # Also collapse consecutive ungrouped author replies on the same path+line if migrating an older session
    collapsed_existing: List[Dict[str, Any]] = []
    for t in raw_existing_threads:
        t_copy = dict(t)
        t_copy["messages"] = list(t.get("messages") or [])
        if (
            t_copy.get("kind") == "github_existing"
            and collapsed_existing
            and collapsed_existing[-1].get("kind") == "github_existing"
            and pr_author
            and str(t_copy.get("title") or "").lower().startswith(f"@{pr_author.lower()}:")
            and collapsed_existing[-1].get("path") == t_copy.get("path")
            and int(collapsed_existing[-1].get("startLine") or 1) == int(t_copy.get("startLine") or 1)
        ):
            prev_msgs = [m for m in collapsed_existing[-1].get("messages") or [] if not m.get("isTriageNote")]
            for m in t_copy["messages"]:
                if not m.get("isTriageNote"):
                    prev_msgs.append(
                        {
                            "author": f"@{pr_author} (GitHub Reply)",
                            "body": m.get("body") or "",
                            "timestamp": m.get("timestamp") or "",
                        }
                    )
            collapsed_existing[-1]["messages"] = prev_msgs
            collapsed_existing[-1].pop("triageStatus", None)
            continue
        collapsed_existing.append(t_copy)

    existing_threads: List[Dict[str, Any]] = []
    for t in collapsed_existing:
        if t.get("kind") == "github_existing":
            tid = str(t.get("id") or "")
            existing_threads.append(
                _apply_triage_to_existing_thread(
                    t, assessments_by_id.get(tid), pr_author, session["updatedAt"]
                )
            )
        else:
            existing_threads.append(t)

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

        is_uncertain = (
            finding.get("status") == "uncertain"
            or finding.get("confidence") == "uncertain"
            or bool(finding.get("uncertain"))
            or bool(finding.get("uncertaintyQuestion"))
        )
        raw_status = str(finding.get("status") or "").strip().lower()
        if is_uncertain:
            status = "uncertain"
        elif raw_status in ("dismissed", "fixed"):
            status = raw_status
        elif raw_status == "unstaged" or finding.get("unstagedByUser"):
            status = "candidate"
        else:
            status = "staged"
        kind = finding.get("kind") or "ai_candidate"
        uncertainty_q = (
            finding.get("uncertaintyQuestion")
            or finding.get("preferenceQuestion")
            or (
                f"Do you want Quasar to flag issues like \"{title}\" in future reviews, or skip this pattern?"
                if is_uncertain
                else ""
            )
        )
        pref_category = finding.get("preferenceCategory") or finding.get("category") or "general"
        pref_options = finding.get("preferenceOptions") or []

        if is_uncertain:
            default_body = (
                f"### 🤔 Preference Check: {title}\n\n"
                f"**Question for You**: *{uncertainty_q}*\n\n"
                f"*Context & Trade-off*: {why}\n\n"
                f"---\n"
                f"**Candidate GitHub Comment (if kept)**:\n> {draft}\n\n"
                f"*(Use the **🤔 Calibrate Preference** button above or 👍/👎 to teach Quasar how to handle this in the future.)*"
            )
        else:
            default_body = (
                f"**{title}**\n\n"
                f"*CliffsNotes Context*: {why}\n\n"
                f"---\n"
                f"**Draft GitHub Comment**:\n> {draft}"
            )

        new_threads.append(
            {
                "id": t_id,
                "kind": kind,
                "severity": sev,
                "status": status,
                "selectedForFix": bool(finding.get("selectedForFix", False)),
                "uncertain": is_uncertain,
                "uncertaintyQuestion": uncertainty_q,
                "preferenceCategory": pref_category,
                "preferenceOptions": pref_options,
                "path": finding.get("path", ""),
                "startLine": start_l,
                "endLine": end_l,
                "title": title,
                "whyItMatters": why,
                "draftComment": draft,
                "messages": finding.get("messages")
                or [
                    {
                        "author": "Quasar",
                        "body": default_body,
                        "timestamp": session["updatedAt"],
                    }
                ],
            }
        )

    session["threads"] = existing_threads + new_threads

    try:
        from manage_preferences import build_prompt_summary, load_preferences

        repo_name = (session.get("pr") or {}).get("repo")
        session["reviewerPreferences"] = build_prompt_summary(load_preferences(), repo_name)
    except Exception:
        pass

    if "coverageMatrix" in payload:
        session["coverageMatrix"] = payload["coverageMatrix"]
    if "repoChecks" in payload:
        session["repoChecks"] = payload["repoChecks"]
    if "dismissedChecks" in payload:
        session["dismissedChecks"] = payload["dismissedChecks"]

    return session


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish synthesized Quasar session.")
    parser.add_argument("--input", required=True, help="Path to JSON file with synthesized CliffsNotes & findings")
    parser.add_argument("--workspace-dir", default=None, help="Window workspace directory that owns this Quasar session")
    parser.add_argument("--artifact-out", help="Optional path to also copy the generated cliffs_notes.md")
    args = parser.parse_args()

    os.makedirs(REVIEW_STATE_DIR, exist_ok=True)
    with open(args.input, "r", encoding="utf-8") as f:
        payload = json.load(f)

    target_ws = args.workspace_dir or payload.get("workspaceDir") or payload.get("workspace_dir")
    state_dir, active_session_path, cliffs_notes_md_path = get_session_paths(target_ws)
    os.makedirs(state_dir, exist_ok=True)

    existing: Dict[str, Any] = {}
    if os.path.isfile(active_session_path):
        try:
            with open(active_session_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {}

    merged = merge_session(existing, payload)
    merged["stateDir"] = state_dir
    for cand_path in (args.input, args.artifact_out, merged.get("scratchDir")):
        if cand_path:
            m_conv = re.search(
                r"/brain/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:/|$)",
                os.path.abspath(str(cand_path)),
            )
            if m_conv:
                merged["conversationId"] = m_conv.group(1)
                break
    with open(active_session_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)

    md_content = render_cliffs_notes_markdown(merged)
    with open(cliffs_notes_md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    archive_review_session(merged, md_content)

    if args.artifact_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.artifact_out)), exist_ok=True)
        with open(args.artifact_out, "w", encoding="utf-8") as f:
            f.write(md_content)

    print(
        json.dumps(
            {
                "status": "published",
                "sessionId": merged.get("sessionId"),
                "stateDir": state_dir,
                "activeSessionPath": active_session_path,
                "cliffsNotesMarkdownPath": cliffs_notes_md_path,
                "chaptersCount": len(merged.get("chapters") or []),
                "threadsCount": len(merged.get("threads") or []),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
