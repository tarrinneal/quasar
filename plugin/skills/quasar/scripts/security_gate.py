#!/usr/bin/env python3
"""Deterministic Pre-Review Security & Prompt-Injection Gate for Quasar.

Runs purely in Python inside `collect_pr_context.py` BEFORE any LLM review agent
reads the PR description, comments, or diff:
  - Never claims a PR is "safe" (returns `status: "none"` when no signals fire,
    producing zero UI noise and no false sense of security).
  - Halts LLM review (`status: "blocked"`, `haltLlmReview: True`) when high-risk
    prompt-injection directives, Trojan-Source BiDi / Unicode tag smuggling,
    obfuscated `eval`/`exec` payloads, remote shell-pipe execution (`curl | sh`),
    or untrusted `pull_request_target` checkouts are detected—so poisoned text is
    never fed into LLM subagents.
  - Surfaces a factual warning (`status: "warning"`, `haltLlmReview: False`) when
    a PR modifies CI workflows, build/install hooks, git/IDE hooks, or local
    coding-agent configuration files, reminding the reviewer to inspect those
    files before checking out the branch or running local commands.
"""

import os
import posixpath
import re
from typing import Any, Dict, List, Optional, Tuple

# Bidirectional override/isolate controls (Trojan Source, CVE-2021-42574)
BIDI_CONTROL_POINTS: Dict[int, str] = {
    0x061C: "ALM",
    0x202A: "LRE",
    0x202B: "RLE",
    0x202C: "PDF",
    0x202D: "LRO",
    0x202E: "RLO",
    0x2066: "LRI",
    0x2067: "RLI",
    0x2068: "FSI",
    0x2069: "PDI",
}

# Zero-width characters used in multi-char invisible payload runs
ZERO_WIDTH_RUN_RE = re.compile(r"[\u200b-\u200f\u2060]{4,}")

# Explicit prompt-injection / agent-hijack directives
PROMPT_INJECTION_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        "Instruction-override directive targeting LLM prompts",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\s+"
            r"(?:all\s+|any\s+|every\s+|your\s+)?"
            r"(?:previous|prior|above|earlier|preceding|system)\s+"
            r"(?:instructions?|prompts?|directives?|rules?|guidelines?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Fake system/model role boundary token",
        re.compile(
            r"(?:<\|(?:im_start|im_end|system|endoftext)\|>|"
            r"</?(?:system_prompt|system-reminder|antml:[a-z_]+)>)",
            re.IGNORECASE,
        ),
    ),
    (
        "Review-hijack directive instructing an AI reviewer to suppress findings or auto-approve",
        re.compile(
            r"(?:"
            r"\b(?:ai|llm|automated|code)\s+reviewer\s*:\s*(?:ignore|approve|lgtm|do\s+not|skip|report\s+no)\b|"
            r"\b(?:do\s+not|never)\s+(?:mention|report|flag|disclose)\s+(?:any\s+)?"
            r"(?:vulnerabilit(?:y|ies)|security\s+issues?|backdoors?|bugs?|findings?)\s+in\s+this\s+(?:pr|pull\s+request|diff|commit|file)\b|"
            r"\byou\s+are\s+reviewing\s+a\s+(?:safe|trusted|benign)\s+(?:pr|pull\s+request)\b[^.\n]{0,80}\b(?:approve|no\s+issues|ignore)\b"
            r")",
            re.IGNORECASE,
        ),
    ),
]

HTML_COMMENT_RE = re.compile(r"<!--([\s\S]{1,4000}?)-->")
HIDDEN_COMMENT_HIJACK_RE = re.compile(
    r"\b(?:ignore\s+(?:all\s+)?previous|system\s+prompt|approve\s+this\s+pr|"
    r"mark\s+(?:this\s+)?as\s+safe|report\s+no\s+(?:issues|bugs|findings)|"
    r"do\s+not\s+(?:report|flag)|run_command|invoke_subagent|curl\s+https?://)\b",
    re.IGNORECASE,
)

# High-risk remote / obfuscated code execution on added diff lines
REMOTE_PIPE_EXEC_RE = re.compile(
    r"\b(?:curl|wget)\b[^\n|#;]{3,200}\|\s*(?:sudo\s+)?(?:ba|z|da|k|a)?sh\b"
    r"|\b(?:curl|wget)\b[^\n|#;]{3,200}\|\s*(?:sudo\s+)?(?:python3?|node|perl|ruby)\b"
    r"|\bpowershell(?:\.exe)?\b[^\n#]{0,120}\s(?:-enc|-encodedcommand|invoke-expression|\biex\b)\b",
    re.IGNORECASE,
)

OBFUSCATED_EXEC_RE = re.compile(
    r"\b(?:eval|exec)\s*\(\s*(?:atob|Buffer\.from|base64\.b64decode|zlib\.decompress|codecs\.decode|marshal\.loads)\s*\("
    r"|\bFunction\s*\(\s*atob\s*\(",
    re.IGNORECASE,
)

PACKAGE_JSON_LIFECYCLE_RE = re.compile(
    r'"(?:preinstall|install|postinstall|preuninstall|postuninstall|prepare|prepublish)"\s*:',
)

CI_WORKFLOW_PATH_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"^\.github/(?:workflows|actions)/.+"),
    re.compile(r"^\.circleci/.+"),
    re.compile(r"^\.gitlab-ci\.ya?ml$"),
    re.compile(r"^(?:azure-pipelines\.ya?ml|Jenkinsfile|\.travis\.ya?ml)$"),
    re.compile(r"^\.buildkite/.+"),
]

GIT_OR_IDE_HOOK_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"^(?:\.githooks|\.husky|\.lefthook)(?:/|$)"),
    re.compile(r"^\.pre-commit-config\.ya?ml$"),
    re.compile(r"^\.vscode/(?:tasks|launch|settings)\.json$"),
    re.compile(r"^\.devcontainer/.+"),
    re.compile(r"^(?:\.envrc|\.direnv/.*)$"),
]

AGENT_CONFIG_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"(?:^|/)(?:AGENTS|CLAUDE|GEMINI)\.md$"),
    re.compile(r"^(?:\.gemini|\.claude|\.cursor|\.codex|\.windsurf)/.+"),
    re.compile(r"^(?:\.cursorrules|\.clinerules|\.windsurfrules|mcp\.json|\.mcp\.json)$"),
    re.compile(r"^\.github/copilot-instructions\.md$"),
]

BUILD_SCRIPT_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"(?:^|/)(?:Makefile|GNUmakefile|CMakeLists\.txt|Podfile|Fastfile|Rakefile|Justfile)$"),
    re.compile(r"(?:^|/)[^/]+\.cmake$"),
    re.compile(r"(?:^|/)(?:setup\.py|build\.rs|conanfile\.py)$"),
    re.compile(r"(?:^|/)(?:build\.gradle(?:\.kts)?|settings\.gradle(?:\.kts)?|gradlew(?:\.bat)?)$"),
]


def _reveal_control_chars(text: str, limit: int = 140) -> str:
    """Renders invisible/BiDi/tag control characters visibly and truncates safely."""
    out: List[str] = []
    for ch in text:
        cp = ord(ch)
        if cp in BIDI_CONTROL_POINTS:
            out.append(f"[U+{cp:04X} {BIDI_CONTROL_POINTS[cp]}]")
        elif 0xE0000 <= cp <= 0xE007F:
            out.append(f"[U+{cp:05X} TAG]")
        elif cp in (0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF):
            out.append(f"[U+{cp:04X}]")
        elif ch in ("\r", "\n", "\t"):
            out.append(" ")
        else:
            out.append(ch)
    rendered = re.sub(r"\s{2,}", " ", "".join(out)).strip()
    if len(rendered) > limit:
        return rendered[: limit - 1] + "…"
    return rendered


def _find_invisible_unicode_issue(text: str) -> Optional[Tuple[str, str]]:
    """Checks for Trojan-Source BiDi controls, Unicode Tag ASCII smuggling, or hidden ZW runs."""
    if not text:
        return None
    for idx, ch in enumerate(text):
        cp = ord(ch)
        if cp in BIDI_CONTROL_POINTS:
            window = text[max(0, idx - 30) : min(len(text), idx + 50)]
            return (
                f"Bidirectional Unicode control character U+{cp:04X} ({BIDI_CONTROL_POINTS[cp]}) detected (Trojan-Source risk).",
                _reveal_control_chars(window),
            )
        if 0xE0000 <= cp <= 0xE007F:
            window = text[max(0, idx - 30) : min(len(text), idx + 50)]
            return (
                f"Invisible Unicode Tag character U+{cp:05X} detected (LLM ASCII-smuggling risk).",
                _reveal_control_chars(window),
            )
    m_zw = ZERO_WIDTH_RUN_RE.search(text)
    if m_zw:
        s, e = m_zw.span()
        window = text[max(0, s - 25) : min(len(text), e + 35)]
        return (
            f"Cluster of {e - s} consecutive invisible zero-width Unicode characters detected.",
            _reveal_control_chars(window),
        )
    return None


def _find_prompt_injection_in_prose(text: str) -> Optional[Tuple[str, str]]:
    """Scans untrusted prose (PR title, body, comments) for prompt-injection payloads."""
    if not text:
        return None
    for m_html in HTML_COMMENT_RE.finditer(text):
        inner = m_html.group(1) or ""
        if HIDDEN_COMMENT_HIJACK_RE.search(inner):
            return (
                "Hidden HTML comment contains instructions aimed at an AI reviewer or tool.",
                _reveal_control_chars(m_html.group(0)),
            )
    for detail, pat in PROMPT_INJECTION_PATTERNS:
        m = pat.search(text)
        if m:
            s, e = m.span()
            window = text[max(0, s - 25) : min(len(text), e + 45)]
            return (detail, _reveal_control_chars(window))
    return None


def _iter_added_diff_lines(diff_text: str) -> List[Tuple[str, int, str]]:
    """Extracts `(rel_path, new_line_number, line_content)` for every added line in a unified diff."""
    added: List[Tuple[str, int, str]] = []
    cur_file = ""
    new_line = 0
    in_hunk = False
    for raw_ln in (diff_text or "").splitlines():
        if raw_ln.startswith("diff --git "):
            in_hunk = False
            m_git = re.match(r"^diff --git a/(.+?) b/(.+)$", raw_ln)
            if m_git:
                cur_file = m_git.group(2).strip()
            continue
        if raw_ln.startswith("+++ b/"):
            cur_file = raw_ln[6:].strip()
            continue
        if raw_ln.startswith("@@ "):
            m_hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw_ln)
            if m_hunk:
                new_line = int(m_hunk.group(1))
                in_hunk = True
            continue
        if not in_hunk or not cur_file:
            continue
        if raw_ln.startswith("+") and not raw_ln.startswith("+++"):
            added.append((cur_file, new_line, raw_ln[1:]))
            new_line += 1
        elif raw_ln.startswith("-") and not raw_ln.startswith("---"):
            continue
        elif raw_ln.startswith("\\"):
            continue
        else:
            new_line += 1
    return added


def _matches_any(path: str, patterns: List[re.Pattern[str]]) -> bool:
    norm = posixpath.normpath(path).replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return any(p.search(norm) for p in patterns)


def run_security_gate(
    metadata: Dict[str, Any],
    comments: List[Dict[str, Any]],
    files_list: List[Dict[str, Any]],
    diff_text: str,
    allow_quarantined: bool = False,
) -> Dict[str, Any]:
    """Evaluates PR metadata, comments, changed file paths, and unified diff deterministically.

    Returns a normalized `securityGate` dictionary:
      - `status`: `"none"` | `"warning"` | `"blocked"`
      - `haltLlmReview`: `bool` (`True` when `status == "blocked"` and not `allow_quarantined`)
      - `overridden`: `bool`
      - `summary`: `str` (empty when `status == "none"`)
      - `signals`: `List[Dict[str, Any]]`
    """
    signals: List[Dict[str, Any]] = []
    seen_keys = set()

    def add_signal(
        severity: str,
        category: str,
        detail: str,
        location: str = "",
        path: str = "",
        line: int = 1,
        excerpt: str = "",
    ) -> None:
        key = (severity, category, path or location, detail)
        if key in seen_keys:
            return
        seen_keys.add(key)
        entry: Dict[str, Any] = {
            "severity": severity,  # "blocked" | "warning"
            "category": category,
            "location": location or (f"{path}:L{line}" if path else "PR Metadata"),
            "detail": detail,
        }
        if path:
            entry["path"] = path
            entry["line"] = max(1, int(line or 1))
        if excerpt:
            entry["excerpt"] = excerpt
        signals.append(entry)

    # 1. Scan PR title, body, and existing comments for invisible Unicode & prompt injection
    prose_sources: List[Tuple[str, str]] = [
        ("PR Title", str(metadata.get("title") or "")),
        ("PR Description", str(metadata.get("body") or "")),
    ]
    for idx, c in enumerate(comments or [], 1):
        c_body = str(c.get("body") or "")
        c_path = str(c.get("path") or "")
        c_loc = f"{c_path}:L{c.get('line') or 1} (Comment)" if c_path else f"PR Comment #{idx}"
        prose_sources.append((c_loc, c_body))

    for loc_label, text in prose_sources:
        if not text:
            continue
        uni_hit = _find_invisible_unicode_issue(text)
        if uni_hit:
            add_signal(
                severity="blocked",
                category="bidi_unicode",
                location=loc_label,
                detail=uni_hit[0],
                excerpt=uni_hit[1],
            )
        inj_hit = _find_prompt_injection_in_prose(text)
        if inj_hit:
            add_signal(
                severity="blocked",
                category="prompt_injection",
                location=loc_label,
                detail=inj_hit[0],
                excerpt=inj_hit[1],
            )

    # 2. Scan changed file paths for sensitive execution / config surfaces (warning tier)
    workflow_added_lines: Dict[str, List[Tuple[int, str]]] = {}
    for f in files_list or []:
        if str(f.get("status") or "") == "removed":
            continue
        rel_path = str(f.get("path") or "").strip()
        if not rel_path:
            continue
        first_line = int(f.get("firstChangedStartLine") or 1)
        if _matches_any(rel_path, CI_WORKFLOW_PATH_PATTERNS):
            add_signal(
                severity="warning",
                category="ci_workflow",
                path=rel_path,
                line=first_line,
                location=f"{rel_path}:L{first_line}",
                detail="Modifies CI/CD workflow or action definition.",
            )
            workflow_added_lines[rel_path] = []
        elif _matches_any(rel_path, GIT_OR_IDE_HOOK_PATTERNS):
            add_signal(
                severity="warning",
                category="git_or_ide_hook",
                path=rel_path,
                line=first_line,
                location=f"{rel_path}:L{first_line}",
                detail="Modifies Git hook, DevContainer, direnv, or IDE task/launch configuration.",
            )
        elif _matches_any(rel_path, AGENT_CONFIG_PATTERNS):
            add_signal(
                severity="warning",
                category="agent_config",
                path=rel_path,
                line=first_line,
                location=f"{rel_path}:L{first_line}",
                detail="Modifies repository coding-agent instructions, hooks, or MCP configuration.",
            )
        elif _matches_any(rel_path, BUILD_SCRIPT_PATTERNS):
            add_signal(
                severity="warning",
                category="build_hook",
                path=rel_path,
                line=first_line,
                location=f"{rel_path}:L{first_line}",
                detail="Modifies build or install script that executes commands during build/setup.",
            )

    # 3. Scan added diff lines for injections, BiDi Unicode, remote-pipe exec, obfuscation, & lifecycle hooks
    added_lines = _iter_added_diff_lines(diff_text)
    for rel_path, ln_no, content in added_lines:
        if rel_path in workflow_added_lines:
            workflow_added_lines[rel_path].append((ln_no, content))

        uni_hit = _find_invisible_unicode_issue(content)
        if uni_hit:
            add_signal(
                severity="blocked",
                category="bidi_unicode",
                path=rel_path,
                line=ln_no,
                detail=uni_hit[0],
                excerpt=uni_hit[1],
            )

        inj_hit = _find_prompt_injection_in_prose(content)
        if inj_hit:
            add_signal(
                severity="blocked",
                category="prompt_injection",
                path=rel_path,
                line=ln_no,
                detail=inj_hit[0],
                excerpt=inj_hit[1],
            )

        m_pipe = REMOTE_PIPE_EXEC_RE.search(content)
        if m_pipe:
            add_signal(
                severity="blocked",
                category="remote_exec",
                path=rel_path,
                line=ln_no,
                detail="Downloads and pipes remote content directly into a shell or interpreter.",
                excerpt=_reveal_control_chars(content),
            )

        m_obf = OBFUSCATED_EXEC_RE.search(content)
        if m_obf:
            add_signal(
                severity="blocked",
                category="obfuscation",
                path=rel_path,
                line=ln_no,
                detail="Executes dynamically decoded or compressed payload (`eval`/`exec` on encoded data).",
                excerpt=_reveal_control_chars(content),
            )

        if os.path.basename(rel_path) == "package.json" and PACKAGE_JSON_LIFECYCLE_RE.search(content):
            add_signal(
                severity="warning",
                category="build_hook",
                path=rel_path,
                line=ln_no,
                detail="Adds or modifies `package.json` install/prepare lifecycle hook.",
                excerpt=_reveal_control_chars(content),
            )

    # 4. Check GitHub Actions workflows for `pull_request_target` + untrusted head checkout ("Pwn Request")
    for wf_path, wf_lines in workflow_added_lines.items():
        joined = "\n".join(txt for _, txt in wf_lines)
        has_prt = bool(re.search(r"\bpull_request_target\b", joined))
        has_head_ref = bool(
            re.search(
                r"github\.event\.pull_request\.head\.(?:sha|ref)|refs/pull/\$\{\{\s*github\.event\.number",
                joined,
            )
        )
        if has_prt and has_head_ref:
            prt_line = next(
                (ln for ln, txt in wf_lines if "pull_request_target" in txt),
                wf_lines[0][0] if wf_lines else 1,
            )
            add_signal(
                severity="blocked",
                category="ci_pwn_request",
                path=wf_path,
                line=prt_line,
                detail="Workflow combines `pull_request_target` with checkout of untrusted PR head ref/SHA.",
            )

    has_blocked = any(s.get("severity") == "blocked" for s in signals)
    has_warning = any(s.get("severity") == "warning" for s in signals)

    if not signals:
        return {
            "status": "none",
            "haltLlmReview": False,
            "overridden": False,
            "summary": "",
            "signals": [],
        }

    # Sort blocked signals ahead of warning signals
    signals.sort(key=lambda s: (0 if s.get("severity") == "blocked" else 1, str(s.get("location") or "")))

    if has_blocked:
        blocked_count = sum(1 for s in signals if s.get("severity") == "blocked")
        halt = not bool(allow_quarantined)
        if halt:
            summary = (
                f"Quarantined before LLM review: {blocked_count} high-risk prompt-injection or "
                f"code-execution signal{'s' if blocked_count != 1 else ''} detected. "
                "Inspect the flagged lines in the read-only diff before running any agent review or local commands."
            )
        else:
            summary = (
                f"Quarantine overridden by user ({blocked_count} high-risk signal{'s' if blocked_count != 1 else ''} present). "
                "Do NOT check out or execute code from this PR locally without manual verification."
            )
        return {
            "status": "blocked",
            "haltLlmReview": halt,
            "overridden": bool(allow_quarantined),
            "summary": summary,
            "signals": signals,
        }

    assert has_warning
    warn_count = len(signals)
    return {
        "status": "warning",
        "haltLlmReview": False,
        "overridden": False,
        "summary": (
            f"Touches {warn_count} CI workflow, build/install script, hook, or agent-config surface"
            f"{'s' if warn_count != 1 else ''}. Inspect these changes before checking out the branch or running local commands."
        ),
        "signals": signals,
    }


def normalize_security_gate(raw: Any) -> Optional[Dict[str, Any]]:
    """Normalizes a serialized `securityGate` dict; returns `None` when status is `'none'` or empty."""
    if not isinstance(raw, dict):
        return None
    status = str(raw.get("status") or "none").strip().lower()
    raw_signals = raw.get("signals")
    signals: List[Dict[str, Any]] = []
    if isinstance(raw_signals, list):
        for item in raw_signals:
            if not isinstance(item, dict):
                continue
            detail = str(item.get("detail") or "").strip()
            if not detail:
                continue
            sev = str(item.get("severity") or status).strip().lower()
            if sev not in ("blocked", "warning"):
                sev = "blocked" if status == "blocked" else "warning"
            sig: Dict[str, Any] = {
                "severity": sev,
                "category": str(item.get("category") or "security").strip(),
                "location": str(item.get("location") or "PR").strip(),
                "detail": detail,
            }
            if item.get("path"):
                sig["path"] = str(item["path"]).strip()
                sig["line"] = max(1, int(item.get("line") or 1))
            if item.get("excerpt"):
                sig["excerpt"] = str(item["excerpt"]).strip()
            signals.append(sig)
    if status not in ("blocked", "warning") or not signals:
        return None
    return {
        "status": status,
        "haltLlmReview": bool(raw.get("haltLlmReview", False)),
        "overridden": bool(raw.get("overridden", False)),
        "summary": str(raw.get("summary") or "").strip(),
        "signals": signals,
    }
