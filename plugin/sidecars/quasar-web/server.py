#!/usr/bin/env python3
"""Quasar Web UI Sidecar & Standalone HTML Exporter.

Serves the interactive Quasar Code Review Web App in Antigravity Web / Desktop
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


def find_flutter_web_dir() -> Optional[str]:
    candidates = [
        os.path.abspath(os.path.join(THIS_DIR, "..", "..", "..", "app", "build", "web")),
        os.path.join(THIS_DIR, "flutter_web"),
        os.path.expanduser("~/.gemini/config/plugins/quasar/sidecars/quasar-web/flutter_web"),
    ]
    for d in candidates:
        if os.path.isfile(os.path.join(d, "index.html")):
            return d
    return None


def find_agy_binary() -> Optional[str]:
    import shutil

    candidates = [
        os.environ.get("AGY_BIN") or "",
        os.path.expanduser("~/.gemini/bin/agy"),
        shutil.which("agy") or "",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def list_all_brain_dirs() -> List[str]:
    gemini_root = os.path.expanduser("~/.gemini")
    dirs: List[str] = []
    if os.path.isdir(gemini_root):
        try:
            for name in os.listdir(gemini_root):
                cand = os.path.join(gemini_root, name, "brain")
                if os.path.isdir(cand):
                    dirs.append(cand)
        except Exception:
            pass
    return dirs


def find_brain_conversation_dir(cid: str) -> Optional[str]:
    if not cid:
        return None
    for bdir in list_all_brain_dirs():
        cand = os.path.join(bdir, cid)
        if os.path.isdir(cand):
            return cand
    return None


def get_agentapi_cmd() -> List[str]:
    for cand in glob.glob(os.path.expanduser("~/.gemini/*/bin/agentapi")):
        if os.path.isfile(cand):
            return [cand]
    env_exe = os.environ.get("ANTIGRAVITY_AGENTAPI_EXE")
    if env_exe and os.path.isfile(env_exe):
        if os.path.basename(env_exe) == "agentapi":
            return [env_exe]
        return [env_exe, "agentapi"]
    return ["agentapi"]


def discover_ls_env(workspace_dir: Optional[str] = None) -> Dict[str, str]:
    env = dict(os.environ)
    if env.get("ANTIGRAVITY_LS_ADDRESS") and env.get("ANTIGRAVITY_CSRF_TOKEN"):
        return env
    try:
        ps_out = subprocess.check_output(["/bin/ps", "-axww", "-o", "pid=,command="], text=True, timeout=3)
        norm_ws = re.sub(r"[^a-zA-Z0-9]", "_", workspace_dir or "")
        procs: List[Tuple[int, int, str, Optional[int]]] = []
        my_pid = os.getpid()
        for raw in ps_out.splitlines():
            line = raw.strip()
            if not line:
                continue
            m_pid = re.match(r"^(\d+)\s+(.*)$", line)
            if not m_pid:
                continue
            pid = int(m_pid.group(1))
            if pid == my_pid:
                continue
            cmd = m_pid.group(2)
            is_ls = "language_server" in cmd
            is_agy = "/bin/agy" in cmd or " agy " in cmd or cmd.startswith("agy ")
            if not is_ls and not is_agy:
                continue
            if "--csrf_token" not in cmd and "--csrf-token" not in cmd:
                continue
            m_csrf = re.search(r"--csrf[-_]token[=\s]+(\S+)", cmd)
            if not m_csrf:
                continue
            csrf = m_csrf.group(1)
            is_lsp = "--enable_lsp" in cmd or "--enable-lsp" in cmd
            m_ws = re.search(r"--(?:workspace[-_]id|add-dir)[=\s]+(\S+)", cmd)
            ws_id = m_ws.group(1) if m_ws else ""
            matches_ws = bool(
                workspace_dir
                and (
                    (norm_ws and norm_ws in ws_id)
                    or (ws_id and (workspace_dir in ws_id or ws_id in workspace_dir))
                )
            )
            m_hub = re.search(r"--hub[-_]port[=\s]+(\d+)", cmd)
            hub_port = int(m_hub.group(1)) if m_hub else None
            score = (0 if matches_ws else 20) + (10 if is_lsp else 0)
            procs.append((score, pid, csrf, hub_port))
        procs.sort(key=lambda x: x[0])
        lsof_bin = "/usr/sbin/lsof" if os.path.exists("/usr/sbin/lsof") else "lsof"
        for _, pid, csrf, hub_port in procs:
            if hub_port:
                env["ANTIGRAVITY_LS_ADDRESS"] = f"localhost:{hub_port}"
                env["ANTIGRAVITY_CSRF_TOKEN"] = csrf
                return env
            try:
                lsof_out = subprocess.check_output(
                    [lsof_bin, "-Pan", "-p", str(pid), "-a", "-iTCP", "-sTCP:LISTEN"],
                    text=True,
                    timeout=3,
                )
                listeners: List[Tuple[int, int]] = []
                for l_line in lsof_out.splitlines():
                    m_fd = re.search(
                        r"\s+(\d+)u\s+IPv[46]\s+.*?(?:127\.0\.0\.1|\[::1\]|localhost|\*):(\d+)\s+\(LISTEN\)",
                        l_line,
                    )
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


def resolve_conversation_context(
    conversation_id: Optional[str] = None,
    workspace_dir: Optional[str] = None,
) -> Tuple[str, str, str, Dict[str, str]]:
    """Returns (conv_id, project_id, workspace_dir, env) for communicating with the active conversation."""
    cmd_prefix = get_agentapi_cmd()
    env = discover_ls_env(workspace_dir)
    env.pop("ANTIGRAVITY_SOURCE_METADATA", None)

    conv_id = (
        conversation_id
        or os.environ.get("ANTIGRAVITY_SIDECAR_CONVERSATION_ID")
        or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
        or ""
    ).strip()

    if not conv_id:
        try:
            candidates: List[Tuple[float, str]] = []
            for brain_dir in list_all_brain_dirs():
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

    project_id = ""
    resolved_ws = (workspace_dir or "").strip()
    if conv_id:
        try:
            proc = subprocess.run(
                cmd_prefix + ["get-conversation-metadata", conv_id],
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0 and proc.stdout:
                data = json.loads(proc.stdout)
                meta = (
                    ((data.get("response") or {}).get("conversationMetadata") or {}).get("metadata")
                    or ((data.get("response") or {}).get("conversation_metadata") or {}).get("metadata")
                    or {}
                )
                root_id = (meta.get("rootConversationId") or meta.get("root_conversation_id") or "").strip()
                if root_id and root_id != conv_id:
                    conv_id = root_id
                project_id = (meta.get("projectId") or meta.get("project_id") or "").strip()
                if not resolved_ws:
                    ws_uris = meta.get("workspaceUris") or meta.get("workspace_uris") or []
                    for uri in ws_uris:
                        if isinstance(uri, str) and uri.startswith("file://"):
                            cand_ws = urllib.parse.unquote(uri[len("file://") :])
                            if os.path.isdir(cand_ws):
                                resolved_ws = cand_ws
                            break
        except Exception:
            pass

    if project_id:
        env["ANTIGRAVITY_PROJECT_ID"] = project_id

    if not resolved_ws:
        resolved_ws = os.path.expanduser("~")

    return conv_id, project_id, resolved_ws, env


ACTIVE_AGENT_JOB: Dict[str, Any] = {}


def dispatch_via_agy_cli(
    prompt: str,
    title: str = "Quasar Code Review",
    conversation_id: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    effort: str = "high",
    allow_edits: bool = False,
    baseline_mtime: float = 0.0,
) -> Dict[str, Any]:
    import time

    agy_bin = find_agy_binary()
    if not agy_bin:
        return {"ok": False, "error": "Antigravity CLI (`agy`) binary not found at ~/.gemini/bin/agy or PATH."}

    eff = effort.lower().strip() if effort else "high"
    if eff not in ("low", "medium", "high", "xhigh", "max"):
        eff = "high"

    real_state_dir = os.path.realpath(os.path.expanduser("~/.gemini/quasar"))
    real_plugin_dir = os.path.realpath(os.path.expanduser("~/.gemini/config/plugins/quasar"))
    real_agents_dir = os.path.realpath(os.path.expanduser("~/.gemini/config/agents"))
    real_ws = (
        os.path.realpath(os.path.expanduser(workspace_dir))
        if (workspace_dir and os.path.isdir(os.path.expanduser(workspace_dir)))
        else ""
    )

    allow_rules = [
        r"command(regex:^python3\s+.*quasar/skills/quasar/scripts/.*)",
        "command(git)",
        f"read_file({real_state_dir}/)",
        f"write_file({real_state_dir}/)",
        f"read_file({real_plugin_dir}/)",
        f"read_file({real_agents_dir}/)",
    ]
    if allow_edits:
        allow_rules.append(f"write_file({real_agents_dir}/)")
    if real_ws:
        allow_rules.append(f"read_file({real_ws}/)")
        if allow_edits:
            allow_rules.extend(
                [
                    f"write_file({real_ws}/)",
                    "command(flutter)",
                    "command(dart)",
                    "command(npm)",
                    "command(pnpm)",
                    "command(yarn)",
                    "command(npx)",
                    "command(pytest)",
                    "command(python3)",
                    "command(cargo)",
                    "command(go)",
                    "command(make)",
                ]
            )
    session_settings = json.dumps({"permissions": {"allow": allow_rules}})

    cmd: List[str] = [
        agy_bin,
        "--output-format=stream-json",
        f"--mode={'accept-edits' if allow_edits else 'auto'}",
    ]
    seen_dirs = set()
    for d in (real_ws, real_state_dir, real_plugin_dir):
        if d and os.path.isdir(d) and d not in seen_dirs:
            seen_dirs.add(d)
            cmd.extend(["--add-dir", d])
    cmd.append(f"--settings={session_settings}")
    cmd.append(f"--print={prompt}")

    ACTIVE_AGENT_JOB.clear()
    ACTIVE_AGENT_JOB.update({
        "active": True,
        "title": title,
        "promptPreview": prompt.splitlines()[0][:120] if prompt else title,
        "startedAt": time.time(),
        "baselineMtime": baseline_mtime,
        "conversationId": conversation_id or "",
        "mode": "agy-cli",
        "cliStage": "🚀 Running Antigravity CLI (`agy`)...",
        "cliDetail": f"Starting {title} ({eff} effort)...",
        "subagents": [],
        "completedAt": None,
    })

    def _run_stream() -> None:
        try:
            run_cwd = workspace_dir if (workspace_dir and os.path.isdir(workspace_dir)) else os.getcwd()
            proc = subprocess.Popen(
                cmd,
                cwd=run_cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            ACTIVE_AGENT_JOB["pid"] = proc.pid
            if proc.stdout:
                for raw_line in proc.stdout:
                    ln = raw_line.strip()
                    if not ln:
                        continue
                    m_uuid = re.search(
                        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
                        ln,
                    )
                    if m_uuid and not ACTIVE_AGENT_JOB.get("conversationId"):
                        ACTIVE_AGENT_JOB["conversationId"] = m_uuid.group(1)
                    if ln.startswith("{") and ln.endswith("}"):
                        try:
                            ev = json.loads(ln)
                            cid_cand = (
                                ev.get("conversation_id")
                                or ev.get("conversationId")
                                or ev.get("session_id")
                                or ""
                            )
                            if cid_cand:
                                ACTIVE_AGENT_JOB["conversationId"] = str(cid_cand)
                            if "collect_pr_context" in ln:
                                ACTIVE_AGENT_JOB["cliStage"] = "📥 Phase 1/3 — Fetching PR context & snapshots"
                                ACTIVE_AGENT_JOB["cliDetail"] = "Materializing base/head file snapshots..."
                            elif "invoke_subagent" in ln or "review-" in ln:
                                ACTIVE_AGENT_JOB["cliStage"] = "🔬 Phase 2/3 — Running specialist review lenses"
                                for m_ag in re.findall(r"review-[a-z-]+", ln):
                                    subs = ACTIVE_AGENT_JOB.setdefault("subagents", [])
                                    if m_ag not in subs:
                                        subs.append(m_ag)
                                ACTIVE_AGENT_JOB["cliDetail"] = "Analyzing architecture, bugs, & edge cases..."
                            elif "publish_review_session" in ln:
                                ACTIVE_AGENT_JOB["cliStage"] = "📖 Phase 3/3 — Publishing CliffsNotes & comments"
                                ACTIVE_AGENT_JOB["cliDetail"] = "Writing session & rendering diffs..."
                            elif "answer_comment_question" in ln:
                                ACTIVE_AGENT_JOB["cliStage"] = "💬 Posting inline answer to thread"
                                ACTIVE_AGENT_JOB["cliDetail"] = "Updating comment thread in place..."
                        except Exception:
                            pass
            proc.wait()
        except Exception as exc:
            ACTIVE_AGENT_JOB["cliError"] = str(exc)
        finally:
            ACTIVE_AGENT_JOB["active"] = False
            ACTIVE_AGENT_JOB["completedAt"] = time.time()

    threading.Thread(target=_run_stream, daemon=True).start()
    return {
        "ok": True,
        "mode": "agy-cli",
        "conversationId": conversation_id or "",
        "workspaceDir": workspace_dir or "",
    }


def dispatch_to_agent(
    prompt: str,
    title: str = "Quasar Code Review",
    conversation_id: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    effort: str = "high",
    allow_edits: bool = False,
) -> Dict[str, Any]:
    import time

    cmd_prefix = get_agentapi_cmd()
    conv_id, project_id, resolved_ws, env = resolve_conversation_context(conversation_id, workspace_dir)
    if workspace_dir and os.path.isdir(workspace_dir):
        resolved_ws = workspace_dir

    all_sess = list_all_sessions()
    baseline_mtime = max((s["mtime"] for s in all_sess), default=0.0)

    if os.environ.get("QUASAR_PREFER_AGY_CLI") == "1":
        return dispatch_via_agy_cli(
            prompt=prompt,
            title=title,
            conversation_id=conv_id,
            workspace_dir=resolved_ws,
            effort=effort,
            allow_edits=allow_edits,
            baseline_mtime=baseline_mtime,
        )

    def _record_active(cid: Optional[str], mode: str) -> None:
        ACTIVE_AGENT_JOB.clear()
        ACTIVE_AGENT_JOB.update({
            "active": True,
            "title": title,
            "promptPreview": prompt.splitlines()[0][:120] if prompt else title,
            "startedAt": time.time(),
            "baselineMtime": baseline_mtime,
            "conversationId": cid or "",
            "mode": mode,
            "completedAt": None,
        })

    def _is_conv_deliverable(cid_check: Optional[str]) -> bool:
        if not cid_check:
            return False
        c_dir = find_brain_conversation_dir(cid_check)
        if not c_dir or not os.path.isdir(c_dir):
            return True
        undeliv_dir = os.path.join(c_dir, ".system_generated", "messages", "undelivered")
        if os.path.isdir(undeliv_dir):
            try:
                if [n for n in os.listdir(undeliv_dir) if not n.startswith(".")]:
                    return False
            except Exception:
                pass
        t_path = os.path.join(c_dir, ".system_generated", "logs", "transcript.jsonl")
        if os.path.isfile(t_path):
            try:
                with open(t_path, "r", encoding="utf-8", errors="replace") as tf:
                    tail_lines = tf.readlines()[-20:]
                parsed_steps = []
                for ln in tail_lines:
                    ln = ln.strip()
                    if ln.startswith("{") and ln.endswith("}"):
                        try:
                            parsed_steps.append(json.loads(ln))
                        except Exception:
                            pass
                for idx in range(len(parsed_steps) - 1, -1, -1):
                    if parsed_steps[idx].get("type") == "PLANNER_RESPONSE":
                        tcs = parsed_steps[idx].get("tool_calls") or []
                        if tcs:
                            gen_after = sum(
                                1 for s in parsed_steps[idx + 1 :] if s.get("type") == "GENERIC"
                            )
                            if gen_after < len(tcs):
                                return False
                        break
            except Exception:
                pass
        return True

    def _clear_undelivered(cid_clean: Optional[str]) -> None:
        if not cid_clean:
            return
        c_dir = find_brain_conversation_dir(cid_clean)
        if not c_dir:
            return
        undeliv_dir = os.path.join(c_dir, ".system_generated", "messages", "undelivered")
        msg_dir = os.path.join(c_dir, ".system_generated", "messages")
        if os.path.isdir(undeliv_dir):
            try:
                for fn in os.listdir(undeliv_dir):
                    if fn.startswith("."):
                        continue
                    try:
                        os.unlink(os.path.join(undeliv_dir, fn))
                    except Exception:
                        pass
                    meta_json = os.path.join(msg_dir, f"{fn}.json")
                    if os.path.isfile(meta_json):
                        try:
                            os.unlink(meta_json)
                        except Exception:
                            pass
            except Exception:
                pass

    def _call_ls_rpc(method: str, body_obj: Dict[str, Any], timeout_s: float = 10.0) -> Dict[str, Any]:
        ls_addr = (env.get("ANTIGRAVITY_LS_ADDRESS") or "").strip()
        csrf_token = (env.get("ANTIGRAVITY_CSRF_TOKEN") or "").strip()
        if not ls_addr or not csrf_token:
            raise RuntimeError("ANTIGRAVITY_LS_ADDRESS or ANTIGRAVITY_CSRF_TOKEN is unavailable")
        url = f"http://{ls_addr}/exa.language_server_pb.LanguageServerService/{method}"
        data_bytes = json.dumps(body_obj).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "Connect-Protocol-Version": "1",
                "x-codeium-csrf-token": csrf_token,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()
            parsed = json.loads(raw) if raw else {}
            if isinstance(parsed, dict) and parsed.get("code") and parsed.get("message"):
                raise RuntimeError(str(parsed.get("message")))
            return parsed if isinstance(parsed, dict) else {}

    def _resolve_requested_model() -> str:
        requested_model = "MODEL_PLACEHOLDER_M260"
        try:
            model_data = _call_ls_rpc("GetCascadeModelConfigData", {}, 6.0)
            def_m = (
                ((model_data.get("defaultOverrideModelConfig") or {}).get("modelOrAlias") or {}).get("model")
            )
            if def_m:
                requested_model = str(def_m)
            else:
                for cfg in model_data.get("clientModelConfigs") or []:
                    if isinstance(cfg, dict) and cfg.get("isRecommended"):
                        m = ((cfg.get("modelOrAlias") or {}).get("model") or "").strip()
                        if m:
                            requested_model = m
                            break
        except Exception:
            pass
        return requested_model

    if conv_id and _is_conv_deliverable(conv_id):
        try:
            proc = subprocess.run(
                cmd_prefix + ["send-message", f"--title={title}", conv_id, prompt],
                env=env,
                capture_output=True,
                text=True,
                timeout=12,
            )
            if proc.returncode == 0 and '"error"' not in (proc.stdout or "").lower():
                time.sleep(1.2)
                if _is_conv_deliverable(conv_id):
                    _record_active(conv_id, "send-message")
                    return {
                        "ok": True,
                        "mode": "send-message",
                        "conversationId": conv_id,
                        "projectId": project_id,
                        "workspaceDir": resolved_ws,
                        "stdout": proc.stdout.strip(),
                    }
                _clear_undelivered(conv_id)
        except Exception:
            pass

        if _is_conv_deliverable(conv_id):
            try:
                requested_model = _resolve_requested_model()
                _call_ls_rpc(
                    "SendUserCascadeMessage",
                    {
                        "cascadeId": conv_id,
                        "items": [{"text": str(prompt)}],
                        "cascadeConfig": {
                            "plannerConfig": {
                                "conversational": {},
                                "requestedModel": {"model": requested_model},
                            }
                        },
                        "blocking": False,
                    },
                    12.0,
                )
                _record_active(conv_id, "send-message")
                return {
                    "ok": True,
                    "mode": "send-message",
                    "conversationId": conv_id,
                    "projectId": project_id,
                    "workspaceDir": resolved_ws,
                    "stdout": json.dumps({"conversationId": conv_id}),
                }
            except Exception:
                pass

    def _start_cascade_via_ls_http() -> Dict[str, Any]:
        requested_model = _resolve_requested_model()

        start_req: Dict[str, Any] = {
            "baseTrajectoryIdentifier": {"lastActiveDoc": True},
            "source": "CORTEX_TRAJECTORY_SOURCE_AGENT_API",
            "trajectoryType": "CORTEX_TRAJECTORY_TYPE_CASCADE",
        }
        if resolved_ws and os.path.isdir(resolved_ws):
            ws_uri = "file://" + urllib.parse.quote(os.path.realpath(resolved_ws))
            start_req["workspaceUris"] = [ws_uri]

        start_res = _call_ls_rpc("StartCascade", start_req, 10.0)
        new_cid = str(start_res.get("cascadeId") or "").strip()
        if not new_cid:
            raise RuntimeError("StartCascade did not return a cascadeId")

        if title:
            try:
                _call_ls_rpc(
                    "UpdateConversationAnnotations",
                    {
                        "cascadeId": new_cid,
                        "cascadeIds": [new_cid],
                        "annotations": {"title": str(title)},
                        "mergeAnnotations": True,
                    },
                    5.0,
                )
            except Exception:
                pass

        _call_ls_rpc(
            "SendUserCascadeMessage",
            {
                "cascadeId": new_cid,
                "items": [{"text": str(prompt)}],
                "cascadeConfig": {
                    "plannerConfig": {
                        "conversational": {},
                        "requestedModel": {"model": requested_model},
                    }
                },
                "blocking": False,
            },
            12.0,
        )
        _record_active(new_cid, "new-conversation")
        return {
            "ok": True,
            "mode": "new-conversation",
            "conversationId": new_cid,
            "projectId": project_id,
            "workspaceDir": resolved_ws,
            "stdout": json.dumps({"conversationId": new_cid}),
        }

    if not project_id:
        try:
            return _start_cascade_via_ls_http()
        except Exception as http_err:
            last_http_err = str(http_err)
    else:
        last_http_err = ""

    try:
        proc = subprocess.run(
            cmd_prefix + ["new-conversation", f"--title={title}", prompt],
            env=env,
            capture_output=True,
            text=True,
            timeout=12,
        )
        if proc.returncode == 0:
            out_str = (proc.stdout or "").strip()
            m_uuid = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", out_str)
            new_cid = m_uuid.group(1) if m_uuid else conv_id
            _record_active(new_cid, "new-conversation")
            return {
                "ok": True,
                "mode": "new-conversation",
                "conversationId": new_cid,
                "projectId": project_id,
                "workspaceDir": resolved_ws,
                "stdout": out_str,
            }
        if project_id:
            try:
                return _start_cascade_via_ls_http()
            except Exception:
                pass
        # Fallback to direct standalone Antigravity CLI (`agy`) when no IDE server is running!
        cli_res = dispatch_via_agy_cli(
            prompt=prompt,
            title=title,
            conversation_id=conv_id,
            workspace_dir=resolved_ws,
            effort=effort,
            allow_edits=allow_edits,
            baseline_mtime=baseline_mtime,
        )
        if cli_res.get("ok"):
            return cli_res
        return {
            "ok": False,
            "error": (proc.stderr or proc.stdout or last_http_err or cli_res.get("error") or "agentapi failed").strip(),
            "prompt": prompt,
        }
    except Exception as e:
        cli_res = dispatch_via_agy_cli(
            prompt=prompt,
            title=title,
            conversation_id=conv_id,
            workspace_dir=resolved_ws,
            effort=effort,
            allow_edits=allow_edits,
            baseline_mtime=baseline_mtime,
        )
        if cli_res.get("ok"):
            return cli_res
        return {"ok": False, "error": last_http_err or cli_res.get("error") or str(e), "prompt": prompt}


def get_live_agent_status(session: Optional[Dict[str, Any]], all_sessions: List[Dict[str, Any]]) -> Dict[str, Any]:
    import time

    now = time.time()
    current_max_mtime = max((s["mtime"] for s in all_sessions), default=0.0)

    # Check if a web-dispatched job just completed because a session file was updated
    if ACTIVE_AGENT_JOB.get("active"):
        started_at = float(ACTIVE_AGENT_JOB.get("startedAt") or now)
        baseline_mtime = float(ACTIVE_AGENT_JOB.get("baselineMtime") or 0.0)
        if ACTIVE_AGENT_JOB.get("mode") != "agy-cli" and current_max_mtime > baseline_mtime + 0.5 and (now - started_at) > 1.0:
            ACTIVE_AGENT_JOB["active"] = False
            ACTIVE_AGENT_JOB["completedAt"] = now
        elif (now - started_at) > 600:
            ACTIVE_AGENT_JOB["active"] = False

    if not ACTIVE_AGENT_JOB.get("active") and ACTIVE_AGENT_JOB.get("completedAt"):
        comp_at = float(ACTIVE_AGENT_JOB["completedAt"])
        if (now - comp_at) <= 8.0:
            return {
                "active": False,
                "completedJustNow": True,
                "title": ACTIVE_AGENT_JOB.get("title") or "Quasar Agent Task",
                "stage": "✅ Agent finished — Review updated!",
                "detail": "The latest review changes have been loaded.",
                "percent": 100,
                "elapsedSeconds": int(comp_at - float(ACTIVE_AGENT_JOB.get("startedAt") or comp_at)),
            }

    # Inspect recent transcript activity across all ~/.gemini/*/brain directories
    target_cid = (
        ACTIVE_AGENT_JOB.get("conversationId")
        or (session or {}).get("conversationId")
        or ""
    ).strip()

    transcript_path = None
    if target_cid:
        c_dir = find_brain_conversation_dir(target_cid)
        if c_dir:
            cand = os.path.join(c_dir, ".system_generated", "logs", "transcript.jsonl")
            if os.path.isfile(cand):
                transcript_path = cand

    if not transcript_path:
        recent: List[Tuple[float, str]] = []
        for bdir in list_all_brain_dirs():
            try:
                for entry in os.listdir(bdir):
                    tp = os.path.join(bdir, entry, ".system_generated", "logs", "transcript.jsonl")
                    if os.path.isfile(tp):
                        recent.append((os.path.getmtime(tp), tp))
            except Exception:
                pass
        recent.sort(reverse=True)
        if recent and (now - recent[0][0]) < 45:
            transcript_path = recent[0][1]

    stage = ACTIVE_AGENT_JOB.get("cliStage") or "Initializing Quasar Agent..."
    detail = ACTIVE_AGENT_JOB.get("cliDetail") or "Waiting for agent response..."
    percent = 20
    subagents: List[str] = list(ACTIVE_AGENT_JOB.get("subagents") or [])
    transcript_is_running = False
    transcript_started_at = None

    if transcript_path and os.path.isfile(transcript_path):
        try:
            t_mtime = os.path.getmtime(transcript_path)
            with open(transcript_path, "r", encoding="utf-8", errors="replace") as tf:
                lines = tf.readlines()[-35:]
            steps = []
            for ln in lines:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    steps.append(json.loads(ln))
                except Exception:
                    pass
            if steps:
                last_step = steps[-1]
                last_type = last_step.get("type") or ""
                last_tools = last_step.get("tool_calls") or []
                # If the transcript was modified recently and the last step has tool calls or is USER_INPUT / TOOL_RESPONSE
                if (now - t_mtime) < 45 and (last_tools or last_type != "PLANNER_RESPONSE"):
                    transcript_is_running = True

                # Scan backwards to last USER_INPUT to determine current pipeline phase
                recent_slice = []
                for st in reversed(steps):
                    recent_slice.append(st)
                    if st.get("type") == "USER_INPUT":
                        break
                recent_slice.reverse()

                saw_quasar = False
                for st in recent_slice:
                    raw_str = json.dumps(st)
                    if any(k in raw_str for k in ("collect_pr_context", "publish_review_session", "answer_comment_question", "manage_fixes", "review-architect", "review-bug-hunter")):
                        saw_quasar = True
                    for tc in st.get("tool_calls") or []:
                        args_obj = tc.get("arguments") or tc.get("args") or {}
                        if isinstance(args_obj, str):
                            try:
                                args_obj = json.loads(args_obj)
                            except Exception:
                                args_obj = {}
                        t_act = (args_obj.get("toolAction") or args_obj.get("toolSummary") or tc.get("name") or "").strip()
                        if t_act:
                            detail = f"Current step: {t_act}"
                        cmd_line = str(args_obj.get("CommandLine") or "")
                        if "collect_pr_context.py" in cmd_line:
                            stage = "Phase 1/3: Fetching PR Context, Diffs & Snapshots"
                            percent = max(percent, 28)
                        elif "invoke_subagent" in str(tc.get("name") or "") or "Subagents" in args_obj:
                            stage = "Phase 2/3: Running 4 Multi-Lens Subagents (Architect, Bug Hunter, Test Critic, Advocate)"
                            percent = max(percent, 62)
                            for sa in (args_obj.get("Subagents") or []):
                                role = sa.get("Role") or sa.get("TypeName")
                                if role and role not in subagents:
                                    subagents.append(role)
                        elif "publish_review_session.py" in cmd_line:
                            stage = "Phase 3/3: Synthesizing Study Guide, Flowchart & Inline Findings"
                            percent = max(percent, 90)
                        elif "answer_comment_question.py" in cmd_line:
                            stage = "Updating Inline Comment Thread & Preferences"
                            percent = max(percent, 75)
                        elif "manage_fixes.py" in cmd_line:
                            stage = "Applying Code Fixes & Syncing Diff Snapshots"
                            percent = max(percent, 80)
                if not saw_quasar and not ACTIVE_AGENT_JOB.get("active"):
                    transcript_is_running = False
        except Exception:
            pass

    is_active = bool(ACTIVE_AGENT_JOB.get("active") or transcript_is_running)
    if not is_active:
        return {"active": False}

    started_at = float(ACTIVE_AGENT_JOB.get("startedAt") or transcript_started_at or now)
    elapsed = max(1, int(now - started_at))
    # Smoothly advance progress slightly with time so the bar never looks frozen while subagents think
    if percent < 85:
        percent = min(85, max(percent, 15 + int(elapsed * 0.8)))

    return {
        "active": True,
        "title": ACTIVE_AGENT_JOB.get("title") or "Quasar Multi-Agent Code Review",
        "stage": stage,
        "detail": detail,
        "percent": percent,
        "subagents": subagents,
        "elapsedSeconds": elapsed,
        "startedAt": started_at,
    }


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
                        "type": "context",
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
            "rows": rows[:10000],
        })
    return result


def build_state_payload(
    session_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    session, session_path = load_target_session(session_id)
    all_sessions = list_all_sessions()
    _, _, default_ws, _ = resolve_conversation_context(conversation_id)
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
            "effortLevel": s.get("effortLevel") or "high",
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
            "defaultEffort": prefs.get("default_effort") or prefs.get("defaultEffort") or "high",
            "defaultCommentsOnly": bool(prefs.get("default_comments_only", prefs.get("defaultCommentsOnly", False))),
            "rulesCount": len((prefs.get("prefer_rules") or []) + (prefs.get("avoid_rules") or []) + (prefs.get("custom_rules") or [])),
            "rules": ((prefs.get("prefer_rules") or []) + (prefs.get("avoid_rules") or []) + (prefs.get("custom_rules") or []))[:30],
        }
    except Exception:
        pass

    agent_status = get_live_agent_status(session, all_sessions)
    if (
        session
        and not agent_status.get("active")
        and any(
            t.get("status") != "fixed" and t.get("fixStatus") in ("in_progress", "needs_input")
            for t in (session.get("threads") or [])
        )
    ):
        try:
            fix_script = os.path.join(SCRIPTS_DIR, "manage_fixes.py")
            ws_dir = session.get("workspaceDir") or session.get("repoDir") or default_ws
            rec_cmd = [sys.executable, fix_script, "--reconcile-fixes", "--agent-idle"]
            if ws_dir:
                rec_cmd.extend(["--workspace-dir", ws_dir])
            rec_proc = subprocess.run(rec_cmd, capture_output=True, text=True, timeout=6)
            if rec_proc.returncode == 0 and (rec_proc.stdout or "").strip():
                rec_out = json.loads(rec_proc.stdout)
                if rec_out.get("changed") and session_path and os.path.isfile(session_path):
                    with open(session_path, "r", encoding="utf-8") as sf:
                        session = json.load(sf)
        except Exception:
            pass

    return {
        "ok": True,
        "activeSession": session,
        "activeSessionPath": session_path,
        "defaultWorkspaceDir": default_ws,
        "filesDiff": compute_file_diffs(session),
        "sessions": session_summaries,
        "githubAuth": gh_auth or {"configured": False},
        "preferences": prefs_summary,
        "agentStatus": agent_status,
        "sidecarPort": PORT,
    }


WEB_UI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Quasar — Code Review & Study Guide</title>
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2358a6ff' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M5.5 5.5L9.8 9.8'/%3E%3Cpath d='M14.2 14.2L18.5 18.5'/%3E%3Cellipse cx='12' cy='12' rx='9.2' ry='3.8' transform='rotate(-32 12 12)'/%3E%3Ccircle cx='12' cy='12' r='2.3' fill='%2379c0ff'/%3E%3C/svg%3E" />
  <script src="/preload.js"></script>
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
      background: var(--q-bg) !important;
      color: var(--q-fg) !important;
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
    @keyframes qSpin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
    @keyframes qShimmer {
      0% { background-position: 200% 0; }
      100% { background-position: -200% 0; }
    }
    .q-spin {
      animation: qSpin 1.8s linear infinite;
    }
    .q-progress-fill {
      background: linear-gradient(90deg, #1f6feb, #58a6ff, #a371f7, #58a6ff, #1f6feb);
      background-size: 200% 100%;
      animation: qShimmer 2.4s linear infinite;
      transition: width 0.5s ease;
    }
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
        <span id="agentPill" class="hidden q-badge border-blue-500/60 bg-blue-500/15 text-blue-300"></span>
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

  <!-- Live Agent Activity Banner -->
  <div id="agentActivityBanner" class="hidden max-w-7xl w-full mx-auto px-4 pt-3"></div>

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
    const URL_PARAMS = new URLSearchParams(window.location.search);
    const SIDECAR_TOKEN = URL_PARAMS.get('token') || '';
    const CONVERSATION_ID = URL_PARAMS.get('conversationId') || (window.sidecar && window.sidecar.conversationId) || '';
    let OPTIMISTIC_AGENT_JOB = null;
    let TRACKED_FIX_SESSION_ID = null;
    const TRACKED_FIXING_IDS = new Set();
    const KNOWN_FIXED_IDS = new Set();

    function checkFixTransitions(session) {
      if (!session || !Array.isArray(session.threads)) {
        TRACKED_FIX_SESSION_ID = null;
        TRACKED_FIXING_IDS.clear();
        KNOWN_FIXED_IDS.clear();
        return;
      }
      if (TRACKED_FIX_SESSION_ID !== session.sessionId) {
        TRACKED_FIX_SESSION_ID = session.sessionId;
        TRACKED_FIXING_IDS.clear();
        KNOWN_FIXED_IDS.clear();
        for (const t of session.threads) {
          if (t.status === 'fixed') KNOWN_FIXED_IDS.add(String(t.id));
          else if (t.fixStatus === 'in_progress' || t.fixStatus === 'needs_input') {
            TRACKED_FIXING_IDS.add(String(t.id));
          }
        }
        return;
      }
      let newlyFixed = 0;
      for (const t of session.threads) {
        const tid = String(t.id);
        if (t.status !== 'fixed' && (t.fixStatus === 'in_progress' || t.fixStatus === 'needs_input')) {
          TRACKED_FIXING_IDS.add(tid);
        } else if (t.status === 'fixed' && !KNOWN_FIXED_IDS.has(tid)) {
          KNOWN_FIXED_IDS.add(tid);
          TRACKED_FIXING_IDS.delete(tid);
          newlyFixed++;
        }
      }
      if (newlyFixed > 0) {
        const rem = session.threads.filter((t) => t.status !== 'fixed' && t.fixStatus === 'in_progress').length;
        showToast(
          `✅ Agent finished fixing ${newlyFixed} comment${newlyFixed === 1 ? '' : 's'} in working tree${rem > 0 ? ` (${rem} still fixing...)` : '!'}`,
          false
        );
      }
    }

    function formatElapsed(sec) {
      const s = Math.max(0, Math.floor(sec || 0));
      const m = Math.floor(s / 60);
      const r = s % 60;
      return `${m}:${r < 10 ? '0' : ''}${r}`;
    }

    function startOptimisticAgentBanner(title, stage, detail) {
      OPTIMISTIC_AGENT_JOB = {
        active: true,
        title: title || 'Quasar Agent Working...',
        stage: stage || 'Phase 1/3: Dispatching request & collecting PR context...',
        detail: detail || 'Sent task to Antigravity agent...',
        percent: 18,
        startedAt: Date.now() / 1000,
      };
      renderAgentActivityBanner();
    }

    function renderAgentActivityBanner() {
      const banner = document.getElementById('agentActivityBanner');
      const pill = document.getElementById('agentPill');
      if (!banner) return;

      const srv = STATE?.agentStatus || {};
      let status = null;
      if (srv.active || srv.completedJustNow) {
        status = srv;
        if (srv.completedJustNow) OPTIMISTIC_AGENT_JOB = null;
      } else if (OPTIMISTIC_AGENT_JOB && (Date.now() / 1000 - OPTIMISTIC_AGENT_JOB.startedAt) < 25) {
        const el = Math.floor(Date.now() / 1000 - OPTIMISTIC_AGENT_JOB.startedAt);
        status = { ...OPTIMISTIC_AGENT_JOB, elapsedSeconds: el, percent: Math.min(40, 18 + el) };
      } else {
        OPTIMISTIC_AGENT_JOB = null;
      }

      if (!status || (!status.active && !status.completedJustNow)) {
        banner.className = 'hidden';
        banner.innerHTML = '';
        if (pill) pill.className = 'hidden';
        return;
      }

      if (status.completedJustNow) {
        if (pill) {
          pill.className = 'q-badge border-emerald-500/60 bg-emerald-500/15 text-emerald-300';
          pill.innerHTML = '✅ Review Updated';
        }
        banner.className = 'max-w-7xl w-full mx-auto px-4 pt-3';
        banner.innerHTML = `
          <div class="rounded-lg border border-emerald-500/50 bg-emerald-950/30 px-4 py-3 flex items-center justify-between text-xs">
            <div class="flex items-center gap-2.5">
              <span class="text-base">🎉</span>
              <div>
                <div class="font-bold text-emerald-300">${esc(status.stage || 'Agent finished — Review updated!')}</div>
                <div class="text-[var(--q-muted)]">${esc(status.title || '')} completed in ${formatElapsed(status.elapsedSeconds || 0)}</div>
              </div>
            </div>
            <button class="q-btn" onclick="OPTIMISTIC_AGENT_JOB=null; document.getElementById('agentActivityBanner').className='hidden';">Dismiss</button>
          </div>`;
        return;
      }

      const elapsed = status.startedAt
        ? Math.max(status.elapsedSeconds || 1, Math.floor(Date.now() / 1000 - status.startedAt))
        : (status.elapsedSeconds || 1);
      const pct = Math.min(95, Math.max(12, status.percent || 20));
      const subBadges = (status.subagents || []).map((sa) =>
        `<span class="px-2 py-0.5 rounded-full bg-purple-500/20 border border-purple-500/40 text-purple-200 text-[11px]">🤖 ${esc(sa)}</span>`
      ).join('');

      if (pill) {
        pill.className = 'q-badge border-blue-500/60 bg-blue-500/15 text-blue-300';
        pill.innerHTML = `<span class="inline-block w-2 h-2 rounded-full bg-blue-400 animate-ping"></span> Agent Working (${formatElapsed(elapsed)})`;
      }

      banner.className = 'max-w-7xl w-full mx-auto px-4 pt-3';
      banner.innerHTML = `
        <div class="rounded-xl border border-blue-500/50 bg-[#111827] px-4 py-3.5 shadow-lg space-y-2.5">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex items-center gap-2.5">
              <svg class="q-spin text-blue-400 shrink-0" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                <path d="M12 2a10 10 0 1 0 10 10" stroke-linecap="round"/>
              </svg>
              <div>
                <div class="text-xs font-bold text-blue-200 flex items-center gap-2">
                  <span>${esc(status.title || 'Quasar Multi-Agent Review in Progress')}</span>
                  <span class="px-2 py-0.5 rounded bg-blue-500/20 text-blue-300 font-mono text-[11px]">${formatElapsed(elapsed)} elapsed</span>
                </div>
                <div class="text-xs text-blue-300/90 font-medium mt-0.5">${esc(status.stage || 'Analyzing PR & running review subagents...')}</div>
              </div>
            </div>
            <div class="text-[11px] text-[var(--q-muted)] font-mono">${esc(status.detail || 'Auto-refreshing when complete...')}</div>
          </div>
          ${subBadges ? `<div class="flex flex-wrap gap-1.5 pt-0.5">${subBadges}</div>` : ''}
          <div class="w-full h-1.5 bg-black/50 rounded-full overflow-hidden">
            <div class="h-full q-progress-fill rounded-full" style="width: ${pct}%"></div>
          </div>
        </div>`;
    }

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
      const headers = { 'Content-Type': 'application/json' };
      if (SIDECAR_TOKEN) {
        headers['X-Sidecar-Token'] = SIDECAR_TOKEN;
      }
      let fullPath = path;
      if (SIDECAR_TOKEN) {
        fullPath += (fullPath.includes('?') ? '&' : '?') + `token=${encodeURIComponent(SIDECAR_TOKEN)}`;
      }
      if (CONVERSATION_ID && method === 'GET' && !fullPath.includes('conversationId=')) {
        fullPath += (fullPath.includes('?') ? '&' : '?') + `conversationId=${encodeURIComponent(CONVERSATION_ID)}`;
      }
      const opts = { method, headers, credentials: 'include' };
      if (body !== null) {
        const payload = (typeof body === 'object' && body !== null && CONVERSATION_ID && !body.conversationId)
          ? { ...body, conversationId: CONVERSATION_ID }
          : body;
        opts.body = JSON.stringify(payload);
      }
      const res = await fetch(`${API_BASE}${fullPath}`, opts);
      return await res.json();
    }

    async function refreshState(sessionId = null) {
      try {
        const q = sessionId ? `?sessionId=${encodeURIComponent(sessionId)}` : '';
        const data = await apiCall(`/api/state${q}`);
        if (data && data.ok) {
          checkFixTransitions(data.activeSession);
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
      renderAgentActivityBanner();
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

      const fixingNow = activeThreads.filter((t) => t.status !== 'fixed' && t.fixStatus === 'in_progress');
      const queuedFixes = activeThreads.filter(
        (t) => t.selectedForFix && t.status !== 'fixed' && t.fixStatus !== 'in_progress'
      );
      const fixedDone = activeThreads.filter((t) => t.status === 'fixed');
      const batchBar = document.getElementById('batchFixBar');
      if (batchBar) {
        const barParts = [];
        if (fixingNow.length > 0) {
          barParts.push(
            `<span class="q-badge text-blue-300 border-blue-500/50 animate-pulse">⏳ Agent Fixing ${fixingNow.length} Comment${fixingNow.length === 1 ? '' : 's'}...</span>`
          );
        }
        if (fixedDone.length > 0) {
          barParts.push(
            `<span class="q-badge text-emerald-300 border-emerald-500/50">✅ ${fixedDone.length} Fixed</span>`
          );
        }
        if (queuedFixes.length > 0) {
          barParts.push(
            `<button class="q-btn q-btn-primary" onclick="fixSelectedComments()">🔧 Fix ${queuedFixes.length} Selected Comment(s) with Agent</button>`
          );
        }
        batchBar.innerHTML = barParts.join('');
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

      const sg = session.securityGate;
      const sgHtml = (sg && typeof sg === 'object' && (sg.status === 'blocked' || sg.status === 'warning') && Array.isArray(sg.signals) && sg.signals.length > 0) ? (() => {
        const isBlocked = sg.status === 'blocked';
        const isHalted = isBlocked && !sg.overridden;
        const boxCls = isBlocked
          ? 'border-red-500/70 bg-red-500/12 text-red-100'
          : 'border-amber-500/60 bg-amber-500/10 text-amber-100';
        const titleTxt = isHalted
          ? '🚨 Security / Prompt-Injection Quarantine — LLM Review Halted'
          : isBlocked
          ? '🚨 Security / Prompt-Injection Alert (Quarantine Overridden by User)'
          : '⚠️ Touches Build / CI / Agent Config — Inspect Before Running Locally';
        const rows = sg.signals.map((sig) => `
          <div class="p-2 rounded bg-black/30 border border-white/10 flex flex-wrap items-start justify-between gap-2 text-xs">
            <div class="space-y-0.5 min-w-0 flex-1">
              <div><b>${sig.severity === 'blocked' ? '🚨' : '⚠️'} ${esc(sig.location || sig.path || 'PR')}:</b> ${esc(sig.detail || '')}</div>
              ${sig.excerpt ? `<div class="font-mono text-[11px] opacity-90 break-all"><code>${esc(sig.excerpt)}</code></div>` : ''}
            </div>
            ${sig.path ? `<button class="q-btn" onclick="openFileInDiff('${esc(sig.path)}', ${sig.line || 1})">± Inspect Diff</button>` : ''}
          </div>
        `).join('');
        return `
          <div class="q-card p-4 mb-3 space-y-2 border ${boxCls}">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <span class="font-bold text-sm">${titleTxt}</span>
              ${isHalted ? `<button class="q-btn border-red-400/60 text-red-200" onclick="overrideQuarantine()">🛡️ Override Quarantine & Run Review</button>` : ''}
            </div>
            ${sg.summary ? `<div class="text-xs opacity-90">${esc(sg.summary)}</div>` : ''}
            <div class="space-y-1.5">${rows}</div>
          </div>`;
      })() : '';

      return `
        ${tutorialHtml}
        ${sgHtml}
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
              <span class="q-badge">${esc((session.effortLevel || 'high').toUpperCase())} EFFORT</span>
              ${pr.url ? `<a href="${esc(pr.url)}" target="_blank" class="q-btn">🔗 GitHub PR</a>` : ''}
            </div>
          </div>
          ${cn.tldr ? `<div class="text-xs bg-black/25 border border-[var(--q-border)] rounded p-3"><b>TL;DR:</b> ${esc(cn.tldr)}</div>` : ''}
          ${(() => {
            const iva = cn.intentVsActuality;
            if (!iva || typeof iva !== 'object') return '';
            const disc = Array.isArray(iva.discrepancies) ? iva.discrepancies.filter(Boolean) : [];
            if (!iva.actualBehavior && !iva.statedIntent && !disc.length) return '';
            const isDiv = String(iva.status || '').toLowerCase() === 'diverges' || disc.length > 0;
            if (isDiv) {
              return `<div class="text-xs rounded p-3 border border-amber-500/45 bg-amber-500/10 space-y-1">
                <div class="font-bold text-amber-300">⚠️ Blind-Diff Check: Diverges from Stated Intent (<code>review-blind-diff</code>)</div>
                ${iva.statedIntent ? `<div><b>Stated Intent:</b> ${esc(iva.statedIntent)}</div>` : ''}
                ${iva.actualBehavior ? `<div><b>Actual Diff:</b> ${esc(iva.actualBehavior)}</div>` : ''}
                ${disc.map((d) => `<div class="pl-3">• ${esc(d)}</div>`).join('')}
              </div>`;
            }
            return `<details class="text-xs rounded px-3 py-2 border border-emerald-500/30 bg-emerald-500/5">
              <summary class="cursor-pointer font-semibold text-emerald-300">✅ Blind-Diff Check: Code matches stated PR intent</summary>
              <div class="mt-1.5 space-y-1 text-[var(--q-muted)]">
                ${iva.statedIntent ? `<div><b>Stated Intent:</b> ${esc(iva.statedIntent)}</div>` : ''}
                ${iva.actualBehavior ? `<div><b>Actual Diff:</b> ${esc(iva.actualBehavior)}</div>` : ''}
              </div>
            </details>`;
          })()}
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
      const sev = String(t.severity || '').toLowerCase();
      const isExistingGh = t.kind === 'github_existing';
      const isStaged = Boolean(t.stagedForGithub || t.status === 'staged');
      const ghColorCls = t.triageStatus === 'addressed_or_refuted'
        ? 'text-emerald-300 border-emerald-500/40'
        : t.triageStatus === 'partially_addressed'
        ? 'text-amber-300 border-amber-500/40'
        : t.triageStatus === 'recommended_fix'
        ? 'text-orange-300 border-orange-500/40'
        : 'text-purple-300 border-purple-500/40';
      const sevBadge = t.status === 'uncertain'
        ? '<span class="q-badge text-amber-300 border-amber-500/40">🤔 Uncertain — Calibrate</span>'
        : isExistingGh
        ? `<span class="q-badge ${ghColorCls}">${esc(t.triageBadge || '👀 GitHub Comment')}</span>`
        : sev === 'blocking'
        ? '<span class="q-badge text-red-300 border-red-500/40">🔴 Blocking</span>'
        : sev === 'design'
        ? '<span class="q-badge text-yellow-300 border-yellow-500/40">🟡 Design</span>'
        : '<span class="q-badge text-green-300 border-green-500/40">🟢 Nit</span>';

      const statusBadge = t.status === 'fixed'
        ? '<span class="q-badge text-emerald-300 border-emerald-500/50">✅ Fixed in Working Tree</span>'
        : t.fixStatus === 'in_progress'
        ? '<span class="q-badge text-blue-300 border-blue-500/50 animate-pulse">⏳ Fixing with Agent...</span>'
        : t.fixStatus === 'needs_input'
        ? '<span class="q-badge text-amber-300 border-amber-500/50">💬 Waiting in Chat</span>'
        : t.fixStatus === 'needs_attention'
        ? '<span class="q-badge text-orange-300 border-orange-500/50">⚠️ Check Fix</span>'
        : isExistingGh
        ? `<span class="q-badge text-[var(--q-muted)]">💬 GitHub${t.author ? ' (@' + esc(String(t.author).replace(/^@/, '')) + ')' : ''}</span>`
        : isStaged
        ? '<span class="q-badge text-blue-300 border-blue-500/50">☁️ Staged for GitHub</span>'
        : '<span class="q-badge text-[var(--q-muted)]">Draft</span>';

      const srcAgents = (!isExistingGh && Array.isArray(t.sourceAgents) && t.sourceAgents.length)
        ? t.sourceAgents
        : (!isExistingGh && t.sourceAgent ? [t.sourceAgent] : []);
      const subagentBadge = srcAgents.length
        ? `<span class="q-badge text-purple-200 border-purple-400/40" title="Suggested by ${esc(srcAgents.join(', '))}">🤖 ${esc(srcAgents.join(', '))}</span>`
        : '';

      const showFixBtn = t.status !== 'fixed' && (!isExistingGh || t.selectedForFix || t.triageStatus !== 'addressed_or_refuted');
      const whatDone = t.whatIsDone || (t.triageStatus === 'addressed_or_refuted' ? (t.triageSummary || 'Already addressed in code or thread.') : 'Checked against current HEAD code.');
      const whatNotDone = t.whatIsNotDone || (t.triageStatus === 'addressed_or_refuted' ? 'None — comment has been addressed.' : (t.triageSummary || 'See comment details.'));
      const whatShould = t.whatShouldBeDone || (t.triageStatus === 'addressed_or_refuted' ? 'No code changes needed.' : 'Inspect and update or reply.');
      const replies = (t.messages || []).slice(1).filter((m) => !(isExistingGh && m && m.isTriageNote));
      return `
        <div class="p-3.5 rounded-lg border border-[var(--q-border)] bg-black/25 space-y-2">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex items-center gap-2 flex-wrap">
              ${sevBadge}
              ${statusBadge}
              ${subagentBadge}
              <span class="text-xs font-bold">${esc(t.title || 'Comment')}</span>
              <button class="text-[11px] text-blue-400 hover:underline font-mono" onclick="openFileInDiff('${esc(t.path)}', ${t.startLine || 1})">
                ${esc(t.targetFile || t.path)}:${t.startLine || 1}
              </button>
            </div>
            <div class="flex items-center gap-1.5 flex-wrap">
              ${showFixBtn ? `
              <button class="q-btn ${t.selectedForFix ? 'q-btn-primary' : ''}" onclick="threadAction('${esc(t.id)}', 'toggleFix')">
                ${t.fixStatus === 'in_progress' ? '⏳ Fixing...' : (t.selectedForFix ? '☑️ Queued for Fix' : '🔧 Queue for Fix')}
              </button>` : ''}
              ${t.status !== 'fixed' && (t.fixStatus === 'in_progress' || t.fixStatus === 'needs_input' || t.fixStatus === 'needs_attention' || t.selectedForFix) ? `
              <button class="q-btn q-btn-success" onclick="threadAction('${esc(t.id)}', 'markFixed')">✓ Mark Fixed</button>` : ''}
              <button class="q-btn" onclick="openReviseCommentModal('${esc(t.id)}')">💬 Ask / Follow Up...</button>
              ${!isExistingGh ? `
              <button class="q-btn" onclick="openTeachSubagentModal('${esc(t.id)}', '${esc(srcAgents[0] || '')}')" title="Teach or tune this review subagent">🎓 Teach Subagent</button>
              <button class="q-btn" onclick="openEditDraftModal('${esc(t.id)}')">✏️ Edit</button>
              <button class="q-btn" onclick="threadAction('${esc(t.id)}', 'toggleStage')">
                ${isStaged ? 'Unstage' : '☁️ Stage'}
              </button>` : ''}
              <button class="q-btn" onclick="threadAction('${esc(t.id)}', 'rate', { rating: 'up' })" title="Helpful — flag more like this">👍</button>
              <button class="q-btn" onclick="threadAction('${esc(t.id)}', 'dismiss')" title="Dismiss comment">✕</button>
            </div>
          </div>
          ${(!isExistingGh && t.whyItMatters) ? `<div class="text-xs text-[var(--q-muted)]"><b>Why it matters:</b> ${esc(t.whyItMatters)}</div>` : ''}
          <div class="text-xs whitespace-pre-wrap leading-relaxed">${esc(t.draftComment || '')}</div>
          ${t.suggestion ? `<pre class="text-[11px] bg-black/40 border border-[var(--q-border)] rounded p-2.5 overflow-x-auto"><code>${esc(t.suggestion)}</code></pre>` : ''}
          ${(isExistingGh && t.status !== 'fixed') ? `
            <div class="p-2.5 rounded border ${t.triageStatus === 'addressed_or_refuted' ? 'border-emerald-500/35 bg-emerald-500/10' : t.triageStatus === 'partially_addressed' ? 'border-amber-500/35 bg-amber-500/10' : 'border-blue-500/35 bg-blue-500/10'} text-xs space-y-1">
              <div class="font-semibold">🤖 Quasar Follow-Up — ${esc(t.triageLabel || t.triageBadge || 'Code Assessment')}</div>
              <div><b>✅ What's Done:</b> ${esc(whatDone)}</div>
              <div><b>⚠️ What Isn't Done:</b> ${esc(whatNotDone)}</div>
              <div><b>👉 What Should Be Done:</b> ${esc(whatShould)}</div>
            </div>` : ''}
          ${((!STATE.activeSession?.pr || STATE.activeSession.pr.isOwnedByUser !== false) && t.status !== 'fixed' && ((Array.isArray(t.solutionOptions) && t.solutionOptions.length >= 2) || t.selectedSolution)) ? `
            <div class="space-y-1.5 pt-2 border-t border-cyan-500/30 text-xs">
              <div class="flex items-center justify-between gap-2">
                <span class="text-cyan-300 font-semibold">🧭 Choose How to Solve This:</span>
                ${t.selectedSolution ? `<button type="button" class="text-[11px] text-[var(--q-muted)] hover:underline" onclick="threadAction('${esc(t.id)}', 'selectSolution', { optionId: 'clear' })">Clear selection</button>` : ''}
              </div>
              <div class="space-y-1">
                ${(t.solutionOptions || []).map((opt, idx) => {
                  const oid = opt.id || ('opt-' + (idx + 1));
                  const isSel = t.selectedSolutionId === oid;
                  return `<button type="button" class="w-full text-left p-2 rounded border transition ${isSel ? 'border-cyan-400 bg-cyan-500/15 text-cyan-100' : 'border-[var(--q-border)] bg-black/30 hover:border-cyan-500/50'}" onclick="threadAction('${esc(t.id)}', 'selectSolution', { optionId: '${esc(oid)}' })">
                    <div class="font-semibold">${isSel ? '✅ ' : '○ '}${esc(opt.label || ('Option ' + (idx + 1)))}${opt.recommended ? ' <span class="text-[10px] text-amber-300 font-normal">(Recommended)</span>' : ''}</div>
                    ${opt.description ? `<div class="text-[11px] text-[var(--q-muted)] mt-0.5">${esc(opt.description)}</div>` : ''}
                  </button>`;
                }).join('')}
              </div>
              ${(t.selectedSolution && t.selectedSolutionId === 'custom') ? `<div class="p-2 rounded border border-cyan-400 bg-cyan-500/15 text-cyan-100"><span class="font-semibold">✅ Custom approach:</span> ${esc(t.selectedSolution)}</div>` : ''}
              <div class="flex items-center gap-2 pt-0.5">
                <button type="button" class="q-btn" onclick="openCustomSolutionModal('${esc(t.id)}')">✏️ Custom Approach...</button>
              </div>
            </div>` : ''}
          ${t.status === 'uncertain' ? `
            <div class="space-y-1.5 pt-1.5 border-t border-amber-500/30 text-xs">
              ${(t.uncertaintyQuestion || t.calibrationQuestion) ? `<div class="text-amber-200">${esc(t.uncertaintyQuestion || t.calibrationQuestion)}</div>` : ''}
              <div class="flex flex-wrap items-center gap-2">
                <span class="text-amber-300 font-medium">🤔 Calibrate Preference:</span>
                <button class="q-btn q-btn-primary" onclick="threadAction('${esc(t.id)}', 'calibrate', { decision: 'keep_always' })">✅ Stage & Always Flag Similar</button>
                <button class="q-btn" onclick="threadAction('${esc(t.id)}', 'calibrate', { decision: 'keep_once' })">Keep Once</button>
                <button class="q-btn" onclick="threadAction('${esc(t.id)}', 'calibrate', { decision: 'dismiss_skip' })">🚫 Dismiss & Skip Pattern in Future</button>
              </div>
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

      const allStops = [];
      (session.chapters || []).forEach((ch) => {
        (ch.stops || []).forEach((s) => allStops.push(s));
      });
      if (typeof window.ACTIVE_TOUR_STOP_IDX !== 'number' || window.ACTIVE_TOUR_STOP_IDX >= allStops.length) {
        window.ACTIVE_TOUR_STOP_IDX = 0;
      }
      const curStop = allStops[window.ACTIVE_TOUR_STOP_IDX] || null;
      const tourStripHtml = curStop ? `
        <div class="px-4 py-2 border-b border-[var(--q-border)] bg-sky-950/35 flex flex-wrap items-center justify-between gap-2 text-xs">
          <div class="flex items-center gap-2 min-w-0">
            <span class="font-bold text-sky-300 shrink-0">📘 Stop ${window.ACTIVE_TOUR_STOP_IDX + 1}/${allStops.length} (${esc(curStop.stopNumber || '')})</span>
            <span class="truncate text-slate-200">${esc(curStop.cliffsNotes || curStop.title || '')}</span>
          </div>
          <div class="flex items-center gap-1.5 shrink-0">
            <button class="q-btn" onclick="stepDiffTourStop(-1)">◀ Prev</button>
            <button class="q-btn q-btn-primary" onclick="stepDiffTourStop(1)">Next Stop ▶</button>
            <button class="q-btn" onclick="setTab('guide')">📖 Study Guide</button>
          </div>
        </div>` : '';

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
            ${tourStripHtml}
            <div class="px-4 py-2.5 border-b border-[var(--q-border)] flex flex-wrap items-center justify-between gap-2">
              <div class="font-mono text-xs font-bold">${esc(activeFile.path)}</div>
              <div class="flex items-center gap-1.5">
                <button class="q-btn ${DIFF_MODE === 'split' ? 'q-btn-primary' : ''}" onclick="DIFF_MODE='split'; renderAll();">Split</button>
                <button class="q-btn ${DIFF_MODE === 'unified' ? 'q-btn-primary' : ''}" onclick="DIFF_MODE='unified'; renderAll();">Unified</button>
                <button class="q-btn" onclick="promptAskAtLine('${esc(activeFile.path)}', 1)">➕ Comment / Ask on File...</button>
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

    function stepDiffTourStop(delta) {
      const session = STATE?.activeSession;
      if (!session) return;
      const allStops = [];
      (session.chapters || []).forEach((ch) => {
        (ch.stops || []).forEach((s) => allStops.push(s));
      });
      if (!allStops.length) return;
      const cur = typeof window.ACTIVE_TOUR_STOP_IDX === 'number' ? window.ACTIVE_TOUR_STOP_IDX : 0;
      const next = (cur + delta + allStops.length) % allStops.length;
      window.ACTIVE_TOUR_STOP_IDX = next;
      const stop = allStops[next];
      if (stop && !stop.read && stop.id) {
        toggleStopRead(stop.id);
      }
      openFileInDiff(stop.path, stop.startLine || 1);
    }

    function renderCommentsTab(session) {
      const allThreads = (session.threads || []).filter((t) => t.status !== 'dismissed');
      const pr = session.pr || {};
      const isOwned = pr.isOwnedByUser !== false;
      const isRemotePr = Boolean((pr.number && pr.owner && pr.mode !== 'local') || session.isTutorial || pr.mode === 'tutorial');
      const queuedCount = allThreads.filter((t) => t.selectedForFix && t.status !== 'fixed').length;
      const stagedCount = allThreads.filter((t) => t.stagedForGithub || t.status === 'staged').length;
      const filtered = allThreads.filter((t) => {
        if (COMMENT_FILTER === 'staged') return Boolean(t.stagedForGithub || t.status === 'staged');
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
              <button class="q-btn ${COMMENT_FILTER === 'staged' ? 'q-btn-primary' : ''}" onclick="COMMENT_FILTER='staged'; renderAll();">☁️ Staged for GitHub (${stagedCount})</button>
              ${(isOwned || queuedCount > 0) ? `<button class="q-btn ${COMMENT_FILTER === 'fix' ? 'q-btn-primary' : ''}" onclick="COMMENT_FILTER='fix'; renderAll();">🔧 Queued for Fix (${queuedCount})</button>` : ''}
              <button class="q-btn ${COMMENT_FILTER === 'uncertain' ? 'q-btn-primary' : ''}" onclick="COMMENT_FILTER='uncertain'; renderAll();">🤔 Uncertain (${allThreads.filter(t => t.status === 'uncertain').length})</button>
            </div>
            <div class="flex items-center gap-2">
              ${(isOwned || queuedCount > 0) ? `<button class="q-btn q-btn-primary" onclick="fixSelectedComments()">🔧 Send ${queuedCount > 0 ? queuedCount + ' ' : ''}to Agent to Fix</button>` : ''}
              ${isRemotePr ? `<button class="q-btn q-btn-success" onclick="openPublishModal()">☁️ Publish to GitHub (${stagedCount})</button>` : ''}
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
      startOptimisticAgentBanner(
        'Quasar Revise Comment',
        `Updating comment ${threadId}`,
        `Dispatching instruction to Quasar agent: "${instruction.slice(0, 60)}"`
      );
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
        OPTIMISTIC_AGENT_JOB = null;
        renderAgentActivityBanner();
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
        <div class="text-sm font-bold">💬 Inline Comment or Question at <code>${esc(path)}:${line}</code></div>
        <textarea id="askLineInput" rows="3" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2.5 text-xs"
                  placeholder="Write your own review comment, tell Quasar what to comment on, or ask a question..."></textarea>
        <div class="flex items-center justify-between gap-2 pt-1 flex-wrap">
          <div class="flex items-center gap-1.5 text-xs text-[var(--q-muted)]">
            <span>Severity (for comments):</span>
            <select id="askLineSeverity" class="bg-black/40 border border-[var(--q-border)] rounded px-2 py-1 text-xs">
              <option value="DESIGN" selected>🟡 Design</option>
              <option value="NIT">🟢 Nit</option>
              <option value="BLOCKING">🔴 Blocking</option>
            </select>
          </div>
          <div class="flex justify-end gap-2 flex-wrap">
            <button class="q-btn" onclick="closeModal()">Cancel</button>
            <button class="q-btn" onclick="submitLineAction('${esc(path)}', ${line}, 'qa')">💬 Ask Question</button>
            <button class="q-btn" onclick="submitLineAction('${esc(path)}', ${line}, 'ai_comment')">✨ Ask Quasar to Comment</button>
            <button class="q-btn q-btn-primary" onclick="submitLineAction('${esc(path)}', ${line}, 'manual')">➕ Add Review Comment</button>
          </div>
        </div>
      `);
    }

    async function submitLineAction(path, line, mode = 'qa') {
      const text = (document.getElementById('askLineInput')?.value || '').trim();
      const severity = document.getElementById('askLineSeverity')?.value || 'DESIGN';
      if (!text && mode !== 'ai_comment') return;
      closeModal();
      if (mode === 'manual') {
        const res = await apiCall('/api/add-comment', 'POST', {
          sessionId: STATE.activeSession?.sessionId,
          path,
          line,
          mode: 'manual',
          severity,
          question: text,
        });
        if (res && res.ok) {
          showToast('✅ Added & staged inline review comment!');
          await refreshState(STATE.activeSession?.sessionId);
        } else {
          showToast(res?.error || 'Failed to add comment', true);
        }
        return;
      }
      startOptimisticAgentBanner(
        mode === 'ai_comment' ? `Drafting Comment at ${path}:${line}` : `Quasar Question at ${path}:${line}`,
        mode === 'ai_comment' ? 'Inspecting code & staging inline review comment' : 'Analyzing code & drafting inline response',
        text ? `"${text.slice(0, 60)}"` : `Inspecting ${path}:${line}`
      );
      showToast(mode === 'ai_comment' ? '✨ Asking Quasar to draft inline comment...' : '💬 Sending inline question to Quasar agent...');
      const res = await apiCall('/api/ask-line', 'POST', {
        sessionId: STATE.activeSession?.sessionId,
        path,
        line,
        mode,
        severity,
        question: text,
      });
      if (res && res.ok) {
        showToast(mode === 'ai_comment' ? '✨ Quasar is drafting your inline comment!' : '✨ Question posted & dispatched to agent!');
        await refreshState(STATE.activeSession?.sessionId);
      } else {
        OPTIMISTIC_AGENT_JOB = null;
        renderAgentActivityBanner();
        showToast(res?.error || 'Failed to dispatch request', true);
      }
    }

    function openCustomSolutionModal(threadId) {
      const t = (STATE.activeSession?.threads || []).find((th) => th.id === threadId);
      if (!t) return;
      const existing = t.selectedSolutionId === 'custom' ? (t.selectedSolution || '') : '';
      showModal(`
        <div class="text-sm font-bold">🧭 Custom Solution Approach</div>
        <div class="text-xs text-[var(--q-muted)]">${esc(t.title || t.path || '')}</div>
        <textarea id="customSolInput" rows="3" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2 text-xs"
                  placeholder="Describe how you want this comment solved...">${esc(existing)}</textarea>
        <div class="flex justify-end gap-2 pt-2">
          <button class="q-btn" onclick="closeModal()">Cancel</button>
          <button class="q-btn q-btn-primary" onclick="submitCustomSolution('${esc(threadId)}')">Save Approach</button>
        </div>
      `);
    }

    async function submitCustomSolution(threadId) {
      const text = (document.getElementById('customSolInput')?.value || '').trim();
      if (!text) return;
      closeModal();
      await threadAction(threadId, 'selectSolution', { optionId: 'custom', customText: text, queueForFix: true });
    }

    let PENDING_SOLUTION_THREADS = [];

    function openResolveSolutionsModal(unresolvedThreads) {
      PENDING_SOLUTION_THREADS = unresolvedThreads;
      const cardsHtml = unresolvedThreads.map((t, tIdx) => {
        const opts = Array.isArray(t.solutionOptions) ? t.solutionOptions : [];
        const recIdx = Math.max(0, opts.findIndex((o) => o.recommended));
        return `
          <div class="p-3 rounded border border-[var(--q-border)] bg-black/30 space-y-2">
            <div class="font-semibold text-xs text-cyan-200">${esc(t.title || 'Comment')} <span class="text-[var(--q-muted)] font-mono font-normal">(${esc(t.path || '')}:${t.startLine || 1})</span></div>
            <div class="space-y-1">
              ${opts.map((opt, idx) => {
                const oid = opt.id || ('opt-' + (idx + 1));
                const checked = idx === recIdx ? 'checked' : '';
                return `<label class="flex items-start gap-2 p-1.5 rounded hover:bg-white/5 cursor-pointer text-xs">
                  <input type="radio" name="sol_choice_${tIdx}" value="${esc(oid)}" ${checked} class="mt-0.5" onchange="document.getElementById('sol_custom_${tIdx}').classList.add('hidden')" />
                  <div>
                    <div class="font-medium">${esc(opt.label || ('Option ' + (idx + 1)))}${opt.recommended ? ' <span class="text-[10px] text-amber-300">(Recommended)</span>' : ''}</div>
                    ${opt.description ? `<div class="text-[11px] text-[var(--q-muted)]">${esc(opt.description)}</div>` : ''}
                  </div>
                </label>`;
              }).join('')}
              <label class="flex items-start gap-2 p-1.5 rounded hover:bg-white/5 cursor-pointer text-xs">
                <input type="radio" name="sol_choice_${tIdx}" value="custom" class="mt-0.5" onchange="document.getElementById('sol_custom_${tIdx}').classList.remove('hidden')" />
                <div class="w-full space-y-1">
                  <div class="font-medium">✏️ Custom approach...</div>
                  <input id="sol_custom_${tIdx}" type="text" class="hidden w-full bg-black/40 border border-[var(--q-border)] rounded px-2 py-1 text-xs" placeholder="Describe how you want this solved..." />
                </div>
              </label>
            </div>
          </div>`;
      }).join('');

      showModal(`
        <div class="text-sm font-bold">🧭 Choose How to Solve Comments (${unresolvedThreads.length})</div>
        <div class="text-xs text-[var(--q-muted)]">These queued comments have multiple valid solutions. Select your preferred approach before dispatching to Quasar:</div>
        <div class="space-y-2.5 max-h-[55vh] overflow-y-auto pr-1">${cardsHtml}</div>
        <div class="flex justify-end gap-2 pt-2">
          <button class="q-btn" onclick="closeModal()">Cancel</button>
          <button class="q-btn q-btn-primary" onclick="confirmSolutionsAndFix()">🔧 Confirm &amp; Fix Selected</button>
        </div>
      `);
    }

    async function confirmSolutionsAndFix() {
      const choices = {};
      for (let i = 0; i < PENDING_SOLUTION_THREADS.length; i++) {
        const t = PENDING_SOLUTION_THREADS[i];
        const checked = document.querySelector(`input[name="sol_choice_${i}"]:checked`);
        const val = checked ? checked.value : '';
        if (val === 'custom') {
          const customText = (document.getElementById(`sol_custom_${i}`)?.value || '').trim();
          if (!customText) {
            showToast(`Please enter a custom approach for "${t.title || t.id}"`, true);
            return;
          }
          choices[t.id] = { id: 'custom', text: customText };
        } else if (val) {
          choices[t.id] = val;
        }
      }
      closeModal();
      await fixSelectedComments(choices);
    }

    async function fixSelectedComments(solutionChoices = null) {
      const isOwned = !STATE.activeSession?.pr || STATE.activeSession.pr.isOwnedByUser !== false;
      if (isOwned && !solutionChoices) {
        const queuedThreads = (STATE.activeSession?.threads || []).filter(
          (t) => t.status !== 'dismissed' && t.status !== 'fixed' && Boolean(t.selectedForFix)
        );
        const unresolved = queuedThreads.filter(
          (t) => Array.isArray(t.solutionOptions) && t.solutionOptions.length >= 2 && !t.selectedSolution
        );
        if (unresolved.length > 0) {
          openResolveSolutionsModal(unresolved);
          return;
        }
      }
      startOptimisticAgentBanner(
        'Quasar Fix Selected Comments',
        'Applying queued review fixes to codebase',
        'Preparing fix manifest and dispatching to Quasar agent...'
      );
      showToast('🔧 Preparing fix manifest and dispatching to Quasar agent...');
      const payload = {
        sessionId: STATE.activeSession?.sessionId,
      };
      if (solutionChoices && Object.keys(solutionChoices).length > 0) {
        payload.solutionChoices = solutionChoices;
      }
      const res = await apiCall('/api/fix-selected', 'POST', payload);
      if (res && res.ok) {
        showToast(`🔧 Dispatched ${res.count || ''} comment(s) to agent for fixing!`);
        await refreshState(STATE.activeSession?.sessionId);
      } else {
        OPTIMISTIC_AGENT_JOB = null;
        renderAgentActivityBanner();
        showToast(res?.error || 'Select at least one comment with Queue for Fix first.', true);
      }
    }

    function openPublishModal() {
      const stagedCount = (STATE.activeSession?.threads || []).filter((t) => t.status !== 'dismissed' && (t.stagedForGithub || t.status === 'staged')).length;
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
        if (res.clientSidePublish && res.url && res.token) {
          try {
            let ghRes = await fetch(res.url, {
              method: 'POST',
              headers: {
                'Accept': 'application/vnd.github+json',
                'Authorization': `Bearer ${res.token}`,
                'Content-Type': 'application/json',
              },
              body: JSON.stringify(res.payload),
            });
            if (ghRes.status === 422 && res.payload?.comments?.length) {
              const fallbackPayload = {
                ...res.payload,
                comments: res.payload.comments.map((c) => {
                  const copy = { ...c };
                  delete copy.start_line;
                  delete copy.start_side;
                  return copy;
                }),
              };
              ghRes = await fetch(res.url, {
                method: 'POST',
                headers: {
                  'Accept': 'application/vnd.github+json',
                  'Authorization': `Bearer ${res.token}`,
                  'Content-Type': 'application/json',
                },
                body: JSON.stringify(fallbackPayload),
              });
            }
            if (ghRes.ok) {
              showToast(`✅ Published review to GitHub (${event})!`);
              return;
            }
            const errJson = await ghRes.json().catch(() => ({}));
            showToast(errJson.message || `GitHub API returned HTTP ${ghRes.status}`, true);
            return;
          } catch (err) {
            showToast(`Browser GitHub publish failed: ${err.message}`, true);
            return;
          }
        }
        if (res.result?.message) {
          showToast(`✅ ${res.result.message}`);
        } else {
          showToast(`✅ Published review to GitHub (${event})!`);
        }
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

    function openTeachSubagentModal(threadId, defaultAgent) {
      const t = (STATE.activeSession?.threads || []).find((x) => x.id === threadId);
      const ag = defaultAgent || (t?.sourceAgents && t.sourceAgents[0]) || t?.sourceAgent || 'review-bug-hunter';
      showModal(`
        <div class="text-sm font-bold">🎓 Teach / Tune Subagent: <code>${esc(ag)}</code></div>
        <p class="text-xs text-[var(--q-muted)]">
          Save a persistent learned directive scoped to <code>${esc(ag)}</code> (upgrade-safe in <code>~/.gemini/quasar/preferences.json</code>), and optionally ask Antigravity to evolve <code>${esc(ag)}/AGENT.md</code>.
        </p>
        <div class="space-y-2 text-xs">
          <div>
            <label class="block font-semibold mb-1">Subagent Name</label>
            <input id="teachSubagentName" type="text" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2 font-mono" value="${esc(ag)}" />
          </div>
          <div>
            <label class="block font-semibold mb-1">Directive Type</label>
            <select id="teachSubagentSentiment" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2">
              <option value="avoid">🚫 Suppress / Stop Flagging Patterns Like This</option>
              <option value="prefer">✅ Hunt For / Prioritize Similar Issues</option>
              <option value="custom">📌 Custom Reasoning / Style Directive</option>
            </select>
          </div>
          <div>
            <label class="block font-semibold mb-1">Learned Directive / Feedback</label>
            <textarea id="teachSubagentRule" rows="3" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2">${esc(t ? `Do not flag patterns similar to: ${t.title}` : '')}</textarea>
          </div>
          <label class="flex items-center gap-2">
            <input id="teachSubagentEvolve" type="checkbox" />
            <span>Also ask Antigravity CLI to evolve <code>AGENT.md</code> with this feedback</span>
          </label>
        </div>
        <div class="flex justify-end gap-2">
          <button class="q-btn" onclick="closeModal()">Cancel</button>
          <button class="q-btn q-btn-primary" onclick="submitTeachSubagent('${esc(threadId)}')">🎓 Save Subagent Learning</button>
        </div>
      `);
    }

    async function submitTeachSubagent(threadId) {
      const subagent = document.getElementById('teachSubagentName')?.value?.trim() || '';
      const sentiment = document.getElementById('teachSubagentSentiment')?.value || 'custom';
      const rule = document.getElementById('teachSubagentRule')?.value?.trim() || '';
      const evolveAgent = Boolean(document.getElementById('teachSubagentEvolve')?.checked);
      if (!subagent || !rule) {
        showToast('Please enter a subagent name and directive', true);
        return;
      }
      closeModal();
      await threadAction(threadId, 'teachSubagent', { subagent, sentiment, rule, evolveAgent });
    }

    async function submitToken() {
      const token = document.getElementById('ghTokenInput')?.value?.trim() || '';
      if (!token) {
        showToast('Token is empty', true);
        return;
      }
      closeModal();
      showToast('🔑 Verifying GitHub token...');
      let verifiedLogin = '';
      let verifiedName = '';
      try {
        const ghRes = await fetch('https://api.github.com/user', {
          headers: {
            'Authorization': `Bearer ${token}`,
            'Accept': 'application/vnd.github+json',
          },
        });
        if (ghRes.status === 401 || ghRes.status === 403) {
          const errData = await ghRes.json().catch(() => ({}));
          showToast(`GitHub rejected token (${ghRes.status}): ${errData.message || 'Bad credentials'}`, true);
          return;
        }
        if (ghRes.ok) {
          const userData = await ghRes.json();
          verifiedLogin = userData.login || '';
          verifiedName = userData.name || '';
        }
      } catch (_) {
        // Fallback to backend verification if browser fetch is blocked
      }
      const res = await apiCall('/api/github-token', 'POST', {
        token,
        login: verifiedLogin,
        name: verifiedName,
      });
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
                   value="${esc(STATE?.activeSession?.repoDir || STATE?.defaultWorkspaceDir || '')}" placeholder="/path/to/repo" />
          </div>
          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="block font-semibold mb-1">Effort Level</label>
              <select id="newReviewEffort" class="w-full bg-black/40 border border-[var(--q-border)] rounded p-2">
                <option value="low">⚡ Low Effort (Fast)</option>
                <option value="medium">⚖️ Medium Effort (Balanced)</option>
                <option value="high" selected>🔬 High Effort (Deep Multi-Lens)</option>
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
      const effort = document.getElementById('newReviewEffort')?.value || 'high';
      const commentsOnly = document.getElementById('newReviewMode')?.value === 'comments_only';
      closeModal();
      startOptimisticAgentBanner(
        `Quasar Code Review (${prUrl || repoDir || 'Local Branch'})`,
        'Phase 1/3 • Collecting PR diff & context snapshot',
        `Starting ${effort}-effort review pipeline and spawning multi-lens subagents...`
      );
      showToast('🚀 Starting Quasar review...');
      const res = await apiCall('/api/start-review', 'POST', { prUrl, repoDir, effort, commentsOnly });
      if (res && res.ok && res.quarantined) {
        OPTIMISTIC_AGENT_JOB = null;
        renderAgentActivityBanner();
        await refreshState(res.sessionId);
        showToast('🚨 Quarantined before LLM review — inspect flagged diff lines first!', true);
      } else if (res && res.ok) {
        showToast('🚀 Review context collected and dispatched to agent!');
        await refreshState(res.sessionId);
      } else {
        OPTIMISTIC_AGENT_JOB = null;
        renderAgentActivityBanner();
        showToast(res?.error || 'Failed to start review', true);
      }
    }

    async function overrideQuarantine() {
      if (!confirm('Override Security / Prompt-Injection Quarantine? Only proceed if you manually inspected all flagged lines in the read-only diff and confirmed they are benign.')) {
        return;
      }
      startOptimisticAgentBanner(
        'Quasar Code Review (Quarantine Overridden)',
        'Phase 2/3 • Running multi-lens analysis',
        'Quarantine overridden by user — dispatching review subagents...'
      );
      const res = await apiCall('/api/override-quarantine', 'POST', {
        sessionId: STATE?.activeSession?.sessionId,
      });
      if (res && res.ok) {
        showToast('🛡️ Quarantine overridden — review dispatched to agent.');
        await refreshState(STATE?.activeSession?.sessionId);
      } else {
        OPTIMISTIC_AGENT_JOB = null;
        renderAgentActivityBanner();
        showToast(res?.error || 'Failed to override quarantine', true);
      }
    }

    (async () => {
      await detectApiBase();
      if (STATE) renderAll();
      await refreshState();
      let lastPollAt = Date.now();
      setInterval(() => {
        if (STATE?.agentStatus?.active || OPTIMISTIC_AGENT_JOB) {
          renderAgentActivityBanner();
        }
        const pollIntervalMs = (STATE?.agentStatus?.active || OPTIMISTIC_AGENT_JOB) ? 2000 : 4000;
        if (Date.now() - lastPollAt >= pollIntervalMs) {
          lastPollAt = Date.now();
          if (!document.getElementById('modalBackdrop')?.classList.contains('fixed')) {
            refreshState(STATE?.activeSession?.sessionId);
          }
        }
      }, 1000);
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

        if pathname == "/preload.js":
            preload_candidates = [
                os.environ.get("ANTIGRAVITY_SIDECAR_PRELOAD_JS") or "",
                os.path.join(THIS_DIR, "preload.js"),
            ]
            p_path = next((p for p in preload_candidates if p and os.path.isfile(p)), None)
            content = open(p_path, "r", encoding="utf-8").read() if p_path else "// preload.js fallback\nwindow.sidecar = window.sidecar || {};\n"
            body = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

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
            cid = (qs.get("conversationId") or [None])[0]
            self._send_json(200, build_state_payload(sid, cid))
            return

        if pathname == "/legacy":
            self._send_html(WEB_UI_HTML)
            return

        flutter_web_dir = find_flutter_web_dir()
        if flutter_web_dir:
            import mimetypes

            clean_rel = pathname.lstrip("/")
            if clean_rel.startswith("flutter/"):
                clean_rel = clean_rel[len("flutter/") :]
            if pathname in ("/", "/index.html", "/flutter", "/flutter/"):
                clean_rel = "index.html"
            cand_static = os.path.realpath(os.path.join(flutter_web_dir, clean_rel))
            root_real = os.path.realpath(flutter_web_dir)
            if (cand_static == root_real or cand_static.startswith(root_real + os.sep)) and os.path.isfile(cand_static):
                mime, _ = mimetypes.guess_type(cand_static)
                if cand_static.endswith(".wasm"):
                    mime = "application/wasm"
                elif cand_static.endswith(".js"):
                    mime = "application/javascript; charset=utf-8"
                elif cand_static.endswith(".json"):
                    mime = "application/json; charset=utf-8"
                with open(cand_static, "rb") as sf:
                    raw_bytes = sf.read()
                self.send_response(200)
                self.send_header("Content-Type", mime or "application/octet-stream")
                self.send_header("Content-Length", str(len(raw_bytes)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(raw_bytes)
                return

        if pathname in ("/", "/full", "/index.html", "/ui") or pathname.startswith("/c/") or pathname.startswith("/s/") or pathname.startswith("/_sidecar/"):
            self._send_html(WEB_UI_HTML)
            return

        self._send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        pathname = parsed.path
        data = self._read_json()

        if pathname == "/api/tutorial":
            try:
                _, _, default_ws, _ = resolve_conversation_context(data.get("conversationId"))
                script = os.path.join(SCRIPTS_DIR, "collect_pr_context.py")
                cmd = [sys.executable, script, "--tutorial"]
                if default_ws and os.path.isdir(default_ws):
                    cmd.extend(["--workspace-dir", default_ws])
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if proc.returncode != 0:
                    self._send_json(500, {"ok": False, "error": (proc.stderr or proc.stdout or "Failed to generate tutorial").strip()})
                    return
                out = json.loads(proc.stdout or "{}")
                self._send_json(200, {"ok": True, "sessionId": out.get("sessionId") or out.get("session_id") or "quasar-interactive-tutorial"})
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

            ws_dir = session.get("workspaceDir") or session.get("repoDir")
            pref_script = os.path.join(SCRIPTS_DIR, "manage_preferences.py")
            pref_cmd: List[str] = []
            msg = ""
            if act == "toggleStage":
                currently_staged = bool(target.get("stagedForGithub") or target.get("status") == "staged")
                if not currently_staged and target.get("kind") in ("user_qa", "user_question"):
                    target["kind"] = "ai_candidate"
                    if not target.get("severity") or str(target.get("severity")).upper() == "QA":
                        target["severity"] = "DESIGN"
                target["stagedForGithub"] = not currently_staged
                target["status"] = "staged" if target["stagedForGithub"] else "candidate"
                if not target["stagedForGithub"]:
                    target["unstagedByUser"] = True
                else:
                    target.pop("unstagedByUser", None)
                msg = "Staged for GitHub review" if target["stagedForGithub"] else "Unstaged from GitHub review"
            elif act == "toggleFix":
                target["selectedForFix"] = not bool(target.get("selectedForFix"))
                if not target["selectedForFix"]:
                    target.pop("fixStatus", None)
                msg = "Queued for Agent Fix" if target["selectedForFix"] else "Removed from Agent Fix queue"
            elif act == "markFixed":
                fix_script = os.path.join(SCRIPTS_DIR, "manage_fixes.py")
                m_cmd = [
                    sys.executable,
                    fix_script,
                    "--complete-fix",
                    "--thread-id",
                    str(tid),
                    "--summary",
                    "Marked as fixed in working tree.",
                ]
                if ws_dir:
                    m_cmd.extend(["--workspace-dir", ws_dir])
                subprocess.run(m_cmd, capture_output=True, text=True, timeout=10)
                self._send_json(200, {"ok": True, "message": "Marked comment as Fixed"})
                return
            elif act == "dismiss":
                target["status"] = "dismissed"
                target["stagedForGithub"] = False
                target["selectedForFix"] = False
                msg = "Comment dismissed"
            elif act == "editDraft":
                new_draft = str(data.get("draftComment") or "").strip()
                if new_draft:
                    orig_draft = str(target.get("draftComment") or "").strip()
                    target["draftComment"] = new_draft
                    try:
                        from answer_comment_question import rebuild_primary_finding_message_body
                        if target.get("messages"):
                            target["messages"][0]["body"] = rebuild_primary_finding_message_body(target)
                    except Exception:
                        if target.get("messages"):
                            target["messages"][0]["body"] = new_draft
                    pref_cmd = [
                        sys.executable,
                        pref_script,
                        "--record-feedback",
                        "--thread-id",
                        str(tid),
                        "--action",
                        "edit_draft",
                        "--original-draft",
                        orig_draft,
                        "--updated-draft",
                        new_draft,
                    ]
                    msg = "Saved edited draft & recorded phrasing preference"
            elif act == "calibrate":
                decision = data.get("decision")
                if decision == "keep_always":
                    target["status"] = "staged"
                    target["stagedForGithub"] = True
                    cal_act = "calibrate_keep"
                    msg = "Staged & saved preference to always flag similar patterns"
                elif decision == "dismiss_skip":
                    target["status"] = "dismissed"
                    target["stagedForGithub"] = False
                    cal_act = "calibrate_skip"
                    msg = "Dismissed & saved preference to skip this pattern in future reviews"
                else:
                    target["status"] = "staged"
                    target["stagedForGithub"] = True
                    cal_act = "calibrate_once"
                    msg = "Kept for this review"
                pref_cmd = [
                    sys.executable,
                    pref_script,
                    "--record-feedback",
                    "--thread-id",
                    str(tid),
                    "--action",
                    cal_act,
                ]
            elif act == "rate":
                rating = data.get("rating")
                pref_cmd = [
                    sys.executable,
                    pref_script,
                    "--record-feedback",
                    "--thread-id",
                    str(tid),
                    "--action",
                    "upvote" if rating == "up" else "downvote",
                ]
                msg = "Recorded positive feedback 👍" if rating == "up" else "Recorded feedback 👎"
            elif act == "teachSubagent":
                subagent_name = str(data.get("subagent") or target.get("sourceAgent") or "").strip()
                sentiment = str(data.get("sentiment") or "custom").strip().lower()
                if sentiment not in ("prefer", "avoid", "custom"):
                    sentiment = "custom"
                rule_text = str(data.get("rule") or "").strip()
                evolve_agent = bool(data.get("evolveAgent"))
                fb_action = "downvote" if sentiment == "avoid" else ("upvote" if sentiment == "prefer" else "meta_feedback")
                if sentiment == "avoid":
                    target["status"] = "dismissed"
                    target["stagedForGithub"] = False
                    target["selectedForFix"] = False
                elif sentiment == "prefer":
                    target["status"] = "staged"
                    target["stagedForGithub"] = True
                if rule_text:
                    pref_cmd = [
                        sys.executable,
                        pref_script,
                        "--record-feedback",
                        "--thread-id",
                        str(tid),
                        "--action",
                        fb_action,
                        "--sentiment",
                        sentiment,
                        "--reason",
                        rule_text,
                        "--learned-rule",
                        rule_text,
                    ]
                    if subagent_name:
                        pref_cmd.extend(["--subagent", subagent_name])
                if evolve_agent and subagent_name and rule_text:
                    evolve_prompt = (
                        f"Using the `quasar` skill, evolve the review subagent `{subagent_name}` based on reviewer feedback on "
                        f"`{target.get('path')}:{target.get('startLine')}` (\"{target.get('title')}\"):\n\n"
                        f"> {rule_text}\n\n"
                        f"1. Distill and save a clean reusable directive scoped to `{subagent_name}` via `manage_preferences.py --subagent {subagent_name}`.\n"
                        f"2. If `{subagent_name}` has an `AGENT.md` file under `~/.gemini/config/plugins/quasar/agents/{subagent_name}/` or workspace `.gemini/agents/{subagent_name}/`, refine its rules/checklist while keeping valid YAML frontmatter."
                    )
                    dispatch_to_agent(
                        evolve_prompt,
                        title=f"Evolve Subagent: {subagent_name}",
                        conversation_id=session.get("conversationId"),
                        workspace_dir=ws_dir,
                        effort=session.get("effortLevel") or "high",
                        allow_edits=True,
                    )
                msg = f"🎓 Taught {subagent_name or 'Quasar'}: {rule_text}"
            elif act == "selectSolution":
                opt_id = str(data.get("optionId") or "").strip()
                custom_text = str(data.get("customText") or "").strip()
                if opt_id in ("clear", "none"):
                    target.pop("selectedSolutionId", None)
                    target.pop("selectedSolution", None)
                    msg = "Cleared selected solution"
                elif opt_id == "custom" and custom_text:
                    target["selectedSolutionId"] = "custom"
                    target["selectedSolution"] = custom_text
                    if data.get("queueForFix"):
                        target["selectedForFix"] = True
                    msg = "Saved custom solution approach"
                else:
                    matched = next(
                        (
                            o
                            for o in (target.get("solutionOptions") or [])
                            if isinstance(o, dict) and str(o.get("id") or "") == opt_id
                        ),
                        None,
                    )
                    if matched:
                        target["selectedSolutionId"] = str(matched.get("id") or opt_id)
                        label = str(matched.get("label") or "").strip()
                        desc = str(matched.get("description") or "").strip()
                        target["selectedSolution"] = (
                            f"{label} — {desc}" if (label and desc) else (label or desc)
                        )
                        msg = f"Selected solution: {label or opt_id}"
                    elif custom_text:
                        target["selectedSolutionId"] = opt_id or "custom"
                        target["selectedSolution"] = custom_text
                        msg = "Saved selected solution"

            save_session_everywhere(session, session_path)
            if pref_cmd:
                if ws_dir:
                    pref_cmd.extend(["--workspace-dir", ws_dir])
                try:
                    subprocess.run(pref_cmd, capture_output=True, text=True, timeout=10)
                except Exception:
                    pass
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
                f"--workspace-dir \"{ws_dir or ''}\" --thread-id \"{tid}\" --updated-draft \"<REVISED_COMMENT>\" "
                f"--answer \"<BRIEF_SUMMARY_OF_CHANGE>\"` "
                f"(and `--updated-title` / `--updated-why` / `--updated-severity` as needed)."
            )
            res = dispatch_to_agent(
                prompt,
                title="Quasar: Revise Comment",
                conversation_id=data.get("conversationId") or session.get("conversationId"),
                workspace_dir=ws_dir,
            )
            self._send_json(200 if res.get("ok") else 500, {"ok": bool(res.get("ok")), "error": res.get("error"), "dispatch": res})
            return

        if pathname in ("/api/ask-line", "/api/add-comment"):
            import datetime
            import time

            from answer_comment_question import (
                derive_concise_title,
                rebuild_primary_finding_message_body,
            )

            session, session_path = load_target_session(data.get("sessionId"))
            if not session:
                self._send_json(404, {"ok": False, "error": "No active session"})
                return
            rel_path = (data.get("path") or "").strip()
            start_line = max(1, int(data.get("startLine") or data.get("line") or 1))
            end_line = max(start_line, int(data.get("endLine") or start_line))
            question = (data.get("question") or data.get("comment") or "").strip()
            default_mode = "manual" if pathname == "/api/add-comment" else "qa"
            mode = str(data.get("mode") or default_mode).strip().lower()
            severity = str(data.get("severity") or "DESIGN").strip().upper()
            ws_dir = session.get("workspaceDir") or session.get("repoDir")
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            threads = session.setdefault("threads", [])

            if mode == "manual":
                if not question:
                    self._send_json(400, {"ok": False, "error": "Comment text is required"})
                    return
                tid = f"finding-user-{int(time.time() * 1000)}"
                new_t = {
                    "id": tid,
                    "kind": "ai_candidate",
                    "createdBy": "user",
                    "sourceAgents": [],
                    "path": rel_path,
                    "startLine": start_line,
                    "endLine": end_line,
                    "severity": severity,
                    "status": "staged",
                    "stagedForGithub": True,
                    "title": derive_concise_title(question),
                    "whyItMatters": "",
                    "draftComment": question,
                    "messages": [],
                }
                new_t["messages"] = [
                    {
                        "author": "You",
                        "body": rebuild_primary_finding_message_body(new_t),
                        "timestamp": now_iso,
                    }
                ]
                threads.append(new_t)
                save_session_everywhere(session, session_path)
                self._send_json(200, {"ok": True, "threadId": tid, "mode": "manual"})
                return

            if mode in ("ai_comment", "comment"):
                tid = f"finding-inline-{int(time.time() * 1000)}"
                placeholder_title = (
                    derive_concise_title(question)
                    if question
                    else f"Drafting comment on {os.path.basename(rel_path) or 'PR'}:{start_line}..."
                )
                pending_body = (
                    f"_⏳ Quasar is drafting an inline review comment for: \"{question}\"..._"
                    if question
                    else f"_⏳ Quasar is inspecting `{rel_path}:{start_line}` and drafting an inline review comment..._"
                )
                threads.append({
                    "id": tid,
                    "kind": "ai_candidate",
                    "createdBy": "quasar_inline",
                    "pendingInlineDraft": True,
                    "sourceAgent": "quasar-inline",
                    "sourceAgents": ["quasar-inline"],
                    "path": rel_path,
                    "startLine": start_line,
                    "endLine": end_line,
                    "severity": severity,
                    "status": "staged",
                    "stagedForGithub": True,
                    "title": placeholder_title,
                    "whyItMatters": "",
                    "draftComment": "",
                    "messages": [{"author": "Quasar", "body": pending_body, "timestamp": now_iso, "pending": True}],
                })
                save_session_everywhere(session, session_path)
                is_owned = (session.get("pr") or {}).get("isOwnedByUser", True) is not False
                goal_md = (
                    f"Reviewer Instruction / Topic:\n> {question}"
                    if question
                    else f"Inspect `{rel_path}` around lines {start_line}-{end_line} and draft a concise, high-signal code review comment."
                )
                sol_flag = " [--solution-options '<JSON_ARRAY_IF_2+_SOLUTIONS>']" if is_owned else ""
                prompt = (
                    f"Using the `quasar` skill, add an inline review comment on `{rel_path}:{start_line}` (thread ID: `{tid}`):\n\n"
                    f"{goal_md}\n\n"
                    f"Inspect the code, honor `~/.gemini/quasar/PREFERENCES.md`, and populate the comment in place using:\n"
                    f"`python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/answer_comment_question.py "
                    f"--workspace-dir \"{ws_dir or ''}\" --thread-id \"{tid}\" --promote-to-comment "
                    f"--updated-title \"<SHORT_TITLE>\" --updated-why \"<CONCISE_CONTEXT>\" "
                    f"--updated-severity \"{severity}\" --updated-draft \"<CONCISE_GITHUB_COMMENT>\"{sol_flag}`."
                )
                res = dispatch_to_agent(
                    prompt,
                    title=f"Add Inline Comment: {os.path.basename(rel_path)}:{start_line}",
                    conversation_id=data.get("conversationId") or session.get("conversationId"),
                    workspace_dir=ws_dir,
                    effort=session.get("effortLevel") or "high",
                )
                self._send_json(200 if res.get("ok") else 500, {"ok": bool(res.get("ok")), "error": res.get("error"), "threadId": tid, "dispatch": res})
                return

            tid = f"qa-{int(time.time() * 1000)}"
            threads.append({
                "id": tid,
                "kind": "user_qa",
                "path": rel_path,
                "startLine": start_line,
                "endLine": end_line,
                "severity": "QA",
                "status": "open",
                "title": derive_concise_title(question, f"Question on {os.path.basename(rel_path)}:{start_line}"),
                "draftComment": question,
                "messages": [
                    {"author": "You", "body": question, "timestamp": now_iso},
                    {"author": "Quasar", "body": "_⏳ Quasar is analyzing this code hunk..._", "timestamp": now_iso, "pending": True},
                ],
            })
            save_session_everywhere(session, session_path)
            prompt = (
                f"Using the `quasar` skill, answer my inline question on `{rel_path}:{start_line}` (thread ID: `{tid}`):\n\n"
                f"> {question}\n\n"
                f"Post the answer back into the thread using `python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/answer_comment_question.py "
                f"--workspace-dir \"{ws_dir or ''}\" --thread-id \"{tid}\" --answer \"...\"`."
            )
            res = dispatch_to_agent(
                prompt,
                title=f"Quasar Q&A: {os.path.basename(rel_path)}:{start_line}",
                conversation_id=data.get("conversationId") or session.get("conversationId"),
                workspace_dir=ws_dir,
            )
            self._send_json(200 if res.get("ok") else 500, {"ok": bool(res.get("ok")), "error": res.get("error"), "threadId": tid, "dispatch": res})
            return

        if pathname == "/api/fix-selected":
            session, _ = load_target_session(data.get("sessionId"))
            if not session:
                self._send_json(404, {"ok": False, "error": "No active session"})
                return
            ws_dir = session.get("workspaceDir") or session.get("repoDir")
            script = os.path.join(SCRIPTS_DIR, "manage_fixes.py")
            cmd = [sys.executable, script, "--prepare-fix"]
            if ws_dir:
                cmd.extend(["--workspace-dir", ws_dir])
            sol_choices = data.get("solutionChoices")
            if isinstance(sol_choices, dict) and sol_choices:
                cmd.extend(["--solution-choices-json", json.dumps(sol_choices)])
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            prep = {}
            try:
                prep = json.loads(proc.stdout or "{}")
            except Exception:
                pass
            count = prep.get("count") or prep.get("selectedCount") or 0
            if not count:
                self._send_json(400, {"ok": False, "error": "No comments are currently queued for fix. Click 'Queue for Fix' on one or more comments first."})
                return
            prompt_path = prep.get("promptPath") or prep.get("fixRequestPath") or ""
            prompt = (prep.get("agentPrompt") or "").strip()
            if not prompt and prompt_path and os.path.isfile(prompt_path):
                try:
                    with open(prompt_path, "r", encoding="utf-8") as f:
                        prompt = f.read().strip()
                except Exception:
                    pass
            if not prompt:
                prompt = f"Using the `quasar` skill, fix the {count} selected review comments in `{prompt_path}`."
            res = dispatch_to_agent(
                prompt,
                title="Quasar: Fix Selected Comments",
                conversation_id=data.get("conversationId") or session.get("conversationId"),
                workspace_dir=ws_dir,
                allow_edits=True,
            )
            self._send_json(200 if res.get("ok") else 500, {"ok": bool(res.get("ok")), "error": res.get("error"), "count": count, "dispatch": res})
            return

        if pathname == "/api/publish-github":
            session, _ = load_target_session(data.get("sessionId"))
            if not session:
                self._send_json(404, {"ok": False, "error": "No active session"})
                return
            event = data.get("event") or "COMMENT"
            body = data.get("body") or ""
            ws_dir = session.get("workspaceDir") or session.get("repoDir")
            script = os.path.join(SCRIPTS_DIR, "post_pending_review.py")
            cmd = [sys.executable, script, "--from-session", "--event", event]
            if body:
                cmd.extend(["--summary-body", body])
            if ws_dir:
                cmd.extend(["--workspace-dir", ws_dir])
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            out = {}
            try:
                out = json.loads(proc.stdout or "{}")
            except Exception:
                pass
            if proc.returncode == 0 and (out.get("ok") or out.get("status") in ("submitted", "pending_created", "exported")):
                if out.get("status") == "needs_browser_publish":
                    token = ""
                    if os.path.isfile(GITHUB_AUTH_PATH):
                        try:
                            with open(GITHUB_AUTH_PATH, "r", encoding="utf-8") as f:
                                token = (json.load(f).get("token") or "").strip()
                        except Exception:
                            pass
                    if not token:
                        token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
                    if not token:
                        self._send_json(400, {"ok": False, "error": "Please click '🔑 GitHub Key' and save your GitHub Personal Access Token first."})
                        return
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "clientSidePublish": True,
                            "url": out.get("url"),
                            "token": token,
                            "payload": out.get("payload"),
                            "event": out.get("event") or event,
                            "result": out,
                        },
                    )
                    return
                self._send_json(200, {"ok": True, "result": out})
            else:
                self._send_json(400, {"ok": False, "error": out.get("error") or proc.stderr or proc.stdout or "Failed to publish review"})
            return

        if pathname == "/api/github-token":
            token = (data.get("token") or "").strip()
            if not token:
                self._send_json(400, {"ok": False, "error": "Token is empty"})
                return
            browser_login = (data.get("login") or "").strip()
            browser_name = (data.get("name") or "").strip()
            if browser_login:
                os.makedirs(STATE_DIR, exist_ok=True)
                preview = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else "***"
                with open(GITHUB_AUTH_PATH, "w", encoding="utf-8") as f:
                    json.dump({"token": token, "login": browser_login, "name": browser_name, "tokenPreview": preview}, f, indent=2)
                os.chmod(GITHUB_AUTH_PATH, 0o600)
                self._send_json(200, {"ok": True, "login": browser_login})
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
                err_str = str(e)
                if "Remote end closed connection" in err_str or "RemoteDisconnected" in err_str:
                    login = "github-user"
                    os.makedirs(STATE_DIR, exist_ok=True)
                    preview = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else "***"
                    with open(GITHUB_AUTH_PATH, "w", encoding="utf-8") as f:
                        json.dump({"token": token, "login": login, "name": "", "tokenPreview": preview}, f, indent=2)
                    os.chmod(GITHUB_AUTH_PATH, 0o600)
                    self._send_json(200, {"ok": True, "login": login})
                    return
                self._send_json(400, {"ok": False, "error": f"GitHub token check failed: {e}"})
            return

        if pathname == "/api/reply-thread":
            import datetime

            session, session_path = load_target_session(data.get("sessionId"))
            if not session:
                self._send_json(404, {"ok": False, "error": "No active session"})
                return
            tid = str(data.get("threadId") or "").strip()
            question = str(data.get("question") or "").strip()
            change_mode = bool(data.get("changeMode", False))
            target = next((t for t in (session.get("threads") or []) if str(t.get("id")) == tid), None)
            if not target or not question:
                self._send_json(400, {"ok": False, "error": "Missing threadId or question"})
                return
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            msgs = [
                m
                for m in (target.get("messages") or [])
                if not m.get("pending") and "⏳ Quasar is " not in str(m.get("body") or "")
            ]
            msgs.append({"author": "You", "body": question, "timestamp": now_iso})
            msgs.append({
                "author": "Quasar",
                "body": "⏳ Quasar is analyzing your comment / question...",
                "timestamp": now_iso,
                "pending": True,
            })
            target["messages"] = msgs
            save_session_everywhere(session, session_path)

            ws_dir = session.get("workspaceDir") or session.get("repoDir")
            rel_path = target.get("targetFile") or target.get("path") or ""
            start_l = target.get("startLine") or 1
            is_gh_existing = target.get("kind") == "github_existing"
            prompt = (
                f"Using the `quasar` skill, respond to my comment on thread `{tid}` in `{rel_path}:L{start_l}`:\n\n"
                f"Reviewer Question / Request:\n\"{question}\"\n\n"
                f"Thread Title: {target.get('title') or ''}\n"
                f"Current Comment Text:\n```\n{target.get('draftComment') or ''}\n```\n\n"
                + (
                    f"Investigate the code and reply in place by running:\n"
                    f"`python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/answer_comment_question.py "
                    f"--workspace-dir \"{ws_dir or ''}\" --thread-id \"{tid}\" --answer \"<DETAILED_MARKDOWN_ANSWER>\" "
                    + ("" if is_gh_existing else "[--updated-draft \"<REVISED_COMMENT_IF_REQUESTED>\"]")
                    + "`"
                )
            )
            if change_mode and not is_gh_existing:
                prompt += "\n(The user asked to revise or refine this comment; pass `--updated-draft` if the draft text should change.)"

            res = dispatch_to_agent(
                prompt,
                title=f"Quasar Thread Q&A: {os.path.basename(rel_path)}:L{start_l}",
                conversation_id=data.get("conversationId") or session.get("conversationId"),
                workspace_dir=ws_dir,
                effort=session.get("effortLevel") or "high",
            )
            self._send_json(200 if res.get("ok") else 500, {"ok": bool(res.get("ok")), "error": res.get("error"), "dispatch": res})
            return

        if pathname == "/api/open-in-editor":
            import shutil

            session, _ = load_target_session(data.get("sessionId"))
            rel_path = str(data.get("path") or "").strip()
            line = int(data.get("line") or 1)
            repo_dir = (
                str(data.get("repoDir") or "").strip()
                or ((session or {}).get("repoDir") or "")
                or ((session or {}).get("workspaceDir") or "")
            )
            head_root = (session or {}).get("headSnapshotRoot") or ""
            full_path = ""
            if repo_dir and rel_path and os.path.isfile(os.path.join(repo_dir, rel_path)):
                full_path = os.path.join(repo_dir, rel_path)
            elif head_root and rel_path and os.path.isfile(os.path.join(head_root, rel_path)):
                full_path = os.path.join(head_root, rel_path)
            if not full_path:
                self._send_json(404, {"ok": False, "error": f"File not found on disk: {rel_path}"})
                return
            code_bin = shutil.which("code") or "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"
            if os.path.isfile(code_bin):
                subprocess.Popen([code_bin, "-g", f"{full_path}:{line}"])
                self._send_json(200, {"ok": True, "opened": f"{full_path}:{line}"})
                return
            subprocess.Popen(["open", full_path])
            self._send_json(200, {"ok": True, "opened": full_path})
            return

        if pathname == "/api/start-review":
            conv_id, _, default_ws, _ = resolve_conversation_context(data.get("conversationId"))
            pr_url = (data.get("prUrl") or "").strip()
            repo_dir = (data.get("repoDir") or default_ws or os.getcwd()).strip()
            effort = (data.get("effort") or "high").strip().lower()
            if effort not in ("low", "medium", "high"):
                effort = "high"
            comments_only = bool(data.get("commentsOnly", False))
            branch_name = (data.get("branch") or "").strip()

            # Run deterministic `collect_pr_context.py` pre-scan BEFORE invoking any LLM agent!
            collect_script = os.path.join(SCRIPTS_DIR, "collect_pr_context.py")
            cmd = [
                sys.executable,
                collect_script,
                "--effort",
                effort,
                "--comments-only" if comments_only else "--full-guide",
                "--workspace-dir",
                repo_dir,
            ]
            if pr_url:
                cmd.extend(["--pr", pr_url])
                if os.path.isdir(repo_dir):
                    cmd.extend(["--repo-dir", repo_dir])
            else:
                cmd.extend(["--branch", branch_name or "HEAD", "--repo-dir", repo_dir])

            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if proc.returncode == 0 and (proc.stdout or "").strip():
                    ctx_out = json.loads(proc.stdout)
                    if ctx_out.get("halt_llm_review"):
                        self._send_json(
                            200,
                            {
                                "ok": True,
                                "quarantined": True,
                                "sessionId": ctx_out.get("session_id"),
                                "securityGate": ctx_out.get("security_gate"),
                            },
                        )
                        return
            except Exception:
                ctx_out = {}

            mode_str = " (comments only)" if comments_only else ""
            if pr_url:
                prompt = f"Using the `quasar` skill, review {pr_url} on {effort} effort{mode_str} with --workspace-dir \"{repo_dir}\""
            else:
                branch_part = f" branch `{branch_name}`" if branch_name else " local branch"
                prompt = f"Using the `quasar` skill, review{branch_part} in {repo_dir} on {effort} effort{mode_str}"
            res = dispatch_to_agent(
                prompt,
                title="Quasar Code Review",
                conversation_id=conv_id,
                workspace_dir=repo_dir,
                effort=effort,
                allow_edits=False,
            )
            self._send_json(
                200 if res.get("ok") else 500,
                {
                    "ok": bool(res.get("ok")),
                    "error": res.get("error"),
                    "sessionId": (ctx_out.get("session_id") if isinstance(ctx_out, dict) else None),
                    "dispatch": res,
                },
            )
            return

        if pathname == "/api/override-quarantine":
            session, _ = load_target_session(data.get("sessionId"))
            if not session:
                self._send_json(404, {"ok": False, "error": "No active session found"})
                return
            conv_id, _, default_ws, _ = resolve_conversation_context(data.get("conversationId"))
            ws_dir = session.get("workspaceDir") or session.get("repoDir") or default_ws or os.getcwd()
            collect_script = os.path.join(SCRIPTS_DIR, "collect_pr_context.py")
            proc = subprocess.run(
                [sys.executable, collect_script, "--override-quarantine", "--workspace-dir", ws_dir],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if proc.returncode != 0:
                self._send_json(
                    500,
                    {"ok": False, "error": (proc.stderr or proc.stdout or "Failed to override quarantine").strip()},
                )
                return
            pr_meta = session.get("pr") or {}
            effort = str(session.get("effortLevel") or "high").lower()
            comments_only = bool(session.get("commentsOnly", False))
            state_dir = session.get("stateDir") or ""
            publish_flags = f'--comments-only --workspace-dir "{ws_dir}"' if comments_only else f'--workspace-dir "{ws_dir}"'
            target_desc = pr_meta.get("url") or f"branch `{pr_meta.get('headRefName') or 'HEAD'}` in {session.get('repoDir') or ws_dir}"
            prompt = (
                f"The user manually inspected and overrode the security quarantine for {target_desc} in workspace \"{ws_dir}\". "
                f"Context and snapshots are ready in \"{state_dir}/active_session.json\" and \"{state_dir}/pr_context.json\" "
                f"(`halt_llm_review` is now `false`; do NOT execute local repo scripts). "
                f"Using the `quasar` skill on {effort} effort, run Phase 2 and Phase 3 (`publish_review_session.py {publish_flags}`)."
            )
            res = dispatch_to_agent(
                prompt,
                title="Quasar Code Review (Quarantine Overridden)",
                conversation_id=conv_id,
                workspace_dir=ws_dir,
                effort=effort,
                allow_edits=False,
            )
            self._send_json(200 if res.get("ok") else 500, {"ok": bool(res.get("ok")), "error": res.get("error"), "dispatch": res})
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
