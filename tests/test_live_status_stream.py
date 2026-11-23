import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = REPO_ROOT / "plugin" / "sidecars" / "quasar-web"
sys.path.insert(0, str(SERVER_DIR))

import server  # noqa: E402


class TestLiveStatusAndStreaming(unittest.TestCase):
    def setUp(self) -> None:
        with server._JOB_LOCK:
            server.ACTIVE_AGENT_JOB.clear()
            server._SESSION_META_CACHE.clear()

    def tearDown(self) -> None:
        with server._JOB_LOCK:
            server.ACTIVE_AGENT_JOB.clear()
            server._SESSION_META_CACHE.clear()

    def test_stream_events_ignore_tool_output_false_positives(self) -> None:
        server._init_active_agent_job(
            title="Quasar Code Review: PR #42",
            prompt_preview="Review PR #42",
            baseline_mtime=0.0,
            conversation_id="conv-1",
            mode="agy-cli",
        )
        # Reading SKILL.md includes names of scripts and all 15 review-* subagents in tool output.
        skill_text = (
            "Available subagents: review-architect, review-bug-hunter, review-test-critic, "
            "review-advocate, review-security, review-performance.\n"
            "Phase 1: python3 collect_pr_context.py\n"
            "Phase 3: python3 publish_review_session.py\n"
        )
        server._process_cli_stream_event(
            {
                "event": "step_update",
                "conversation_id": "conv-1",
                "step_update": {
                    "step_index": 2,
                    "step_type": "tool",
                    "state": "DONE",
                    "tool_name": "read_file",
                    "tool_info": {
                        "name": "read_file",
                        "parameters": {"Path": "/tmp/plugin/skills/quasar/SKILL.md"},
                        "output": skill_text,
                    },
                },
            },
            server.ACTIVE_AGENT_JOB,
        )

        status = server.get_live_agent_status(None, [])
        self.assertTrue(status["active"])
        self.assertEqual(status["subagents"], [])
        self.assertIn("Phase 1/3", status["stage"])
        self.assertIn("Quasar skill instructions", status["detail"])
        self.assertLess(status["percent"], 32)

    def test_stream_and_subagent_transcript_fine_grained_progression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sub1_tp = os.path.join(tmp, "sub1", ".system_generated", "logs", "transcript.jsonl")
            sub2_tp = os.path.join(tmp, "sub2", ".system_generated", "logs", "transcript.jsonl")
            os.makedirs(os.path.dirname(sub1_tp), exist_ok=True)
            os.makedirs(os.path.dirname(sub2_tp), exist_ok=True)

            with open(sub1_tp, "w", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "type": "PLANNER_RESPONSE",
                            "status": "RUNNING",
                            "tool_calls": [
                                {"name": "read_file", "args": {"Path": '"/repo/lib/auth.dart"'}},
                            ],
                        }
                    )
                    + "\n"
                )
            with open(sub2_tp, "w", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "type": "PLANNER_RESPONSE",
                            "status": "RUNNING",
                            "tool_calls": [
                                {"name": "grep_search", "args": {"Query": '"token_store"'}},
                            ],
                        }
                    )
                    + "\n"
                )

            server._init_active_agent_job(
                title="Quasar Code Review: PR #99",
                prompt_preview="Review PR #99",
                baseline_mtime=0.0,
                conversation_id="coord-99",
                mode="agy-cli",
            )

            # 1. Context fetch
            server._process_cli_stream_event(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_index": 3,
                        "step_type": "tool",
                        "state": "DONE",
                        "tool_name": "run_command",
                        "tool_info": {
                            "name": "run_command",
                            "parameters": {"CommandLine": "python3 scripts/collect_pr_context.py --pr 99"},
                            "output": '{"ok": true}',
                        },
                    },
                },
                server.ACTIVE_AGENT_JOB,
            )
            st1 = server.get_live_agent_status(None, [])
            self.assertIn("Phase 1/3", st1["stage"])
            self.assertLessEqual(st1["percent"], 31)

            # 2. Spawn 2 subagents
            for idx, (tname, role, cid, tp) in enumerate(
                [
                    ("review-architect", "Architecture & API Lens", "cid-sub-1", sub1_tp),
                    ("review-bug-hunter", "Bug & Edge-Case Hunter", "cid-sub-2", sub2_tp),
                ],
                start=4,
            ):
                server._process_cli_stream_event(
                    {
                        "event": "step_update",
                        "step_update": {
                            "step_index": idx,
                            "step_type": "tool",
                            "state": "DONE",
                            "tool_name": "invoke_subagent",
                            "tool_info": {
                                "name": "invoke_subagent",
                                "parameters": {"TypeName": tname, "Role": role},
                                "output": json.dumps(
                                    {"conversationId": cid, "logAbsoluteUri": f"file://{tp}"}
                                ),
                            },
                        },
                    },
                    server.ACTIVE_AGENT_JOB,
                )

            # 3. Coordinator calls schedule (must NOT overwrite live subagent status!)
            server._process_cli_stream_event(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_index": 6,
                        "step_type": "tool",
                        "state": "DONE",
                        "tool_name": "schedule",
                        "tool_info": {"name": "schedule", "parameters": {"DurationSeconds": 60}},
                    },
                },
                server.ACTIVE_AGENT_JOB,
            )

            st2 = server.get_live_agent_status(None, [])
            self.assertIn("0/2 complete", st2["stage"])
            self.assertNotIn("schedule", st2["detail"].lower())
            self.assertIn("Reading auth.dart", st2["detail"])
            self.assertEqual(len(st2["subagentProgress"]), 2)
            self.assertEqual(st2["subagentProgress"][0]["status"], "running")

            # 4. Mark subagent 1 complete via transcript
            with open(sub1_tp, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {"type": "PLANNER_RESPONSE", "status": "DONE", "tool_calls": []}
                    )
                    + "\n"
                )
            st3 = server.get_live_agent_status(None, [])
            self.assertIn("1/2 complete", st3["stage"])
            self.assertEqual(st3["subagentProgress"][0]["status"], "done")
            self.assertEqual(st3["subagentProgress"][1]["status"], "running")
            self.assertGreater(st3["percent"], st1["percent"])

            # 5. Synthesis & publish
            server._process_cli_stream_event(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_index": 7,
                        "step_type": "tool",
                        "state": "DONE",
                        "tool_name": "write_file",
                        "tool_info": {
                            "name": "write_file",
                            "parameters": {"Path": "/tmp/ws/review_synthesis.json"},
                        },
                    },
                },
                server.ACTIVE_AGENT_JOB,
            )
            st4 = server.get_live_agent_status(None, [])
            self.assertIn("Phase 3/3", st4["stage"])
            self.assertGreaterEqual(st4["percent"], 86)

            server._process_cli_stream_event(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_index": 8,
                        "step_type": "tool",
                        "state": "DONE",
                        "tool_name": "run_command",
                        "tool_info": {
                            "name": "run_command",
                            "parameters": {"CommandLine": "python3 scripts/publish_review_session.py"},
                        },
                    },
                },
                server.ACTIVE_AGENT_JOB,
            )
            st5 = server.get_live_agent_status(None, [])
            self.assertIn("Publishing CliffsNotes", st5["stage"])
            self.assertGreaterEqual(st5["percent"], 92)

    def test_transcript_replay_with_json_quoted_args_for_ide_hub_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain_root = os.path.join(tmp, "brain")
            coord_logs = os.path.join(brain_root, "coord-cid", ".system_generated", "logs")
            sub_logs = os.path.join(brain_root, "sub-sec-cid", ".system_generated", "logs")
            os.makedirs(coord_logs, exist_ok=True)
            os.makedirs(sub_logs, exist_ok=True)
            sub_tp = os.path.join(sub_logs, "transcript.jsonl")
            coord_tp = os.path.join(coord_logs, "transcript.jsonl")

            with open(sub_tp, "w", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "type": "PLANNER_RESPONSE",
                            "status": "RUNNING",
                            "tool_calls": [
                                {"name": "read_file", "args": {"Path": '"/repo/crypto.py"'}},
                            ],
                        }
                    )
                    + "\n"
                )
            with open(coord_tp, "w", encoding="utf-8") as f:
                f.write(json.dumps({"type": "USER_INPUT", "content": "Review PR #5"}) + "\n")
                f.write(
                    json.dumps(
                        {
                            "type": "PLANNER_RESPONSE",
                            "status": "DONE",
                            "tool_calls": [
                                {
                                    "name": "invoke_subagent",
                                    "args": {
                                        "TypeName": '"review-security"',
                                        "Role": '"Security Auditor"',
                                    },
                                }
                            ],
                        }
                    )
                    + "\n"
                )
                f.write(
                    json.dumps(
                        {
                            "type": "GENERIC",
                            "content": json.dumps(
                                {
                                    "conversationId": "sub-sec-cid",
                                    "logAbsoluteUri": f"file://{sub_tp}",
                                }
                            ),
                        }
                    )
                    + "\n"
                )

            server._init_active_agent_job(
                title="Quasar Code Review: PR #5",
                prompt_preview="Review PR #5",
                baseline_mtime=time.time(),
                conversation_id="coord-cid",
                mode="http-hub",
            )

            with mock.patch.object(
                server,
                "find_brain_conversation_dir",
                side_effect=lambda cid: os.path.join(brain_root, cid) if os.path.isdir(os.path.join(brain_root, cid)) else None,
            ):
                st = server.get_live_agent_status(None, [])

            self.assertTrue(st["active"])
            self.assertEqual(st["subagents"], ["Security Auditor"])
            self.assertIn("0/1 complete", st["stage"])
            self.assertIn("Reading crypto.py", st["detail"])

    def test_http_agent_status_and_sse_stream_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws_dir = os.path.join(tmp, "workspaces")
            hist_dir = os.path.join(tmp, "history")
            os.makedirs(ws_dir, exist_ok=True)
            os.makedirs(hist_dir, exist_ok=True)

            with mock.patch.object(server, "WORKSPACES_DIR", ws_dir), \
                 mock.patch.object(server, "HISTORY_DIR", hist_dir):
                srv = server._QuasarHTTPServer(("127.0.0.1", 0), server.QuasarWebHandler)
                port = srv.server_address[1]
                srv_thread = threading.Thread(target=srv.serve_forever, daemon=True)
                srv_thread.start()
                try:
                    # 1. Verify fast /api/agent-status + no-store header
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    conn.request("GET", "/api/agent-status")
                    resp = conn.getresponse()
                    self.assertEqual(resp.status, 200)
                    self.assertIn("no-store", resp.getheader("Cache-Control") or "")
                    data = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(data["ok"])
                    self.assertFalse(data["agentStatus"]["active"])
                    conn.close()

                    # 2. Open SSE /api/agent-stream and verify live push without reload
                    sse_conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    sse_conn.request("GET", "/api/agent-stream")
                    sse_resp = sse_conn.getresponse()
                    self.assertEqual(sse_resp.status, 200)
                    self.assertIn("text/event-stream", sse_resp.getheader("Content-Type") or "")
                    self.assertIn("no-store", sse_resp.getheader("Cache-Control") or "")

                    def read_sse_event() -> dict:
                        ev_name = ""
                        while True:
                            raw_ln = sse_resp.fp.readline()
                            if not raw_ln:
                                return {}
                            ln = raw_ln.decode("utf-8", errors="replace").strip()
                            if ln.startswith("event:"):
                                ev_name = ln.split(":", 1)[1].strip()
                            elif ln.startswith("data:") and ev_name == "status":
                                sse_resp.fp.readline()  # blank terminator
                                return json.loads(ln.split(":", 1)[1].strip())

                    first = read_sse_event()
                    self.assertFalse(first["agentStatus"]["active"])

                    server._init_active_agent_job(
                        title="PR #77 Review",
                        prompt_preview="Review PR #77",
                        baseline_mtime=0.0,
                        conversation_id="conv-sse",
                        mode="agy-cli",
                    )
                    server._process_cli_stream_event(
                        {
                            "event": "step_update",
                            "step_update": {
                                "step_index": 1,
                                "step_type": "tool",
                                "state": "DONE",
                                "tool_name": "run_command",
                                "tool_info": {
                                    "name": "run_command",
                                    "parameters": {"CommandLine": "python3 collect_pr_context.py --pr 77"},
                                },
                            },
                        },
                        server.ACTIVE_AGENT_JOB,
                    )
                    server._notify_status_change()

                    pushed = read_sse_event()
                    self.assertTrue(pushed["agentStatus"]["active"])
                    self.assertIn("Phase 1/3", pushed["agentStatus"]["stage"])
                    sse_conn.close()
                finally:
                    srv.shutdown()
                    srv.server_close()

    @unittest.skipUnless(shutil.which("node"), "Node.js required for Web UI client JS check")
    def test_web_ui_modal_closed_allows_auto_refresh_and_banner_updates(self) -> None:
        self.assertIn('<div id="modalBackdrop" class="hidden">', server.WEB_UI_HTML)

        node_script = r"""
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(0, 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
const appJs = scripts[scripts.length - 1][1];

const els = {
  modalBackdrop: { className: 'hidden', classList: { contains: (c) => els.modalBackdrop.className.split(/\s+/).includes(c) } },
  modalBox: { innerHTML: '' },
  agentActivityBanner: { className: 'hidden', innerHTML: '' },
  agentPill: { className: 'hidden', innerHTML: '' },
  mainContent: { innerHTML: 'KEEP_ME' },
  sessionSelect: { innerHTML: '', onchange: null },
  ghAuthBtn: { textContent: '' },
  inboxCountBadge: { textContent: '0' },
  progressPill: { textContent: '' },
  commentsCountBadge: { textContent: '0' },
  toastBanner: { className: 'hidden', textContent: '' },
};

let refreshCalls = 0;
const sandbox = {
  console,
  Date,
  Math,
  JSON,
  Set,
  Number,
  String,
  Boolean,
  Array,
  Object,
  URLSearchParams,
  encodeURIComponent,
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  window: {
    location: { protocol: 'file:', origin: 'http://127.0.0.1:49341', search: '' },
    sidecar: {},
    __QUASAR_INITIAL_STATE__: {
      activeSession: { sessionId: 's1', updatedAt: 'u1', analysisStatus: 'analyzing', chapters: [], threads: [] },
      sessions: [{ id: 's1', sessionId: 's1', title: 'PR #42' }],
      agentStatus: { active: false },
    },
  },
  document: {
    getElementById: (id) => els[id] || null,
  },
  fetch: async (url) => {
    if (String(url).includes('/api/state')) {
      refreshCalls++;
      return {
        json: async () => ({
          ok: true,
          activeSession: { sessionId: 's1', chapters: [], threads: [] },
          sessions: [{ id: 's1', sessionId: 's1', title: 'PR #42' }],
          agentStatus: { active: false, completedJustNow: true },
          sessionMaxMtime: 20,
        }),
      };
    }
    return { json: async () => ({ ok: true }) };
  },
  confirm: () => true,
};
sandbox.window.renderAll = () => {};
vm.createContext(sandbox);
vm.runInContext(appJs, sandbox);

if (sandbox.isModalOpen() !== false) {
  throw new Error('Expected modal to be closed on initial page load');
}
sandbox.showModal('<div>hi</div>');
if (sandbox.isModalOpen() !== true) {
  throw new Error('Expected modal to be open after showModal');
}
sandbox.closeModal();
if (sandbox.isModalOpen() !== false) {
  throw new Error('Expected modal to be closed after closeModal');
}

(async () => {
  // Seed initial mtime from first status event (modal closed)
  await sandbox.applyStatusPayload({
    ok: true,
    sessionMaxMtime: 10,
    activeSessionId: 's1',
    activeAnalysisStatus: 'analyzing',
    agentStatus: { active: false },
  });
  els.mainContent.innerHTML = 'KEEP_ME';

  // Active subagent status update without mtime increase: updates banner WITHOUT wiping mainContent
  await sandbox.applyStatusPayload({
    ok: true,
    sessionMaxMtime: 10,
    activeSessionId: 's1',
    activeAnalysisStatus: 'analyzing',
    agentStatus: {
      active: true,
      title: 'PR #42',
      stage: '🔬 Phase 2/3 — Running review lenses (1/2 complete)',
      detail: 'Bug Hunter: Reading auth.dart',
      percent: 58,
      subagents: ['Architecture & API Lens', 'Bug & Edge-Case Hunter'],
      subagentProgress: [
        { label: 'Architecture & API Lens', status: 'done', detail: 'Completed analysis' },
        { label: 'Bug & Edge-Case Hunter', status: 'running', detail: 'Reading auth.dart' },
      ],
      recentSteps: ['Fetching PR context', 'Launching Bug Hunter'],
      elapsedSeconds: 42,
      startedAt: Date.now() / 1000 - 42,
    },
  });

  if (!els.agentActivityBanner.innerHTML.includes('58%')) {
    throw new Error('Expected progress percentage in banner');
  }
  if (!els.agentActivityBanner.innerHTML.includes('Bug &amp; Edge-Case Hunter')) {
    throw new Error('Expected subagent pill in banner');
  }
  if (els.mainContent.innerHTML !== 'KEEP_ME') {
    throw new Error('Banner status update must not clobber mainContent');
  }
  if (refreshCalls !== 0) {
    throw new Error('Did not expect full /api/state refresh when sessionMaxMtime unchanged');
  }

  // Session file updated on disk -> triggers full /api/state refresh
  await sandbox.applyStatusPayload({
    ok: true,
    sessionMaxMtime: 20,
    activeSessionId: 's1',
    activeAnalysisStatus: 'complete',
    agentStatus: { active: false, completedJustNow: true, stage: '✅ Agent finished — Review updated!', elapsedSeconds: 50 },
  });
  if (refreshCalls !== 1) {
    throw new Error(`Expected 1 /api/state refresh on session mtime jump, got ${refreshCalls}`);
  }
  console.log('WEB_UI_VM_OK');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""
        proc = subprocess.run(
            ["node", "-e", node_script],
            input=server.WEB_UI_HTML,
            text=True,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}")
        self.assertIn("WEB_UI_VM_OK", proc.stdout)
