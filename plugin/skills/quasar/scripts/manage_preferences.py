#!/usr/bin/env python3
"""Manages persistent Reviewer Preferences, Premade Skill Seeding, and Meta-Feedback Learning for Quasar.

Stores structured preferences and feedback history in:
  ~/.gemini/quasar/preferences.json
And renders a human- and LLM-readable markdown guide in:
  ~/.gemini/quasar/PREFERENCES.md

Three-Layer Non-Destructive Preference Hierarchy:
  1. Quasar Built-in Core Defaults (CliffsNotes comprehension-first + default collaborative styleProfile)
  2. Imported Premade Review Skills (Baseline starting point; `origin: "seed_skill"`)
     - Imported via `--import-skill /path/to/SKILL.md`
     - Seeds initial prefer/avoid/custom rules and supplementary instructions WITHOUT overwriting
       Quasar's core defaults or any user-learned preferences.
  3. Learned & Calibrated User Preferences (`origin: "learned"` / `"manual"`)
     - Highest priority! Grown over time via 👍/👎, uncertain-finding calibration, draft edits,
       and meta-feedback. Automatically supersedes conflicting seed-skill rules.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import sys
from typing import Any, Dict, List, Optional, Tuple

REVIEW_STATE_DIR = os.path.expanduser("~/.gemini/quasar")
SEED_SKILLS_DIR = os.path.join(REVIEW_STATE_DIR, "seed_skills")
ACTIVE_SESSION_PATH = os.path.join(REVIEW_STATE_DIR, "active_session.json")
CLIFFS_NOTES_MD_PATH = os.path.join(REVIEW_STATE_DIR, "cliffs_notes.md")
PREFERENCES_JSON_PATH = os.path.join(REVIEW_STATE_DIR, "preferences.json")
PREFERENCES_MD_PATH = os.path.join(REVIEW_STATE_DIR, "PREFERENCES.md")


def default_preferences() -> Dict[str, Any]:
    return {
        "version": 2,
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "defaultEffort": "medium",
        "styleProfile": {
            "commentTone": "constructive, collaborative questions rather than prescriptive demands",
            "nitThreshold": "only high-signal polish that improves readability or prevents bugs",
            "verbosity": "concise (1-3 sentences with concrete code suggestion when helpful)",
            "uncertaintyBehavior": "ask preference on borderline architectural or testing trade-offs",
        },
        "seedSkills": [],
        "rules": [],
        "categoryStats": {
            "BLOCKING": {"upvotes": 0, "downvotes": 0, "staged": 0, "dismissed": 0},
            "DESIGN": {"upvotes": 0, "downvotes": 0, "staged": 0, "dismissed": 0},
            "NIT": {"upvotes": 0, "downvotes": 0, "staged": 0, "dismissed": 0},
            "UNCERTAIN": {"calibrated_keep": 0, "calibrated_skip": 0},
        },
        "editedDraftExamples": [],
        "feedbackHistory": [],
    }


def load_preferences() -> Dict[str, Any]:
    os.makedirs(REVIEW_STATE_DIR, exist_ok=True)
    if not os.path.isfile(PREFERENCES_JSON_PATH):
        prefs = default_preferences()
        save_preferences(prefs)
        return prefs
    try:
        with open(PREFERENCES_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        base = default_preferences()
        base.update(data)
        if base.get("defaultEffort") not in ("low", "medium", "high"):
            base["defaultEffort"] = "medium"
        base.setdefault("seedSkills", [])
        base.setdefault("rules", [])
        return base
    except Exception:
        return default_preferences()


def parse_frontmatter(md_text: str) -> Tuple[Dict[str, str], str]:
    """Extracts optional YAML frontmatter (name, description) and body from a SKILL.md."""
    meta: Dict[str, str] = {}
    body = md_text
    if md_text.startswith("---"):
        parts = md_text.split("---", 2)
        if len(parts) >= 3:
            fm_raw = parts[1]
            body = parts[2].strip()
            for line in fm_raw.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and v and v not in (">-", "|"):
                        meta[k] = v
    return meta, body


def extract_seed_rules_from_markdown(
    body: str, skill_name: str
) -> Tuple[List[Dict[str, str]], Optional[str]]:
    """Extracts actionable review rules and a concise overview from a premade SKILL.md or guide.

    Categorizes bullets into `prefer`, `avoid`, or `custom` based on heading context and phrasing,
    so they can act as a non-destructive starting point for Quasar's preference system.
    """
    extracted: List[Dict[str, str]] = []
    current_heading = "general"
    current_sentiment = "custom"
    overview_lines: List[str] = []

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Track section headings
        if line.startswith("#"):
            heading_text = line.lstrip("#").strip()
            h_lower = heading_text.lower()
            current_heading = heading_text[:50]
            if any(w in h_lower for w in ["avoid", "don't", "do not", "skip", "ignore", "never", "anti-pattern", "false positive"]):
                current_sentiment = "avoid"
            elif any(w in h_lower for w in ["prefer", "always", "must", "check", "look for", "flag", "focus", "require", "priority"]):
                current_sentiment = "prefer"
            else:
                current_sentiment = "custom"
            continue

        # Capture first couple of prose sentences as overview
        if (
            not line.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.", "`", "|", ">"))
            and len(overview_lines) < 3
            and len(line) > 25
        ):
            overview_lines.append(line)

        # Extract bullet points or numbered items that look like review guidelines
        m = re.match(r"^(?:[-*+]|\d+\.)\s+(.+)$", line)
        if not m:
            continue
        item_text = m.group(1).strip()
        # Clean markdown bold/code wrappers for length check
        plain = re.sub(r"[*_`]", "", item_text).strip()
        if len(plain) < 12 or len(plain) > 280:
            continue
        # Ignore pure CLI command examples or file tree bullets
        if plain.startswith(("python3 ", "bash ", "git ", "npm ", "cd ", "./", "http://", "https://")):
            continue

        lower_item = plain.lower()
        sentiment = current_sentiment
        if re.match(r"^(never|do not|don't|avoid|skip|ignore|refrain from)\b", lower_item):
            sentiment = "avoid"
        elif re.match(r"^(always|prefer|must|ensure|verify|check that|flag|require|make sure)\b", lower_item):
            sentiment = "prefer"

        category = "general"
        if any(w in lower_item for w in ["test", "coverage", "assert", "mock", "fixture"]):
            category = "test_strictness"
        elif any(w in lower_item for w in ["api", "public", "interface", "breaking", "architecture", "layer", "abstraction"]):
            category = "api_design"
        elif any(w in lower_item for w in ["null", "crash", "async", "future", "race", "leak", "error", "exception"]):
            category = "bug_hunter"
        elif any(w in lower_item for w in ["tone", "phrase", "question", "polite", "concise", "nit", "style", "format"]):
            category = "comment_tone"
        elif any(w in lower_item for w in ["changelog", "version", "pubspec", "doc", "readme"]):
            category = "repo_hygiene"

        extracted.append(
            {
                "rule": item_text,
                "sentiment": sentiment,
                "category": category,
                "section": current_heading,
                "seedSkillName": skill_name,
            }
        )

    overview = " ".join(overview_lines)[:500] if overview_lines else None
    # Cap automatic heuristic extraction to the top 25 most actionable guidelines so we don't flood the list
    return extracted[:25], overview


def import_premade_skill(
    prefs: Dict[str, Any],
    skill_path_or_dir: str,
    custom_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Imports a premade review skill (SKILL.md or markdown guide) as a starting point.

    Non-destructive guarantees:
      - Never overwrites Quasar's existing `styleProfile`
      - Never overwrites or removes existing user-learned rules (`origin != "seed_skill"`)
      - Marks imported rules with `origin: "seed_skill"` so user feedback can evolve or override them
    """
    expanded = os.path.abspath(os.path.expanduser(skill_path_or_dir))
    if os.path.isdir(expanded):
        cand = os.path.join(expanded, "SKILL.md")
        if os.path.isfile(cand):
            expanded = cand
        else:
            # Find first .md file in directory
            md_files = [
                os.path.join(expanded, f)
                for f in os.listdir(expanded)
                if f.lower().endswith(".md")
            ]
            if md_files:
                expanded = md_files[0]

    if not os.path.isfile(expanded):
        raise FileNotFoundError(f"Premade skill file not found: {skill_path_or_dir}")

    with open(expanded, "r", encoding="utf-8", errors="replace") as f:
        raw_md = f.read()

    fm, body = parse_frontmatter(raw_md)
    parent_dir_name = os.path.basename(os.path.dirname(expanded))
    file_stem = os.path.splitext(os.path.basename(expanded))[0]
    inferred_name = (
        custom_name
        or fm.get("name")
        or (parent_dir_name if file_stem.upper() == "SKILL" and parent_dir_name else file_stem)
        or "custom-review-skill"
    )
    safe_slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", inferred_name).strip("-").lower() or "seed-skill"
    description = fm.get("description") or ""

    os.makedirs(SEED_SKILLS_DIR, exist_ok=True)
    snapshot_path = os.path.join(SEED_SKILLS_DIR, f"{safe_slug}.md")
    shutil.copyfile(expanded, snapshot_path)

    extracted_rules, overview = extract_seed_rules_from_markdown(body, inferred_name)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Update or append to prefs["seedSkills"]
    seed_skills: List[Dict[str, Any]] = [
        s for s in (prefs.get("seedSkills") or []) if s.get("slug") != safe_slug
    ]
    seed_entry = {
        "slug": safe_slug,
        "name": inferred_name,
        "description": description or overview or f"Imported review skill from {expanded}",
        "sourcePath": expanded,
        "snapshotPath": snapshot_path,
        "importedAt": now_iso,
        "extractedRulesCount": len(extracted_rules),
        "extractedRuleCount": len(extracted_rules),
    }
    seed_skills.append(seed_entry)
    prefs["seedSkills"] = seed_skills

    # Remove old seed rules from the SAME skill slug (if re-importing), but keep all learned rules and other seed skills
    existing_rules: List[Dict[str, Any]] = [
        r
        for r in (prefs.get("rules") or [])
        if not (r.get("origin") == "seed_skill" and r.get("seedSkillSlug") == safe_slug)
    ]
    existing_rule_texts = {
        re.sub(r"[*_`]", "", (r.get("rule") or "")).strip().lower()
        for r in existing_rules
    }

    added_count = 0
    for idx, item in enumerate(extracted_rules, 1):
        norm = re.sub(r"[*_`]", "", item["rule"]).strip().lower()
        if not norm or norm in existing_rule_texts:
            continue
        existing_rule_texts.add(norm)
        existing_rules.append(
            {
                "id": f"seed-{safe_slug}-{idx}-{int(datetime.datetime.now().timestamp())}",
                "createdAt": now_iso,
                "origin": "seed_skill",
                "seedSkillName": inferred_name,
                "seedSkillSlug": safe_slug,
                "sentiment": item["sentiment"],
                "category": item["category"],
                "repo": None,
                "rule": item["rule"],
                "sourceFindingTitle": f"Seeded from skill: {inferred_name}",
            }
        )
        added_count += 1

    prefs["rules"] = existing_rules

    history = prefs.setdefault("feedbackHistory", [])
    history.append(
        {
            "id": f"fb-seed-{int(datetime.datetime.now().timestamp())}",
            "timestamp": now_iso,
            "repo": None,
            "threadId": None,
            "path": expanded,
            "findingTitle": f"Imported Premade Skill: {inferred_name}",
            "action": "import_skill",
            "userFeedback": f"Seeded {added_count} starting-point preference rules from {expanded} (preserving existing preferences).",
            "learnedRule": f"Baseline skill '{inferred_name}' registered ({added_count} seed rules).",
        }
    )
    prefs["feedbackHistory"] = history[-100:]
    seed_entry["addedRulesCount"] = added_count
    return seed_entry


def discover_candidate_skills(workspace_dir: Optional[str] = None) -> List[Dict[str, str]]:
    """Discovers existing SKILL.md files and review guides on the user's machine for 1-click import."""
    home = os.path.expanduser("~")
    search_dirs = [
        os.path.join(home, ".gemini", "config", "skills"),
        os.path.join(home, ".gemini", "skills"),
        os.path.join(home, ".gemini", "config", "plugins"),
    ]
    if workspace_dir and os.path.isdir(workspace_dir):
        search_dirs.insert(0, workspace_dir)

    found: List[Dict[str, str]] = []
    seen_paths = set()

    for root_dir in search_dirs:
        if not os.path.isdir(root_dir):
            continue
        for current_root, dirs, files in os.walk(root_dir):
            # Avoid deep traversal into node_modules or .git
            dirs[:] = [
                d
                for d in dirs
                if d not in (".git", "node_modules", ".dart_tool", "build", "dist", "__pycache__")
            ]
            for f in files:
                if f in ("SKILL.md", "AGENTS.md", "REVIEW.md", ".cursorrules"):
                    full_p = os.path.abspath(os.path.join(current_root, f))
                    if full_p in seen_paths:
                        continue
                    # Skip Quasar's own SKILL.md
                    if "/quasar/skills/quasar/SKILL.md" in full_p or "/plugin/skills/quasar/SKILL.md" in full_p:
                        continue
                    seen_paths.add(full_p)
                    try:
                        with open(full_p, "r", encoding="utf-8", errors="ignore") as mf:
                            head_text = mf.read(2048)
                        fm, _ = parse_frontmatter(head_text)
                        name = fm.get("name") or os.path.basename(os.path.dirname(full_p)) or f
                        desc = fm.get("description") or f"From {full_p}"
                        found.append(
                            {
                                "name": name,
                                "path": full_p,
                                "description": desc[:140],
                            }
                        )
                    except Exception:
                        pass
    return found


def render_preferences_markdown(prefs: Dict[str, Any]) -> str:
    style = prefs.get("styleProfile") or {}
    seed_skills: List[Dict[str, Any]] = prefs.get("seedSkills") or []
    rules: List[Dict[str, Any]] = prefs.get("rules") or []
    stats = prefs.get("categoryStats") or {}
    edits: List[Dict[str, Any]] = prefs.get("editedDraftExamples") or []
    history: List[Dict[str, Any]] = prefs.get("feedbackHistory") or []

    learned_rules = [r for r in rules if r.get("origin") != "seed_skill"]
    seed_rules = [r for r in rules if r.get("origin") == "seed_skill"]

    lines: List[str] = [
        "# 🧠 Quasar Learned Reviewer Preferences",
        "",
        "> **How Precedence Works**: Quasar combines **(1) its built-in CliffsNotes review defaults**, **(2) any Premade Review Skills you import as a starting point**, and **(3) your Learned Preferences grown from inline feedback & calibration**. Your explicit feedback (`Learned` rules) always takes highest priority and refines or overrides starting-point skill rules over time.",
        "",
        "## 1. Reviewer Style & Tone Profile",
        f"- **Comment Tone**: {style.get('commentTone', 'Constructive and collaborative')}",
        f"- **Nit Threshold**: {style.get('nitThreshold', 'High-signal only')}",
        f"- **Comment Verbosity**: {style.get('verbosity', 'Concise')}",
        f"- **When Uncertain**: {style.get('uncertaintyBehavior', 'Ask the reviewer to calibrate preference')}",
        "",
    ]

    lines.append("## 2. Imported Premade Review Skills (Starting-Point Baseline)")
    if seed_skills:
        for s in seed_skills:
            lines.append(
                f"- 📦 **`{s.get('name', 'skill')}`** — {s.get('description', '')}"
            )
            lines.append(
                f"  - *Source*: [`{os.path.basename(s.get('sourcePath', 'SKILL.md'))}`](file://{s.get('sourcePath', '')}) • *Snapshot*: [`{os.path.basename(s.get('snapshotPath', ''))}`](file://{s.get('snapshotPath', '')}) • *Seeded Rules*: `{s.get('extractedRulesCount', 0)}`"
            )
    else:
        lines.append(
            "- *No premade review skill imported yet. Run `Quasar: Import Premade Review Skill as Starting Point...` to seed initial preferences from an existing `SKILL.md` or review guide.*"
        )
    lines.append("")

    prefer_learned = [
        r for r in learned_rules if r.get("sentiment") in ("prefer", "always", "keep", "upvote")
    ]
    avoid_learned = [
        r for r in learned_rules if r.get("sentiment") in ("avoid", "never", "skip", "downvote")
    ]
    custom_learned = [
        r for r in learned_rules if r not in prefer_learned and r not in avoid_learned
    ]

    lines.append("## 3. Learned Preferences — What to Flag & Prioritize (Highest Priority)")
    if prefer_learned:
        for r in prefer_learned:
            scope_badge = f" `[repo: {r['repo']}]`" if r.get("repo") else ""
            cat_badge = f" *({r.get('category', 'general')})*" if r.get("category") else ""
            lines.append(f"- ✅ **{r.get('rule', '')}**{scope_badge}{cat_badge}")
    else:
        lines.append(
            "- *No learned positive rules yet. Click 👍 or calibrate uncertain findings to teach Quasar.*"
        )
    lines.append("")

    lines.append("## 4. Learned Preferences — What to Suppress & Avoid (Highest Priority)")
    if avoid_learned:
        for r in avoid_learned:
            scope_badge = f" `[repo: {r['repo']}]`" if r.get("repo") else ""
            cat_badge = f" *({r.get('category', 'general')})*" if r.get("category") else ""
            lines.append(f"- 🚫 **{r.get('rule', '')}**{scope_badge}{cat_badge}")
    else:
        lines.append(
            "- *No learned suppression rules yet. Click 👎 on noisy findings to teach Quasar what to skip.*"
        )
    lines.append("")

    if custom_learned:
        lines.append("## 5. Learned Custom Guidelines & Conventions")
        for r in custom_learned:
            scope_badge = f" `[repo: {r['repo']}]`" if r.get("repo") else ""
            lines.append(f"- 📌 **{r.get('rule', '')}**{scope_badge}")
        lines.append("")

    if seed_rules:
        lines.append("## 6. Starting-Point Rules Seeded from Premade Skills")
        lines.append(
            "> These rules were imported from your premade review skill(s) as a baseline starting point. Whenever you upvote, downvote, or calibrate a finding, your learned rules in Sections 3–5 automatically refine or override these baseline rules."
        )
        for r in seed_rules:
            icon = (
                "✅"
                if r.get("sentiment") in ("prefer", "always", "keep", "upvote")
                else ("🚫" if r.get("sentiment") in ("avoid", "never", "skip", "downvote") else "📘")
            )
            skill_tag = f" `[from: {r.get('seedSkillName', 'skill')}]`" if r.get("seedSkillName") else ""
            lines.append(f"- {icon} {r.get('rule', '')}{skill_tag}")
        lines.append("")

    if edits:
        lines.append("## 7. Reviewer Phrasing Examples (Learned from Your Draft Edits)")
        for ex in edits[-8:]:
            lines.append(f"- **Context**: `{ex.get('path', '')}` ({ex.get('title', '')})")
            lines.append(f"  - *Quasar's Original Draft*: > {ex.get('originalDraft', '')}")
            lines.append(f"  - *Reviewer's Preferred Phrasing*: > {ex.get('editedDraft', '')}")
        lines.append("")

    lines.append("## 8. Feedback & Calibration Telemetry")
    lines.append("| Category | 👍 Upvotes | ✅ Staged | 👎 Downvotes | 🗑️ Dismissed |")
    lines.append("| :--- | :---: | :---: | :---: | :---: |")
    for sev_key in ["BLOCKING", "DESIGN", "NIT"]:
        s = stats.get(sev_key) or {}
        lines.append(
            f"| **{sev_key}** | {s.get('upvotes', 0)} | {s.get('staged', 0)} | {s.get('downvotes', 0)} | {s.get('dismissed', 0)} |"
        )
    unc = stats.get("UNCERTAIN") or {}
    lines.append("")
    lines.append(
        f"- **Uncertain Findings Calibrated**: {unc.get('calibrated_keep', 0)} kept as preferences, {unc.get('calibrated_skip', 0)} skipped as preferences."
    )
    lines.append("")

    if history:
        lines.append("## 9. Recent Meta-Feedback & Skill Evolution Log")
        for item in reversed(history[-15:]):
            ts = (item.get("timestamp") or "")[:19].replace("T", " ")
            action = item.get("action", "feedback")
            title = item.get("findingTitle") or item.get("path") or "General"
            fb = item.get("userFeedback") or item.get("learnedRule") or ""
            lines.append(f"- `{ts}` **[{action.upper()}]** *{title}* — {fb}")
        lines.append("")

    return "\n".join(lines)


def save_preferences(prefs: Dict[str, Any]) -> None:
    os.makedirs(REVIEW_STATE_DIR, exist_ok=True)
    prefs["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with open(PREFERENCES_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=2)
    with open(PREFERENCES_MD_PATH, "w", encoding="utf-8") as f:
        f.write(render_preferences_markdown(prefs))


def build_prompt_summary(prefs: Dict[str, Any], repo_name: Optional[str] = None) -> Dict[str, Any]:
    """Builds a compact summary dict to embed inside pr_context.json and active_session.json."""
    rules = prefs.get("rules") or []
    applicable_rules = [
        r for r in rules if not r.get("repo") or r.get("repo") == repo_name
    ]
    learned_rules = [r for r in applicable_rules if r.get("origin") != "seed_skill"]
    seed_rules = [r for r in applicable_rules if r.get("origin") == "seed_skill"]

    return {
        "defaultEffort": prefs.get("defaultEffort") if prefs.get("defaultEffort") in ("low", "medium", "high") else "medium",
        "styleProfile": prefs.get("styleProfile") or {},
        "seedSkills": [
            {
                "name": s.get("name"),
                "slug": s.get("slug"),
                "description": s.get("description"),
                "snapshotPath": s.get("snapshotPath"),
            }
            for s in (prefs.get("seedSkills") or [])
        ],
        "activeRulesCount": len(applicable_rules),
        "learnedRulesCount": len(learned_rules),
        "seedRulesCount": len(seed_rules),
        "preferRules": [
            r.get("rule")
            for r in learned_rules
            if r.get("sentiment") in ("prefer", "always", "keep", "upvote")
        ]
        + [
            f"[Seed: {r.get('seedSkillName', 'skill')}] {r.get('rule')}"
            for r in seed_rules
            if r.get("sentiment") in ("prefer", "always", "keep", "upvote")
        ],
        "avoidRules": [
            r.get("rule")
            for r in learned_rules
            if r.get("sentiment") in ("avoid", "never", "skip", "downvote")
        ]
        + [
            f"[Seed: {r.get('seedSkillName', 'skill')}] {r.get('rule')}"
            for r in seed_rules
            if r.get("sentiment") in ("avoid", "never", "skip", "downvote")
        ],
        "customRules": [
            r.get("rule")
            for r in learned_rules
            if r.get("sentiment")
            not in ("prefer", "always", "keep", "upvote", "avoid", "never", "skip", "downvote")
        ]
        + [
            f"[Seed: {r.get('seedSkillName', 'skill')}] {r.get('rule')}"
            for r in seed_rules
            if r.get("sentiment")
            not in ("prefer", "always", "keep", "upvote", "avoid", "never", "skip", "downvote")
        ],
        "recentEditedDrafts": (prefs.get("editedDraftExamples") or [])[-5:],
        "categoryStats": prefs.get("categoryStats") or {},
        "preferencesMarkdownPath": PREFERENCES_MD_PATH,
    }


def add_or_update_rule(
    prefs: Dict[str, Any],
    rule_text: str,
    sentiment: str = "custom",
    category: str = "general",
    repo: Optional[str] = None,
    source_title: Optional[str] = None,
    origin: str = "learned",
) -> Dict[str, Any]:
    cleaned = rule_text.strip()
    if not cleaned:
        return {}
    rules: List[Dict[str, Any]] = prefs.get("rules") or []
    # Deduplicate by case-insensitive rule text; if a seed rule matches, promote it to learned!
    for existing in rules:
        if (existing.get("rule") or "").strip().lower() == cleaned.lower():
            existing["sentiment"] = sentiment
            existing["origin"] = origin
            existing["category"] = category or existing.get("category") or "general"
            if repo:
                existing["repo"] = repo
            existing["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            return existing

    new_rule = {
        "id": f"rule-{len(rules) + 1}-{int(datetime.datetime.now().timestamp())}",
        "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "origin": origin,
        "sentiment": sentiment,
        "category": category or "general",
        "repo": repo,
        "rule": cleaned,
        "sourceFindingTitle": source_title or "",
    }
    rules.append(new_rule)
    prefs["rules"] = rules
    return new_rule


def normalize_sev_bucket(sev: str) -> str:
    s = (sev or "").upper()
    if "BLOCK" in s:
        return "BLOCKING"
    if "NIT" in s:
        return "NIT"
    return "DESIGN"


def update_active_session_thread(
    thread_id: str,
    action: str,
    user_feedback: str,
    learned_rule: str,
    updated_draft: Optional[str] = None,
    prefs: Optional[Dict[str, Any]] = None,
    workspace_dir: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    try:
        from collect_pr_context import get_session_paths, render_cliffs_notes_markdown

        _state_dir, active_session_path, cliffs_notes_md_path = get_session_paths(
            workspace_dir,
            thread_id=thread_id if thread_id != "__none__" else None,
        )
    except Exception:
        active_session_path = ACTIVE_SESSION_PATH
        cliffs_notes_md_path = CLIFFS_NOTES_MD_PATH
        render_cliffs_notes_markdown = None

    if not os.path.isfile(active_session_path):
        return None
    try:
        with open(active_session_path, "r", encoding="utf-8") as f:
            session = json.load(f)
    except Exception:
        return None

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    target_thread = None
    for t in session.get("threads") or []:
        if t.get("id") == thread_id:
            target_thread = t
            break

    if target_thread:
        msgs = target_thread.get("messages") or []
        if updated_draft:
            target_thread["draftComment"] = updated_draft.strip()

        if action in ("upvote", "calibrate_keep"):
            target_thread["feedbackRating"] = "up"
            if target_thread.get("status") == "uncertain" or action == "calibrate_keep":
                target_thread["status"] = "staged"
                target_thread["uncertaintyResolved"] = True
            badge = "👍 **Marked Helpful & Learned Preference**"
            note = learned_rule or user_feedback or "Reinforced this finding pattern for future reviews."
            msgs.append(
                {
                    "author": "Quasar Preference Learner",
                    "body": f"{badge}\n> {note}",
                    "timestamp": now_iso,
                }
            )
        elif action in ("downvote", "calibrate_skip"):
            target_thread["feedbackRating"] = "down"
            target_thread["status"] = "dismissed"
            target_thread["uncertaintyResolved"] = True
            badge = "👎 **Dismissed & Learned Suppression Rule**"
            note = learned_rule or user_feedback or "Will avoid similar findings in future reviews."
            msgs.append(
                {
                    "author": "Quasar Preference Learner",
                    "body": f"{badge}\n> {note}",
                    "timestamp": now_iso,
                }
            )
        elif action == "calibrate_once":
            target_thread["status"] = "staged"
            target_thread["uncertaintyResolved"] = True
            msgs.append(
                {
                    "author": "Quasar Preference Learner",
                    "body": "📌 **Kept for this review only** (no global rule saved).",
                    "timestamp": now_iso,
                }
            )
        elif action == "meta_feedback":
            target_thread["feedbackRating"] = "calibrated"
            note = learned_rule or user_feedback
            body = f"🧠 **Feedback Recorded**:\n> {note}"
            if updated_draft:
                body += f"\n\n---\n**Updated Draft GitHub Comment**:\n> {target_thread['draftComment']}"
            msgs.append(
                {
                    "author": "Quasar Preference Learner",
                    "body": body,
                    "timestamp": now_iso,
                }
            )
        target_thread["messages"] = msgs

    if prefs is not None:
        repo_name = (session.get("pr") or {}).get("repo")
        session["reviewerPreferences"] = build_prompt_summary(prefs, repo_name)

    session["updatedAt"] = now_iso
    with open(active_session_path, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)

    try:
        if render_cliffs_notes_markdown:
            with open(cliffs_notes_md_path, "w", encoding="utf-8") as mf:
                mf.write(render_cliffs_notes_markdown(session))
    except Exception:
        pass

    return target_thread


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Quasar Reviewer Preferences, Skill Seeding & Feedback Learning.")
    parser.add_argument("--show", action="store_true", help="Print current preferences summary as JSON")
    parser.add_argument("--discover-skills", action="store_true", help="List candidate SKILL.md and review guide files on disk")
    parser.add_argument("--workspace-dir", help="Optional workspace directory for skill discovery")
    parser.add_argument("--import-skill", help="Path to a premade SKILL.md, review guide, or skill folder to import as a starting point")
    parser.add_argument("--skill-name", help="Optional display name when importing a premade skill")
    parser.add_argument("--remove-seed-skill", help="Remove a previously imported seed skill (and its un-promoted seed rules) by slug")
    parser.add_argument("--record-feedback", action="store_true", help="Record reviewer feedback on a finding or session")
    parser.add_argument("--thread-id", help="Thread ID in active_session.json")
    parser.add_argument(
        "--action",
        choices=[
            "upvote",
            "downvote",
            "calibrate_keep",
            "calibrate_skip",
            "calibrate_once",
            "meta_feedback",
            "edit_draft",
            "stage",
            "dismiss",
        ],
        default="meta_feedback",
        help="Feedback action type",
    )
    parser.add_argument("--reason", help="User's explanation or feedback text")
    parser.add_argument("--learned-rule", help="Generalized preference rule to persist for future reviews")
    parser.add_argument("--category", default="general", help="Preference category (e.g. architecture, bug_hunter, test_strictness, nit_threshold, comment_tone)")
    parser.add_argument("--sentiment", choices=["prefer", "avoid", "custom"], help="Whether Quasar should prefer or avoid this pattern")
    parser.add_argument("--origin", choices=["learned", "seed_skill"], default="learned", help="Rule origin tier")
    parser.add_argument("--repo", help="Optional repository name if rule is repo-specific")
    parser.add_argument("--original-draft", help="Original draft comment before user edit")
    parser.add_argument("--updated-draft", help="Updated draft comment after user edit or refinement")
    parser.add_argument("--add-rule", help="Directly add a preference rule string")
    parser.add_argument("--delete-rule-id", help="Delete a preference rule by its ID")
    parser.add_argument("--set-tone", help="Update preferred comment tone in styleProfile")
    parser.add_argument("--set-nit-threshold", help="Update preferred nit threshold in styleProfile")
    parser.add_argument("--set-verbosity", help="Update preferred comment verbosity in styleProfile")
    parser.add_argument("--set-effort", choices=["low", "medium", "high"], help="Update default review effort level (low, medium, high)")
    args = parser.parse_args()

    if args.discover_skills:
        discovered = discover_candidate_skills(args.workspace_dir)
        print(
            json.dumps(
                {
                    "ok": True,
                    "candidates": discovered,
                    "discoveredSkills": discovered,
                },
                indent=2,
            )
        )
        return

    prefs = load_preferences()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    imported_skill_info: Optional[Dict[str, Any]] = None

    if args.import_skill:
        try:
            imported_skill_info = import_premade_skill(prefs, args.import_skill, args.skill_name)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            return

    if args.remove_seed_skill:
        slug = args.remove_seed_skill.strip().lower()
        prefs["seedSkills"] = [
            s for s in (prefs.get("seedSkills") or []) if (s.get("slug") or "").lower() != slug
        ]
        prefs["rules"] = [
            r
            for r in (prefs.get("rules") or [])
            if not (r.get("origin") == "seed_skill" and (r.get("seedSkillSlug") or "").lower() == slug)
        ]

    if args.set_effort:
        prefs["defaultEffort"] = args.set_effort.strip().lower()
    if args.set_tone:
        prefs.setdefault("styleProfile", {})["commentTone"] = args.set_tone.strip()
    if args.set_nit_threshold:
        prefs.setdefault("styleProfile", {})["nitThreshold"] = args.set_nit_threshold.strip()
    if args.set_verbosity:
        prefs.setdefault("styleProfile", {})["verbosity"] = args.set_verbosity.strip()

    if args.delete_rule_id:
        prefs["rules"] = [
            r for r in (prefs.get("rules") or []) if r.get("id") != args.delete_rule_id
        ]

    if args.add_rule:
        sentiment = args.sentiment or "custom"
        add_or_update_rule(
            prefs,
            rule_text=args.add_rule,
            sentiment=sentiment,
            category=args.category,
            repo=args.repo,
            origin=args.origin,
        )

    thread_info: Optional[Dict[str, Any]] = None
    session_repo: Optional[str] = args.repo
    active_sess_path_for_main = ACTIVE_SESSION_PATH
    try:
        from collect_pr_context import get_session_paths

        _s_dir, active_sess_path_for_main, _md_p = get_session_paths(
            args.workspace_dir, thread_id=args.thread_id
        )
    except Exception:
        pass
    if os.path.isfile(active_sess_path_for_main):
        try:
            with open(active_sess_path_for_main, "r", encoding="utf-8") as f:
                sess = json.load(f)
            session_repo = session_repo or (sess.get("pr") or {}).get("repo")
            if args.thread_id:
                for t in sess.get("threads") or []:
                    if t.get("id") == args.thread_id:
                        thread_info = t
                        break
        except Exception:
            pass

    if args.record_feedback:
        sev_bucket = normalize_sev_bucket((thread_info or {}).get("severity", "DESIGN"))
        cat_stats = prefs.setdefault("categoryStats", {})
        bucket_stats = cat_stats.setdefault(
            sev_bucket, {"upvotes": 0, "downvotes": 0, "staged": 0, "dismissed": 0}
        )
        unc_stats = cat_stats.setdefault(
            "UNCERTAIN", {"calibrated_keep": 0, "calibrated_skip": 0}
        )

        if args.action == "upvote":
            bucket_stats["upvotes"] = bucket_stats.get("upvotes", 0) + 1
        elif args.action == "downvote":
            bucket_stats["downvotes"] = bucket_stats.get("downvotes", 0) + 1
        elif args.action == "stage":
            bucket_stats["staged"] = bucket_stats.get("staged", 0) + 1
        elif args.action == "dismiss":
            bucket_stats["dismissed"] = bucket_stats.get("dismissed", 0) + 1
        elif args.action == "calibrate_keep":
            unc_stats["calibrated_keep"] = unc_stats.get("calibrated_keep", 0) + 1
        elif args.action == "calibrate_skip":
            unc_stats["calibrated_skip"] = unc_stats.get("calibrated_skip", 0) + 1

        finding_title = (thread_info or {}).get("title") or ""
        finding_path = (thread_info or {}).get("path") or ""
        finding_cat = args.category
        if finding_cat == "general" and thread_info and thread_info.get("preferenceCategory"):
            finding_cat = thread_info.get("preferenceCategory")

        rule_to_save = args.learned_rule
        if not rule_to_save and args.reason and args.action in ("upvote", "downvote", "calibrate_keep", "calibrate_skip", "meta_feedback"):
            rule_to_save = args.reason.strip()

        if rule_to_save and args.action != "calibrate_once":
            inferred_sentiment = args.sentiment
            if not inferred_sentiment:
                if args.action in ("upvote", "calibrate_keep"):
                    inferred_sentiment = "prefer"
                elif args.action in ("downvote", "calibrate_skip"):
                    inferred_sentiment = "avoid"
                else:
                    inferred_sentiment = "custom"
            add_or_update_rule(
                prefs,
                rule_text=rule_to_save,
                sentiment=inferred_sentiment,
                category=finding_cat,
                repo=args.repo,
                source_title=finding_title,
                origin="learned",
            )

        if args.action == "edit_draft" and args.original_draft and args.updated_draft:
            if args.original_draft.strip() != args.updated_draft.strip():
                edits = prefs.setdefault("editedDraftExamples", [])
                edits.append(
                    {
                        "timestamp": now_iso,
                        "path": finding_path,
                        "title": finding_title,
                        "originalDraft": args.original_draft.strip(),
                        "editedDraft": args.updated_draft.strip(),
                    }
                )
                prefs["editedDraftExamples"] = edits[-20:]

        history = prefs.setdefault("feedbackHistory", [])
        history.append(
            {
                "id": f"fb-{len(history) + 1}-{int(datetime.datetime.now().timestamp())}",
                "timestamp": now_iso,
                "repo": session_repo,
                "threadId": args.thread_id,
                "path": finding_path,
                "findingTitle": finding_title,
                "action": args.action,
                "userFeedback": args.reason or "",
                "learnedRule": rule_to_save or "",
            }
        )
        prefs["feedbackHistory"] = history[-100:]

    save_preferences(prefs)

    if args.thread_id and args.record_feedback:
        update_active_session_thread(
            thread_id=args.thread_id,
            action=args.action,
            user_feedback=args.reason or "",
            learned_rule=args.learned_rule or args.reason or "",
            updated_draft=args.updated_draft,
            prefs=prefs,
            workspace_dir=args.workspace_dir,
        )
    elif os.path.isfile(active_sess_path_for_main):
        update_active_session_thread(
            thread_id="__none__",
            action="sync_prefs",
            user_feedback="",
            learned_rule="",
            prefs=prefs,
            workspace_dir=args.workspace_dir,
        )

    out = build_prompt_summary(prefs, session_repo)
    out["ok"] = True
    out["preferencesJsonPath"] = PREFERENCES_JSON_PATH
    out["preferencesMarkdownPath"] = PREFERENCES_MD_PATH
    out["rules"] = prefs.get("rules") or []
    if imported_skill_info:
        out["importedSkill"] = imported_skill_info
        out["extractedRulesCount"] = imported_skill_info.get("extractedRulesCount", 0)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
