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
import re
import sys
from typing import Any, Dict, List, Optional

from collect_pr_context import (
    REVIEW_STATE_DIR,
    TOP_LEVEL_COMMENTS_FILENAME,
    archive_review_session,
    assess_existing_github_comment,
    format_solution_option_text,
    get_session_paths,
    inspect_comment_code_state,
    normalize_and_format_quasar_followup,
    normalize_solution_options,
    render_cliffs_notes_markdown,
    resolve_target_window,
    sanitize_mermaid_diagram,
)


def _apply_triage_to_existing_thread(
    t: Dict[str, Any],
    override: Optional[Dict[str, Any]],
    pr_author: str,
    now_iso: str,
    is_owned: bool = True,
    head_root: str = "",
    base_root: str = "",
    repo_dir: str = "",
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

    raw_path = (thread.get("path") or "").strip()
    rel_path_for_check = (
        (thread.get("targetFile") or "").strip()
        if raw_path == TOP_LEVEL_COMMENTS_FILENAME
        else raw_path
    )
    line_num = int(thread.get("startLine") or 1)
    code_state = inspect_comment_code_state(
        rel_path=rel_path_for_check,
        line=line_num,
        body=raw_body,
        head_root=head_root,
        base_root=base_root,
        repo_dir=repo_dir,
    )

    if override:
        raw_triage = {
            "triageStatus": override.get("triageStatus") or override.get("verdict") or thread.get("triageStatus") or "needs_attention",
            "triageCategory": override.get("triageCategory") or thread.get("triageCategory") or "",
            "triageBadge": override.get("triageBadge") or "",
            "triageLabel": override.get("triageLabel") or "",
            "triageSummary": (
                override.get("triageSummary")
                or override.get("reason")
                or override.get("rationale")
                or thread.get("triageSummary")
                or ""
            ),
            "whatIsDone": (
                override.get("whatIsDone")
                or override.get("what_is_done")
                or ""
            ),
            "whatIsNotDone": (
                override.get("whatIsNotDone")
                or override.get("what_is_not_done")
                or override.get("what_isnt_done")
                or ""
            ),
            "whatShouldBeDone": (
                override.get("whatShouldBeDone")
                or override.get("what_should_be_done")
                or ""
            ),
        }
        if "recommendedForFix" in override:
            raw_triage["recommendedForFix"] = bool(override["recommendedForFix"])
        triage = normalize_and_format_quasar_followup(
            raw_triage,
            author=author,
            rel_path=rel_path_for_check,
            line=line_num,
            code_state=code_state,
        )
    elif thread.get("triageStatus") and thread.get("triageBadge") and thread.get("triageSummary"):
        triage = normalize_and_format_quasar_followup(
            {
                "triageStatus": thread["triageStatus"],
                "triageCategory": thread.get("triageCategory") or thread["triageStatus"],
                "triageBadge": thread["triageBadge"],
                "triageLabel": thread.get("triageLabel") or thread["triageBadge"],
                "triageSummary": thread["triageSummary"],
                "whatIsDone": thread.get("whatIsDone") or "",
                "whatIsNotDone": thread.get("whatIsNotDone") or "",
                "whatShouldBeDone": thread.get("whatShouldBeDone") or "",
                "recommendedForFix": bool(
                    thread.get(
                        "recommendedForFix",
                        thread["triageStatus"] in ("recommended_fix", "partially_addressed"),
                    )
                ),
            },
            author=author,
            rel_path=rel_path_for_check,
            line=line_num,
            code_state=code_state,
        )
    else:
        triage = assess_existing_github_comment(
            author=author,
            body=raw_body,
            replies=replies,
            pr_author=pr_author,
            rel_path=rel_path_for_check,
            line=line_num,
            head_root=head_root,
            base_root=base_root,
            repo_dir=repo_dir,
            code_state=code_state,
        )

    left_tag = triage["triageLeftTag"]
    thread.update(
        {
            "triageStatus": triage["triageStatus"],
            "triageCategory": triage["triageCategory"],
            "triageBadge": triage["triageBadge"],
            "triageLabel": triage["triageLabel"],
            "triageSummary": triage["triageSummary"],
            "whatIsDone": triage["whatIsDone"],
            "whatIsNotDone": triage["whatIsNotDone"],
            "whatShouldBeDone": triage["whatShouldBeDone"],
            "recommendedForFix": triage["recommendedForFix"],
        }
    )
    if is_owned:
        raw_sol = (
            (override.get("solutionOptions") or override.get("fixOptions"))
            if override
            else None
        ) or thread.get("solutionOptions")
        sol_opts = normalize_solution_options(raw_sol)
        if sol_opts:
            thread["solutionOptions"] = sol_opts
            if override and override.get("selectedSolution"):
                thread["selectedSolution"] = str(override["selectedSolution"]).strip()
            if override and override.get("selectedSolutionId"):
                thread["selectedSolutionId"] = str(override["selectedSolutionId"]).strip()
        else:
            thread.pop("solutionOptions", None)
    else:
        thread.pop("solutionOptions", None)
        thread.pop("selectedSolutionId", None)
        thread.pop("selectedSolution", None)

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
    thread["whyItMatters"] = (
        f"{triage['triageLabel']} — "
        f"✅ Done: {triage['whatIsDone']} | "
        f"⚠️ Not Done: {triage['whatIsNotDone']} | "
        f"👉 Should Do: {triage['whatShouldBeDone']}"
    )
    # Keep GitHub messages first, followed by Quasar's structured follow-up, then any later user Q&A
    gh_msgs: List[Dict[str, Any]] = []
    qa_msgs: List[Dict[str, Any]] = []
    for idx_m, m in enumerate(msgs):
        m_auth = str(m.get("author") or "")
        if idx_m == 0 or "(GitHub" in m_auth:
            gh_msgs.append(m)
        else:
            qa_msgs.append(m)
    followup_msg = {
        "author": triage["followupAuthor"],
        "body": triage["followupBody"],
        "timestamp": now_iso,
        "isTriageNote": True,
    }
    thread["messages"] = gh_msgs + [followup_msg] + qa_msgs
    return thread


def merge_session(existing: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    session = dict(existing)
    session["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    session["analysisStatus"] = payload.get("analysisStatus", "ready")
    if payload.get("windowId") or existing.get("windowId") or os.environ.get("QUASAR_WINDOW_ID"):
        session["windowId"] = (
            payload.get("windowId")
            or existing.get("windowId")
            or os.environ.get("QUASAR_WINDOW_ID")
        )
    if payload.get("windowPid") or existing.get("windowPid") or os.environ.get("QUASAR_WINDOW_PID"):
        try:
            session["windowPid"] = int(
                payload.get("windowPid")
                or existing.get("windowPid")
                or os.environ.get("QUASAR_WINDOW_PID")
                or 0
            )
        except Exception:
            pass
    effort = (
        payload.get("effortLevel")
        or payload.get("effort_level")
        or existing.get("effortLevel")
        or "high"
    )
    session["effortLevel"] = str(effort).lower() if str(effort).lower() in ("low", "medium", "high") else "high"
    comments_only = bool(
        payload.get(
            "commentsOnly",
            payload.get("comments_only", existing.get("commentsOnly", False)),
        )
    )
    session["commentsOnly"] = comments_only

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
    elif comments_only:
        session["chapters"] = []

    # Map any explicit AI assessments for existing GitHub comments (from existingCommentAssessments or findings)
    assessments_by_id: Dict[str, Dict[str, Any]] = {}
    raw_assessments = payload.get("existingCommentAssessments") or payload.get("existing_comment_assessments") or []
    if isinstance(raw_assessments, dict):
        for k, v in raw_assessments.items():
            if isinstance(v, dict):
                assessments_by_id[str(k)] = v
    elif isinstance(raw_assessments, list):
        for item in raw_assessments:
            if isinstance(item, dict):
                aid = item.get("id") or item.get("threadId") or item.get("thread_id")
                if aid:
                    assessments_by_id[str(aid)] = item

    for finding in payload.get("findings") or payload.get("threads") or []:
        if finding.get("kind") == "github_existing":
            fid = finding.get("id") or finding.get("threadId") or finding.get("thread_id")
            if fid:
                assessments_by_id[str(fid)] = finding

    pr_info = session.get("pr") or {}
    pr_author = (pr_info.get("author") or "").strip()
    is_owned = pr_info.get("isOwnedByUser", True) is not False
    prior_threads_by_id: Dict[str, Dict[str, Any]] = {
        str(t.get("id")): t for t in (session.get("threads") or []) if t.get("id")
    }
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
                    t,
                    assessments_by_id.get(tid),
                    pr_author,
                    session["updatedAt"],
                    is_owned=is_owned,
                    head_root=head_root,
                    base_root=base_root,
                    repo_dir=repo_dir,
                )
            )
        else:
            existing_threads.append(t)

    head_root = session.get("headSnapshotRoot") or ""
    new_threads: List[Dict[str, Any]] = []
    for idx, finding in enumerate(payload.get("findings") or payload.get("threads") or [], 1):
        if finding.get("kind") in ("github_existing", "user_qa"):
            continue
        t_id = finding.get("id") or f"finding-{idx}"
        prior_t = prior_threads_by_id.get(str(t_id)) or {}
        sev = finding.get("severity") or "DESIGN"
        draft = finding.get("draftComment") or finding.get("body") or ""
        why = finding.get("whyItMatters") or finding.get("rationale") or ""
        title = finding.get("title") or f"Finding #{idx}"
        start_l = int(finding.get("startLine") or finding.get("line") or 1)
        end_l = int(finding.get("endLine") or start_l)

        raw_path = (finding.get("path") or "").strip()
        target_file = (finding.get("targetFile") or raw_path or "").strip()
        file_in_diff = bool(
            raw_path
            and (
                not head_root
                or os.path.isfile(os.path.join(head_root, raw_path))
            )
        )
        is_top_level = bool(
            finding.get("isTopLevel")
            or not raw_path
            or not file_in_diff
        )
        effective_path = "" if is_top_level else raw_path

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
        solution_options = (
            normalize_solution_options(
                finding.get("solutionOptions")
                or finding.get("fixOptions")
                or prior_t.get("solutionOptions")
            )
            if is_owned
            else []
        )
        selected_sol_id = (
            str(finding.get("selectedSolutionId") or prior_t.get("selectedSolutionId") or "").strip()
            if is_owned
            else ""
        )
        selected_sol_text = (
            str(finding.get("selectedSolution") or prior_t.get("selectedSolution") or "").strip()
            if is_owned
            else ""
        )
        if selected_sol_id and not selected_sol_text and solution_options:
            matched_opt = next(
                (o for o in solution_options if o.get("id") == selected_sol_id),
                None,
            )
            if matched_opt:
                selected_sol_text = format_solution_option_text(matched_opt)

        top_level_note = (
            f"\n\n📌 *Top-Level PR Comment"
            + (f" (Untouched file: `{target_file}`)*" if target_file else "*")
            if is_top_level
            else ""
        )

        raw_sources = (
            finding.get("sourceAgents")
            or finding.get("source_agents")
            or finding.get("subagents")
            or finding.get("lenses")
            or finding.get("sourceAgent")
            or finding.get("source_agent")
            or finding.get("subagent")
            or finding.get("lens")
            or prior_t.get("sourceAgents")
            or prior_t.get("sourceAgent")
            or []
        )
        if isinstance(raw_sources, str):
            raw_sources = [p.strip() for p in raw_sources.split(",") if p.strip()]
        source_agents: List[str] = []
        if isinstance(raw_sources, list):
            for s_ag in raw_sources:
                cleaned_ag = re.sub(r"[`*_\"']", "", str(s_ag or "")).strip()
                if cleaned_ag and cleaned_ag not in source_agents:
                    source_agents.append(cleaned_ag)

        source_agents_md = (
            "\n\n🤖 *Suggested by*: " + ", ".join(f"`{a}`" for a in source_agents)
            if source_agents
            else ""
        )

        if is_uncertain:
            default_body = (
                f"### 🤔 Preference Check: {title}{top_level_note}{source_agents_md}\n\n"
                f"**Question for You**: *{uncertainty_q}*\n\n"
                f"*Context & Trade-off*: {why}\n\n"
                f"---\n"
                f"**Candidate GitHub Comment (if kept)**:\n> {draft}\n\n"
                f"*(Use the **🤔 Calibrate Preference** button above or 👍/👎 to teach Quasar how to handle this in the future.)*"
            )
        else:
            default_body = (
                f"**{title}**{top_level_note}{source_agents_md}\n\n"
                f"*CliffsNotes Context*: {why}\n\n"
                f"---\n"
                f"**Draft GitHub Comment**:\n> {draft}"
            )

        thread_entry: Dict[str, Any] = {
            "id": t_id,
            "kind": kind,
            "severity": sev,
            "status": status,
            "sourceAgent": source_agents[0] if source_agents else "",
            "sourceAgents": source_agents,
            "isTopLevel": is_top_level,
            "targetFile": target_file,
            "selectedForFix": bool(finding.get("selectedForFix", prior_t.get("selectedForFix", False))),
            "uncertain": is_uncertain,
            "uncertaintyQuestion": uncertainty_q,
            "preferenceCategory": pref_category,
            "preferenceOptions": pref_options,
            "path": effective_path,
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
        if solution_options:
            thread_entry["solutionOptions"] = solution_options
        if selected_sol_id:
            thread_entry["selectedSolutionId"] = selected_sol_id
        if selected_sol_text:
            thread_entry["selectedSolution"] = selected_sol_text
        new_threads.append(thread_entry)

    session["threads"] = existing_threads + new_threads

    if comments_only and "cliffsNotes" not in payload:
        cn = dict(session.get("cliffsNotes") or {})
        cn["tldr"] = (
            f"Comments-Only Review — {len(new_threads)} new finding(s) and "
            f"{len([t for t in existing_threads if t.get('kind') == 'github_existing'])} existing GitHub comment(s) ready to publish or fix."
        )
        cn["whatAndWhy"] = "Study guide generation was skipped (Comments-Only mode)."
        cn["architectureDiagram"] = ""
        session["cliffsNotes"] = cn

    try:
        from manage_preferences import build_prompt_summary, load_preferences

        repo_name = (session.get("pr") or {}).get("repo")
        session["reviewerPreferences"] = build_prompt_summary(load_preferences(), repo_name)
    except Exception:
        pass

    if "topLevelComment" in payload or "topLevelCommentIntro" in payload:
        session["topLevelCommentIntro"] = (
            payload.get("topLevelCommentIntro")
            or payload.get("topLevelComment")
            or ""
        ).strip()
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
    parser.add_argument("--comments-only", action="store_true", help="Publish in Comments-Only mode (skip study guide & reading tour)")
    parser.add_argument("--artifact-out", help="Optional path to also copy the generated cliffs_notes.md")
    args = parser.parse_args()

    os.makedirs(REVIEW_STATE_DIR, exist_ok=True)
    with open(args.input, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if args.comments_only:
        payload["commentsOnly"] = True

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

    # Also check sibling pr_context.json in input scratch directory for window_id/window_pid stamped at review start
    sibling_ctx_path = os.path.join(os.path.dirname(os.path.abspath(args.input)), "pr_context.json")
    if os.path.isfile(sibling_ctx_path):
        try:
            with open(sibling_ctx_path, "r", encoding="utf-8") as cf:
                sibling_ctx = json.load(cf)
            if not payload.get("windowId") and sibling_ctx.get("window_id"):
                payload["windowId"] = sibling_ctx["window_id"]
            if not payload.get("windowPid") and sibling_ctx.get("window_pid"):
                payload["windowPid"] = sibling_ctx["window_pid"]
        except Exception:
            pass

    merged = merge_session(existing, payload)
    merged["stateDir"] = state_dir
    if not merged.get("windowId"):
        win_id, win_pid = resolve_target_window(state_dir, existing)
        if win_id:
            merged["windowId"] = win_id
        if win_pid:
            merged["windowPid"] = win_pid
    env_conv_id = (os.environ.get("ANTIGRAVITY_CONVERSATION_ID") or "").strip()
    if env_conv_id:
        merged["conversationId"] = env_conv_id
    else:
        for cand_path in (args.input, args.artifact_out, merged.get("scratchDir")):
            if cand_path:
                m_conv = re.search(
                    r"/brain/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:/|$)",
                    os.path.abspath(str(cand_path)),
                )
                if m_conv:
                    merged["conversationId"] = m_conv.group(1)
                    break
    md_content = render_cliffs_notes_markdown(merged)
    with open(active_session_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    with open(cliffs_notes_md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    archive_review_session(merged, md_content)

    if args.artifact_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.artifact_out)), exist_ok=True)
        with open(args.artifact_out, "w", encoding="utf-8") as f:
            f.write(md_content)

    html_review_path = os.path.join(state_dir, "quasar_review.html")
    try:
        sidecar_dirs = [
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "sidecars", "quasar-web")),
            os.path.expanduser("~/.gemini/config/plugins/quasar/sidecars/quasar-web"),
        ]
        for sdir in sidecar_dirs:
            if os.path.isfile(os.path.join(sdir, "server.py")):
                if sdir not in sys.path:
                    sys.path.insert(0, sdir)
                import server as quasar_web_server  # type: ignore

                quasar_web_server.export_standalone_html(html_review_path, target_ws)
                if args.artifact_out:
                    artifact_html = os.path.join(
                        os.path.dirname(os.path.abspath(args.artifact_out)),
                        "quasar_review.html",
                    )
                    quasar_web_server.export_standalone_html(artifact_html, target_ws)
                break
    except Exception:
        pass

    print(
        json.dumps(
            {
                "status": "published",
                "sessionId": merged.get("sessionId"),
                "stateDir": state_dir,
                "activeSessionPath": active_session_path,
                "cliffsNotesMarkdownPath": cliffs_notes_md_path,
                "interactiveHtmlPath": html_review_path if os.path.isfile(html_review_path) else None,
                "sidecarUrl": "sidecar://quasar/quasar-web/",
                "webUiUrl": "http://127.0.0.1:49341",
                "commentsOnly": bool(merged.get("commentsOnly", False)),
                "chaptersCount": len(merged.get("chapters") or []),
                "threadsCount": len(merged.get("threads") or []),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
