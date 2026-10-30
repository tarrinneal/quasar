#!/usr/bin/env python3
"""Quasar Web UI Sidecar & Standalone HTML Exporter.

Serves the interactive Quasar Code Review Web App in Jetski Web / Desktop
Aux Pane (`SIDECAR_UI_ENTRYPOINT_AUX_PANE`), Full Pane (`SIDECAR_UI_ENTRYPOINT_FULL_PANE`),
and standalone browser (`http://127.0.0.1:49341`), and can also export a self-contained
interactive HTML artifact (`--export-html /path/to/quasar_review.html`).
"""

import argparse
import difflib
import glob
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
CANDIDATE_SCRIPT_DIRS = [
    os.path.abspath(os.path.join(THIS_DIR, "..", "..", "skills", "quasar", "scripts")),
    os.path.expanduser("~/.gemini/config/plugins/quasar/skills/quasar/scripts"),
]
SCRIPTS_DIR = next((d for d in CANDIDATE_SCRIPT_DIRS if os.path.isdir(d)), CANDIDATE_SCRIPT_DIRS[0])
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

STATE_DIR = os.path.expanduser("~/.gemini/quasar")
WORKSPACES_DIR = os.path.join(STATE_DIR, "workspaces")
HISTORY_DIR = os.path.join(STATE_DIR, "history")
GITHUB_AUTH_PATH = os.path.join(STATE_DIR, "github_auth.json")
STANDALONE_PORT = 49341
PORT = int(os.environ.get("ANTIGRAVITY_SIDECAR_WEB_PORT") or os.environ.get("PORT") or STANDALONE_PORT)


def find_mermaid_js_path() -> Optional[str]:
    candidates = [
        os.path.join(THIS_DIR, "mermaid-preview.js"),
        os.path.abspath(os.path.join(THIS_DIR, "..", "..", "..", "extension", "media", "mermaid-preview.js")),
    ]
    for ext_dir in glob.glob(os.path.expanduser("~/.*/extensions/codeium.codeium-dev-*/media/mermaid-preview.js")):
        candidates.append(ext_dir)
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def get_agentapi_binary() -> str:
    env_exe = os.environ.get("ANTIGRAVITY_AGENTAPI_EXE")
    if env_exe and os.path.isfile(env_exe):
        return env_exe
    for cand in glob.glob(os.path.expanduser("~/.gemini/*/bin/agentapi")):
        if os.path.isfile(cand):
            return cand
    return "agentapi"


def discover_ls_env(workspace_dir: Optional[str] = None) -> Dict[str, str]:
    env = dict(os.environ)
    if env.get("ANTIGRAVITY_LS_ADDRESS") and env.get("ANTIGRAVITY_CSRF_TOKEN"):
        return env
    try:
        ps_out = subprocess.check_output(["/bin/ps", "-axww", "-o", "pid=,command="], text=True, timeout=3)
        norm_ws = re.sub(r"[^a-zA-Z0-9]", "_", workspace_dir or "")
        procs: List[Tuple[int, int, str]] = []
        for raw in ps_out.splitlines():
            line = raw.strip()
            if "language_server" not in line or "--csrf_token" not in line:
                continue
            m_pid = re.match(r"^(\d+)\s+(.*)$", line)
            if not m_pid:
                continue
            pid = int(m_pid.group(1))
            cmd = m_pid.group(2)
            m_csrf = re.search(r"--csrf_token\s+(\S+)", cmd)
            if not m_csrf:
                continue
            csrf = m_csrf.group(1)
            is_lsp = "--enable_lsp" in cmd
            m_ws = re.search(r"--workspace_id\s+(\S+)", cmd)
            ws_id = m_ws.group(1) if m_ws else ""
            matches_ws = bool(norm_ws and ws_id and norm_ws in ws_id)
            score = (10 if is_lsp else 0) + (0 if matches_ws else 5)
            procs.append((score, pid, csrf))
        procs.sort(key=lambda x: x[0])
        lsof_bin = "/usr/sbin/lsof" if os.path.exists("/usr/sbin/lsof") else "lsof"
        for _, pid, csrf in procs:
            try:
                lsof_out = subprocess.check_output(
                    [lsof_bin, "-Pan", "-p", str(pid), "-a", "-iTCP", "-sTCP:LISTEN"],
                    text=True,
                    timeout=3,
                )
                listeners: List[Tuple[int, int]] = []
                for l_line in lsof_out.splitlines():
                    m_fd = re.search(r"\s+(\d+)u\s+IPv[46]\s+.*?(?:127\.0\.0\.1|localhost|\*):(\d+)\s+\(LISTEN\)", l_line)
                    if m_fd:
                        listeners.append((int(m_fd.group(1)), int(m_fd.group(2))))
                listeners.sort(key=lambda x: x[0])
                chosen = next((p for fd, p in listeners if fd == 11), None)
                if not chosen and listeners:
                    chosen = listeners[1][1] if len(listeners) >= 2 else listeners[0][1]
                if chosen:
                    env["ANTIGRAVITY_LS_ADDRESS"] = f"localhost:{chosen}"
                    env["ANTIGRAVITY_CSRF_TOKEN"] = csrf
                    return env
            except Exception:
                continue
    except Exception:
        pass
    return env


def dispatch_to_agent(prompt: str, title: str = "Quasar Code Review", conversation_id: Optional[str] = None, workspace_dir: Optional[str] = None) -> Dict[str, Any]:
    exe = get_agentapi_binary()
    env = discover_ls_env(workspace_dir)
    conv_id = (conversation_id or os.environ.get("ANTIGRAVITY_CONVERSATION_ID") or "").strip()

    if not conv_id:
        # Check most recently active root transcript
        try:
            brain_dir = os.path.expanduser("~/.gemini/jetski/brain")
            candidates: List[Tuple[float, str]] = []
            if os.path.isdir(brain_dir):
                for entry in os.listdir(brain_dir):
                    t_path = os.path.join(brain_dir, entry, ".system_generated", "logs", "transcript.jsonl")
                    if os.path.isfile(t_path):
                        candidates.append((os.path.getmtime(t_path), entry))
            candidates.sort(reverse=True)
            if candidates:
                conv_id = candidates[0][1]
        except Exception:
            pass

    if conv_id:
        try:
            proc = subprocess.run(
                [exe, "send-message", f"--title={title}", conv_id, prompt],
                env=env,
                capture_output=True,
                text=True,
                timeout=8,
            )
            if proc.returncode == 0 and '"error"' not in (proc.stdout or "").lower():
                return {"ok": True, "mode": "send-message", "conversationId": conv_id, "stdout": proc.stdout.strip()}
        except Exception:
            pass

    try:
        proc = subprocess.run(
            [exe, "new-conversation", f"--title={title}", prompt],
            env=env,
            capture_output=True,
            text=True,
            timeout=8,
        )
        if proc.returncode == 0:
            return {"ok": True, "mode": "new-conversation", "stdout": proc.stdout.strip()}
        return {"ok": False, "error": (proc.stderr or proc.stdout or "agentapi failed").strip(), "prompt": prompt}
    except Exception as e:
        return {"ok": False, "error": str(e), "prompt": prompt}


def list_all_sessions() -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}

    def record(session_path: str) -> None:
        if not os.path.isfile(session_path):
            return
        try:
            mtime = os.path.getmtime(session_path)
            with open(session_path, "r", encoding="utf-8") as f:
                sess = json.load(f)
            if not isinstance(sess, dict):
                return
            sid = str(sess.get("sessionId") or "").strip()
            if not sid:
                pr = sess.get("pr") or {}
                sid = f"{pr.get('owner', 'local')}-{pr.get('repo', 'repo')}-{pr.get('number') or pr.get('headRefName') or 'session'}"
            sid = re.sub(r"[^a-zA-Z0-9._-]+", "-", sid).strip("-")
            existing = by_id.get(sid)
            if not existing or mtime >= existing["mtime"]:
                by_id[sid] = {
                    "id": sid,
                    "mtime": mtime,
                    "sessionPath": session_path,
                    "session": sess,
                }
        except Exception:
            pass

    if os.path.isdir(WORKSPACES_DIR):
        for entry in os.listdir(WORKSPACES_DIR):
            record(os.path.join(WORKSPACES_DIR, entry, "active_session.json"))
    if os.path.isdir(HISTORY_DIR):
        for entry in os.listdir(HISTORY_DIR):
            record(os.path.join(HISTORY_DIR, entry, "session.json"))

    items = list(by_id.values())
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def load_target_session(session_id: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    all_sessions = list_all_sessions()
    if not all_sessions:
        return None, None
    if session_id:
        for item in all_sessions:
            if item["id"] == session_id or (item["session"].get("sessionId") == session_id):
                return item["session"], item["sessionPath"]
    return all_sessions[0]["session"], all_sessions[0]["sessionPath"]


def save_session_everywhere(session: Dict[str, Any], session_path: Optional[str] = None) -> None:
    import datetime

    session["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    state_dir = session.get("stateDir")
    if state_dir and os.path.isdir(state_dir):
        target_path = os.path.join(state_dir, "active_session.json")
    elif session_path:
        target_path = session_path
    else:
        os.makedirs(WORKSPACES_DIR, exist_ok=True)
        ws_dir = os.path.join(WORKSPACES_DIR, "web-default")
        os.makedirs(ws_dir, exist_ok=True)
        target_path = os.path.join(ws_dir, "active_session.json")
        session["stateDir"] = ws_dir

    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)

    try:
        from collect_pr_context import archive_review_session, render_cliffs_notes_markdown

        md_text = render_cliffs_notes_markdown(session)
        md_path = os.path.join(os.path.dirname(target_path), "cliffs_notes.md")
        with open(md_path, "w", encoding="utf-8") as mf:
            mf.write(md_text)
        archive_review_session(session, md_text)
    except Exception:
        pass


def compute_file_diffs(session: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not session:
        return []
    base_root = session.get("baseSnapshotRoot") or ""
    head_root = session.get("headSnapshotRoot") or ""
    repo_dir = session.get("repoDir") or ""

    rel_paths: List[str] = []
    seen = set()

    for ch in session.get("chapters") or []:
        for st in ch.get("stops") or []:
            p = st.get("path")
            if p and p not in seen:
                seen.add(p)
                rel_paths.append(p)

    for th in session.get("threads") or []:
        p = th.get("path")
        if p and p not in seen:
            seen.add(p)
            rel_paths.append(p)

    if head_root and os.path.isdir(head_root):
        for root, _, files in os.walk(head_root):
            for fn in sorted(files):
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, head_root).replace(os.sep, "/")
                if rel not in seen:
                    seen.add(rel)
                    rel_paths.append(rel)

    if base_root and os.path.isdir(base_root):
        for root, _, files in os.walk(base_root):
            for fn in sorted(files):
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, base_root).replace(os.sep, "/")
                if rel not in seen:
                    seen.add(rel)
                    rel_paths.append(rel)

    result: List[Dict[str, Any]] = []
    for rel in rel_paths[:60]:
        base_file = os.path.join(base_root, rel) if base_root else ""
        head_file = os.path.join(head_root, rel) if head_root else ""
        local_file = os.path.join(repo_dir, rel) if repo_dir else ""

        base_lines: List[str] = []
        head_lines: List[str] = []
        if base_file and os.path.isfile(base_file):
            try:
                with open(base_file, "r", encoding="utf-8", errors="replace") as f:
                    base_lines = [ln.rstrip("\r\n") for ln in f.readlines()]
            except Exception:
                pass

        target_head = head_file if (head_file and os.path.isfile(head_file)) else (local_file if (local_file and os.path.isfile(local_file)) else "")
        if target_head:
            try:
                with open(target_head, "r", encoding="utf-8", errors="replace") as f:
                    head_lines = [ln.rstrip("\r\n") for ln in f.readlines()]
            except Exception:
                pass

        sm = difflib.SequenceMatcher(None, base_lines, head_lines)
        rows: List[Dict[str, Any]] = []
        additions = 0
        deletions = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for offset in range(i2 - i1):
                    rows.append({
                        "type": " context",
                        "baseLine": i1 + offset + 1,
                        "headLine": j1 + offset + 1,
                        "baseText": base_lines[i1 + offset],
                        "headText": head_lines[j1 + offset],
                    })
            elif tag == "delete":
                for i in range(i1, i2):
                    deletions += 1
                    rows.append({
                        "type": "del",
                        "baseLine": i + 1,
                        "headLine": None,
                        "baseText": base_lines[i],
                        "headText": "",
                    })
            elif tag == "insert":
                for j in range(j1, j2):
                    additions += 1
                    rows.append({
                        "type": "add",
                        "baseLine": None,
                        "headLine": j + 1,
                        "baseText": "",
                        "headText": head_lines[j],
                    })
            elif tag == "replace":
                max_len = max(i2 - i1, j2 - j1)
                for k in range(max_len):
                    b_idx = i1 + k if (i1 + k) < i2 else None
                    h_idx = j1 + k if (j1 + k) < j2 else None
                    if b_idx is not None:
                        deletions += 1
                    if h_idx is not None:
                        additions += 1
                    rows.append({
                        "type": "mod",
                        "baseLine": (b_idx + 1) if b_idx is not None else None,
                        "headLine": (h_idx + 1) if h_idx is not None else None,
                        "baseText": base_lines[b_idx] if b_idx is not None else "",
                        "headText": head_lines[h_idx] if h_idx is not None else "",
                    })

        result.append({
            "path": rel,
            "additions": additions,
            "deletions": deletions,
            "rows": rows[:1500],
        })
    return result


def build_state_payload(session_id: Optional[str] = None) -> Dict[str, Any]:
    session, session_path = load_target_session(session_id)
    all_sessions = list_all_sessions()
    session_summaries = []
    for item in all_sessions[:30]:
        s = item["session"]
        pr = s.get("pr") or {}
        stops_total = sum(len(ch.get("stops") or []) for ch in (s.get("chapters") or []))
        stops_read = sum(sum(1 for st in (ch.get("stops") or []) if st.get("read")) for ch in (s.get("chapters") or []))
        threads_active = sum(1 for t in (s.get("threads") or []) if t.get("status") != "dismissed")
        session_summaries.append({
            "id": item["id"],
            "sessionId": s.get("sessionId") or item["id"],
            "title": f"PR #{pr['number']}: {pr.get('title', '')}" if pr.get("number") else (pr.get("title") or f"Branch: {pr.get('headRefName', item['id'])}"),
            "repo": pr.get("repo") or "",
            "updatedAt": s.get("updatedAt") or "",
            "stopsRead": stops_read,
            "stopsTotal": stops_total,
            "threadsCount": threads_active,
            "effortLevel": s.get("effortLevel") or "medium",
            "commentsOnly": bool(s.get("commentsOnly", False)),
            "isTutorial": bool(s.get("isTutorial") or pr.get("isTutorial")),
        })

    gh_auth = None
    if os.path.isfile(GITHUB_AUTH_PATH):
        try:
            with open(GITHUB_AUTH_PATH, "r", encoding="utf-8") as f:
                raw_auth = json.load(f)
            if raw_auth.get("token"):
                gh_auth = {
                    "configured": True,
                    "login": raw_auth.get("login") or "GitHub User",
                    "tokenPreview": raw_auth.get("tokenPreview") or "***",
                }
        except Exception:
            pass

    prefs_summary = {}
    try:
        from manage_preferences import load_preferences

        prefs = load_preferences()
        prefs_summary = {
            "defaultEffort": prefs.get("defaultEffort") or "medium",
            "defaultCommentsOnly": bool(prefs.get("defaultCommentsOnly", False)),
            "rulesCount": len(prefs.get("rules") or []),
            "rules": (prefs.get("rules") or [])[:30],
        }
    except Exception:
        pass

    return {
        "ok": True,
        "activeSession": session,
        "activeSessionPath": session_path,
        "filesDiff": compute_file_diffs(session),
        "sessions": session_summaries,
        "githubAuth": gh_auth or {"configured": False},
        "preferences": prefs_summary,
        "sidecarPort": PORT,
    }


WEB_UI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Quasar — Code Review & Study Guide</title>
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
  <script src="/mermaid-preview.js"></script>
  <!--__QUASAR_INLINE_SCRIPTS__-->
  <style>
    :root {
      --q-bg: var(--app-background, #0d1117);
      --q-fg: var(--app-foreground, #e6edf3);
      --q-card: var(--app-card, #161b22);
      --q-border: var(--app-border, #30363d);
      --q-muted: var(--app-muted-foreground, #8b949e);
      --q-accent: var(--app-primary, #58a6ff);
    }
    body {
      background: var(--q-bg);
      color: var(--q-fg);
      font-family: var(--vscode-font-family, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif);
      margin: 0;
      padding: 0;
    }
    .q-card {
      background: var(--q-card);
      border: 1px solid var(--q-border);
      border-radius: 10px;
    }
    .q-btn {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 5px 10px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 500;
      border: 1px solid var(--q-border);
      background: rgba(255, 255, 255, 0.04);
      color: var(--q-fg);
      cursor: pointer;
      transition: background 0.12s, border-color 0.12s;
    }
    .q-btn:hover {
      background: rgba(88, 166, 255, 0.14);
      border-color: rgba(88, 166, 255, 0.45);
    }
    .q-btn-primary {
      background: rgba(56, 139, 253, 0.2);
      border-color: rgba(56, 139, 253, 0.55);
      color: #79c0ff;
    }
    .q-btn-success {
      background: rgba(46, 160, 67, 0.2);
      border-color: rgba(46, 160, 67, 0.55);
      color: #56d364;
    }
    .q-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 600;
      border: 1px solid var(--q-border);
    }
    .diff-table {
      width: 100%;
      border-collapse: collapse;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
      line-height: 1.45;
    }
    .diff-table td {
      padding: 1px 8px;
      vertical-align: top;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .diff-ln {
      width: 42px;
      min-width: 42px;
      text-align: right;
      color: var(--q-muted);
      user-select: none;
      border-right: 1px solid var(--q-border);
      cursor: pointer;
    }
    .diff-ln:hover {
      color: #79c0ff;
      background: rgba(56, 139, 253, 0.15);
    }
    .diff-add { background: rgba(46, 160, 67, 0.16); }
    .diff-del { background: rgba(248, 81, 73, 0.16); }
    .diff-mod-left { background: rgba(248, 81, 73, 0.14); }
    .diff-mod-right { background: rgba(46, 160, 67, 0.14); }
    .mermaid-box svg { max-width: 100%; height: auto; }
  </style>
</head>
<body class="min-h-screen flex flex-col">
  <!-- Top Bar -->
  <header class="border-b border-[var(--q-border)] bg-[var(--q-card)] px-4 py-2.5 sticky top-0 z-30">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center gap-2.5 min-w-0">
        <span class="text-base font-bold tracking-tight flex items-center gap-1.5">
          <span>🌌</span>
          <span>Quasar</span>
          <span class="text-xs font-normal text-[var(--q-muted)]">Web</span>
        </span>
        <select id="sessionSelect" class="bg-black/30 border border-[var(--q-border)] rounded px-2.5 py-1 text-xs max-w-xs truncate" title="Switch active or past review session"></select>
        <button class="q-btn" onclick="startTutorial()" title="Load the interactive sandbox PR tutorial">🎓 Tutorial</button>
        <button class="q-btn" onclick="openNewReviewModal()" title="Review a GitHub PR URL or local git branch">➕ New Review</button>
      </div>
      <div class="flex items-center gap-2">
        <span id="progressPill" class="q-badge text-[var(--q-muted)]">0/0 stops read</span>
        <button class="q-btn q-btn-success" onclick="openPublishModal()" title="Publish staged comments to GitHub PR as you">☁️ Publish to GitHub</button>
        <button class="q-btn" onclick="openTokenModal()" id="ghAuthBtn" title="Configure GitHub Personal Access Token">🔑 GitHub Key</button>
        <button class="q-btn" onclick="refreshState()" title="Refresh session">↻</button>
      </div>
    </div>

    <!-- Navigation Tabs -->
    <div class="flex items-center gap-2 mt-2.5 pt-2 border-t border-[var(--q-border)] text-xs">
      <button id="tabBtn-guide" class="q-btn q-btn-primary" onclick="setTab('guide')">📖 Study Guide & Reading Tour</button>
      <button id="tabBtn-diff" class="q-btn" onclick="setTab('diff')">± Side-by-Side Diff & Inline Q&A</button>
      <button id="tabBtn-comments" class="q-btn" onclick="setTab('comments')">💬 Review Comments & Fix Cart <span id="commentsCountBadge" class="ml-1 px-1.5 py-0.2 rounded-full bg-white/10">0</span></button>
      <div class="ml-auto flex items-center gap-1.5" id="batchFixBar"></div>
    </div>
  </header>

  <!-- Toast Banner -->
  <div id="toastBanner" class="hidden fixed bottom-4 right-4 z-50 max-w-md px-4 py-2.5 rounded-lg border border-blue-500/50 bg-[#161b22] text-xs shadow-xl"></div>

  <!-- Main Content Container -->
  <main id="mainContent" class="flex-1 p-4 max-w-7xl w-full mx-auto space-y-4"></main>

  <!-- Modal Container -->
  <div id="modalBackdrop" class="hidden fixed inset-0 bg-black/70 z-40 flex items-center justify-center p-4">
    <div id="modalBox" class="q-card max-w-lg w-full p-5 space-y-3 shadow-2xl"></div>
  </div>

  <script>
    let STATE = window.__QUASAR_INITIAL_STATE__ || null;
    let ACTIVE_TAB = 'guide';
    let SELECTED_DIFF_FILE = null;
    let DIFF_MODE = 'split'; // 'split' | 'unified'
    let COMMENT_FILTER = 'all';
    let API_BASE = '';

    async function detectApiBase() {
      if (window.location.protocol.startsWith('http')) {
        API_BASE = window.location.origin;
        return;
      }
      API_BASE = 'http://127.0.0.1:49341';
    }

    function showToast(msg, isError = false) {
      const el = document.getElementById('toastBanner');
      el.textContent = msg;
      el.className = `fixed bottom-4 right-4 z-50 max-w-md px-4 py-2.5 rounded-lg border text-xs shadow-xl ${
        isError ? 'border-red-500/60 bg-red-950/90 text-red-200' : 'border-blue-500/60 bg-[#161b22] text-blue-200'
      }`;
      setTimeout(() => { el.className = 'hidden'; }, 4200);
    }

    async function apiCall(path, method = 'GET', body = null) {
      const opts = { method, headers: { 'Content-Type': 'application/json' } };
      if (body !== null) opts.body = JSON.stringify(body);
      const res = await fetch(`${API_BASE}${path}`, opts);
      return await res.json();
    }

    async function refreshState(sessionId = null) {
      try {
        const q = sessionId ? `?sessionId=${encodeURIComponent(sessionId)}` : '';
        const data = await apiCall(`/api/state${q}`);
        if (data && data.ok) {
          STATE = data;
          renderAll();
        }
      } catch (err) {
        if (STATE) {
          renderAll();
        } else {
          document.getElementById('mainContent').innerHTML = `<div class="q-card p-6 text-sm">Could not reach Quasar sidecar server (${err.message}).</div>`;
        }
      }
    }

    function setTab(tab) {
      ACTIVE_TAB = tab;
      ['guide', 'diff', 'comments'].forEach((t) => {
        const btn = document.getElementById(`tabBtn-${t}`);
        if (btn) btn.className = t === tab ? 'q-btn q-btn-primary' : 'q-btn';
      });
      renderAll();
    }

    function esc(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    function renderAll() {
      if (!STATE) return;
      const session = STATE.activeSession;
      const sessions = STATE.sessions || [];
      const sel = document.getElementById('sessionSelect');
      if (sel) {
        sel.innerHTML = sessions.length
          ? sessions.map((s) => `<option value="${esc(s.id)}" ${session && (session.sessionId === s.sessionId || session.sessionId === s.id) ? 'selected' : ''}>${esc(s.title)}</option>`).join('')
          : '<option value="">No active reviews yet</option>';
        sel.onchange = (e) => refreshState(e.target.value);
      }

      const ghBtn = document.getElementById('ghAuthBtn');
      if (ghBtn && STATE.githubAuth) {
        ghBtn.textContent = STATE.githubAuth.configured ? `🔑 @${STATE.githubAuth.login}` : '🔑 GitHub Key';
      }

      if (!session) {
        document.getElementById('mainContent').innerHTML = `
          <div class="q-card p-8 text-center space-y-4">
            <div class="text-2xl font-bold">🌌 Welcome to Quasar Web</div>
            <p class="text-sm text-[var(--q-muted)] max-w-lg mx-auto">
              Review any GitHub Pull Request or local git branch with interactive CliffsNotes study guides, clickable SVG flowcharts, side-by-side diffs, inline agent Q&A, and 1-click GitHub publishing.
            </p>
            <div class="flex justify-center gap-3">
              <button class="q-btn q-btn-primary" onclick="startTutorial()">🎓 Start Interactive Tutorial (Sandbox PR)</button>
              <button class="q-btn" onclick="openNewReviewModal()">➕ Review a GitHub PR or Local Branch</button>
            </div>
          </div>`;
        return;
      }

      const chapters = session.chapters || [];
      let totalStops = 0, readStops = 0;
      chapters.forEach((ch) => (ch.stops || []).forEach((st) => {
        totalStops++;
        if (st.read) readStops++;
      }));
      const pill = document.getElementById('progressPill');
      if (pill) {
        pill.textContent = session.commentsOnly ? '💬 Comments Only' : `${readStops}/${totalStops} stops read`;
      }

      const activeThreads = (session.threads || []).filter((t) => t.status !== 'dismissed');
      const badge = document.getElementById('commentsCountBadge');
      if (badge) badge.textContent = String(activeThreads.length);

      const queuedFixes = activeThreads.filter((t) => t.selectedForFix && t.status !== 'fixed');
      const batchBar = document.getElementById('batchFixBar');
      if (batchBar) {
        batchBar.innerHTML = queuedFixes.length > 0
          ? `<button class="q-btn q-btn-primary" onclick="fixSelectedComments()">🔧 Fix ${queuedFixes.length} Selected Comment(s) with Agent</button>`
          : '';
      }

      if (ACTIVE_TAB === 'guide') renderGuideTab(session);
      else if (ACTIVE_TAB === 'diff') renderDiffTab(session);
      else renderCommentsTab(session);
    }

    function renderPrSummaryBanner(session) {
      const pr = session.pr || {};
      const cn = session.cliffsNotes || {};
      const isTutorial = Boolean(session.isTutorial || pr.isTutorial);
      const tutorialHtml = isTutorial ? `
        <div class="q-card p-4 border-purple-500/40 bg-purple-950/20 space-y-2 mb-4">
          <div class="flex items-center justify-between">
            <span class="font-bold text-sm text-purple-300">🎓 Interactive Quasar Tutorial — 5 Quick Things to Try</span>
            <span class="q-badge text-purple-300 border-purple-500/40">Sandbox PR #42</span>
          </div>
          <ol class="list-decimal list-inside text-xs space-y-1 text-[var(--q-fg)]">
            <li><b>Step through the Reading Tour</b>: Check off tour stops below or click <b>Open in Diff Viewer</b> to inspect the side-by-side changes.</li>
            <li><b>Ask Agent to Change a Comment</b>: On any finding card, click <b>💬 Change Comment</b> (e.g. <i>"Make this 1 sentence without a code suggestion"</i>).</li>
            <li><b>Calibrate an Uncertain Finding</b>: Look for the <b>🤔 Uncertain</b> finding and click <b>Keep & Always Flag</b> or <b>Dismiss & Skip Pattern</b>.</li>
            <li><b>Queue Comments for Agent Fix</b>: Check <b>🔧 Queue for Fix</b> on any finding and click <b>Fix Selected Comments with Agent</b> in the top bar.</li>
            <li><b>Ask an Inline Question</b>: Switch to the <b>± Side-by-Side Diff</b> tab and click any line number to ask Quasar about that line.</li>
          </ol>
        </div>` : '';

      return `
        ${tutorialHtml}
        <div class="q-card p-4 space-y-2">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div class="text-base font-bold">${esc(pr.number ? `PR #${pr.number}: ${pr.title || ''}` : (pr.title || pr.headRefName || 'Review Session'))}</div>
              <div class="text-xs text-[var(--q-muted)]">
                ${esc(pr.repo || '')} • ${esc(pr.baseRefName || 'base')} ← ${esc(pr.headRefName || 'head')}
                • <span class="text-green-400">+${pr.additions || 0}</span> / <span class="text-red-400">-${pr.deletions || 0}</span>
              </div>
            </div>
            <div class="flex items-center gap-1.5">
              <span class="q-badge">${esc((session.effortLevel || 'medium').toUpperCase())} EFFORT</span>
              ${pr.url ? `<a href="${esc(pr.url)}" target="_blank" class="q-btn">🔗 GitHub PR</a>` : ''}
            </div>
          </div>
          ${cn.tldr ? `<div class="text-xs bg-black/25 border border-[var(--q-border)] rounded p-3"><b>TL;DR:</b> ${esc(cn.tldr)}</div>` : ''}
          ${cn.whatAndWhy ? `<div class="text-xs text-[var(--q-muted)]">${esc(cn.whatAndWhy)}</div>` : ''}
        </div>`;
    }

    function renderGuideTab(session) {
      const cn = session.cliffsNotes || {};
      const chapters = session.chapters || [];
      const activeThreads = (session.threads || []).filter((t) => t.status !== 'dismissed');

      const diagramHtml = cn.architectureDiagram ? `
        <div class="q-card p-4 space-y-2">
          <div class="text-xs font-bold uppercase tracking-wider text-[var(--q-muted)]">🗺️ Architecture Flowchart</div>
          <div id="mermaidContainer" class="mermaid-box overflow-x-auto p-2 bg-black/20 rounded border border-[var(--q-border)]"></div>
        </div>` : '';

      const chaptersHtml = chapters.length ? chapters.map((ch, idx) => `
        <div class="q-card p-4 space-y-3">
          <div class="flex items-center justify-between">
            <div>
              <div class="text-sm font-bold">Chapter ${idx + 1}: ${esc(ch.title || '')}</div>
              ${ch.summary ? `<div class="text-xs text-[var(--q-muted)]">${esc(ch.summary)}</div>` : ''}
            </div>
          </div>
          <div class="space-y-2">
            ${(ch.stops || []).map((st) => `
              <div class="p-3 rounded border border-[var(--q-border)] bg-black/20 flex flex-col gap-1.5">
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <label class="flex items-center gap-2 text-xs font-semibold cursor-pointer">
                    <input type="checkbox" ${st.read ? 'checked' : ''} onchange="toggleStopRead('${esc(st.id)}')" />
                    <span class="text-blue-400">Stop ${esc(st.stopNumber || '')}:</span>
                    <span>${esc(st.title || '')}</span>
                    <code class="text-[11px] text-[var(--q-muted)]">${esc(st.path)}:${st.startLine || 1}-${st.endLine || 1}</code>
                  </label>
                  <div class="flex items-center gap-1.5">
                    <button class="q-btn" onclick="openFileInDiff('${esc(st.path)}', ${st.startLine || 1})">± View in Diff</button>
                    <button class="q-btn" onclick="promptAskAtLine('${esc(st.path)}', ${st.startLine || 1})">💬 Ask Quasar</button>
                  </div>
                </div>
                <div class="text-xs">${esc(st.cliffsNotes || st.description || '')}</div>
                ${st.whyItChanged || st.callersAndInvariants ? `
                  <details class="text-xs text-[var(--q-muted)] mt-1">
                    <summary class="cursor-pointer hover:text-[var(--q-fg)]">Why it changed & callers/invariants</summary>
                    <div class="mt-1.5 space-y-1 pl-3 border-l border-[var(--q-border)]">
                      ${st.whyItChanged ? `<div><b>Why:</b> ${esc(st.whyItChanged)}</div>` : ''}
                      ${st.callersAndInvariants ? `<div><b>Callers & Invariants:</b> ${esc(st.callersAndInvariants)}</div>` : ''}
                    </div>
                  </details>` : ''}
              </div>
            `).join('')}
          </div>
        </div>
      `).join('') : `<div class="q-card p-4 text-xs text-[var(--q-muted)]">${session.commentsOnly ? 'Comments-Only mode enabled — study guide and reading tour were skipped.' : 'No reading tour chapters in this session.'}</div>`;

      const topFindingsHtml = activeThreads.length ? `
        <div class="q-card p-4 space-y-3">
          <div class="flex items-center justify-between">
            <div class="text-sm font-bold">💬 Review Comments (${activeThreads.length})</div>
            <button class="q-btn" onclick="setTab('comments')">Open Full Comment Cart →</button>
          </div>
          <div class="space-y-2.5">
            ${activeThreads.map((t) => renderThreadCardHtml(t)).join('')}
          </div>
        </div>` : '';

      document.getElementById('mainContent').innerHTML = `
        ${renderPrSummaryBanner(session)}
        ${diagramHtml}
        ${chaptersHtml}
        ${topFindingsHtml}
      `;

      if (cn.architectureDiagram && window.QuasarMermaidPreview) {
        const container = document.getElementById('mermaidContainer');
        if (container) {
          try {
            container.innerHTML = window.QuasarMermaidPreview.renderMermaidSvg(cn.architectureDiagram);
          } catch (e) {
            container.innerHTML = `<pre class="text-xs">${esc(cn.architectureDiagram)}</pre>`;
          }
        }
      }
    }

    function renderThreadCardHtml(t) {
      const sevBadge = t.status === 'uncertain'
        ? '<span class="q-badge text-amber-300 border-amber-500/40">🤔 Uncertain — Calibrate</span>'
        : t.severity === 'blocking'
        ? '<span class="q-badge text-red-300 border-red-500/40">🔴 Blocking</span>'
        : t.severity === 'design'
        ? '<span class="q-badge text-yellow-300 border-yellow-500/40">🟡 Design</span>'
        : t.kind === 'github_existing'
        ? `<span class="q-badge text-purple-300 border-purple-500/40">${esc(t.triageBadge || '👀 GitHub Comment')}</span>`
        : '<span class="q-badge text-green-300 border-green-500/40">🟢 Nit</span>';

      const statusBadge = t.status === 'fixed'
        ? '<span class="q-badge text-emerald-300 border-emerald-500/50">✅ Fixed</span>'
        : t.stagedForGithub
        ? '<span class="q-badge text-blue-300 border-blue-500/50">☁️ Staged for GitHub</span>'
        : '<span class="q-badge text-[var(--q-muted)]">Draft</span>';

      const replies = (t.messages || []).slice(1);
      return `
        <div class="p-3.5 rounded-lg border border-[var(--q-border)] bg-black/25 space-y-2">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex items-center gap-2 flex-wrap">
              ${sevBadge}
              ${statusBadge}
              <span class="text-xs font-bold">${esc(t.title || 'Comment')}</span>
              <button class="text-[11px] text-blue-400 hover:underline font-mono" onclick="openFileInDiff('${esc(t.path)}', ${t.startLine || 1})">
                ${esc(t.path)}:${t.startLine || 1}
              </button>
            </div>
            <div class="flex items-center gap-1.5 flex-wrap">
              <button class="q-btn ${t.selectedForFix ? 'q-btn-primary' : ''}" onclick="threadAction('${esc(t.id)}', 'toggleFix')">
                ${t.selectedForFix ? '☑️ Queued for Fix' : '🔧 Queue for Fix'}
              </button>
              <button class="q-btn" onclick="openReviseCommentModal('${esc(t.id)}')">💬 Change Comment...</button>
              <button class="q-btn" onclick="openEditDraftModal('${esc(t.id)}')">✏️ Edit</button>
              <button class="q-btn" onclick="threadAction('${esc(t.id)}', 'toggleStage')">
                ${t.stagedForGithub ? 'Unstage' : '☁️ Stage'}
              </button>
              <button class="q-btn" onclick="threadAction('${esc(t.id)}', 'rate', { rating: 'up' })" title="Helpful — flag more like this">👍</button>
              <button class="q-btn" onclick="threadAction('${esc(t.id)}', 'dismiss')" title="Dismiss comment">✕</button>
            </div>
          </div>
          <div class="text-xs whitespace-pre-wrap leading-relaxed">${esc(t.draftComment || '')}</div>
          ${t.suggestion ? `<pre class="text-[11px] bg-black/40 border border-[var(--q-border)] rounded p-2.5 overflow-x-auto"><code>${esc(t.suggestion)}</code></pre>` : ''}
          ${t.status === 'uncertain' ? `
            <div class="flex flex-wrap items-center gap-2 pt-1.5 border-t border-amber-500/30 text-xs">
              <span class="text-amber-300 font-medium">🤔 Calibrate Preference:</span>
              <button class="q-btn q-btn-primary" onclick="threadAction('${esc(t.id)}', 'calibrate', { decision: 'keep_always' })">✅ Stage & Always Flag Similar</button>
              <button class="q-btn" onclick="threadAction('${esc(t.id)}', 'calibrate', { decision: 'keep_once' })">Keep Once</button>
              <button class="q-btn" onclick="threadAction('${esc(t.id)}', 'calibrate', { decision: 'dismiss_skip' })">🚫 Dismiss & Skip Pattern in Future</button>
            </div>` : ''}
          ${replies.length ? `
            <div class="space-y-1.5 pt-2 border-t border-[var(--q-border)]">
              ${replies.map((m) => `
                <div class="text-xs pl-3 border-l-2 border-blue-500/40">
                  <span class="font-semibold text-blue-300">${esc(m.author || 'Quasar')}:</span>
                  <span class="whitespace-pre-wrap">${esc(m.body || '')}</span>
                </div>
              `).join('')}
            </div>` : ''}
        </div>`;
    }

    function renderDiffTab(session) {
      const files = STATE.filesDiff || [];
      if (!files.length) {
        document.getElementById('mainContent').innerHTML = `<div class="q-card p-6 text-xs text-[var(--q-muted)]">No file snapshots found for this session.</div>`;
        return;
      }
      if (!SELECTED_DIFF_FILE || !files.some((f) => f.path === SELECTED_DIFF_FILE)) {
        SELECTED_DIFF_FILE = files[0].path;
      }
      const activeFile = files.find((f) => f.path === SELECTED_DIFF_FILE) || files[0];
      const fileThreads = (session.threads || []).filter((t) => t.status !== 'dismissed' && t.path === activeFile.path);
      const threadsByLine = {};
      fileThreads.forEach((t) => {
        const ln = t.endLine || t.startLine || 1;
        if (!threadsByLine[ln]) threadsByLine[ln] = [];
        threadsByLine[ln].push(t);
      });

      const rowsHtml = (activeFile.rows || []).map((r) => {
        const lineForThread = r.headLine || r.baseLine || 1;
        const inlineThreads = (r.headLine && threadsByLine[r.headLine]) ? threadsByLine[r.headLine] : [];
        const threadRow = inlineThreads.length ? `
          <tr>
            <td colspan="${DIFF_MODE === 'split' ? 4 : 3}" class="p-3 bg-[#0d1117] border-y border-blue-500/40">
              <div class="space-y-2">${inlineThreads.map((t) => renderThreadCardHtml(t)).join('')}</div>
            </td>
          </tr>` : '';

        if (DIFF_MODE === 'unified') {
          const rowCls = r.type === 'add' ? 'diff-add' : r.type === 'del' ? 'diff-del' : r.type === 'mod' ? 'diff-mod-right' : '';
          const text = r.type === 'del' ? `- ${r.baseText}` : r.type === 'add' ? `+ ${r.headText}` : `  ${r.headText || r.baseText}`;
          return `
            <tr class="${rowCls}" id="diff-line-${lineForThread}">
              <td class="diff-ln" onclick="promptAskAtLine('${esc(activeFile.path)}', ${lineForThread})" title="Click to ask Quasar about line ${lineForThread}">${r.baseLine || ''}</td>
              <td class="diff-ln" onclick="promptAskAtLine('${esc(activeFile.path)}', ${lineForThread})" title="Click to ask Quasar about line ${lineForThread}">${r.headLine || ''} +</td>
              <td>${esc(text)}</td>
            </tr>${threadRow}`;
        }

        const leftCls = r.type === 'del' ? 'diff-del' : r.type === 'mod' ? 'diff-mod-left' : '';
        const rightCls = r.type === 'add' ? 'diff-add' : r.type === 'mod' ? 'diff-mod-right' : '';
        return `
          <tr id="diff-line-${lineForThread}">
            <td class="diff-ln ${leftCls}" onclick="promptAskAtLine('${esc(activeFile.path)}', ${lineForThread})">${r.baseLine || ''}</td>
            <td class="${leftCls} w-1/2 border-r border-[var(--q-border)]">${esc(r.baseText)}</td>
            <td class="diff-ln ${rightCls}" onclick="promptAskAtLine('${esc(activeFile.path)}', ${lineForThread})" title="Click to ask Quasar at line ${lineForThread}">${r.headLine ? `${r.headLine} +` : ''}</td>
            <td class="${rightCls} w-1/2">${esc(r.headText)}</td>
          </tr>${threadRow}`;
      }).join('');

      document.getElementById('mainContent').innerHTML = `
        <div class="flex flex-col md:flex-row gap-4">
          <aside class="md:w-64 shrink-0 q-card p-3 space-y-1.5 self-start">
            <div class="text-xs font-bold uppercase tracking-wider text-[var(--q-muted)] px-1">Changed Files (${files.length})</div>
            ${files.map((f) => `
              <button class="w-full text-left px-2.5 py-1.5 rounded text-xs flex items-center justify-between gap-2 ${f.path === activeFile.path ? 'bg-blue-500/20 text-blue-300 font-semibold' : 'hover:bg-white/5'}"
                      onclick="selectDiffFile('${esc(f.path)}')">
                <span class="truncate">${esc(f.path)}</span>
                <span class="text-[10px] shrink-0"><span class="text-green-400">+${f.additions}</span> <span class="text-red-400">-${f.deletions}</span></span>
              </button>
            `).join('')}
          </aside>
          <section class="flex-1 min-w-0 q-card overflow-hidden">
            <div class="px-4 py-2.5 border-b border-[var(--q-border)] flex flex-wrap items-center justify-between gap-2">
              <div class="font-mono text-xs font-bold">${esc(activeFile.path)}</div>
              <div class="flex items-center gap-1.5">
                <button class="q-btn ${DIFF_MODE === 'split' ? 'q-btn-primary' : ''}" onclick="DIFF_MODE='split'; renderAll();">Split</button>
                <button class="q-btn ${DIFF_MODE === 'unified' ? 'q-btn-primary' : ''}" onclick="DIFF_MODE='unified'; renderAll();">Unified</button>
                <button class="q-btn" onclick="promptAskAtLine('${esc(activeFile.path)}', 1)">💬 Ask Quasar on File...</button>
              </div>
            </div>
            <div class="overflow-x-auto">
              <table class="diff-table">
                <tbody>${rowsHtml}</tbody>
              </table>
            </div>
          </section>
        </div>`;
    }

    function renderCommentsTab(session) {
      const allThreads = (session.threads || []).filter((t) => t.status !== 'dismissed');
      const filtered = allThreads.filter((t) => {
        if (COMMENT_FILTER === 'staged') return Boolean(t.stagedForGithub);
        if (COMMENT_FILTER === 'fix') return Boolean(t.selectedForFix);
        if (COMMENT_FILTER === 'uncertain') return t.status === 'uncertain';
        return true;
      });

      document.getElementById('mainContent').innerHTML = `
        ${renderPrSummaryBanner(session)}
        <div class="q-card p-4 space-y-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex items-center gap-1.5">
              <button class="q-btn ${COMMENT_FILTER === 'all' ? 'q-btn-primary' : ''}" onclick="COMMENT_FILTER='all'; renderAll();">All (${allThreads.length})</button>
              <button class="q-btn ${COMMENT_FILTER === 'staged' ? 'q-btn-primary' : ''}" onclick="COMMENT_FILTER='staged'; renderAll();">☁️ Staged for GitHub (${allThreads.filter(t => t.stagedForGithub).length})</button>
              <button class="q-btn ${COMMENT_FILTER === 'fix' ? 'q-btn-primary' : ''}" onclick="COMMENT_FILTER='fix'; renderAll();">🔧 Queued for Fix (${allThreads.filter(t => t.selectedForFix).length})</button>
              <button class="q-btn ${COMMENT_FILTER === 'uncertain' ? 'q-btn-primary' : ''}" onclick="COMMENT_FILTER='uncertain'; renderAll();">🤔 Uncertain (${allThreads.filter(t => t.status === 'uncertain').length})</button>
            </div>
            <div class="flex items-center gap-2">
              <button class="q-btn q-btn-primary" onclick="fixSelectedComments()">🔧 Fix Queued Comments with Agent</button>
              <button class="q-btn q-btn-success" onclick="openPublishModal()">☁️ Publish Staged to GitHub</button>
            </div>
          </div>
          <div class="space-y-2.5">
            ${filtered.length ? filtered.map((t) => renderThreadCardHtml(t)).join('') : '<div class="text-xs text-[var(--q-muted)] py-4 text-center">No comments match this filter.</div>'}
          </div>
        </div>`;
    }

    function selectDiffFile(path) {
      SELECTED_DIFF_FILE = path;
      renderAll();
    }

    function openFileInDiff(path, line = 1) {
      SELECTED_DIFF_FILE = path;
      setTab('diff');
      setTimeout(() => {
        const row = document.getElementById(`diff-line-${line}`);
        if (row) row.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 60);
    }

    async function toggleStopRead(stopId) {
      const res = await apiCall('/api/toggle-stop-read', 'POST', {
        sessionId: STATE.activeSession?.sessionId,
        stopId,
      });
      if (res && res.ok) await refreshState(STATE.activeSession?.sessionId);
    }

    async function threadAction(threadId, action, extra = {}) {
      const res = await apiCall('/api/thread-action', 'POST', {
        sessionId: STATE.activeSession?.sessionId,
        threadId,
        action,
        ...extra,
      });
      if (res && res.ok) {
        if (res.message) showToast(res.message);
        await refreshState(STATE.activeSession?.sessionId);
      } else {
        showToast(res?.error || 'Action failed', true);
      }
    }

    async function startTutorial() {
      showToast('🎓 Creating Interactive Sandbox PR Tutorial...');
      const res = await apiCall('/api/tutorial', 'POST', {});
      if (res && res.ok) {
        showToast('🎓 Sandbox PR Tutorial loaded!');
        ACTIVE_TAB = 'guide';
        await refreshState(res.sessionId);
      } else {
        showToast(res?.error || 'Failed to start tutorial', true);
      }
    }

    function closeModal() {
      document.getElementById('modalBackdrop').className = 'hidden';
    }

    function showModal(html) {
      const box = document.getElementById('modalBox');
      box.innerHTML = html;
      document.getElementById('modalBackdrop').className = 'fixed inset-0 bg-black/70 z-40 flex items-center justify-center p-4';
    }

    function openReviseCommentModal(threadId) {
      const t = (STATE.activeSession?.threads || []).find((x) => x.id === threadId);
      if (!t) return;
      showModal(`
        <div class="text-sm font-bold">💬 Ask Agent to Change Quasar Comment</div>
        <div class="text-xs text-[var(--q-muted)]">${esc(t.title)} (<code>${esc(t.path)}:${t.startLine || 1}</code>)</div>
        <textarea id="reviseInstructionInput" rows="3" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2.5 text-xs"
                  placeholder="e.g. Make this shorter (1 sentence), remove the code suggestion, or downgrade to a Nit"></textarea>
        <div class="flex justify-end gap-2">
          <button class="q-btn" onclick="closeModal()">Cancel</button>
          <button class="q-btn q-btn-primary" onclick="submitReviseComment('${esc(threadId)}')">✨ Send to Agent</button>
        </div>
      `);
    }

    async function submitReviseComment(threadId) {
      const instruction = document.getElementById('reviseInstructionInput')?.value?.trim();
      if (!instruction) return;
      closeModal();
      showToast('💬 Sending comment revision request to Quasar agent...');
      const res = await apiCall('/api/revise-comment', 'POST', {
        sessionId: STATE.activeSession?.sessionId,
        threadId,
        instruction,
      });
      if (res && res.ok) {
        showToast('✨ Agent notified to revise this comment!');
        await refreshState(STATE.activeSession?.sessionId);
      } else {
        showToast(res?.error || 'Could not dispatch to agent', true);
      }
    }

    function openEditDraftModal(threadId) {
      const t = (STATE.activeSession?.threads || []).find((x) => x.id === threadId);
      if (!t) return;
      showModal(`
        <div class="text-sm font-bold">✏️ Edit Comment Draft</div>
        <textarea id="editDraftInput" rows="5" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2.5 text-xs">${esc(t.draftComment || '')}</textarea>
        <div class="flex justify-end gap-2">
          <button class="q-btn" onclick="closeModal()">Cancel</button>
          <button class="q-btn q-btn-primary" onclick="submitEditDraft('${esc(threadId)}')">Save Draft</button>
        </div>
      `);
    }

    async function submitEditDraft(threadId) {
      const draft = document.getElementById('editDraftInput')?.value || '';
      closeModal();
      await threadAction(threadId, 'editDraft', { draftComment: draft });
    }

    function promptAskAtLine(path, line) {
      showModal(`
        <div class="text-sm font-bold">💬 Ask Quasar at <code>${esc(path)}:${line}</code></div>
        <textarea id="askLineInput" rows="3" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2.5 text-xs"
                  placeholder="Ask a question about this line/hunk, or add a review comment..."></textarea>
        <div class="flex justify-end gap-2">
          <button class="q-btn" onclick="closeModal()">Cancel</button>
          <button class="q-btn q-btn-primary" onclick="submitAskAtLine('${esc(path)}', ${line})">✨ Ask Quasar</button>
        </div>
      `);
    }

    async function submitAskAtLine(path, line) {
      const question = document.getElementById('askLineInput')?.value?.trim();
      if (!question) return;
      closeModal();
      showToast('💬 Sending inline question to Quasar agent...');
      const res = await apiCall('/api/ask-line', 'POST', {
        sessionId: STATE.activeSession?.sessionId,
        path,
        line,
        question,
      });
      if (res && res.ok) {
        showToast('✨ Question posted & dispatched to agent!');
        await refreshState(STATE.activeSession?.sessionId);
      } else {
        showToast(res?.error || 'Failed to post question', true);
      }
    }

    async function fixSelectedComments() {
      showToast('🔧 Preparing fix manifest and dispatching to Quasar agent...');
      const res = await apiCall('/api/fix-selected', 'POST', {
        sessionId: STATE.activeSession?.sessionId,
      });
      if (res && res.ok) {
        showToast(`🔧 Dispatched ${res.count || ''} comment(s) to agent for fixing!`);
      } else {
        showToast(res?.error || 'Select at least one comment with Queue for Fix first.', true);
      }
    }

    function openPublishModal() {
      const stagedCount = (STATE.activeSession?.threads || []).filter((t) => t.status !== 'dismissed' && t.stagedForGithub).length;
      showModal(`
        <div class="text-sm font-bold">☁️ Publish Review to GitHub (${stagedCount} staged comment(s))</div>
        <div class="space-y-2 text-xs">
          <label class="block font-semibold">Review Event</label>
          <select id="pubEventSelect" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2">
            <option value="COMMENT">💬 COMMENT (Post inline comments immediately as you)</option>
            <option value="APPROVE">✅ APPROVE (Approve PR + post staged comments)</option>
            <option value="REQUEST_CHANGES">🔴 REQUEST_CHANGES (Request changes + post staged comments)</option>
            <option value="PENDING">📝 PENDING (Create unpublished Draft Pending Review on GitHub)</option>
          </select>
          <label class="block font-semibold mt-2">Optional Top-Level Summary</label>
          <textarea id="pubBodyInput" rows="3" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2"
                    placeholder="Optional top-level review summary..."></textarea>
        </div>
        <div class="flex justify-end gap-2">
          <button class="q-btn" onclick="closeModal()">Cancel</button>
          <button class="q-btn q-btn-success" onclick="submitPublishGithub()">☁️ Submit to GitHub</button>
        </div>
      `);
    }

    async function submitPublishGithub() {
      const event = document.getElementById('pubEventSelect')?.value || 'COMMENT';
      const body = document.getElementById('pubBodyInput')?.value || '';
      closeModal();
      showToast('☁️ Publishing review to GitHub...');
      const res = await apiCall('/api/publish-github', 'POST', {
        sessionId: STATE.activeSession?.sessionId,
        event,
        body,
      });
      if (res && res.ok) {
        showToast(`✅ Published review to GitHub (${event})!`);
      } else {
        showToast(res?.error || 'Failed to publish review', true);
      }
    }

    function openTokenModal() {
      showModal(`
        <div class="text-sm font-bold">🔑 Configure GitHub Personal Access Token</div>
        <p class="text-xs text-[var(--q-muted)]">
          Stored locally in <code>~/.gemini/quasar/github_auth.json</code> (0600) so Quasar can fetch private PRs and publish reviews under your GitHub account.
        </p>
        <input id="ghTokenInput" type="password" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2 text-xs"
               placeholder="ghp_... or github_pat_..." />
        <div class="flex justify-end gap-2">
          <button class="q-btn" onclick="closeModal()">Cancel</button>
          <button class="q-btn q-btn-primary" onclick="submitToken()">Save & Verify</button>
        </div>
      `);
    }

    async function submitToken() {
      const token = document.getElementById('ghTokenInput')?.value?.trim() || '';
      closeModal();
      const res = await apiCall('/api/github-token', 'POST', { token });
      if (res && res.ok) {
        showToast(`🔑 Authenticated as @${res.login}!`);
        await refreshState(STATE.activeSession?.sessionId);
      } else {
        showToast(res?.error || 'Token verification failed', true);
      }
    }

    function openNewReviewModal() {
      showModal(`
        <div class="text-sm font-bold">➕ Start a New Quasar Review</div>
        <div class="space-y-2.5 text-xs">
          <div>
            <label class="block font-semibold mb-1">GitHub PR URL / Number (or leave blank for Local Branch)</label>
            <input id="newReviewPrInput" type="text" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2"
                   placeholder="https://github.com/owner/repo/pull/123" />
          </div>
          <div>
            <label class="block font-semibold mb-1">Local Repository Path (for Local Branch or PR checkout)</label>
            <input id="newReviewRepoInput" type="text" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2"
                   value="${esc(STATE?.activeSession?.repoDir || '')}" placeholder="/path/to/repo" />
          </div>
          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="block font-semibold mb-1">Effort Level</label>
              <select id="newReviewEffort" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2">
                <option value="low">⚡ Low Effort (Fast)</option>
                <option value="medium" selected>⚖️ Medium Effort (Balanced)</option>
                <option value="high">🔬 High Effort (Deep 4-Lens)</option>
              </select>
            </div>
            <div>
              <label class="block font-semibold mb-1">Review Mode</label>
              <select id="newReviewMode" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2">
                <option value="full">📖 Full Study Guide + Comments</option>
                <option value="comments_only">💬 Comments Only</option>
              </select>
            </div>
          </div>
        </div>
        <div class="flex justify-end gap-2">
          <button class="q-btn" onclick="closeModal()">Cancel</button>
          <button class="q-btn q-btn-primary" onclick="submitNewReview()">🚀 Start Review with Agent</button>
        </div>
      `);
    }

    async function submitNewReview() {
      const prUrl = document.getElementById('newReviewPrInput')?.value?.trim() || '';
      const repoDir = document.getElementById('newReviewRepoInput')?.value?.trim() || '';
      const effort = document.getElementById('newReviewEffort')?.value || 'medium';
      const commentsOnly = document.getElementById('newReviewMode')?.value === 'comments_only';
      closeModal();
      showToast('🚀 Starting Quasar review...');
      const res = await apiCall('/api/start-review', 'POST', { prUrl, repoDir, effort, commentsOnly });
      if (res && res.ok) {
        showToast('🚀 Review context collected and dispatched to agent!');
        await refreshState();
      } else {
        showToast(res?.error || 'Failed to start review', true);
      }
    }

    (async () => {
      await detectApiBase();
      if (STATE) renderAll();
      await refreshState();
      setInterval(() => {
        if (!document.getElementById('modalBackdrop')?.classList.contains('fixed')) {
          refreshState(STATE?.activeSession?.sessionId);
        }
      }, 4000);
    })();
  </script>
</body>
</html>
"""


class QuasarWebHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, data: Dict[str, Any]) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Sidecar-Token")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_OPTIONS(self) -> None:
        self._send_json(200, {"ok": True})

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        pathname = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if pathname == "/mermaid-preview.js":
            m_path = find_mermaid_js_path()
            content = open(m_path, "r", encoding="utf-8").read() if m_path else "// mermaid-preview.js not found"
            body = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if pathname == "/api/state":
            sid = (qs.get("sessionId") or [None])[0]
            self._send_json(200, build_state_payload(sid))
            return

        if pathname in ("/", "/full", "/index.html", "/ui") or pathname.startswith("/c/") or pathname.startswith("/s/"):
            self._send_html(WEB_UI_HTML)
            return

        self._send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        pathname = parsed.path
        data = self._read_json()

        if pathname == "/api/tutorial":
            try:
                script = os.path.join(SCRIPTS_DIR, "collect_pr_context.py")
                proc = subprocess.run(
                    [sys.executable, script, "--tutorial"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                out = json.loads(proc.stdout or "{}")
                self._send_json(200, {"ok": True, "sessionId": out.get("sessionId") or "quasar-interactive-tutorial"})
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        if pathname == "/api/toggle-stop-read":
            session, session_path = load_target_session(data.get("sessionId"))
            if not session:
                self._send_json(404, {"ok": False, "error": "No active session"})
                return
            stop_id = data.get("stopId")
            for ch in session.get("chapters") or []:
                for st in ch.get("stops") or []:
                    if st.get("id") == stop_id:
                        st["read"] = not bool(st.get("read"))
            save_session_everywhere(session, session_path)
            self._send_json(200, {"ok": True})
            return

        if pathname == "/api/thread-action":
            session, session_path = load_target_session(data.get("sessionId"))
            if not session:
                self._send_json(404, {"ok": False, "error": "No active session"})
                return
            tid = data.get("threadId")
            act = data.get("action")
            target = next((t for t in (session.get("threads") or []) if t.get("id") == tid), None)
            if not target:
                self._send_json(404, {"ok": False, "error": "Thread not found"})
                return

            msg = ""
            if act == "toggleStage":
                target["stagedForGithub"] = not bool(target.get("stagedForGithub"))
                target["status"] = "staged" if target["stagedForGithub"] else "candidate"
                msg = "Staged for GitHub review" if target["stagedForGithub"] else "Unstaged from GitHub review"
            elif act == "toggleFix":
                target["selectedForFix"] = not bool(target.get("selectedForFix"))
                msg = "Queued for Agent Fix" if target["selectedForFix"] else "Removed from Agent Fix queue"
            elif act == "dismiss":
                target["status"] = "dismissed"
                target["stagedForGithub"] = False
                target["selectedForFix"] = False
                msg = "Comment dismissed"
            elif act == "editDraft":
                old_draft = target.get("draftComment") or ""
                new_draft = str(data.get("draftComment") or "").strip()
                if new_draft:
                    target["draftComment"] = new_draft
                    if target.get("messages"):
                        target["messages"][0]["body"] = new_draft
                    try:
                        from manage_preferences import record_feedback

                        record_feedback(
                            action="edited_comment",
                            finding_title=target.get("title") or "",
                            category=target.get("category") or "general",
                            before_text=old_draft,
                            after_text=new_draft,
                        )
                    except Exception:
                        pass
                    msg = "Saved edited draft & recorded phrasing preference"
            elif act == "calibrate":
                decision = data.get("decision")
                if decision == "keep_always":
                    target["status"] = "staged"
                    target["stagedForGithub"] = True
                    msg = "Staged & saved preference to always flag similar patterns"
                elif decision == "dismiss_skip":
                    target["status"] = "dismissed"
                    target["stagedForGithub"] = False
                    msg = "Dismissed & saved preference to skip this pattern in future reviews"
                else:
                    target["status"] = "staged"
                    target["stagedForGithub"] = True
                    msg = "Kept for this review"
                try:
                    from manage_preferences import record_feedback

                    record_feedback(
                        action="calibrated_keep" if decision != "dismiss_skip" else "calibrated_skip",
                        finding_title=target.get("title") or "",
                        category=target.get("category") or "general",
                        rule_text=target.get("calibrationQuestion") or target.get("title") or "",
                    )
                except Exception:
                    pass
            elif act == "rate":
                rating = data.get("rating")
                try:
                    from manage_preferences import record_feedback

                    record_feedback(
                        action="thumbs_up" if rating == "up" else "thumbs_down",
                        finding_title=target.get("title") or "",
                        category=target.get("category") or "general",
                    )
                except Exception:
                    pass
                msg = "Recorded positive feedback 👍" if rating == "up" else "Recorded feedback 👎"

            save_session_everywhere(session, session_path)
            self._send_json(200, {"ok": True, "message": msg})
            return

        if pathname == "/api/revise-comment":
            session, _ = load_target_session(data.get("sessionId"))
            if not session:
                self._send_json(404, {"ok": False, "error": "No active session"})
                return
            tid = data.get("threadId")
            instruction = (data.get("instruction") or "").strip()
            target = next((t for t in (session.get("threads") or []) if t.get("id") == tid), None)
            if not target or not instruction:
                self._send_json(400, {"ok": False, "error": "Missing thread or instruction"})
                return
            ws_dir = session.get("workspaceDir") or session.get("repoDir")
            prompt = (
                f"Using the `quasar` skill, revise the Quasar review comment on `{target.get('path')}:{target.get('startLine')}` "
                f"(thread ID: `{tid}`, current title: \"{target.get('title')}\") based on my feedback:\n\n"
                f"> {instruction}\n\n"
                f"Current draft comment:\n```\n{target.get('draftComment') or ''}\n```\n\n"
                f"Run `python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/answer_comment_question.py "
                f"--workspace-dir \"{ws_dir or ''}\" --thread-id \"{tid}\" --revise-finding "
                f"--user-instruction {json.dumps(instruction)} --draft-comment \"<REVISED_COMMENT>\"` "
                f"(and `--title` / `--suggestion` / `--clear-suggestion` / `--severity` as needed)."
            )
            res = dispatch_to_agent(prompt, title="Quasar: Revise Comment", conversation_id=session.get("conversationId"), workspace_dir=ws_dir)
            self._send_json(200, {"ok": True, "dispatch": res})
            return

        if pathname == "/api/ask-line":
            session, _ = load_target_session(data.get("sessionId"))
            if not session:
                self._send_json(404, {"ok": False, "error": "No active session"})
                return
            rel_path = data.get("path") or ""
            line = int(data.get("line") or 1)
            question = (data.get("question") or "").strip()
            ws_dir = session.get("workspaceDir") or session.get("repoDir")
            script = os.path.join(SCRIPTS_DIR, "answer_comment_question.py")
            cmd = [
                sys.executable,
                script,
                "--create-question",
                "--path",
                rel_path,
                "--line",
                str(line),
                "--question",
                question,
            ]
            if ws_dir:
                cmd.extend(["--workspace-dir", ws_dir])
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            created = {}
            try:
                created = json.loads(proc.stdout or "{}")
            except Exception:
                pass
            tid = created.get("threadId") or ""
            prompt = (
                f"Using the `quasar` skill, answer my inline question on `{rel_path}:{line}` (thread ID: `{tid}`):\n\n"
                f"> {question}\n\n"
                f"Post the answer back into the thread using `answer_comment_question.py --workspace-dir \"{ws_dir or ''}\" --thread-id \"{tid}\" --answer \"...\"`."
            )
            res = dispatch_to_agent(prompt, title=f"Quasar Q&A: {os.path.basename(rel_path)}:{line}", conversation_id=session.get("conversationId"), workspace_dir=ws_dir)
            self._send_json(200, {"ok": True, "threadId": tid, "dispatch": res})
            return

        if pathname == "/api/fix-selected":
            session, _ = load_target_session(data.get("sessionId"))
            if not session:
                self._send_json(404, {"ok": False, "error": "No active session"})
                return
            ws_dir = session.get("workspaceDir") or session.get("repoDir")
            script = os.path.join(SCRIPTS_DIR, "manage_fixes.py")
            cmd = [sys.executable, script, "--prepare-fix-request"]
            if ws_dir:
                cmd.extend(["--workspace-dir", ws_dir])
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            prep = {}
            try:
                prep = json.loads(proc.stdout or "{}")
            except Exception:
                pass
            if not prep.get("selectedCount"):
                self._send_json(400, {"ok": False, "error": "No comments are currently queued for fix. Click 'Queue for Fix' on one or more comments first."})
                return
            prompt = prep.get("agentPrompt") or f"Using the `quasar` skill, fix the {prep.get('selectedCount')} selected review comments in `{prep.get('fixRequestPath')}`."
            res = dispatch_to_agent(prompt, title="Quasar: Fix Selected Comments", conversation_id=session.get("conversationId"), workspace_dir=ws_dir)
            self._send_json(200, {"ok": True, "count": prep.get("selectedCount"), "dispatch": res})
            return

        if pathname == "/api/publish-github":
            session, _ = load_target_session(data.get("sessionId"))
            if not session:
                self._send_json(404, {"ok": False, "error": "No active session"})
                return
            event = data.get("event") or "COMMENT"
            body = data.get("body") or ""
            ws_dir = session.get("workspaceDir") or session.get("repoDir")
            script = os.path.join(SCRIPTS_DIR, "export_github_review.py")
            cmd = [sys.executable, script, "--event", event]
            if body:
                cmd.extend(["--body", body])
            if ws_dir:
                cmd.extend(["--workspace-dir", ws_dir])
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            out = {}
            try:
                out = json.loads(proc.stdout or "{}")
            except Exception:
                pass
            if proc.returncode == 0 and out.get("status") in ("submitted", "pending_created", "exported"):
                self._send_json(200, {"ok": True, "result": out})
            else:
                self._send_json(400, {"ok": False, "error": out.get("error") or proc.stderr or proc.stdout or "Failed to publish review"})
            return

        if pathname == "/api/github-token":
            token = (data.get("token") or "").strip()
            if not token:
                self._send_json(400, {"ok": False, "error": "Token is empty"})
                return
            try:
                req = urllib.request.Request(
                    "https://api.github.com/user",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "Quasar-Code-Review",
                    },
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    user_data = json.loads(resp.read().decode("utf-8"))
                login = user_data.get("login") or "user"
                os.makedirs(STATE_DIR, exist_ok=True)
                preview = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else "***"
                with open(GITHUB_AUTH_PATH, "w", encoding="utf-8") as f:
                    json.dump({"token": token, "login": login, "name": user_data.get("name"), "tokenPreview": preview}, f, indent=2)
                os.chmod(GITHUB_AUTH_PATH, 0o600)
                self._send_json(200, {"ok": True, "login": login})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": f"GitHub token check failed: {e}"})
            return

        if pathname == "/api/start-review":
            pr_url = (data.get("prUrl") or "").strip()
            repo_dir = (data.get("repoDir") or os.getcwd()).strip()
            effort = (data.get("effort") or "medium").strip()
            comments_only = bool(data.get("commentsOnly", False))
            mode_str = " (comments only)" if comments_only else ""
            if pr_url:
                prompt = f"Review {pr_url} on {effort} effort{mode_str}"
            else:
                prompt = f"Review local branch in {repo_dir} on {effort} effort{mode_str}"
            res = dispatch_to_agent(prompt, title="Quasar Code Review", workspace_dir=repo_dir)
            self._send_json(200, {"ok": True, "dispatch": res})
            return

        self._send_json(404, {"ok": False, "error": "Unknown endpoint"})


def export_standalone_html(output_path: str, session_id: Optional[str] = None) -> str:
    state = build_state_payload(session_id)
    mermaid_path = find_mermaid_js_path()
    mermaid_js = open(mermaid_path, "r", encoding="utf-8").read() if mermaid_path else ""
    inline_block = (
        f"<script>window.__QUASAR_INITIAL_STATE__ = {json.dumps(state)};</script>\n"
        f"<script>{mermaid_js}</script>"
    )
    html = WEB_UI_HTML.replace('<script src="/mermaid-preview.js"></script>', "").replace(
        "<!--__QUASAR_INLINE_SCRIPTS__-->", inline_block
    )
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Quasar Web UI Sidecar Server & HTML Exporter")
    parser.add_argument("--export-html", default=None, help="Export a self-contained interactive HTML file and exit")
    parser.add_argument("--session-id", default=None, help="Optional sessionId to export")
    parser.add_argument("--standalone-daemon", action="store_true", help="Run as standalone background server")
    args, _ = parser.parse_known_args()

    if args.export_html:
        out = export_standalone_html(args.export_html, args.session_id)
        print(json.dumps({"ok": True, "exportedHtml": out}))
        return

    server = ThreadingHTTPServer(("127.0.0.1", PORT), QuasarWebHandler)
    if PORT != STANDALONE_PORT:
        def run_standalone() -> None:
            try:
                s2 = ThreadingHTTPServer(("127.0.0.1", STANDALONE_PORT), QuasarWebHandler)
                s2.serve_forever()
            except Exception:
                pass
        threading.Thread(target=run_standalone, daemon=True).start()

    print(f"[quasar-web] Sidecar Web UI listening on http://127.0.0.1:{PORT} (and http://127.0.0.1:{STANDALONE_PORT})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
