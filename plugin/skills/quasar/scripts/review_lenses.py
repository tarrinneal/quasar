#!/usr/bin/env python3
"""Discovers Quasar review lenses (`review-*` subagents) and resolves which run at each effort level.

Each lens's default effort levels come from its AGENT.md frontmatter:

    quasarEfforts: [medium, high]      # effort levels the lens runs at by default ([] = opt-in only)
    quasarAppliesWhen: "..."           # when the orchestrator should actually spawn it for a diff
    quasarRole: filter                 # (optional) marks a non-lens helper such as review-advocate

Custom lenses without `quasarEfforts` default to medium + high.

Users override defaults in ~/.gemini/quasar/preferences.json:

    "lensEfforts": { "review-security": ["high"], "review-newcomer": ["medium", "high"] }

CLI:
    review_lenses.py --list [--workspace-dir DIR] [--repo-dir DIR]
    review_lenses.py --set-effort-lenses high --lenses review-architect,review-security
    review_lenses.py --set-lens review-security --efforts medium,high
    review_lenses.py --reset [--lens review-security]
"""

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

EFFORT_LEVELS = ("low", "medium", "high")
CUSTOM_DEFAULT_EFFORTS = ["medium", "high"]

BUILTIN_REVIEW_AGENT_NAMES = {
    "review-architect",
    "review-blind-diff",
    "review-bug-hunter",
    "review-test-critic",
    "review-advocate",
    "review-security",
    "review-performance",
    "review-readability",
    "review-docs",
    "review-concurrency",
    "review-compat",
    "review-accessibility",
    "review-error-handling",
    "review-dependencies",
    "review-newcomer",
}

# Fallbacks used when a built-in AGENT.md lacks quasar* frontmatter (e.g. an older installed copy).
BUILTIN_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "review-advocate": {"role": "filter", "efforts": []},
    "review-architect": {"efforts": ["medium", "high"]},
    "review-blind-diff": {"efforts": ["medium", "high"], "blindDiff": True},
    "review-bug-hunter": {"efforts": ["medium", "high"]},
    "review-test-critic": {"efforts": ["high"]},
    "review-security": {"efforts": ["medium", "high"]},
    "review-newcomer": {"efforts": []},
}

PLUGIN_AGENTS_DIR = os.path.expanduser("~/.gemini/config/plugins/quasar/agents")
GLOBAL_AGENTS_DIR = os.path.expanduser("~/.gemini/config/agents")
SOURCE_AGENTS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "agents")
)


def _parse_inline_list(raw: str) -> List[str]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [p.strip().strip('"').strip("'") for p in raw.split(",") if p.strip()]


def _parse_agent_frontmatter(md_text: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    fm = re.match(r"^---\s*\n(.*?)\n---", md_text, re.DOTALL)
    if not fm:
        return meta
    fm_text = fm.group(1)

    def scalar(key: str) -> Optional[str]:
        m = re.search(rf"^{key}:\s*([^\n]*)", fm_text, re.MULTILINE)
        return m.group(1).strip().strip('"').strip("'") if m else None

    for key in ("name", "description", "quasarAppliesWhen", "quasarRole"):
        val = scalar(key)
        if val is not None:
            meta[key] = val

    blind_raw = scalar("quasarBlindDiff")
    if blind_raw is not None:
        meta["quasarBlindDiff"] = blind_raw.lower() in ("true", "1", "yes")

    m_eff = re.search(r"^quasarEfforts:\s*([^\n]*)", fm_text, re.MULTILINE)
    if m_eff:
        inline = m_eff.group(1).strip()
        if inline:
            levels = _parse_inline_list(inline)
        else:
            # Block-style YAML list on following lines
            block = re.search(r"^quasarEfforts:\s*\n((?:\s+-\s*[^\n]+\n?)+)", fm_text, re.MULTILINE)
            levels = (
                [ln.strip()[1:].strip().strip('"').strip("'") for ln in block.group(1).splitlines() if ln.strip()]
                if block
                else []
            )
        meta["quasarEfforts"] = [lv.lower() for lv in levels if lv.lower() in EFFORT_LEVELS]
    return meta


def discover_review_agents(repo_dir: Optional[str] = None, ws_root: Optional[str] = None) -> List[Dict[str, Any]]:
    """Finds review-* subagents across plugin, global, and workspace agent folders (first match wins)."""
    candidate_roots: List[tuple] = [
        (SOURCE_AGENTS_DIR, "plugin"),
        (PLUGIN_AGENTS_DIR, "plugin"),
        (GLOBAL_AGENTS_DIR, "global"),
    ]
    for extra_root in (repo_dir, ws_root):
        if extra_root:
            candidate_roots.append((os.path.join(extra_root, ".gemini", "agents"), "workspace"))

    discovered: Dict[str, Dict[str, Any]] = {}
    for root, scope in candidate_roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))
        except Exception:
            continue
        for entry in entries:
            agent_dir = os.path.join(root, entry)
            if not os.path.isdir(agent_dir):
                continue
            md_file = next(
                (os.path.join(agent_dir, f) for f in ("AGENT.md", "agent.md") if os.path.isfile(os.path.join(agent_dir, f))),
                None,
            )
            if not md_file:
                continue
            try:
                with open(md_file, "r", encoding="utf-8") as af:
                    meta = _parse_agent_frontmatter(af.read(8192))
            except Exception:
                meta = {}
            name = meta.get("name") or entry
            if not name.startswith("review-") and scope != "plugin":
                continue
            if name in discovered:
                continue
            is_custom = name not in BUILTIN_REVIEW_AGENT_NAMES
            builtin_fb = BUILTIN_DEFAULTS.get(name, {}) if not is_custom else {}
            role = (meta.get("quasarRole") or builtin_fb.get("role") or "lens").lower()
            if "quasarEfforts" in meta:
                default_efforts = meta["quasarEfforts"]
            elif "efforts" in builtin_fb:
                default_efforts = list(builtin_fb["efforts"])
            elif not is_custom:
                default_efforts = ["high"]
            else:
                default_efforts = list(CUSTOM_DEFAULT_EFFORTS) if role == "lens" else []
            blind_diff = bool(
                meta["quasarBlindDiff"]
                if "quasarBlindDiff" in meta
                else builtin_fb.get("blindDiff", False)
            )
            discovered[name] = {
                "name": name,
                "description": meta.get("description") or "",
                "path": md_file,
                "scope": scope,
                "isCustom": is_custom,
                "role": role,
                "blindDiff": blind_diff,
                "appliesWhen": meta.get("quasarAppliesWhen") or "",
                "defaultEfforts": default_efforts,
            }
    return list(discovered.values())


def resolve_lens_efforts(agents: List[Dict[str, Any]], prefs: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Annotates each lens with its effective `efforts` (user override > frontmatter default)."""
    overrides = (prefs or {}).get("lensEfforts") or {}
    out: List[Dict[str, Any]] = []
    for ag in agents:
        a = dict(ag)
        if a.get("role") != "lens":
            a["efforts"] = []
            a["isOverridden"] = False
        elif isinstance(overrides.get(a["name"]), list):
            a["efforts"] = [lv for lv in overrides[a["name"]] if lv in EFFORT_LEVELS]
            a["isOverridden"] = True
        else:
            a["efforts"] = list(a.get("defaultEfforts") or [])
            a["isOverridden"] = False
        out.append(a)
    return out


def lenses_by_effort(resolved: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    return {
        lv: [a["name"] for a in resolved if a.get("role") == "lens" and lv in (a.get("efforts") or [])]
        for lv in EFFORT_LEVELS
    }


def _extract_subagent_learned_rules(prefs: Optional[Dict[str, Any]], agent_name: str) -> Dict[str, List[str]]:
    prefer_list: List[str] = []
    avoid_list: List[str] = []
    custom_list: List[str] = []
    target = (agent_name or "").strip().lower()
    for r in (prefs or {}).get("rules") or []:
        if r.get("origin") == "seed_skill":
            continue
        sub = str(r.get("subagent") or "").strip().lower()
        if sub != target:
            continue
        txt = str(r.get("rule") or "").strip()
        if not txt:
            continue
        sent = str(r.get("sentiment") or "custom").lower()
        if sent in ("prefer", "always", "keep", "upvote"):
            prefer_list.append(txt)
        elif sent in ("avoid", "never", "skip", "downvote"):
            avoid_list.append(txt)
        else:
            custom_list.append(txt)
    return {
        "preferRules": prefer_list,
        "avoidRules": avoid_list,
        "customRules": custom_list,
    }


def build_lens_context(
    prefs: Optional[Dict[str, Any]], effort_level: str, repo_dir: Optional[str] = None, ws_root: Optional[str] = None
) -> Dict[str, Any]:
    """Returns the lens-related keys embedded in pr_context.json."""
    resolved = resolve_lens_efforts(discover_review_agents(repo_dir, ws_root), prefs)
    sub_stats = (prefs or {}).get("subagentStats") or {}
    for a in resolved:
        a["learnedDirectives"] = _extract_subagent_learned_rules(prefs, a["name"])
        a["stats"] = sub_stats.get(a["name"], {})
    by_effort = lenses_by_effort(resolved)
    selected = [
        {
            "name": a["name"],
            "description": a["description"],
            "appliesWhen": a["appliesWhen"],
            "blindDiff": bool(a.get("blindDiff", False)),
            "isCustom": a["isCustom"],
            "path": a["path"],
            "learnedDirectives": a.get("learnedDirectives") or {"preferRules": [], "avoidRules": [], "customRules": []},
        }
        for a in resolved
        if a["name"] in by_effort.get(effort_level, [])
    ]
    return {
        "available_review_agents": resolved,
        "effort_lenses": by_effort,
        "review_lenses": selected,
    }


def _load_prefs():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from manage_preferences import load_preferences, save_preferences  # type: ignore

    return load_preferences, save_preferences


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure which Quasar review lenses run at each effort level.")
    parser.add_argument("--list", action="store_true", help="List all lenses and their effective effort levels")
    parser.add_argument("--workspace-dir", help="Workspace dir (to include <ws>/.gemini/agents)")
    parser.add_argument("--repo-dir", help="Repo dir (to include <repo>/.gemini/agents)")
    parser.add_argument("--set-effort-lenses", choices=EFFORT_LEVELS, help="Set the exact lens list for one effort level")
    parser.add_argument("--lenses", default=None, help="Comma-separated lens names for --set-effort-lenses")
    parser.add_argument("--set-lens", help="Lens name whose effort levels to set")
    parser.add_argument("--efforts", default=None, help="Comma-separated effort levels for --set-lens ('' = opt-in only)")
    parser.add_argument("--reset", action="store_true", help="Reset overrides back to AGENT.md defaults (all, or --lens)")
    parser.add_argument("--lens", help="Lens name for --reset")
    args = parser.parse_args()

    load_preferences, save_preferences = _load_prefs()
    prefs = load_preferences()
    agents = discover_review_agents(args.repo_dir, args.workspace_dir)
    lens_names = {a["name"] for a in agents if a.get("role") == "lens"}
    changed = False

    if args.reset:
        overrides = dict(prefs.get("lensEfforts") or {})
        if args.lens:
            overrides.pop(args.lens, None)
        else:
            overrides = {}
        prefs["lensEfforts"] = overrides
        changed = True

    if args.set_effort_lenses:
        level = args.set_effort_lenses
        chosen = {n.strip() for n in (args.lenses or "").split(",") if n.strip()}
        unknown = sorted(chosen - lens_names)
        if unknown:
            print(json.dumps({"ok": False, "error": f"Unknown lens(es): {', '.join(unknown)}"}))
            sys.exit(1)
        resolved = resolve_lens_efforts(agents, prefs)
        overrides = dict(prefs.get("lensEfforts") or {})
        for a in resolved:
            if a.get("role") != "lens":
                continue
            efforts = [lv for lv in a["efforts"] if lv != level]
            if a["name"] in chosen:
                efforts.append(level)
            efforts = [lv for lv in EFFORT_LEVELS if lv in efforts]
            if efforts == list(a.get("defaultEfforts") or []):
                overrides.pop(a["name"], None)
            else:
                overrides[a["name"]] = efforts
        prefs["lensEfforts"] = overrides
        changed = True

    if args.set_lens:
        if args.set_lens not in lens_names:
            print(json.dumps({"ok": False, "error": f"Unknown lens: {args.set_lens}"}))
            sys.exit(1)
        efforts = [lv.strip().lower() for lv in (args.efforts or "").split(",") if lv.strip()]
        efforts = [lv for lv in EFFORT_LEVELS if lv in efforts]
        overrides = dict(prefs.get("lensEfforts") or {})
        default = next((a.get("defaultEfforts") for a in agents if a["name"] == args.set_lens), [])
        if efforts == list(default or []):
            overrides.pop(args.set_lens, None)
        else:
            overrides[args.set_lens] = efforts
        prefs["lensEfforts"] = overrides
        changed = True

    if changed:
        save_preferences(prefs)

    resolved = resolve_lens_efforts(agents, prefs)
    print(
        json.dumps(
            {
                "ok": True,
                "lenses": [a for a in resolved if a.get("role") == "lens"],
                "effortLenses": lenses_by_effort(resolved),
                "overrides": prefs.get("lensEfforts") or {},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
