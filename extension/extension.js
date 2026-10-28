const vscode = require('vscode');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { execFile, execFileSync } = require('node:child_process');

const REVIEW_STATE_DIR = path.join(os.homedir(), '.gemini', 'quasar');
const WORKSPACES_STATE_DIR = path.join(REVIEW_STATE_DIR, 'workspaces');
const HISTORY_STATE_DIR = path.join(REVIEW_STATE_DIR, 'history');
const PREFERENCES_JSON_PATH = path.join(REVIEW_STATE_DIR, 'preferences.json');
const PREFERENCES_MD_PATH = path.join(REVIEW_STATE_DIR, 'PREFERENCES.md');
const GITHUB_AUTH_PATH = path.join(REVIEW_STATE_DIR, 'github_auth.json');
const PLUGIN_SCRIPTS_DIR = path.join(
  os.homedir(),
  '.gemini',
  'config',
  'plugins',
  'quasar',
  'skills',
  'quasar',
  'scripts'
);

// Per-window fallback identifier when a window has no workspace folders open
const EMPTY_WINDOW_FALLBACK_ROOT = path.join(
  REVIEW_STATE_DIR,
  'empty-windows',
  `window-${process.pid}-${crypto.randomBytes(4).toString('hex')}`
);

function resolveWorkspaceRootSync(dirPath) {
  if (!dirPath) return null;
  let cand;
  try {
    cand = fs.realpathSync(dirPath);
  } catch (_) {
    cand = path.resolve(dirPath);
  }
  try {
    if (fs.existsSync(cand) && fs.statSync(cand).isFile()) {
      cand = path.dirname(cand);
    }
  } catch (_) {}
  let cur = cand;
  while (true) {
    if (fs.existsSync(path.join(cur, '.git'))) {
      return cur;
    }
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  return cand;
}

function getWindowWorkspaceRoot() {
  const folders = vscode.workspace.workspaceFolders;
  if (folders && folders.length > 0 && folders[0].uri && folders[0].uri.fsPath) {
    return resolveWorkspaceRootSync(folders[0].uri.fsPath);
  }
  // If no workspace folder is open, check if an open editor belongs to a local git repo outside ~/.gemini/quasar
  const docs = [];
  if (vscode.window.activeTextEditor && vscode.window.activeTextEditor.document) {
    docs.push(vscode.window.activeTextEditor.document);
  }
  for (const doc of vscode.workspace.textDocuments || []) {
    docs.push(doc);
  }
  for (const doc of docs) {
    if (!doc || !doc.uri || doc.uri.scheme !== 'file') continue;
    const fsPath = doc.uri.fsPath;
    if (!fsPath || fsPath.startsWith(REVIEW_STATE_DIR)) continue;
    const root = resolveWorkspaceRootSync(fsPath);
    if (root && fs.existsSync(path.join(root, '.git'))) {
      return root;
    }
  }
  return EMPTY_WINDOW_FALLBACK_ROOT;
}

function computeWorkspaceKey(workspaceDir) {
  let norm;
  try {
    norm = fs.realpathSync(workspaceDir);
  } catch (_) {
    norm = path.resolve(workspaceDir);
  }
  const base =
    path
      .basename(norm)
      .replace(/[^a-zA-Z0-9._-]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'workspace';
  const digest = crypto.createHash('sha256').update(norm, 'utf8').digest('hex').slice(0, 10);
  return `${base}-${digest}`;
}

function getWindowStateDir() {
  const wsRoot = getWindowWorkspaceRoot();
  const wsKey = computeWorkspaceKey(wsRoot);
  return path.join(WORKSPACES_STATE_DIR, wsKey);
}

function getWindowActiveSessionPath() {
  return path.join(getWindowStateDir(), 'active_session.json');
}

function getWindowCliffsNotesMdPath() {
  return path.join(getWindowStateDir(), 'cliffs_notes.md');
}

function getQuasarScriptEnv() {
  const wsRoot = getWindowWorkspaceRoot();
  const stateDir = getWindowStateDir();
  return {
    ...process.env,
    QUASAR_WORKSPACE_DIR: wsRoot,
    QUASAR_STATE_DIR: stateDir,
  };
}

function loadStoredGithubAuth() {
  try {
    if (!fs.existsSync(GITHUB_AUTH_PATH)) return null;
    const data = JSON.parse(fs.readFileSync(GITHUB_AUTH_PATH, 'utf-8'));
    return data && data.token ? data : null;
  } catch (_) {
    return null;
  }
}

function runPostReviewScript(args, timeout = 30000) {
  return new Promise((resolve) => {
    const scriptPath = path.join(PLUGIN_SCRIPTS_DIR, 'post_pending_review.py');
    execFile(
      'python3',
      [scriptPath, ...args],
      { timeout, env: getQuasarScriptEnv() },
      (err, stdout, stderr) => {
        if (err) {
          let parsedErr = null;
          try {
            parsedErr = JSON.parse(stdout || '{}');
          } catch (_) {}
          resolve({
            ok: false,
            error: (parsedErr && parsedErr.error) || (stderr && stderr.trim()) || err.message,
          });
        } else {
          try {
            const parsed = JSON.parse(stdout || '{}');
            resolve({ ok: true, data: parsed, ...parsed });
          } catch (_) {
            resolve({ ok: true, data: {} });
          }
        }
      }
    );
  });
}

function loadReviewerPreferences() {
  try {
    if (!fs.existsSync(PREFERENCES_JSON_PATH)) {
      return { styleProfile: {}, rules: [], categoryStats: {}, feedbackHistory: [] };
    }
    return JSON.parse(fs.readFileSync(PREFERENCES_JSON_PATH, 'utf-8'));
  } catch (_) {
    return { styleProfile: {}, rules: [], categoryStats: {}, feedbackHistory: [] };
  }
}

function runManagePreferences(args) {
  return new Promise((resolve) => {
    const scriptPath = path.join(PLUGIN_SCRIPTS_DIR, 'manage_preferences.py');
    execFile(
      'python3',
      [scriptPath, ...args],
      { timeout: 15000, env: getQuasarScriptEnv() },
      (err, stdout, stderr) => {
        if (err) {
          resolve({ ok: false, error: stderr || err.message });
        } else {
          try {
            const parsed = JSON.parse(stdout || '{}');
            resolve({ ok: true, data: parsed, ...parsed });
          } catch (_) {
            resolve({ ok: true, data: {} });
          }
        }
      }
    );
  });
}

function runManageFixes(args) {
  return new Promise((resolve) => {
    const scriptPath = path.join(PLUGIN_SCRIPTS_DIR, 'manage_fixes.py');
    execFile(
      'python3',
      [scriptPath, ...args],
      { timeout: 15000, env: getQuasarScriptEnv() },
      (err, stdout, stderr) => {
        if (err) {
          resolve({ ok: false, error: stderr || err.message });
        } else {
          try {
            const parsed = JSON.parse(stdout || '{}');
            resolve({ ok: true, data: parsed, ...parsed });
          } catch (_) {
            resolve({ ok: true, data: {} });
          }
        }
      }
    );
  });
}

/**
 * Resolves the `agentapi` CLI wrapper script used to communicate with Antigravity.
 */
function getAgentApiBinary() {
  const geminiRoot = path.join(os.homedir(), '.gemini');
  if (fs.existsSync(geminiRoot)) {
    try {
      for (const entry of fs.readdirSync(geminiRoot, { withFileTypes: true })) {
        if (!entry.isDirectory()) continue;
        const binCand = path.join(geminiRoot, entry.name, 'bin', 'agentapi');
        if (fs.existsSync(binCand)) return binCand;
      }
    } catch (_) {}
  }
  if (
    process.env.ANTIGRAVITY_AGENTAPI_EXE &&
    process.env.ANTIGRAVITY_AGENTAPI_EXE.endsWith('agentapi') &&
    fs.existsSync(process.env.ANTIGRAVITY_AGENTAPI_EXE)
  ) {
    return process.env.ANTIGRAVITY_AGENTAPI_EXE;
  }
  return 'agentapi';
}

/**
 * Discovers `ANTIGRAVITY_LS_ADDRESS` and `ANTIGRAVITY_CSRF_TOKEN` from the running
 * Antigravity `language_server` process when called from the VS Code Extension Host
 * (which does not inherit those environment variables directly).
 */
function discoverAntigravityLsEnv(targetWorkspaceRoot = null) {
  if (process.env.ANTIGRAVITY_LS_ADDRESS && process.env.ANTIGRAVITY_CSRF_TOKEN) {
    return {
      ANTIGRAVITY_LS_ADDRESS: process.env.ANTIGRAVITY_LS_ADDRESS,
      ANTIGRAVITY_CSRF_TOKEN: process.env.ANTIGRAVITY_CSRF_TOKEN,
    };
  }

  try {
    const psOut = execFileSync('/bin/ps', ['-axww', '-o', 'pid=,command='], {
      encoding: 'utf8',
      timeout: 3000,
    });
    const procs = [];
    const normWs = targetWorkspaceRoot
      ? targetWorkspaceRoot.replace(/[^a-zA-Z0-9]/g, '_')
      : '';

    for (const rawLine of psOut.split('\n')) {
      const line = rawLine.trim();
      if (!line || !line.includes('language_server') || !line.includes('--csrf_token')) {
        continue;
      }
      const pidMatch = line.match(/^(\d+)\s+(.*)$/);
      if (!pidMatch) continue;
      const pid = Number(pidMatch[1]);
      const cmd = pidMatch[2];
      const csrfMatch = cmd.match(/--csrf_token\s+([^\s]+)/);
      if (!csrfMatch) continue;
      const csrfToken = csrfMatch[1];
      const isLspOnly = cmd.includes('--enable_lsp');
      const wsMatch = cmd.match(/--workspace_id\s+([^\s]+)/);
      const workspaceId = wsMatch ? wsMatch[1] : '';
      const matchesTargetWs = Boolean(normWs && workspaceId && workspaceId.includes(normWs));
      procs.push({ pid, csrfToken, isLspOnly, workspaceId, matchesTargetWs });
    }

    // Prioritize the main agent server (no --enable_lsp), then workspace-matching server
    procs.sort((a, b) => {
      const scoreA = (!a.isLspOnly ? 0 : 10) + (a.matchesTargetWs ? 0 : 5);
      const scoreB = (!b.isLspOnly ? 0 : 10) + (b.matchesTargetWs ? 0 : 5);
      return scoreA - scoreB;
    });

    const lsofBin = fs.existsSync('/usr/sbin/lsof') ? '/usr/sbin/lsof' : 'lsof';
    for (const proc of procs) {
      try {
        const lsofOut = execFileSync(
          lsofBin,
          ['-Pan', '-p', String(proc.pid), '-a', '-iTCP', '-sTCP:LISTEN'],
          { encoding: 'utf8', timeout: 3000 }
        );
        const listeners = [];
        for (const lLine of lsofOut.split('\n')) {
          const fdMatch = lLine.match(/\s+(\d+)u\s+IPv[46]\s+.*?(?:127\.0\.0\.1|localhost|\*):(\d+)\s+\(LISTEN\)/);
          if (fdMatch) {
            listeners.push({ fd: Number(fdMatch[1]), port: Number(fdMatch[2]) });
          }
        }
        listeners.sort((a, b) => a.fd - b.fd);
        // FD 11u (or the 2nd listening TCP port after FD 10u HTTPS) is the HTTP/gRPC agentapi port
        const grpcListener =
          listeners.find((l) => l.fd === 11) ||
          (listeners.length >= 2 ? listeners[1] : listeners[0]);
        if (grpcListener && grpcListener.port) {
          return {
            ANTIGRAVITY_LS_ADDRESS: `localhost:${grpcListener.port}`,
            ANTIGRAVITY_CSRF_TOKEN: proc.csrfToken,
          };
        }
      } catch (_) {}
    }
  } catch (_) {}

  return {};
}

/**
 * Resolves the root user-facing conversation ID via `agentapi get-conversation-metadata`
 * so we never send prompts to a finished read-only subagent conversation.
 */
function resolveRootConversationId(candId, env) {
  if (!candId || !env || !env.ANTIGRAVITY_LS_ADDRESS) return candId;
  try {
    const exe = getAgentApiBinary();
    const out = execFileSync(exe, ['get-conversation-metadata', candId], {
      env,
      encoding: 'utf8',
      timeout: 3000,
    });
    const jsonStart = out.indexOf('{');
    if (jsonStart >= 0) {
      const parsed = JSON.parse(out.slice(jsonStart));
      const meta =
        parsed &&
        parsed.response &&
        parsed.response.conversationMetadata &&
        parsed.response.conversationMetadata.metadata;
      if (meta && meta.rootConversationId) {
        return meta.rootConversationId;
      }
    }
  } catch (_) {}
  return candId;
}

/**
 * Finds the most recent active Antigravity root conversation ID belonging to the target workspace
 * (or THIS window's workspace), falling back to the most recent root conversation.
 */
function detectActiveConversationId(targetWorkspaceRoot = null, env = null) {
  let wsRoot = targetWorkspaceRoot ? resolveWorkspaceRootSync(targetWorkspaceRoot) : null;
  if (!wsRoot || wsRoot.startsWith(REVIEW_STATE_DIR)) {
    wsRoot = getWindowWorkspaceRoot();
  }
  if (!wsRoot || wsRoot.startsWith(REVIEW_STATE_DIR)) {
    const sess = loadActiveSession();
    if (sess && (sess.repoDir || sess.workspaceDir)) {
      wsRoot = resolveWorkspaceRootSync(sess.repoDir || sess.workspaceDir);
    }
  }

  const geminiRoot = path.join(os.homedir(), '.gemini');
  const brainDirs = [];
  if (fs.existsSync(geminiRoot)) {
    try {
      for (const entry of fs.readdirSync(geminiRoot, { withFileTypes: true })) {
        if (!entry.isDirectory()) continue;
        const bCand = path.join(geminiRoot, entry.name, 'brain');
        if (fs.existsSync(bCand)) brainDirs.push(bCand);
      }
    } catch (_) {}
  }

  const candidates = [];
  for (const brainDir of brainDirs) {
    try {
      const entries = fs.readdirSync(brainDir, { withFileTypes: true });
      for (const entry of entries) {
        if (!entry.isDirectory()) continue;
        const tPath = path.join(brainDir, entry.name, '.system_generated', 'logs', 'transcript.jsonl');
        if (!fs.existsSync(tPath)) continue;
        const stat = fs.statSync(tPath);
        candidates.push({ id: entry.name, tPath, mtimeMs: stat.mtimeMs });
      }
    } catch (_) {}
  }

  candidates.sort((a, b) => b.mtimeMs - a.mtimeMs);

  const inspected = [];
  for (const cand of candidates.slice(0, 25)) {
    try {
      const fd = fs.openSync(cand.tPath, 'r');
      const buf = Buffer.alloc(32768);
      const bytesRead = fs.readSync(fd, buf, 0, buf.length, 0);
      fs.closeSync(fd);
      const headText = buf.slice(0, bytesRead).toString('utf8');
      const isRootUserConv = headText.includes('<ADDITIONAL_METADATA>');
      const matchesWs = Boolean(wsRoot && !wsRoot.startsWith(REVIEW_STATE_DIR) && headText.includes(wsRoot));
      inspected.push({ ...cand, isRootUserConv, matchesWs });
    } catch (_) {}
  }

  // 1. Prefer a root user conversation matching the target workspace root
  const rootWsMatch = inspected.find((c) => c.matchesWs && c.isRootUserConv);
  if (rootWsMatch) {
    return resolveRootConversationId(rootWsMatch.id, env);
  }

  // 2. Otherwise any conversation matching the target workspace root (resolved to its rootConversationId)
  const anyWsMatch = inspected.find((c) => c.matchesWs);
  if (anyWsMatch) {
    return resolveRootConversationId(anyWsMatch.id, env);
  }

  // 3. Fallback to most recent root user conversation
  const anyRootMatch = inspected.find((c) => c.isRootUserConv);
  if (anyRootMatch) {
    return resolveRootConversationId(anyRootMatch.id, env);
  }

  return candidates.length > 0 ? resolveRootConversationId(candidates[0].id, env) : null;
}

function sendPromptToQuasar(prompt, title = 'Quasar', targetWorkspaceRoot = null) {
  return new Promise((resolve) => {
    const exe = getAgentApiBinary();
    const sess = loadActiveSession();
    const effectiveWsRoot =
      targetWorkspaceRoot ||
      (sess && (sess.workspaceDir || sess.repoDir)) ||
      getWindowWorkspaceRoot();
    const lsEnv = discoverAntigravityLsEnv(effectiveWsRoot);
    const env = {
      ...getQuasarScriptEnv(),
      ...lsEnv,
    };
    const convId = detectActiveConversationId(effectiveWsRoot, env);

    const runAgentApi = (args, cb) => {
      execFile(exe, args, { timeout: 15000, env }, (err, stdout, stderr) => {
        const outStr = (stdout || '').trim();
        const errStr = (stderr || '').trim();
        let apiErr = null;
        if (outStr.startsWith('{')) {
          try {
            const parsed = JSON.parse(outStr);
            if (parsed && parsed.error) {
              apiErr = String(parsed.error);
            }
          } catch (_) {}
        }
        if (err || apiErr) {
          cb({
            success: false,
            error: apiErr || (err && err.message) || errStr || 'agentapi failed',
            stderr: errStr,
          });
        } else {
          cb({ success: true, stdout: outStr, conversationId: convId });
        }
      });
    };

    if (convId) {
      runAgentApi(['send-message', `--title=${title}`, convId, prompt], (res) => {
        if (res.success) {
          resolve(res);
        } else {
          // Fallback to starting a new conversation if send-message to convId failed
          runAgentApi(['new-conversation', `--title=${title}`, prompt], resolve);
        }
      });
    } else {
      runAgentApi(['new-conversation', `--title=${title}`, prompt], resolve);
    }
  });
}

function loadActiveSession() {
  try {
    const sessionPath = getWindowActiveSessionPath();
    if (!fs.existsSync(sessionPath)) return null;
    const raw = fs.readFileSync(sessionPath, 'utf-8');
    const session = JSON.parse(raw);
    // Verify session belongs to this window's state directory or workspace
    if (session && session.workspaceDir) {
      const myRoot = getWindowWorkspaceRoot();
      if (
        myRoot &&
        !myRoot.startsWith(REVIEW_STATE_DIR) &&
        resolveWorkspaceRootSync(session.workspaceDir) !== myRoot
      ) {
        return null;
      }
    }
    return session;
  } catch (_) {
    return null;
  }
}

function getSessionArchiveId(session) {
  if (!session || typeof session !== 'object') return null;
  let rawId = String(session.sessionId || '').trim();
  if (!rawId) {
    const pr = session.pr || {};
    if (pr.number) {
      rawId = `${pr.owner || 'gh'}-${pr.repo || 'repo'}-pr-${pr.number}`;
    } else if (pr.headRefName) {
      rawId = `local-${pr.repo || 'repo'}-${pr.headRefName}`;
    }
  }
  const safeId = rawId.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
  return safeId || null;
}

function archiveSessionToHistory(session, mdSourcePath = null) {
  try {
    const safeId = getSessionArchiveId(session);
    if (!safeId) return;
    const histDir = path.join(HISTORY_STATE_DIR, safeId);
    fs.mkdirSync(histDir, { recursive: true });
    fs.writeFileSync(path.join(histDir, 'session.json'), JSON.stringify(session, null, 2), 'utf-8');
    const srcMd = mdSourcePath || getWindowCliffsNotesMdPath();
    if (srcMd && fs.existsSync(srcMd)) {
      try {
        fs.copyFileSync(srcMd, path.join(histDir, 'cliffs_notes.md'));
      } catch (_) {}
    }
  } catch (_) {}
}

function listPastReviews() {
  const byId = new Map();

  const recordCandidate = (sessionPath, mdPath) => {
    if (!fs.existsSync(sessionPath)) return;
    try {
      const stat = fs.statSync(sessionPath);
      const session = JSON.parse(fs.readFileSync(sessionPath, 'utf-8'));
      const safeId = getSessionArchiveId(session);
      if (!safeId) return;
      const updatedMs = session.updatedAt ? Date.parse(session.updatedAt) || stat.mtimeMs : stat.mtimeMs;
      const existing = byId.get(safeId);
      if (!existing || updatedMs >= existing.updatedMs) {
        byId.set(safeId, {
          id: safeId,
          session,
          sessionPath,
          mdPath: fs.existsSync(mdPath) ? mdPath : null,
          updatedMs,
        });
      }
    } catch (_) {}
  };

  if (fs.existsSync(HISTORY_STATE_DIR)) {
    try {
      for (const entry of fs.readdirSync(HISTORY_STATE_DIR, { withFileTypes: true })) {
        if (!entry.isDirectory()) continue;
        const dir = path.join(HISTORY_STATE_DIR, entry.name);
        recordCandidate(path.join(dir, 'session.json'), path.join(dir, 'cliffs_notes.md'));
      }
    } catch (_) {}
  }

  if (fs.existsSync(WORKSPACES_STATE_DIR)) {
    try {
      for (const entry of fs.readdirSync(WORKSPACES_STATE_DIR, { withFileTypes: true })) {
        if (!entry.isDirectory()) continue;
        const dir = path.join(WORKSPACES_STATE_DIR, entry.name);
        recordCandidate(path.join(dir, 'active_session.json'), path.join(dir, 'cliffs_notes.md'));
      }
    } catch (_) {}
  }

  return Array.from(byId.values()).sort((a, b) => b.updatedMs - a.updatedMs);
}

function saveActiveSession(session) {
  try {
    const stateDir = getWindowStateDir();
    fs.mkdirSync(stateDir, { recursive: true });
    session.updatedAt = new Date().toISOString();
    session.workspaceDir = session.workspaceDir || getWindowWorkspaceRoot();
    session.stateDir = stateDir;
    fs.writeFileSync(getWindowActiveSessionPath(), JSON.stringify(session, null, 2), 'utf-8');
    archiveSessionToHistory(session, getWindowCliffsNotesMdPath());
  } catch (err) {
    vscode.window.showErrorMessage(`Failed to save review session: ${err.message}`);
  }
}

/**
 * Checks whether a document URI belongs to a given relative path in the active PR session
 * (matches Head snapshot, Base snapshot, or Local Workspace checkout).
 */
function matchesSessionPath(docFsPath, relPath, session) {
  if (!docFsPath || !relPath || !session) return false;
  const normalizedDoc = path.resolve(docFsPath);
  const headPath = session.headSnapshotRoot ? path.resolve(session.headSnapshotRoot, relPath) : '';
  const basePath = session.baseSnapshotRoot ? path.resolve(session.baseSnapshotRoot, relPath) : '';
  const repoPath = session.repoDir ? path.resolve(session.repoDir, relPath) : '';
  return (
    normalizedDoc === headPath ||
    normalizedDoc === basePath ||
    normalizedDoc === repoPath ||
    normalizedDoc.endsWith(path.sep + relPath)
  );
}

/**
 * Infers the PR relative file path from an open editor's document fsPath.
 */
function inferRelPathFromDocument(docFsPath, session) {
  if (!docFsPath || !session) return null;
  const normalizedDoc = path.resolve(docFsPath);
  for (const root of [session.headSnapshotRoot, session.baseSnapshotRoot, session.repoDir]) {
    if (root && normalizedDoc.startsWith(path.resolve(root) + path.sep)) {
      return path.relative(path.resolve(root), normalizedDoc).split(path.sep).join('/');
    }
  }
  return null;
}

/**
 * Flatten all tour stops across chapters in reading order.
 */
function getAllTourStops(session) {
  if (!session || !Array.isArray(session.chapters)) return [];
  const stops = [];
  for (const ch of session.chapters) {
    for (const stop of ch.stops || []) {
      stops.push({ ...stop, chapterTitle: ch.title, chapterId: ch.id });
    }
  }
  return stops;
}

/**
 * Matches a flowchart node label or Character List symbol to the most relevant Tour Stop.
 */
function findBestTourStopForQuery(session, queryText) {
  const stops = getAllTourStops(session);
  if (stops.length === 0 || !queryText) return null;
  const q = String(queryText || '').trim();
  const qLower = q.toLowerCase();

  const rawTokens = q
    .split(/[^A-Za-z0-9_./-]+/)
    .map((t) => t.trim())
    .filter((t) => t.length >= 3);

  let bestStop = null;
  let bestScore = 0;

  for (const stop of stops) {
    let score = 0;
    const stopPath = String(stop.path || '');
    const stopPathLower = stopPath.toLowerCase();
    const baseName = path.basename(stopPathLower);
    const stemName = baseName.replace(/\.[^.]+$/, '');
    const hay = `${stop.title || ''} ${stop.cliffsNotes || ''} ${stop.description || ''} ${
      stop.whyItChanged || ''
    } ${stop.callersAndInvariants || ''} ${stopPath}`.toLowerCase();

    if (baseName && qLower.includes(baseName)) {
      score += 120;
    } else if (stemName && stemName.length >= 4 && qLower.includes(stemName)) {
      score += 85;
    }

    for (const tok of rawTokens) {
      const tokLower = tok.toLowerCase();
      if (tokLower.includes('/') && stopPathLower.includes(tokLower.replace(/^\//, ''))) {
        score += 95;
      } else if (baseName === tokLower || stemName === tokLower) {
        score += 90;
      } else if (stopPathLower.includes(tokLower) && tokLower.length >= 4) {
        score += 45;
      } else if (hay.includes(tokLower) && tokLower.length >= 4) {
        score += 28;
      }
    }

    if (score > bestScore) {
      bestScore = score;
      bestStop = stop;
    }
  }

  return bestScore >= 25 ? bestStop : null;
}

function runGit(args, cwd) {
  return new Promise((resolve) => {
    execFile('git', args, { cwd, timeout: 10000 }, (err, stdout, stderr) => {
      resolve({
        ok: !err,
        stdout: (stdout || '').trim(),
        stderr: (stderr || '').trim(),
      });
    });
  });
}

async function resolveGitRoot(dirPath) {
  if (!dirPath || !fs.existsSync(dirPath)) return null;
  const res = await runGit(['rev-parse', '--show-toplevel'], dirPath);
  return res.ok && res.stdout ? res.stdout : null;
}

async function detectWorkspaceRepoDir() {
  const activeEditor = vscode.window.activeTextEditor;
  if (activeEditor && activeEditor.document && activeEditor.document.uri.scheme === 'file') {
    const docDir = path.dirname(activeEditor.document.uri.fsPath);
    const root = await resolveGitRoot(docDir);
    if (root) return root;
  }

  const folders = vscode.workspace.workspaceFolders || [];
  for (const folder of folders) {
    const root = await resolveGitRoot(folder.uri.fsPath);
    if (root) return root;
  }

  const session = loadActiveSession();
  if (session && session.repoDir) {
    const root = await resolveGitRoot(session.repoDir);
    if (root) return root;
  }
  return null;
}

function discoverLocalGitRepos() {
  const found = new Set();
  const folders = vscode.workspace.workspaceFolders || [];
  for (const f of folders) {
    if (fs.existsSync(path.join(f.uri.fsPath, '.git'))) {
      found.add(path.resolve(f.uri.fsPath));
    }
  }
  const home = os.homedir();
  for (const sub of ['work', 'dev', 'src', 'projects']) {
    const dir = path.join(home, sub);
    if (!fs.existsSync(dir)) continue;
    try {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        if (!entry.isDirectory()) continue;
        const cand = path.join(dir, entry.name);
        if (fs.existsSync(path.join(cand, '.git'))) {
          found.add(path.resolve(cand));
        }
      }
    } catch (_) {}
  }
  return Array.from(found);
}

// ============================================================================
// 1. TreeDataProvider: CliffsNotes & Guided Reading Tour
// ============================================================================
class CliffsNotesTreeProvider {
  constructor() {
    this._onDidChangeTreeData = new vscode.EventEmitter();
    this.onDidChangeTreeData = this._onDidChangeTreeData.event;
  }

  refresh() {
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element) {
    return element;
  }

  getChildren(element) {
    const session = loadActiveSession();
    const pastReviews = listPastReviews();

    if (element && element.nodeType === 'past-reviews-root') {
      const currentId = session ? getSessionArchiveId(session) : null;
      return (element.pastReviews || pastReviews).map((entry) => {
        const s = entry.session || {};
        const p = s.pr || {};
        const isCurrent = Boolean(currentId && entry.id === currentId);
        const label = p.number
          ? `${isCurrent ? '● ' : ''}PR #${p.number}: ${p.title || entry.id}`
          : `${isCurrent ? '● ' : ''}${p.title || `Branch: ${p.headRefName || entry.id}`}`;
        const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.None);
        const dateStr = entry.updatedMs
          ? new Date(entry.updatedMs).toLocaleDateString(undefined, {
              month: 'short',
              day: 'numeric',
            })
          : '';
        item.description = `${p.repo || ''}${dateStr ? ` • ${dateStr}` : ''}${
          isCurrent ? ' (Active)' : ''
        }`;
        item.iconPath = new vscode.ThemeIcon(
          isCurrent ? 'pass-filled' : p.number ? 'git-pull-request' : 'git-branch'
        );
        const stopsCount = (s.chapters || []).reduce(
          (acc, ch) => acc + ((ch.stops || []).length || 0),
          0
        );
        const threadsCount = (s.threads || []).filter((t) => t.status !== 'dismissed').length;
        item.tooltip = new vscode.MarkdownString(
          `### 🕘 ${label}\n\n` +
            `**Repo**: \`${p.owner ? `${p.owner}/` : ''}${p.repo || 'local'}\`  |  ` +
            `**Tour Stops**: ${stopsCount}  |  **Comments**: ${threadsCount}\n\n` +
            (s.cliffsNotes && s.cliffsNotes.tldr ? `${s.cliffsNotes.tldr}\n\n` : '') +
            `*Click to load this review session into the current window.*`
        );
        item.command = {
          command: 'quasar.switchPastReview',
          title: 'Load Past Review Session',
          arguments: [entry],
        };
        return item;
      });
    }

    if (!session) {
      const prItem = new vscode.TreeItem(
        'Review GitHub PR from URL...',
        vscode.TreeItemCollapsibleState.None
      );
      prItem.command = {
        command: 'quasar.loadPrFromUrl',
        title: 'Review PR from GitHub URL',
      };
      prItem.iconPath = new vscode.ThemeIcon('git-pull-request');

      const branchItem = new vscode.TreeItem(
        'Review Local Branch or Diff...',
        vscode.TreeItemCollapsibleState.None
      );
      branchItem.command = {
        command: 'quasar.reviewLocalBranch',
        title: 'Review Local Branch or Diff',
      };
      branchItem.iconPath = new vscode.ThemeIcon('git-branch');

      const emptyRootItems = [prItem, branchItem];
      if (pastReviews.length > 0) {
        const pastNode = new vscode.TreeItem(
          `Past Reviews (${pastReviews.length})`,
          vscode.TreeItemCollapsibleState.Expanded
        );
        pastNode.iconPath = new vscode.ThemeIcon('history');
        pastNode.nodeType = 'past-reviews-root';
        pastNode.pastReviews = pastReviews;
        emptyRootItems.push(pastNode);
      }
      return emptyRootItems;
    }

    if (!element) {
      const rootItems = [];
      const pr = session.pr || {};
      const isLocal = !pr.number;
      const prLabel = pr.number
        ? `PR #${pr.number}: ${pr.title || ''}`
        : pr.title || `Branch: ${pr.headRefName || 'HEAD'}`;
      const headerItem = new vscode.TreeItem(prLabel, vscode.TreeItemCollapsibleState.None);
      headerItem.description = isLocal
        ? `${pr.headRefName || 'HEAD'} → ${pr.baseRefName || 'main'} • +${pr.additions || 0}/-${pr.deletions || 0}`
        : `@${pr.author || 'author'} • +${pr.additions || 0}/-${pr.deletions || 0}`;
      headerItem.iconPath = new vscode.ThemeIcon(
        session.analysisStatus === 'analyzing'
          ? 'sync~spin'
          : isLocal
          ? 'git-branch'
          : 'book'
      );
      const mdTooltip = new vscode.MarkdownString(
        `### 📖 ${prLabel}\n\n` +
          (isLocal
            ? `**Branch**: \`${pr.headRefName || 'HEAD'}\` → \`${pr.baseRefName || 'main'}\`  |  **Repo**: \`${pr.repo || ''}\`  |  `
            : `**Author**: \`@${pr.author || 'unknown'}\`  |  `) +
          `**Changes**: \`+${pr.additions || 0} / -${pr.deletions || 0}\`\n\n` +
          `${(session.cliffsNotes && session.cliffsNotes.tldr) || ''}\n\n` +
          `*Click to open the full CliffsNotes Study Guide beside your editor.*`
      );
      headerItem.tooltip = mdTooltip;
      headerItem.contextValue = 'pr-header';
      headerItem.command = {
        command: 'quasar.openCliffsNotesGuide',
        title: 'Open CliffsNotes Study Guide',
      };
      rootItems.push(headerItem);

      if (pr.isOwnedByUser && pr.needsBranchSwitch && pr.headRefName) {
        const switchItem = new vscode.TreeItem(
          `Switch Local Repo to PR Branch '${pr.headRefName}'...`,
          vscode.TreeItemCollapsibleState.None
        );
        switchItem.description = `Currently on '${pr.localCurrentBranch || 'another branch'}'`;
        switchItem.iconPath = new vscode.ThemeIcon('git-branch');
        switchItem.tooltip = new vscode.MarkdownString(
          `### 🔀 Switch Local Repository to PR Branch\n\n` +
            `This PR (#${pr.number}) belongs to you, and your local clone (\`${
              session.repoDir || ''
            }\`) is currently on branch \`${pr.localCurrentBranch || 'unknown'}\`.\n\n` +
            `*Click to check out branch \`${pr.headRefName}\` in your local repository.*`
        );
        switchItem.command = {
          command: 'quasar.checkoutPrBranch',
          title: 'Switch Local Repo to PR Branch',
        };
        rootItems.push(switchItem);
      }

      // At-a-Glance CliffsNotes Summary node
      const summaryNode = new vscode.TreeItem(
        'At-a-Glance CliffsNotes',
        vscode.TreeItemCollapsibleState.Expanded
      );
      summaryNode.iconPath = new vscode.ThemeIcon('sparkle');
      summaryNode.contextValue = 'cliffs-summary-root';
      summaryNode.nodeType = 'summary-root';
      rootItems.push(summaryNode);

      // Chapters
      for (const ch of session.chapters || []) {
        const stops = ch.stops || [];
        const readCount = stops.filter((s) => s.read).length;
        const chItem = new vscode.TreeItem(
          ch.title,
          ch.category === 'generated_or_fixture'
            ? vscode.TreeItemCollapsibleState.Collapsed
            : vscode.TreeItemCollapsibleState.Expanded
        );
        chItem.description = `${readCount}/${stops.length} stops`;
        chItem.iconPath = new vscode.ThemeIcon(
          readCount === stops.length && stops.length > 0 ? 'pass-filled' : 'compass'
        );
        const chTooltip = new vscode.MarkdownString(`### ${ch.title}\n\n${ch.summary || ''}\n\n`);
        for (const b of ch.cliffsNotesBullets || []) {
          chTooltip.appendMarkdown(`- ${b}\n`);
        }
        chItem.tooltip = chTooltip;
        chItem.nodeType = 'chapter';
        chItem.chapterData = ch;
        rootItems.push(chItem);
      }

      if (pastReviews.length > 0) {
        const pastNode = new vscode.TreeItem(
          `Past Reviews (${pastReviews.length})`,
          vscode.TreeItemCollapsibleState.Collapsed
        );
        pastNode.description = 'Switch to any previous PR or branch review';
        pastNode.iconPath = new vscode.ThemeIcon('history');
        pastNode.nodeType = 'past-reviews-root';
        pastNode.pastReviews = pastReviews;
        rootItems.push(pastNode);
      }

      const closeNode = new vscode.TreeItem(
        'Close Active Review',
        vscode.TreeItemCollapsibleState.None
      );
      closeNode.description = 'Clear comments, CodeLenses, hover cards & diff tabs';
      closeNode.iconPath = new vscode.ThemeIcon('close');
      closeNode.tooltip = new vscode.MarkdownString(
        '### 🧹 Close Active Review\n\n' +
          'Closes this review session in the IDE, removing all inline comment threads, CodeLenses, hover cards, and snapshot diff tabs.\n\n' +
          '*Your review is automatically saved under **Past Reviews** so you can reopen it anytime.*'
      );
      closeNode.command = {
        command: 'quasar.closeReview',
        title: 'Close Active Review & Clear IDE Annotations',
      };
      rootItems.push(closeNode);

      return rootItems;
    }

    if (element.nodeType === 'summary-root') {
      const cn = session.cliffsNotes || {};
      const items = [];

      const tldrItem = new vscode.TreeItem(
        `TL;DR: ${cn.tldr || cn.whatAndWhy || 'Synthesizing summary...'}`,
        vscode.TreeItemCollapsibleState.None
      );
      tldrItem.iconPath = new vscode.ThemeIcon('lightbulb');
      tldrItem.tooltip = new vscode.MarkdownString(`### ⚡ What & Why\n\n${cn.whatAndWhy || cn.tldr || ''}`);
      tldrItem.command = {
        command: 'quasar.openCliffsNotesGuide',
        title: 'Open Full CliffsNotes',
      };
      items.push(tldrItem);

      if (cn.architectureDiagram && String(cn.architectureDiagram).trim()) {
        const archItem = new vscode.TreeItem(
          'Architecture & Data Flow Flowchart',
          vscode.TreeItemCollapsibleState.None
        );
        archItem.description = 'Interactive diagram • click to open';
        archItem.iconPath = new vscode.ThemeIcon('type-hierarchy-sub');
        archItem.tooltip = new vscode.MarkdownString(
          `### 🧭 Architecture & Data Flow\n\n*Click to open the interactive Architecture Flowchart in the CliffsNotes Study Guide.*`
        );
        archItem.command = {
          command: 'quasar.openCliffsNotesGuide',
          title: 'Open Interactive Architecture Flowchart',
          arguments: ['architecture-section'],
        };
        items.push(archItem);
      }

      if (Array.isArray(cn.keySymbols) && cn.keySymbols.length > 0) {
        const symRoot = new vscode.TreeItem(
          `Character List (${cn.keySymbols.length} Key Symbols)`,
          vscode.TreeItemCollapsibleState.Collapsed
        );
        symRoot.iconPath = new vscode.ThemeIcon('symbol-class');
        symRoot.nodeType = 'symbols-list';
        symRoot.symbols = cn.keySymbols;
        items.push(symRoot);
      }

      if (Array.isArray(cn.designTradeoffs) && cn.designTradeoffs.length > 0) {
        const tdRoot = new vscode.TreeItem(
          `Key Design Trade-offs (${cn.designTradeoffs.length})`,
          vscode.TreeItemCollapsibleState.Collapsed
        );
        tdRoot.iconPath = new vscode.ThemeIcon('scales');
        tdRoot.nodeType = 'tradeoffs-list';
        tdRoot.tradeoffs = cn.designTradeoffs;
        items.push(tdRoot);
      }

      return items;
    }

    if (element.nodeType === 'symbols-list') {
      return (element.symbols || []).map((sym) => {
        const item = new vscode.TreeItem(sym.name, vscode.TreeItemCollapsibleState.None);
        const matchedStop = findBestTourStopForQuery(session, `${sym.name || ''} ${sym.role || ''}`);
        item.description = matchedStop
          ? `[${matchedStop.stopNumber}] ${sym.role || ''}`
          : sym.role;
        item.iconPath = new vscode.ThemeIcon('symbol-method');
        item.tooltip = new vscode.MarkdownString(
          `**\`${sym.name}\`**\n\n${sym.role || ''}${
            matchedStop
              ? `\n\n---\n*Click to jump to Stop ${matchedStop.stopNumber} (\`${path.basename(
                  matchedStop.path || ''
                )}:L${matchedStop.startLine}\`) in Diff Editor.*`
              : '\n\n---\n*Click to open CliffsNotes Study Guide.*'
          }`
        );
        item.command = matchedStop
          ? {
              command: 'quasar.openTourStop',
              title: 'Jump to Symbol Tour Stop in Diff',
              arguments: [matchedStop],
            }
          : {
              command: 'quasar.openCliffsNotesGuide',
              title: 'Open CliffsNotes Study Guide',
              arguments: ['symbols-section'],
            };
        return item;
      });
    }

    if (element.nodeType === 'tradeoffs-list') {
      return (element.tradeoffs || []).map((td) => {
        const item = new vscode.TreeItem(td, vscode.TreeItemCollapsibleState.None);
        item.iconPath = new vscode.ThemeIcon('question');
        item.tooltip = new vscode.MarkdownString(td);
        return item;
      });
    }

    if (element.nodeType === 'chapter') {
      const ch = element.chapterData;
      const children = [];

      // Chapter CliffsNotes collapsible sub-node so notes are easy to scan without cluttering code
      if (Array.isArray(ch.cliffsNotesBullets) && ch.cliffsNotesBullets.length > 0) {
        const notesNode = new vscode.TreeItem(
          'Chapter CliffsNotes',
          vscode.TreeItemCollapsibleState.Collapsed
        );
        notesNode.description = ch.summary || '';
        notesNode.iconPath = new vscode.ThemeIcon('notebook');
        notesNode.nodeType = 'chapter-bullets';
        notesNode.bullets = ch.cliffsNotesBullets;
        const tip = new vscode.MarkdownString(`### 📘 ${ch.title} — CliffsNotes\n\n`);
        for (const b of ch.cliffsNotesBullets) {
          tip.appendMarkdown(`- ${b}\n`);
        }
        notesNode.tooltip = tip;
        children.push(notesNode);
      }

      for (const stop of ch.stops || []) {
        const stopItem = new vscode.TreeItem(
          `[${stop.stopNumber}] ${stop.title}`,
          vscode.TreeItemCollapsibleState.None
        );
        stopItem.description = stop.cliffsNotes || stop.description || '';
        stopItem.iconPath = new vscode.ThemeIcon(stop.read ? 'pass' : 'circle-large-outline');
        const tip = new vscode.MarkdownString(
          `### 📍 Stop ${stop.stopNumber}: \`${stop.path}\` (L${stop.startLine}–${stop.endLine})\n\n` +
            `**CliffsNotes**: ${stop.cliffsNotes || stop.description || ''}\n\n` +
            (stop.whyItChanged ? `**Why it changed**: ${stop.whyItChanged}\n\n` : '') +
            (stop.callersAndInvariants
              ? `**Context & Invariants**: ${stop.callersAndInvariants}\n\n`
              : '') +
            `*Click to open native side-by-side Diff at L${stop.startLine}.*`
        );
        stopItem.tooltip = tip;
        stopItem.command = {
          command: 'quasar.openTourStop',
          title: 'Open Tour Stop in Diff Editor',
          arguments: [stop],
        };
        children.push(stopItem);
      }
      return children;
    }

    if (element.nodeType === 'chapter-bullets') {
      return (element.bullets || []).map((b) => {
        const item = new vscode.TreeItem(b, vscode.TreeItemCollapsibleState.None);
        item.iconPath = new vscode.ThemeIcon('dash');
        item.tooltip = new vscode.MarkdownString(b);
        return item;
      });
    }

    return [];
  }
}

/**
 * Computes or returns the Quasar triage assessment for an existing GitHub PR comment thread:
 * - `recommended_fix`: Quasar thinks the comment is good and the suggested fix should be made (`🛠️ Should Fix`)
 * - `needs_attention`: Requires looking at — a question that needs answering or a design discussion (`❓ Question` / `👀 Needs Look`)
 * - `addressed_or_refuted`: Author already replied or refuted the premise (`⚠️ Disputed` / `💬 Replied`)
 */
function inferExistingCommentTriage(t, session) {
  if (!t || t.kind !== 'github_existing') return null;
  if (t.triageStatus && t.triageBadge && t.triageLabel) {
    return {
      triageStatus: t.triageStatus,
      triageCategory: t.triageCategory || t.triageStatus,
      triageBadge: t.triageBadge,
      triageLabel: t.triageLabel,
      triageSummary: t.triageSummary || t.whyItMatters || '',
      recommendedForFix:
        typeof t.recommendedForFix === 'boolean'
          ? t.recommendedForFix
          : t.triageStatus === 'recommended_fix',
    };
  }

  const prAuthor = (((session && session.pr && session.pr.author) || '') + '').trim().toLowerCase();
  const rawBody = ((t.draftComment || '') + '').trim();
  const lower = rawBody.toLowerCase();
  const msgs = (t.messages || []).filter((m) => !m.isTriageNote);
  const authorReplies = msgs.slice(1).filter((m) => {
    const a = ((m.author || '') + '').split(' ')[0].replace(/^@/, '').toLowerCase();
    return prAuthor && a === prAuthor;
  });

  if (authorReplies.length > 0) {
    const lastReply = ((authorReplies[authorReplies.length - 1].body || '') + '').trim();
    const replyLower = lastReply.toLowerCase();
    if (
      ['mistaken', 'premise', 'intentionally', 'by design', 'does not', "doesn't", 'not needed', 'false positive'].some(
        (kw) => replyLower.includes(kw)
      )
    ) {
      return {
        triageStatus: 'addressed_or_refuted',
        triageCategory: 'refuted',
        triageBadge: '⚠️ Disputed / No Fix',
        triageLabel: '⚠️ Premise Disputed by Author — Do Not Apply',
        triageSummary: `PR author replied explaining why this comment's premise does not apply.`,
        recommendedForFix: false,
      };
    }
    return {
      triageStatus: 'addressed_or_refuted',
      triageCategory: 'replied',
      triageBadge: '💬 Author Replied',
      triageLabel: '💬 Author Replied in Thread',
      triageSummary: 'PR author has already replied to this comment thread.',
      recommendedForFix: false,
    };
  }

  if (lower.includes('```suggestion')) {
    return {
      triageStatus: 'recommended_fix',
      triageCategory: 'suggestion',
      triageBadge: '🛠️ Good Fix (Suggestion)',
      triageLabel: '🛠️ Good Comment — Suggested Fix Should Be Made',
      triageSummary: 'Contains a concrete code suggestion block ready to apply.',
      recommendedForFix: true,
    };
  }

  const bugKeywords = [
    'warning:',
    'warnings-as-errors',
    'typecheck',
    'shadows',
    'shadowing',
    'same scope',
    'emits both',
    'twice in the same',
    'never released',
    'outside the try/finally',
    'out of sync',
    'double-free',
    'completionhandler',
  ];
  if (bugKeywords.some((kw) => lower.includes(kw))) {
    return {
      triageStatus: 'recommended_fix',
      triageCategory: 'bug_or_edge_case',
      triageBadge: '🛠️ Good Catch — Should Fix',
      triageLabel: '🛠️ Good Comment — Suggested Fix Should Be Made',
      triageSummary: 'Identifies a valid bug, edge case, or compiler warning; Quasar recommends making this fix.',
      recommendedForFix: true,
    };
  }

  const isDesignDiscussion = [
    "i don't know if i like",
    'scatters the code',
    'go in there own file',
    'go in their own file',
    'in its own file',
    'what do you think',
    'thoughts on',
  ].some((p) => lower.includes(p));
  const hasQuestion = rawBody.includes('?');
  const isPoliteNitOrDoc =
    [
      'can we get docs',
      'can we add a comment',
      'could we add a comment',
      'needs a comment',
      'more descriptive name',
      'prefer this be an if',
      'small nit',
    ].some((p) => lower.includes(p)) ||
    lower.startsWith('nit:') ||
    lower.startsWith('nit ');

  if (isDesignDiscussion) {
    return {
      triageStatus: 'needs_attention',
      triageCategory: hasQuestion ? 'question' : 'discussion',
      triageBadge: hasQuestion ? '❓ Question — Needs Answer' : '👀 Needs Look (Design)',
      triageLabel: hasQuestion
        ? '❓ Question — Requires Your Answer or Decision'
        : '👀 Needs Attention — Design Discussion',
      triageSummary: hasQuestion
        ? 'Asks an architectural/design question that needs an answer or decision.'
        : 'Raises a design/structure discussion that requires looking at.',
      recommendedForFix: false,
    };
  }

  if (hasQuestion && !isPoliteNitOrDoc) {
    if (lower.includes('is this ') && lower.includes('necessary')) {
      return {
        triageStatus: 'recommended_fix',
        triageCategory: 'cleanup_question',
        triageBadge: '🛠️ Good Catch / ❓ Question',
        triageLabel: '🛠️ Good Comment — Suggested Simplification (or Reply)',
        triageSummary: 'Asks whether this code is redundant; can be simplified directly as suggested.',
        recommendedForFix: true,
      };
    }
    return {
      triageStatus: 'needs_attention',
      triageCategory: 'question',
      triageBadge: '❓ Question — Needs Answer',
      triageLabel: '❓ Question — Requires Your Attention / Answer',
      triageSummary: 'Asks a question on this line that requires looking at and answering.',
      recommendedForFix: false,
    };
  }

  if (
    isPoliteNitOrDoc ||
    ['i think this', 'should be', 'prefer ', 'consider ', 'nit', 'readability', 'comment', 'docs', 'rename'].some((kw) =>
      lower.includes(kw)
    )
  ) {
    return {
      triageStatus: 'recommended_fix',
      triageCategory: 'actionable_improvement',
      triageBadge: '🛠️ Good Comment — Should Fix',
      triageLabel: '🛠️ Good Comment — Suggested Fix Should Be Made',
      triageSummary: 'Clear, actionable improvement suggested by reviewer; Quasar recommends making this fix.',
      recommendedForFix: true,
    };
  }

  return {
    triageStatus: 'needs_attention',
    triageCategory: 'review_comment',
    triageBadge: '👀 Needs Attention',
    triageLabel: '👀 Needs Attention — Review Comment',
    triageSummary: 'Existing GitHub review comment that requires looking at.',
    recommendedForFix: false,
  };
}

// ============================================================================
// 2. TreeDataProvider: Review Comments & Staging Cart
// ============================================================================
class CommentsTreeProvider {
  constructor() {
    this._onDidChangeTreeData = new vscode.EventEmitter();
    this.onDidChangeTreeData = this._onDidChangeTreeData.event;
  }

  refresh() {
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element) {
    return element;
  }

  getChildren(element) {
    const session = loadActiveSession();
    if (!session) return [];
    const threads = (session.threads || []).filter((t) => t.status !== 'dismissed');
    const pr = session.pr || {};
    const isOwned = pr.isOwnedByUser !== false;

    if (!element) {
      let didAutoStage = false;
      for (const t of threads) {
        if (t.kind === 'ai_candidate' && t.status === 'candidate' && !t.unstagedByUser) {
          t.status = 'staged';
          didAutoStage = true;
        }
      }
      if (didAutoStage) {
        saveActiveSession(session);
      }

      const activeThreads = threads.filter((t) => t.status !== 'dismissed');
      const rootNodes = [];
      const queuedForFix = activeThreads.filter((t) => t.selectedForFix && t.status !== 'fixed');
      const fixedThreads = activeThreads.filter((t) => t.status === 'fixed');
      const fixableCount = activeThreads.filter((t) => t.status !== 'fixed').length;

      if ((isOwned || queuedForFix.length > 0) && fixableCount > 0) {
        const fixActionNode = new vscode.TreeItem(
          queuedForFix.length > 0
            ? `Fix ${queuedForFix.length} Checked Comment(s) with Quasar Agent...`
            : 'Author Self-Review: Select & Fix Comments with Agent...',
          vscode.TreeItemCollapsibleState.None
        );
        fixActionNode.description =
          queuedForFix.length > 0
            ? 'Click to dispatch checked comments to Quasar'
            : 'Check boxes below or click to pick issues to fix';
        fixActionNode.iconPath = new vscode.ThemeIcon('wrench');
        fixActionNode.tooltip = new vscode.MarkdownString(
          `### 🔧 Fix Review Comments with Quasar Agent\n\n` +
            `Check the boxes next to any Quasar findings or existing GitHub PR comments below, then click here to have Quasar fix them in \`${session.repoDir || 'your local repo'}\`.`
        );
        fixActionNode.command = {
          command: 'quasar.fixSelectedComments',
          title: 'Fix Selected Comments with Agent',
        };
        rootNodes.push(fixActionNode);
      }

      const uncertain = activeThreads.filter((t) => t.status === 'uncertain');
      const staged = activeThreads.filter((t) => t.status === 'staged');
      const candidates = activeThreads.filter(
        (t) =>
          t.kind === 'ai_candidate' &&
          t.status !== 'staged' &&
          t.status !== 'uncertain' &&
          t.status !== 'fixed'
      );
      const qaThreads = activeThreads.filter(
        (t) => t.kind === 'user_qa' && t.status !== 'staged' && t.status !== 'fixed'
      );
      const existingGhRaw = activeThreads.filter(
        (t) => t.kind === 'github_existing' && t.status !== 'fixed'
      );
      const statusPriority = {
        recommended_fix: 0,
        needs_attention: 1,
        addressed_or_refuted: 2,
      };
      const existingGh = [...existingGhRaw].sort((a, b) => {
        const ta = inferExistingCommentTriage(a, session);
        const tb = inferExistingCommentTriage(b, session);
        const pa = statusPriority[(ta && ta.triageStatus) || 'needs_attention'] ?? 1;
        const pb = statusPriority[(tb && tb.triageStatus) || 'needs_attention'] ?? 1;
        return pa - pb;
      });
      let ghShouldFixCount = 0;
      let ghNeedsLookCount = 0;
      let ghRepliedCount = 0;
      for (const t of existingGh) {
        const tr = inferExistingCommentTriage(t, session);
        if (tr && tr.triageStatus === 'recommended_fix') ghShouldFixCount++;
        else if (tr && tr.triageStatus === 'addressed_or_refuted') ghRepliedCount++;
        else ghNeedsLookCount++;
      }
      const existingSummaryParts = [];
      if (ghShouldFixCount > 0) existingSummaryParts.push(`🛠️ ${ghShouldFixCount} Should Fix`);
      if (ghNeedsLookCount > 0) existingSummaryParts.push(`❓ ${ghNeedsLookCount} Need Look`);
      if (ghRepliedCount > 0) existingSummaryParts.push(`💬 ${ghRepliedCount} Replied`);

      const groups = [
        ...(queuedForFix.length > 0
          ? [
              {
                id: 'queued-fix',
                label: `Queued for Agent Fix (${queuedForFix.length})`,
                icon: 'tools',
                items: queuedForFix,
                expanded: true,
              },
            ]
          : []),
        {
          id: 'staged',
          label: `Staged for GitHub Review (${staged.length})`,
          icon: 'cloud-upload',
          items: staged,
          expanded: true,
        },
        ...(uncertain.length > 0
          ? [
              {
                id: 'uncertain',
                label: `Uncertain — Calibrate Your Preference (${uncertain.length})`,
                icon: 'question',
                items: uncertain,
                expanded: true,
              },
            ]
          : []),
        ...(candidates.length > 0
          ? [
              {
                id: 'candidates',
                label: `Unstaged Draft Findings (${candidates.length})`,
                icon: 'sparkle',
                items: candidates,
                expanded: true,
              },
            ]
          : []),
        ...(qaThreads.length > 0
          ? [
              {
                id: 'qa',
                label: `In-Code Q&A Threads (${qaThreads.length})`,
                icon: 'comment-discussion',
                items: qaThreads,
                expanded: true,
              },
            ]
          : []),
        {
          id: 'existing',
          label:
            existingSummaryParts.length > 0
              ? `${existingSummaryParts.join(' • ')} — GitHub Comments (${existingGh.length})`
              : `Existing GitHub PR Comments (${existingGh.length})`,
          icon: 'github',
          items: existingGh,
          expanded: isOwned && existingGh.length > 0,
        },
        ...(fixedThreads.length > 0
          ? [
              {
                id: 'fixed',
                label: `Fixed in Working Tree (${fixedThreads.length})`,
                icon: 'pass-filled',
                items: fixedThreads,
                expanded: true,
              },
            ]
          : []),
      ];

      for (const g of groups) {
        const node = new vscode.TreeItem(
          g.label,
          g.expanded
            ? vscode.TreeItemCollapsibleState.Expanded
            : vscode.TreeItemCollapsibleState.Collapsed
        );
        if (g.description) {
          node.description = g.description;
        }
        node.iconPath = new vscode.ThemeIcon(g.icon);
        node.groupItems = g.items;
        rootNodes.push(node);
      }
      return rootNodes;
    }

    if (element.groupItems) {
      return element.groupItems.map((t) => {
        const isFixed = t.status === 'fixed';
        const isUncertain = t.status === 'uncertain';
        const isExistingGh = t.kind === 'github_existing';
        const ghTriage = isExistingGh ? inferExistingCommentTriage(t, session) : null;
        const sev = (t.severity || '').toUpperCase();
        const cat = (ghTriage && ghTriage.triageCategory) || '';
        const prefix = isFixed
          ? '✅ [Fixed]'
          : isUncertain
          ? '🤔 [Calibrate]'
          : isExistingGh && ghTriage
          ? ghTriage.triageStatus === 'recommended_fix'
            ? cat === 'suggestion'
              ? '🛠️ [Suggestion]'
              : '🛠️ [Should Fix]'
            : ghTriage.triageStatus === 'addressed_or_refuted'
            ? cat === 'refuted'
              ? '⚠️ [Disputed]'
              : cat === 'addressed'
              ? '✅ [Addressed]'
              : '💬 [Replied]'
            : String(cat).includes('question')
            ? '❓ [Question]'
            : '👀 [Needs Look]'
          : sev.includes('BLOCK')
          ? '🔴 [Blocking]'
          : sev.includes('DESIGN')
          ? '🟡 [Design]'
          : sev.includes('NIT')
          ? '🟢 [Nit]'
          : '💬';
        const ratingBadge =
          t.feedbackRating === 'up'
            ? ' 👍'
            : t.feedbackRating === 'calibrated'
            ? ' 🧠'
            : '';
        const fixBadge =
          !isFixed && t.fixStatus === 'in_progress'
            ? ' 🔧(Fixing...)'
            : !isFixed && t.selectedForFix
            ? ' 🔧'
            : '';
        const rawTitle = String(t.title || path.basename(t.path || ''));
        const strippedBadgeTitle = rawTitle.replace(
          /^(?:🛠️|⚠️|✅|💬|❓|👀|🤔|🔴|🟡|🟢)\s*\[[^\]]+\]\s*/,
          ''
        );
        const authorMatch = strippedBadgeTitle.match(/^@([A-Za-z0-9_.\-[\]]+):\s*(.*)$/);
        const inferredAuthor = t.author || (authorMatch ? authorMatch[1] : '');
        const cleanTitle = (authorMatch ? authorMatch[2] : strippedBadgeTitle) || rawTitle;

        const item = new vscode.TreeItem(
          `${prefix}${fixBadge}${ratingBadge} ${cleanTitle}`,
          vscode.TreeItemCollapsibleState.None
        );
        const locStr = `${path.basename(t.path || '')}:L${t.startLine || 1}`;
        item.description =
          isExistingGh && inferredAuthor
            ? `@${inferredAuthor.replace(/^@/, '')} • ${locStr}`
            : locStr;
        item.contextValue = isFixed
          ? 'thread-fixed'
          : t.status === 'uncertain'
          ? 'thread-uncertain'
          : t.status === 'staged'
          ? 'thread-staged'
          : t.kind === 'ai_candidate' || t.kind === 'user_qa'
          ? 'thread-candidate'
          : 'thread-existing';
        item.threadId = t.id;
        item.threadData = t;

        if (!isFixed && vscode.TreeItemCheckboxState) {
          item.checkboxState = t.selectedForFix
            ? vscode.TreeItemCheckboxState.Checked
            : vscode.TreeItemCheckboxState.Unchecked;
        }

        const tip = new vscode.MarkdownString(
          `### ${prefix} ${t.title || 'Comment'}\n` +
            `**File**: \`${t.path}:L${t.startLine}-${t.endLine || t.startLine}\`\n\n` +
            (isFixed && t.fixedSummary
              ? `**✅ Applied Fix**: ${t.fixedSummary}\n\n`
              : '') +
            (isExistingGh && ghTriage
              ? `**Quasar Assessment**: **${ghTriage.triageLabel}**\n\n> ${ghTriage.triageSummary}\n\n`
              : '') +
            (isUncertain && t.uncertaintyQuestion
              ? `**🤔 Preference Question**: *${t.uncertaintyQuestion}*\n\n`
              : '') +
            (!isExistingGh && t.whyItMatters ? `*CliffsNotes Context*: ${t.whyItMatters}\n\n` : '') +
            `**Comment**:\n> ${t.draftComment || ''}\n\n` +
            (!isFixed ? `*Check the box to queue this comment for Quasar to fix in your branch.*` : '')
        );
        item.tooltip = tip;
        item.command = {
          command: 'quasar.jumpToThread',
          title: 'Jump to Comment Thread',
          arguments: [t],
        };
        return item;
      });
    }

    return [];
  }
}

// ============================================================================
// 3. TreeDataProvider: Test Coverage, Repo Checks & Learned Preferences
// ============================================================================
class ChecksTreeProvider {
  constructor() {
    this._onDidChangeTreeData = new vscode.EventEmitter();
    this.onDidChangeTreeData = this._onDidChangeTreeData.event;
  }

  refresh() {
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element) {
    return element;
  }

  getChildren(element) {
    const prefs = loadReviewerPreferences();
    const rules = prefs.rules || [];
    const seedSkills = prefs.seedSkills || [];
    const learnedRules = rules.filter((r) => r.origin !== 'seed_skill');
    const seedRules = rules.filter((r) => r.origin === 'seed_skill');

    if (element && element.nodeType === 'prefs-root') {
      const children = [];
      const importSkillItem = new vscode.TreeItem(
        'Import Premade Review Skill as Starting Point...',
        vscode.TreeItemCollapsibleState.None
      );
      importSkillItem.iconPath = new vscode.ThemeIcon('sparkle');
      importSkillItem.tooltip = new vscode.MarkdownString(
        'Provide an existing `SKILL.md` or review guide as a non-destructive starting point.\n\n' +
          'Quasar keeps its core defaults and layers your learned feedback on top of this baseline skill over time.'
      );
      importSkillItem.command = {
        command: 'quasar.importReviewSkill',
        title: 'Import Premade Review Skill as Starting Point',
      };
      children.push(importSkillItem);

      const addRuleItem = new vscode.TreeItem(
        'Teach Quasar a Preference or Calibrate Style...',
        vscode.TreeItemCollapsibleState.None
      );
      addRuleItem.iconPath = new vscode.ThemeIcon('lightbulb-sparkle');
      addRuleItem.command = {
        command: 'quasar.managePreferences',
        title: 'Manage & Calibrate Reviewer Preferences',
      };
      children.push(addRuleItem);

      const openGuideItem = new vscode.TreeItem(
        'Open PREFERENCES.md Guide',
        vscode.TreeItemCollapsibleState.None
      );
      openGuideItem.iconPath = new vscode.ThemeIcon('notebook');
      openGuideItem.command = {
        command: 'quasar.openPreferencesGuide',
        title: 'Open PREFERENCES.md',
      };
      children.push(openGuideItem);

      for (const sk of seedSkills) {
        const skItem = new vscode.TreeItem(
          `🌱 Seed Skill: ${sk.name || sk.slug}`,
          vscode.TreeItemCollapsibleState.None
        );
        skItem.description = `${sk.extractedRuleCount || 0} baseline rules • starting point`;
        skItem.tooltip = new vscode.MarkdownString(
          `### 🌱 Starting-Point Review Skill: ${sk.name || sk.slug}\n\n` +
            `**Source**: \`${sk.sourcePath || ''}\`\n\n` +
            `${sk.description || ''}\n\n` +
            `*Used as a non-destructive baseline. Your explicit 👍/👎 and calibrated rules automatically take higher precedence as Quasar grows with you.*`
        );
        skItem.command = {
          command: 'quasar.managePreferences',
          title: 'Manage Seed Skill',
          arguments: [{ isSeedSkill: true, seedSkill: sk }],
        };
        children.push(skItem);
      }

      for (const r of rules) {
        const isSeed = r.origin === 'seed_skill';
        const badge =
          r.sentiment === 'prefer'
            ? isSeed
              ? '🌱 [Seed Prefer]'
              : '✅ [Prefer]'
            : r.sentiment === 'avoid'
            ? isSeed
              ? '🌱 [Seed Avoid]'
              : '🚫 [Avoid]'
            : isSeed
            ? '🌱 [Seed Rule]'
            : '📌 [Rule]';
        const rItem = new vscode.TreeItem(
          `${badge} ${r.rule}`,
          vscode.TreeItemCollapsibleState.None
        );
        rItem.description = isSeed
          ? `${r.seedSkillName || 'seed skill'} • ${r.category || 'general'}`
          : `learned • ${r.category || 'general'}`;
        rItem.tooltip = new vscode.MarkdownString(
          `**${badge}** *(${r.category || 'general'})*\n\n${r.rule}\n\n` +
            (isSeed
              ? `*Imported from starting-point skill*: **${r.seedSkillName || r.seedSkillSlug || 'seed skill'}**\n\n`
              : r.sourceFindingTitle
              ? `*Learned from*: ${r.sourceFindingTitle}\n\n`
              : '') +
            `*Click to manage or remove this preference rule.*`
        );
        rItem.command = {
          command: 'quasar.managePreferences',
          title: 'Manage Preference Rule',
          arguments: [r],
        };
        children.push(rItem);
      }
      return children;
    }

    if (element) return [];

    const items = [];
    const ghAuth = loadStoredGithubAuth();
    const ghAuthItem = new vscode.TreeItem(
      ghAuth && ghAuth.login
        ? `GitHub Account: @${ghAuth.login} (Ready to publish reviews as you)`
        : 'Configure GitHub API Key to Publish Reviews as You...',
      vscode.TreeItemCollapsibleState.None
    );
    ghAuthItem.iconPath = new vscode.ThemeIcon(ghAuth && ghAuth.login ? 'key' : 'unlock');
    ghAuthItem.description =
      ghAuth && ghAuth.login
        ? `Token ${ghAuth.tokenPreview || 'saved'} • click to update`
        : 'Click to set Personal Access Token';
    ghAuthItem.tooltip = new vscode.MarkdownString(
      ghAuth && ghAuth.login
        ? `### 🔑 Authenticated as \`@${ghAuth.login}\`${ghAuth.name ? ` (${ghAuth.name})` : ''}\n\n` +
            `Quasar will use your saved GitHub Personal Access Token (\`${ghAuth.tokenPreview || '***'}\`) to publish PR reviews, comments, approvals, and change requests directly as **@${ghAuth.login}**.\n\n` +
            `*Click to update or clear your GitHub API key.*`
        : `### 🔑 Configure GitHub API Key (Personal Access Token)\n\n` +
            `Provide a GitHub Personal Access Token (\`repo\` scope or \`Pull requests: Read and write\`) so Quasar can publish reviews directly to GitHub PRs as you.`
    );
    ghAuthItem.command = {
      command: 'quasar.configureGithubToken',
      title: 'Configure GitHub API Key / Personal Access Token',
    };
    items.push(ghAuthItem);

    const prefsLabel =
      seedSkills.length > 0
        ? `Reviewer Preferences (${learnedRules.length} learned, ${seedRules.length} from ${seedSkills.length} seed skill${seedSkills.length === 1 ? '' : 's'})`
        : `Learned Reviewer Preferences (${rules.length} rules)`;
    const prefsRoot = new vscode.TreeItem(
      prefsLabel,
      vscode.TreeItemCollapsibleState.Expanded
    );
    prefsRoot.iconPath = new vscode.ThemeIcon('lightbulb-sparkle');
    prefsRoot.nodeType = 'prefs-root';
    prefsRoot.tooltip = new vscode.MarkdownString(
      `### 🧠 Quasar Continuous Preference Learning\n\n` +
        `1. **Core Defaults**: Quasar's built-in CliffsNotes & multi-lens verification.\n` +
        `2. **Starting-Point Seed Skills (${seedSkills.length})**: Premade review skills imported as a non-destructive baseline (${seedRules.length} rules).\n` +
        `3. **Evolved Learned Preferences (${learnedRules.length})**: Rules learned from your calibrations and feedback (highest priority).`
    );
    items.push(prefsRoot);

    const session = loadActiveSession();
    if (!session) return items;

    for (const row of session.coverageMatrix || []) {
      const ci = new vscode.TreeItem(
        `${row.status || '✅'} ${row.behavior || ''}`,
        vscode.TreeItemCollapsibleState.None
      );
      ci.description = row.notes || '';
      ci.iconPath = new vscode.ThemeIcon('beaker');
      ci.tooltip = new vscode.MarkdownString(
        `**Behavior**: ${row.behavior || ''}\n\n**Verification**: ${row.status || ''}\n\n${row.notes || ''}`
      );
      items.push(ci);
    }

    for (const check of session.repoChecks || []) {
      const ri = new vscode.TreeItem(
        `${check.passed ? '✅' : '⚠️'} ${check.name || ''}`,
        vscode.TreeItemCollapsibleState.None
      );
      ri.description = check.detail || '';
      ri.iconPath = new vscode.ThemeIcon(check.passed ? 'check' : 'warning');
      items.push(ri);
    }

    return items;
  }
}

// ============================================================================
// Extension Activation & Native Editor Integrations
// ============================================================================
function activate(context) {
  fs.mkdirSync(REVIEW_STATE_DIR, { recursive: true });

  const cliffsProvider = new CliffsNotesTreeProvider();
  const commentsProvider = new CommentsTreeProvider();
  const checksProvider = new ChecksTreeProvider();

  vscode.window.registerTreeDataProvider('quasar.cliffsNotesTree', cliffsProvider);
  const commentsTreeView = vscode.window.createTreeView('quasar.commentsTree', {
    treeDataProvider: commentsProvider,
    manageCheckboxStateManually: true,
  });
  context.subscriptions.push(commentsTreeView);
  if (typeof commentsTreeView.onDidChangeCheckboxState === 'function') {
    context.subscriptions.push(
      commentsTreeView.onDidChangeCheckboxState(async (e) => {
        if (!e || !Array.isArray(e.items)) return;
        for (const [item, state] of e.items) {
          if (!item || !item.threadId) continue;
          const isChecked =
            vscode.TreeItemCheckboxState && state === vscode.TreeItemCheckboxState.Checked;
          await runManageFixes([
            '--toggle-select',
            '--thread-id',
            String(item.threadId),
            '--selected',
            isChecked ? 'true' : 'false',
          ]);
        }
        refreshAllUI(false);
      })
    );
  }
  vscode.window.registerTreeDataProvider('quasar.checksTree', checksProvider);

  // Status Bar Items (always-visible launcher + active tour stepper)
  const statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 96);
  statusBarItem.command = 'quasar.openMenu';
  context.subscriptions.push(statusBarItem);

  const tourStatusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 95);
  tourStatusBarItem.command = 'quasar.nextTourStop';
  context.subscriptions.push(tourStatusBarItem);

  // Native Comment Controller (for Review Comments + Ask-in-Comment Q&A + Meta-Feedback)
  const commentController = vscode.comments.createCommentController(
    'quasar',
    'Quasar'
  );
  commentController.options = {
    prompt: 'Ask Quasar about these lines, teach Quasar your preference, or stage a comment for GitHub...',
    placeHolder: 'e.g. "Is this null-safe?", "Stop flagging private helpers", or "nit: Prefer final here"',
  };
  context.subscriptions.push(commentController);

  // Allow clicking '+' on any line of a file in the active PR
  commentController.commentingRangeProvider = {
    provideCommentingRanges(document) {
      const session = loadActiveSession();
      if (!session) return [];
      const relPath = inferRelPathFromDocument(document.uri.fsPath, session);
      if (!relPath) return [];
      const lineCount = Math.max(1, document.lineCount);
      return [new vscode.Range(0, 0, lineCount - 1, 0)];
    },
  };

  let activeCommentThreads = new Map();
  let lastSessionId = null;
  let lastAnalysisStatus = null;
  let codeLensEmitter = new vscode.EventEmitter();

  function updateStatusBar(session) {
    if (!session || !session.pr) {
      statusBarItem.text = '$(git-pull-request) Quasar';
      statusBarItem.tooltip =
        'Click to open Quasar (Review Local Branch, GitHub PR, or Manage Preferences)';
      statusBarItem.show();
      tourStatusBarItem.hide();
      return;
    }
    const stops = getAllTourStops(session);
    const readCount = stops.filter((s) => s.read).length;
    const stagedCount = (session.threads || []).filter((t) => t.status === 'staged').length;
    const uncertainCount = (session.threads || []).filter((t) => t.status === 'uncertain').length;
    const queuedFixCount = (session.threads || []).filter(
      (t) => t.selectedForFix && t.status !== 'fixed'
    ).length;
    const targetLabel = session.pr.number
      ? `PR #${session.pr.number}`
      : `$(git-branch) ${session.pr.headBranch || session.pr.headRefName || 'Local Branch'}`;
    const spin = session.analysisStatus === 'analyzing' ? '$(sync~spin) ' : '$(book) ';
    statusBarItem.text = `${spin}Quasar: ${targetLabel}`;
    statusBarItem.tooltip = 'Click to open Quasar Sidebar & Menu';
    statusBarItem.show();

    const uncertainSuffix = uncertainCount > 0 ? ` • $(question) ${uncertainCount} Uncertain` : '';
    const fixSuffix = queuedFixCount > 0 ? ` • $(wrench) ${queuedFixCount} Queued to Fix` : '';
    tourStatusBarItem.text = `$(arrow-down) Tour ${readCount}/${stops.length} • ${stagedCount} Staged${uncertainSuffix}${fixSuffix}`;
    tourStatusBarItem.tooltip = 'Click to step to the next CliffsNotes Tour Stop (Alt+Down)';
    tourStatusBarItem.show();
  }

  /**
   * Synchronizes the native vscode.CommentThread instances in the diff editor
   * with `session.threads` (Review Findings, Uncertain Preference Checks, Staged Comments, In-Code Q&A, Existing GitHub Comments).
   */
  function syncNativeCommentThreads(session) {
    for (const oldThread of activeCommentThreads.values()) {
      oldThread.dispose();
    }
    activeCommentThreads.clear();

    if (!session || !Array.isArray(session.threads)) return;

    for (const t of session.threads) {
      if (t.status === 'dismissed' || !t.path) continue;
      const headFilePath = session.headSnapshotRoot
        ? path.join(session.headSnapshotRoot, t.path)
        : session.repoDir
        ? path.join(session.repoDir, t.path)
        : null;
      if (!headFilePath || !fs.existsSync(headFilePath)) continue;

      const startLine = Math.max(0, (t.startLine || 1) - 1);
      const endLine = Math.max(startLine, (t.endLine || t.startLine || 1) - 1);
      const range = new vscode.Range(startLine, 0, endLine, 0);

      const ghTriage = t.kind === 'github_existing' ? inferExistingCommentTriage(t, session) : null;
      const rawMessages = [...(t.messages || [])];
      if (
        ghTriage &&
        t.status !== 'fixed' &&
        !rawMessages.some((m) => m && m.isTriageNote)
      ) {
        rawMessages.push({
          author: `Quasar Triage (${ghTriage.triageBadge})`,
          body: `**${ghTriage.triageLabel}**\n\n${ghTriage.triageSummary}`,
          isTriageNote: true,
        });
      }

      const comments = rawMessages.map((m) => {
        const md = new vscode.MarkdownString(m.body || '');
        md.isTrusted = true;
        return {
          author: { name: m.author || 'Quasar' },
          body: md,
          mode: vscode.CommentMode.Preview,
        };
      });

      const thread = commentController.createCommentThread(
        vscode.Uri.file(headFilePath),
        range,
        comments
      );
      thread.threadId = t.id;
      thread.relPath = t.path;
      thread.canReply = true;

      const queuedSuffix =
        t.status !== 'fixed' && t.selectedForFix ? ' [🔧 Queued for Fix]' : '';

      if (t.status === 'fixed') {
        thread.contextValue = 'fixed';
        thread.label = `✅ Fixed in Working Tree — ${t.title || ''}`;
        thread.collapsibleState = vscode.CommentThreadCollapsibleState.Collapsed;
      } else if (t.status === 'uncertain') {
        thread.contextValue = 'uncertain';
        thread.label = `🤔 Uncertain (Calibrate Preference)${queuedSuffix} — ${t.title || ''}`;
        thread.collapsibleState = vscode.CommentThreadCollapsibleState.Expanded;
      } else if (t.status === 'staged') {
        thread.contextValue = 'staged';
        const sev = (t.severity || '').toUpperCase();
        const badge = sev.includes('BLOCK')
          ? '🔴 Blocking'
          : sev.includes('DESIGN')
          ? '🟡 Design'
          : sev.includes('NIT')
          ? '🟢 Nit'
          : '💬 Review';
        thread.label = `✅ Staged for GitHub Review (${badge})${queuedSuffix} — ${t.title || ''}`;
        thread.collapsibleState = vscode.CommentThreadCollapsibleState.Expanded;
      } else if (t.kind === 'ai_candidate') {
        thread.contextValue = 'candidate';
        const sev = (t.severity || '').toUpperCase();
        const badge = sev.includes('BLOCK')
          ? '🔴 Blocking'
          : sev.includes('DESIGN')
          ? '🟡 Design'
          : '🟢 Nit';
        thread.label = `${badge} Draft (Unstaged)${queuedSuffix} — ${t.title || ''}`;
        thread.collapsibleState = vscode.CommentThreadCollapsibleState.Expanded;
      } else if (t.kind === 'user_qa') {
        thread.contextValue = 'qa';
        thread.label = `💬 Quasar Q&A${queuedSuffix} — ${t.title || ''}`;
        thread.collapsibleState = vscode.CommentThreadCollapsibleState.Expanded;
      } else {
        thread.contextValue = 'existing';
        const triageHeader = ghTriage ? ghTriage.triageBadge : '👀 Existing GitHub Comment';
        const cleanThreadTitle = String(t.title || '').replace(
          /^(?:🛠️|⚠️|✅|💬|❓|👀|🤔|🔴|🟡|🟢)\s*\[[^\]]+\]\s*/,
          ''
        );
        thread.label = `${triageHeader}${queuedSuffix} — ${cleanThreadTitle}`;
        thread.collapsibleState =
          ghTriage && ghTriage.triageStatus === 'addressed_or_refuted'
            ? vscode.CommentThreadCollapsibleState.Collapsed
            : vscode.CommentThreadCollapsibleState.Expanded;
      }

      activeCommentThreads.set(t.id, thread);
    }
  }

  /**
   * Opens a Tour Stop in the native side-by-side `vscode.diff` editor and scrolls to its line range.
   */
  async function openTourStopInDiff(stop, preserveFocus = false) {
    const session = loadActiveSession();
    if (!session || !stop) return;

    const baseUri = vscode.Uri.file(
      stop.baseSnapshotPath || path.join(session.baseSnapshotRoot || '', stop.path)
    );
    const headUri = vscode.Uri.file(
      stop.headSnapshotPath || path.join(session.headSnapshotRoot || '', stop.path)
    );

    const diffTitle = `[Stop ${stop.stopNumber}] ${path.basename(stop.path)} — ${
      stop.cliffsNotes || stop.description || ''
    }`.slice(0, 90);

    const startLine0 = Math.max(0, (stop.startLine || 1) - 1);
    const endLine0 = Math.max(startLine0, (stop.endLine || stop.startLine || 1) - 1);

    await vscode.commands.executeCommand('vscode.diff', baseUri, headUri, diffTitle, {
      selection: new vscode.Range(startLine0, 0, endLine0, 0),
      preserveFocus,
      preview: true,
    });

    // Mark stop as read
    let updated = false;
    for (const ch of session.chapters || []) {
      for (const s of ch.stops || []) {
        if (s.id === stop.id && !s.read) {
          s.read = true;
          updated = true;
        }
      }
    }
    if (updated) {
      saveActiveSession(session);
      cliffsProvider.refresh();
      updateStatusBar(session);
      updateCliffsNotesWebviewContent();
    }
  }

  let promptedBranchSwitchSessionIds = new Set();
  let cliffsNotesWebviewPanel = null;

  function escapeHtmlText(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatInlineMd(str) {
    let s = escapeHtmlText(str);
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    return s;
  }

  function splitConciseAndDetail(rawText, maxShortChars = 155) {
    const clean = String(rawText || '').replace(/\s+/g, ' ').trim();
    if (!clean) return { shortText: '', extraText: '' };

    // Split on first sentence boundary (. followed by space + capital/backtick)
    const sentMatch = clean.match(/^(.+?[.!?])(?:\s+([A-Z`*].*))?$/);
    let firstSent = sentMatch ? sentMatch[1].trim() : clean;
    const restSent = sentMatch && sentMatch[2] ? sentMatch[2].trim() : '';

    // Strip overly long parenthetical lists (e.g. "(bool, int, double, String, ...)") from the short headline
    const simplifiedFirst = firstSent.replace(/\s*\([^()]{32,}\)/g, '');
    const hadLongParens = simplifiedFirst !== firstSent;

    let shortText = simplifiedFirst;
    let extraText = hadLongParens ? clean : restSent;
    if (shortText.length > maxShortChars) {
      const cut = shortText.lastIndexOf(' ', maxShortChars - 1);
      shortText = (cut > 60 ? shortText.slice(0, cut) : shortText.slice(0, maxShortChars - 1)) + '…';
      extraText = clean;
    }
    return { shortText, extraText };
  }

  function readStopCodeSnippet(session, stop, maxLines = 16) {
    if (!session || !stop) return null;
    const candidates = [
      stop.headSnapshotPath,
      session.headSnapshotRoot && stop.path
        ? path.join(session.headSnapshotRoot, stop.path)
        : null,
      stop.localWorkspacePath,
      session.repoDir && stop.path ? path.join(session.repoDir, stop.path) : null,
    ].filter(Boolean);

    let filePath = null;
    for (const c of candidates) {
      if (fs.existsSync(c)) {
        try {
          if (fs.statSync(c).isFile()) {
            filePath = c;
            break;
          }
        } catch (_) {}
      }
    }
    if (!filePath) return null;

    try {
      const content = fs.readFileSync(filePath, 'utf8');
      const allLines = content.split(/\r?\n/);
      const startL = Math.max(1, parseInt(stop.startLine || 1, 10));
      const endL = Math.max(startL, parseInt(stop.endLine || startL, 10));
      const sliceEnd = Math.min(allLines.length, endL, startL + maxLines - 1);
      const padWidth = String(sliceEnd).length;
      const numbered = [];
      for (let ln = startL; ln <= sliceEnd; ln++) {
        const lineTxt = allLines[ln - 1] !== undefined ? allLines[ln - 1] : '';
        numbered.push(`${String(ln).padStart(padWidth, ' ')} │ ${lineTxt}`);
      }
      const hiddenCount = Math.max(0, Math.min(allLines.length, endL) - sliceEnd);
      return {
        code: numbered.join('\n'),
        startLine: startL,
        endLine: Math.min(allLines.length, endL),
        hiddenCount,
      };
    } catch (_) {
      return null;
    }
  }

  function buildCliffsNotesWebviewHtml(webview, session, sectionAnchor = '') {
    const mermaidScriptUri = webview.asWebviewUri(
      vscode.Uri.file(path.join(context.extensionPath, 'media', 'mermaid-preview.js'))
    );
    if (!session || !session.pr) {
      return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8" /><style>body { font-family: var(--vscode-font-family, system-ui, sans-serif); padding: 32px; color: var(--vscode-foreground); }</style></head>
<body><h2>📖 No Active Quasar Review</h2><p>Load a GitHub PR URL or local branch review to view its interactive CliffsNotes Study Guide.</p></body>
</html>`;
    }

    const pr = session.pr || {};
    const cn = session.cliffsNotes || {};
    const chapters = session.chapters || [];
    const allStops = getAllTourStops(session);
    const readStopsCount = allStops.filter((s) => s.read).length;
    const totalStopsCount = allStops.length;
    const progressPct =
      totalStopsCount > 0 ? Math.round((readStopsCount / totalStopsCount) * 100) : 0;

    const isLocal =
      pr.mode === 'local_branch' ||
      pr.number === 0 ||
      !pr.number ||
      String(pr.url || '').startsWith('local://');
    const headerTitle = isLocal
      ? `📖 ${pr.title || 'Local Branch Review'}`
      : `📖 PR #${pr.number}: ${pr.title || ''}`;

    const activeThreads = (session.threads || []).filter((t) => t.status !== 'dismissed');
    const stagedCount = activeThreads.filter((t) => t.status === 'staged').length;
    const uncertainCount = activeThreads.filter((t) => t.status === 'uncertain').length;
    const queuedFixCount = activeThreads.filter(
      (t) => t.selectedForFix && t.status !== 'fixed'
    ).length;

    const stopsLookupPayload = allStops.map((s) => ({
      stopId: s.id,
      stopNumber: s.stopNumber,
      title: s.title || '',
      path: s.path || '',
      baseName: path.basename(s.path || '').toLowerCase(),
      stemName: path
        .basename(s.path || '')
        .replace(/\.[^.]+$/, '')
        .toLowerCase(),
      line: s.startLine || 1,
      searchHay: `${s.title || ''} ${s.cliffsNotes || ''} ${s.description || ''} ${
        s.whyItChanged || ''
      } ${s.path || ''}`.toLowerCase(),
    }));

    // Concise plain-English headline + optional deep-dive background drawer
    const rawTldr = (cn.tldr || '').trim();
    const rawWhatAndWhy = (cn.whatAndWhy || '').trim();
    const headlineSource = rawTldr || splitConciseAndDetail(rawWhatAndWhy, 190).shortText;
    const hasDeepDive = Boolean(
      (rawWhatAndWhy && rawWhatAndWhy !== headlineSource) ||
        (cn.designTradeoffs && cn.designTradeoffs.length > 0) ||
        (cn.keySymbols && cn.keySymbols.length > 0)
    );

    const symbolsRowsHtml = (cn.keySymbols || [])
      .map((sym) => {
        const matched = findBestTourStopForQuery(session, `${sym.name || ''} ${sym.role || ''}`);
        const badgeHtml = matched
          ? `<span class="q-pill q-pill-accent" title="Matches Stop ${escapeHtmlText(
              matched.stopNumber
            )}">📍 ${escapeHtmlText(matched.stopNumber)}</span>`
          : '';
        const clickAttr = matched
          ? `data-act="openStop" data-stop-id="${escapeHtmlText(matched.id)}"`
          : `data-act="jumpToQuery" data-query="${escapeHtmlText(sym.name || '')}"`;
        const { shortText: shortRole } = splitConciseAndDetail(sym.role || '', 130);
        return `
          <tr class="q-sym-row" ${clickAttr} title="${escapeHtmlText(sym.role || '')}">
            <td class="q-sym-name"><code>${escapeHtmlText(sym.name || '')}</code> ${badgeHtml}</td>
            <td class="q-sym-role">${formatInlineMd(shortRole)}</td>
            <td class="q-sym-action"><button type="button" class="q-btn q-btn-sm" ${clickAttr}>🔍 Diff</button></td>
          </tr>
        `;
      })
      .join('');

    const tradeoffsHtml = (cn.designTradeoffs || [])
      .map((td) => `<li class="q-tradeoff-item">${formatInlineMd(td)}</li>`)
      .join('');

    const chaptersHtml = chapters
      .map((ch) => {
        const stops = ch.stops || [];
        const chRead = stops.filter((s) => s.read).length;
        const allChRead = stops.length > 0 && chRead === stops.length;
        const bullets = ch.cliffsNotesBullets || [];
        const bulletsHtml = bullets.map((b) => `<li>${formatInlineMd(b)}</li>`).join('');
        const { shortText: shortChSummary } = splitConciseAndDetail(ch.summary || '', 160);

        const stopsCardsHtml = stops
          .map((stop) => {
            const isRead = Boolean(stop.read);
            const rawNote = stop.cliffsNotes || stop.description || '';
            const { shortText, extraText } = splitConciseAndDetail(rawNote, 145);
            const snippet = readStopCodeSnippet(session, stop, 14);
            const hasMoreDetails = Boolean(
              extraText || stop.whyItChanged || stop.callersAndInvariants || snippet
            );
            const fileLabel = `${path.basename(stop.path || '')}:L${stop.startLine || 1}${
              stop.endLine && stop.endLine !== stop.startLine ? '–' + stop.endLine : ''
            }`;

            return `
              <div class="q-stop-card ${isRead ? 'q-stop-read' : ''}" id="stop-card-${escapeHtmlText(
              stop.id
            )}">
                <div class="q-stop-header">
                  <div class="q-stop-title-wrap">
                    <button type="button" class="q-read-pill ${
                      isRead ? 'is-read' : ''
                    }" data-act="toggleStopRead" data-stop-id="${escapeHtmlText(
              stop.id
            )}" title="Click to toggle Read / Unread">
                      ${isRead ? '✓' : '○'}
                    </button>
                    <span class="q-stop-num">[${escapeHtmlText(stop.stopNumber)}]</span>
                    <a href="#" class="q-stop-link" data-act="openStop" data-stop-id="${escapeHtmlText(
                      stop.id
                    )}" title="${escapeHtmlText(stop.path || '')}">${escapeHtmlText(fileLabel)}</a>
                    <span class="q-stop-inline-summary">— ${formatInlineMd(shortText)}</span>
                  </div>
                  <div class="q-stop-actions">
                    <button type="button" class="q-btn q-btn-primary q-btn-sm" data-act="openStop" data-stop-id="${escapeHtmlText(
                      stop.id
                    )}">🔍 Diff</button>
                    <button type="button" class="q-btn q-btn-sm" data-act="askAtStop" data-stop-id="${escapeHtmlText(
                      stop.id
                    )}" title="Ask Quasar about this stop">💬 Ask</button>
                  </div>
                </div>
                ${
                  hasMoreDetails
                    ? `<details class="q-stop-details">
                        <summary>Context &amp; code snippet (${escapeHtmlText(fileLabel)})</summary>
                        <div class="q-stop-details-body">
                          ${
                            extraText
                              ? `<div class="q-stop-meta"><strong>Full note:</strong> ${formatInlineMd(
                                  rawNote
                                )}</div>`
                              : ''
                          }
                          ${
                            stop.whyItChanged
                              ? `<div class="q-stop-meta"><strong>Why it changed:</strong> ${formatInlineMd(
                                  stop.whyItChanged
                                )}</div>`
                              : ''
                          }
                          ${
                            stop.callersAndInvariants
                              ? `<div class="q-stop-meta"><strong>Callers &amp; invariants:</strong> ${formatInlineMd(
                                  stop.callersAndInvariants
                                )}</div>`
                              : ''
                          }
                          ${
                            snippet
                              ? `<div class="q-snippet-wrap">
                                  <div class="q-snippet-bar">
                                    <span><code>${escapeHtmlText(stop.path || '')}</code> (L${
                                  snippet.startLine
                                }–${snippet.endLine})</span>
                                    <button type="button" class="q-btn q-btn-sm" data-act="openStop" data-stop-id="${escapeHtmlText(
                                      stop.id
                                    )}">Open Side-by-Side Diff ↗</button>
                                  </div>
                                  <pre class="q-snippet-pre"><code>${escapeHtmlText(
                                    snippet.code
                                  )}</code></pre>
                                  ${
                                    snippet.hiddenCount > 0
                                      ? `<div class="q-snippet-more">+ ${snippet.hiddenCount} more line(s) — click "Open Side-by-Side Diff" to view full hunk</div>`
                                      : ''
                                  }
                                </div>`
                              : ''
                          }
                        </div>
                      </details>`
                    : ''
                }
              </div>
            `;
          })
          .join('');

        return `
          <div class="q-chapter-card">
            <div class="q-chapter-header">
              <div>
                <h3 class="q-chapter-title">${escapeHtmlText(ch.title || '')}</h3>
                ${
                  shortChSummary
                    ? `<p class="q-chapter-summary">${formatInlineMd(shortChSummary)}</p>`
                    : ''
                }
              </div>
              <div class="q-chapter-right">
                <span class="q-pill ${allChRead ? 'q-pill-success' : ''}">${chRead}/${
          stops.length
        }</span>
                ${
                  stops.length > 0
                    ? `<button type="button" class="q-btn q-btn-sm" data-act="markChapterRead" data-chapter-id="${escapeHtmlText(
                        ch.id || ''
                      )}" data-read="${allChRead ? '0' : '1'}">${
                        allChRead ? '↺ Unread' : '✓ Mark Read'
                      }</button>`
                    : ''
                }
              </div>
            </div>
            ${
              bulletsHtml
                ? `<details class="q-stop-details" style="margin-top:6px;">
                    <summary>Chapter notes (${bullets.length} key points)</summary>
                    <ul class="q-chapter-bullets">${bulletsHtml}</ul>
                  </details>`
                : ''
            }
            <div class="q-stops-list">${stopsCardsHtml}</div>
          </div>
        `;
      })
      .join('');

    const commentsCardsHtml = activeThreads
      .map((t) => {
        const isExisting = t.kind === 'github_existing';
        const ghTriage = isExisting ? inferExistingCommentTriage(t, session) : null;
        const sev = String(t.severity || 'DESIGN').toUpperCase();
        const badgeText =
          t.status === 'fixed'
            ? '✅ Fixed'
            : t.status === 'uncertain'
            ? '🤔 Calibrate'
            : isExisting && ghTriage
            ? ghTriage.triageBadge
            : `${t.status === 'staged' ? '📤 Staged • ' : '📝 Draft • '}${sev}`;
        const canStage = t.kind === 'ai_candidate' || t.kind === 'user_staged';
        const isStaged = t.status === 'staged';
        const cleanTitle = String(t.title || path.basename(t.path || '')).replace(
          /^(?:🛠️|⚠️|✅|💬|❓|👀|🤔|🔴|🟡|🟢)\s*\[[^\]]+\]\s*/,
          ''
        );
        return `
          <div class="q-comment-card ${t.status === 'fixed' ? 'is-fixed' : ''}">
            <div class="q-comment-top">
              <div class="q-comment-title-group">
                <span class="q-pill ${
                  t.status === 'uncertain'
                    ? 'q-pill-warn'
                    : isStaged
                    ? 'q-pill-accent'
                    : ''
                }">${escapeHtmlText(badgeText)}</span>
                <a href="#" class="q-stop-link" data-act="jumpToThread" data-thread-id="${escapeHtmlText(
                  t.id
                )}">${escapeHtmlText(cleanTitle)}</a>
                <span class="q-stop-path">${escapeHtmlText(
                  path.basename(t.path || '')
                )}:L${t.startLine || 1}</span>
              </div>
              <div class="q-stop-actions">
                <button type="button" class="q-btn q-btn-sm" data-act="jumpToThread" data-thread-id="${escapeHtmlText(
                  t.id
                )}">🔍 Jump</button>
                ${
                  t.status === 'uncertain'
                    ? `<button type="button" class="q-btn q-btn-primary q-btn-sm" data-act="calibrateThread" data-thread-id="${escapeHtmlText(
                        t.id
                      )}">🤔 Calibrate</button>`
                    : ''
                }
                ${
                  canStage && t.status !== 'fixed'
                    ? `<button type="button" class="q-btn q-btn-sm" data-act="toggleStageThread" data-thread-id="${escapeHtmlText(
                        t.id
                      )}">${isStaged ? 'Unstage' : 'Stage'}</button>`
                    : ''
                }
                ${
                  t.status !== 'fixed'
                    ? `<button type="button" class="q-btn q-btn-sm ${
                        t.selectedForFix ? 'q-btn-primary' : ''
                      }" data-act="toggleFixThread" data-thread-id="${escapeHtmlText(t.id)}">${
                        t.selectedForFix ? '🔧 Queued ✓' : '🔧 Fix'
                      }</button>`
                    : ''
                }
                <button type="button" class="q-btn q-btn-sm" data-act="dismissThread" data-thread-id="${escapeHtmlText(
                  t.id
                )}" title="Remove Comment">🗑️</button>
              </div>
            </div>
            ${
              t.whyItMatters || t.draftComment
                ? `<details class="q-stop-details">
                    <summary>Comment &amp; rationale</summary>
                    <div class="q-stop-details-body">
                      ${
                        t.whyItMatters
                          ? `<div class="q-stop-meta">${formatInlineMd(t.whyItMatters)}</div>`
                          : ''
                      }
                      ${
                        t.draftComment
                          ? `<blockquote class="q-comment-quote">${formatInlineMd(
                              t.draftComment
                            )}</blockquote>`
                          : ''
                      }
                    </div>
                  </details>`
                : ''
            }
          </div>
        `;
      })
      .join('');

    const coverageRowsHtml = (session.coverageMatrix || [])
      .map(
        (row) => `
        <tr>
          <td>${formatInlineMd(row.behavior || '')}</td>
          <td>${formatInlineMd(row.status || '')}</td>
          <td>${formatInlineMd(row.notes || '')}</td>
        </tr>
      `
      )
      .join('');

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${escapeHtmlText(headerTitle)}</title>
  <style>
    :root {
      --q-bg: var(--vscode-editor-background, #0d1117);
      --q-fg: var(--vscode-editor-foreground, #e6edf3);
      --q-muted: var(--vscode-descriptionForeground, #8b949e);
      --q-card-bg: var(--vscode-sideBar-background, rgba(128, 128, 128, 0.05));
      --q-border: var(--vscode-panel-border, rgba(128, 128, 128, 0.22));
      --q-accent: var(--vscode-textLink-foreground, #58a6ff);
      --q-success: #3fb950;
      --q-warn: #d29922;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 0 20px 42px 20px;
      background: var(--q-bg);
      color: var(--q-fg);
      font-family: var(--vscode-font-family, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif);
      font-size: 13px;
      line-height: 1.5;
    }
    a { color: var(--q-accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    code {
      font-family: var(--vscode-editor-font-family, ui-monospace, SFMono-Regular, Menlo, monospace);
      font-size: 0.91em;
      background: rgba(128, 128, 128, 0.15);
      padding: 1px 4.5px;
      border-radius: 4px;
    }
    .q-sticky-top {
      position: sticky;
      top: 0;
      z-index: 20;
      background: var(--q-bg);
      padding: 12px 0 9px 0;
      border-bottom: 1px solid var(--q-border);
      margin-bottom: 14px;
    }
    .q-top-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }
    .q-pr-title {
      margin: 0 0 2px 0;
      font-size: 16px;
      font-weight: 700;
    }
    .q-pr-meta {
      font-size: 11.5px;
      color: var(--q-muted);
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    .q-toolbar-btns {
      display: flex;
      gap: 5px;
      flex-wrap: wrap;
      align-items: center;
    }
    .q-btn {
      background: rgba(128, 128, 128, 0.13);
      color: var(--q-fg);
      border: 1px solid var(--q-border);
      border-radius: 6px;
      padding: 4px 9px;
      font-size: 11.5px;
      font-weight: 500;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }
    .q-btn:hover {
      background: rgba(88, 166, 255, 0.18);
      border-color: var(--q-accent);
    }
    .q-btn-primary {
      background: rgba(56, 139, 253, 0.22);
      border-color: rgba(88, 166, 255, 0.55);
      color: var(--q-fg);
    }
    .q-btn-sm {
      padding: 2px 7px;
      font-size: 11px;
    }
    .q-progress-row {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 8px;
      flex-wrap: wrap;
    }
    .q-progress-track {
      flex: 1;
      min-width: 140px;
      height: 6px;
      background: rgba(128, 128, 128, 0.2);
      border-radius: 999px;
      overflow: hidden;
    }
    .q-progress-fill {
      height: 100%;
      background: linear-gradient(90deg, #388bfd, #3fb950);
      transition: width 0.25s ease;
    }
    .q-progress-label {
      font-size: 11.5px;
      font-weight: 600;
      color: var(--q-muted);
    }
    .q-nav-pills {
      display: flex;
      gap: 5px;
      flex-wrap: wrap;
    }
    .q-pill {
      display: inline-flex;
      align-items: center;
      padding: 1.5px 7px;
      border-radius: 999px;
      font-size: 10.8px;
      font-weight: 600;
      background: rgba(128, 128, 128, 0.15);
      border: 1px solid var(--q-border);
    }
    .q-pill-accent {
      background: rgba(56, 139, 253, 0.18);
      border-color: rgba(88, 166, 255, 0.45);
      color: var(--q-accent);
    }
    .q-pill-success {
      background: rgba(63, 185, 80, 0.18);
      border-color: rgba(63, 185, 80, 0.45);
      color: var(--q-success);
    }
    .q-pill-warn {
      background: rgba(210, 153, 34, 0.2);
      border-color: rgba(210, 153, 34, 0.5);
      color: var(--q-warn);
    }
    .q-section {
      margin: 14px 0;
      padding: 13px 15px;
      border: 1px solid var(--q-border);
      border-radius: 9px;
      background: var(--q-card-bg);
    }
    .q-section h2 {
      margin: 0 0 8px 0;
      font-size: 14.5px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .q-tldr-box {
      padding: 9px 12px;
      border-left: 3px solid var(--q-accent);
      background: rgba(56, 139, 253, 0.08);
      border-radius: 0 7px 7px 0;
      font-size: 13.5px;
      font-weight: 500;
    }
    details.q-stop-details, details.q-section-drawer {
      margin-top: 6px;
    }
    details.q-stop-details > summary, details.q-section-drawer > summary {
      cursor: pointer;
      color: var(--q-muted);
      font-size: 11.5px;
      font-weight: 500;
      user-select: none;
      list-style: none;
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }
    details.q-stop-details > summary::-webkit-details-marker,
    details.q-section-drawer > summary::-webkit-details-marker {
      display: none;
    }
    details.q-stop-details > summary::before,
    details.q-section-drawer > summary::before {
      content: '▸';
      display: inline-block;
      transition: transform 0.15s ease;
      color: var(--q-accent);
    }
    details.q-stop-details[open] > summary::before,
    details.q-section-drawer[open] > summary::before {
      transform: rotate(90deg);
    }
    details.q-stop-details > summary:hover,
    details.q-section-drawer > summary:hover {
      color: var(--q-fg);
    }
    .q-stop-details-body {
      margin-top: 6px;
      padding: 8px 10px;
      border-radius: 6px;
      background: rgba(128, 128, 128, 0.06);
      border: 1px solid rgba(128, 128, 128, 0.15);
    }
    .q-snippet-wrap {
      margin-top: 7px;
      border: 1px solid var(--q-border);
      border-radius: 6px;
      overflow: hidden;
      background: rgba(0, 0, 0, 0.22);
    }
    .q-snippet-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 4px 8px;
      background: rgba(128, 128, 128, 0.1);
      border-bottom: 1px solid var(--q-border);
      font-size: 11px;
      color: var(--q-muted);
    }
    .q-snippet-pre {
      margin: 0;
      padding: 8px 10px;
      overflow-x: auto;
      font-size: 11.5px;
      line-height: 1.42;
    }
    .q-snippet-pre code {
      background: transparent;
      padding: 0;
    }
    .q-snippet-more {
      padding: 4px 10px;
      font-size: 11px;
      color: var(--q-muted);
      border-top: 1px solid rgba(128, 128, 128, 0.15);
    }
    .q-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
      margin-top: 6px;
    }
    .q-table th, .q-table td {
      text-align: left;
      padding: 6px 8px;
      border-bottom: 1px solid var(--q-border);
      vertical-align: top;
    }
    .q-sym-row { cursor: pointer; }
    .q-sym-row:hover { background: rgba(88, 166, 255, 0.08); }
    .q-chapter-card {
      border: 1px solid var(--q-border);
      border-radius: 8px;
      padding: 11px 13px;
      margin-bottom: 10px;
      background: rgba(128, 128, 128, 0.025);
    }
    .q-chapter-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .q-chapter-title { margin: 0; font-size: 13.8px; }
    .q-chapter-summary {
      margin: 2px 0 0 0;
      color: var(--q-muted);
      font-size: 12px;
    }
    .q-chapter-right { display: flex; gap: 6px; align-items: center; }
    .q-chapter-bullets {
      margin: 6px 0 8px 0;
      padding-left: 18px;
      font-size: 12px;
      color: var(--q-muted);
    }
    .q-stop-card {
      border: 1px solid var(--q-border);
      border-radius: 7px;
      padding: 8px 11px;
      margin-top: 7px;
      background: var(--q-bg);
    }
    .q-stop-card:hover { border-color: var(--q-accent); }
    .q-stop-card.q-stop-read {
      border-left: 3px solid var(--q-success);
      opacity: 0.88;
    }
    .q-stop-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .q-stop-title-wrap {
      display: flex;
      align-items: baseline;
      gap: 6px;
      flex-wrap: wrap;
      flex: 1;
      min-width: 220px;
    }
    .q-read-pill {
      border: 1px solid var(--q-border);
      background: rgba(128, 128, 128, 0.12);
      color: var(--q-muted);
      border-radius: 999px;
      padding: 1px 6px;
      font-size: 10.5px;
      font-weight: 700;
      cursor: pointer;
    }
    .q-read-pill.is-read {
      background: rgba(63, 185, 80, 0.18);
      border-color: rgba(63, 185, 80, 0.5);
      color: var(--q-success);
    }
    .q-stop-num { font-weight: 700; color: var(--q-accent); font-size: 12px; }
    .q-stop-link { font-weight: 600; font-size: 12.5px; }
    .q-stop-inline-summary {
      color: var(--q-fg);
      font-size: 12.5px;
    }
    .q-stop-path {
      font-size: 11px;
      color: var(--q-muted);
      font-family: var(--vscode-editor-font-family, monospace);
    }
    .q-stop-actions { display: flex; gap: 4px; flex-wrap: wrap; }
    .q-stop-meta {
      font-size: 12px;
      color: var(--q-muted);
      margin-top: 4px;
    }
    .q-comment-card {
      border: 1px solid var(--q-border);
      border-radius: 7px;
      padding: 8px 11px;
      margin-bottom: 7px;
      background: var(--q-bg);
    }
    .q-comment-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .q-comment-title-group {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
      flex: 1;
    }
    .q-comment-quote {
      margin: 5px 0 0 0;
      padding: 5px 9px;
      border-left: 2.5px solid var(--q-border);
      background: rgba(128, 128, 128, 0.06);
      font-size: 12px;
    }
  </style>
</head>
<body>
  <div class="q-sticky-top">
    <div class="q-top-row">
      <div>
        <h1 class="q-pr-title">${escapeHtmlText(headerTitle)}</h1>
        <div class="q-pr-meta">
          <span><code>${escapeHtmlText(pr.headRefName || 'HEAD')}</code> → <code>${escapeHtmlText(
      pr.baseRefName || 'main'
    )}</code></span>
          <span>•</span>
          <span><code>@${escapeHtmlText(pr.author || 'local')}</code></span>
          <span>•</span>
          <span><code>+${pr.additions || 0} / -${pr.deletions || 0}</code> (${
      pr.changedFilesCount || 0
    } files)</span>
        </div>
      </div>
      <div class="q-toolbar-btns">
        <button type="button" class="q-btn q-btn-primary" data-act="nextTourStop">▶️ Next Stop</button>
        ${
          pr.isOwnedByUser && pr.needsBranchSwitch && pr.headRefName
            ? `<button type="button" class="q-btn q-btn-primary" data-act="runCommand" data-cmd="quasar.checkoutPrBranch">🔀 Switch to '${escapeHtmlText(
                pr.headRefName
              )}'</button>`
            : ''
        }
        ${
          queuedFixCount > 0
            ? `<button type="button" class="q-btn q-btn-primary" data-act="runCommand" data-cmd="quasar.fixSelectedComments">🔧 Fix (${queuedFixCount})</button>`
            : ''
        }
        <button type="button" class="q-btn" data-act="runCommand" data-cmd="quasar.exportPendingReview">🚀 Publish (${stagedCount})</button>
        <button type="button" class="q-btn" data-act="runCommand" data-cmd="quasar.closeReview" title="Close Review">🧹 Close</button>
      </div>
    </div>
    <div class="q-progress-row">
      <span class="q-progress-label">${readStopsCount}/${totalStopsCount} read (${progressPct}%)</span>
      <div class="q-progress-track">
        <div class="q-progress-fill" style="width: ${progressPct}%;"></div>
      </div>
      <div class="q-nav-pills">
        <button type="button" class="q-btn q-btn-sm" id="q-toggle-all-details">▾ Expand All Details</button>
        ${
          totalStopsCount > 0
            ? `<button type="button" class="q-btn q-btn-sm" data-act="markAllRead" data-read="${
                readStopsCount === totalStopsCount ? '0' : '1'
              }">${
                readStopsCount === totalStopsCount ? '↺ Reset Read' : '✓ Mark All Read'
              }</button>`
            : ''
        }
        <a href="#chapters-section" class="q-pill">📚 Tour (${totalStopsCount})</a>
        <a href="#comments-section" class="q-pill">💬 Comments (${activeThreads.length})</a>
      </div>
    </div>
  </div>

  <div class="q-section" id="summary-section">
    <h2>⚡ In Plain English</h2>
    <div class="q-tldr-box">${formatInlineMd(headlineSource)}</div>
    ${
      hasDeepDive
        ? `<details class="q-section-drawer" id="symbols-section">
            <summary>More context, Key Symbols (${(cn.keySymbols || []).length}) &amp; Trade-offs (${
            (cn.designTradeoffs || []).length
          })</summary>
            <div class="q-stop-details-body">
              ${
                rawWhatAndWhy && rawWhatAndWhy !== headlineSource
                  ? `<div style="margin-bottom:10px;">${formatInlineMd(rawWhatAndWhy)}</div>`
                  : ''
              }
              ${
                tradeoffsHtml
                  ? `<div style="margin-top:8px;"><strong>⚖️ Key Trade-offs:</strong><ul class="q-chapter-bullets">${tradeoffsHtml}</ul></div>`
                  : ''
              }
              ${
                symbolsRowsHtml
                  ? `<div style="margin-top:8px;">
                      <strong>🗂️ Key Symbols (click to jump to diff):</strong>
                      <table class="q-table">
                        <tbody>${symbolsRowsHtml}</tbody>
                      </table>
                    </div>`
                  : ''
              }
            </div>
          </details>`
        : ''
    }
  </div>

  ${
    cn.architectureDiagram
      ? `<div class="q-section" id="architecture-section">
          <h2>🧭 Architecture &amp; Data Flow</h2>
          <div id="quasar-flowchart-mount"></div>
        </div>`
      : ''
  }

  <div class="q-section" id="chapters-section">
    <h2>
      <span>📚 Guided Reading Tour</span>
      <span style="font-size:11.5px;font-weight:400;color:var(--q-muted);">Click ▸ on any stop for context &amp; code snippet, or click 🔍 Diff</span>
    </h2>
    ${chaptersHtml || '<p>No tour chapters available yet.</p>'}
  </div>

  <div class="q-section" id="comments-section">
    <h2>
      <span>💬 Review Comments (${activeThreads.length})</span>
      <span>
        ${
          uncertainCount > 0
            ? `<button type="button" class="q-btn q-btn-sm q-btn-primary" data-act="runCommand" data-cmd="quasar.calibrateFindingPreference">🤔 Calibrate ${uncertainCount} Uncertain</button>`
            : ''
        }
      </span>
    </h2>
    ${commentsCardsHtml || '<p style="color:var(--q-muted);">No review comments in this session.</p>'}
  </div>

  ${
    coverageRowsHtml
      ? `<div class="q-section" id="coverage-section">
          <details class="q-section-drawer">
            <summary><strong>🧪 Test Coverage &amp; Hygiene Matrix (${
              (session.coverageMatrix || []).length
            } checks)</strong></summary>
            <div class="q-stop-details-body">
              <table class="q-table">
                <thead><tr><th>Behavior / Change</th><th>Status</th><th>Notes</th></tr></thead>
                <tbody>${coverageRowsHtml}</tbody>
              </table>
            </div>
          </details>
        </div>`
      : ''
  }

  <script src="${mermaidScriptUri}"></script>
  <script>
    (function () {
      const vscode = acquireVsCodeApi();
      const diagramSource = ${JSON.stringify(cn.architectureDiagram || '')};
      const stopsLookup = ${JSON.stringify(stopsLookupPayload)};
      const initialAnchor = ${JSON.stringify(sectionAnchor || '')};

      function matchStopClientSide(label) {
        if (!label || !stopsLookup.length) return null;
        const q = String(label).toLowerCase();
        const tokens = q.split(/[^a-z0-9_./-]+/).filter((t) => t.length >= 3);
        let best = null;
        let bestScore = 0;
        for (const s of stopsLookup) {
          let score = 0;
          if (s.baseName && q.includes(s.baseName)) score += 120;
          else if (s.stemName && s.stemName.length >= 4 && q.includes(s.stemName)) score += 85;
          for (const tok of tokens) {
            if (tok.includes('/') && s.path.toLowerCase().includes(tok.replace(/^\\//, ''))) score += 95;
            else if (s.baseName === tok || s.stemName === tok) score += 90;
            else if (s.path.toLowerCase().includes(tok) && tok.length >= 4) score += 45;
            else if (s.searchHay.includes(tok) && tok.length >= 4) score += 28;
          }
          if (score > bestScore) {
            bestScore = score;
            best = s;
          }
        }
        if (bestScore >= 25 && best) {
          return {
            stopId: best.stopId,
            path: best.path,
            line: best.line,
            badge: 'Stop ' + best.stopNumber + ' (' + best.path + ')',
          };
        }
        return null;
      }

      const mountEl = document.getElementById('quasar-flowchart-mount');
      if (mountEl && diagramSource && window.QuasarFlowchartRenderer) {
        window.QuasarFlowchartRenderer.mountInteractiveFlowchart(mountEl, diagramSource, {
          resolveNodeTarget: (node) => matchStopClientSide(node.label),
          onNodeClick: (info) => {
            if (info.stopId) {
              vscode.postMessage({ type: 'openStop', stopId: info.stopId });
            } else if (info.label) {
              vscode.postMessage({ type: 'jumpToQuery', query: info.label });
            }
          },
        });
      }

      let allExpanded = false;
      const toggleAllBtn = document.getElementById('q-toggle-all-details');
      if (toggleAllBtn) {
        toggleAllBtn.addEventListener('click', () => {
          allExpanded = !allExpanded;
          document.querySelectorAll('details.q-stop-details, details.q-section-drawer').forEach((d) => {
            d.open = allExpanded;
          });
          toggleAllBtn.textContent = allExpanded ? '▸ Collapse All Details' : '▾ Expand All Details';
        });
      }

      document.addEventListener('click', (ev) => {
        const el = ev.target.closest('[data-act]');
        if (!el) return;
        ev.preventDefault();
        ev.stopPropagation();
        const act = el.getAttribute('data-act');
        if (act === 'openStop') {
          vscode.postMessage({ type: 'openStop', stopId: el.getAttribute('data-stop-id') });
        } else if (act === 'toggleStopRead') {
          vscode.postMessage({ type: 'toggleStopRead', stopId: el.getAttribute('data-stop-id') });
        } else if (act === 'askAtStop') {
          vscode.postMessage({ type: 'askAtStop', stopId: el.getAttribute('data-stop-id') });
        } else if (act === 'markChapterRead') {
          vscode.postMessage({
            type: 'markChapterRead',
            chapterId: el.getAttribute('data-chapter-id'),
            read: el.getAttribute('data-read') === '1',
          });
        } else if (act === 'markAllRead') {
          vscode.postMessage({
            type: 'markAllRead',
            read: el.getAttribute('data-read') === '1',
          });
        } else if (act === 'jumpToQuery') {
          vscode.postMessage({ type: 'jumpToQuery', query: el.getAttribute('data-query') });
        } else if (act === 'nextTourStop') {
          vscode.postMessage({ type: 'nextTourStop' });
        } else if (act === 'jumpToThread') {
          vscode.postMessage({ type: 'jumpToThread', threadId: el.getAttribute('data-thread-id') });
        } else if (act === 'toggleStageThread') {
          vscode.postMessage({ type: 'toggleStageThread', threadId: el.getAttribute('data-thread-id') });
        } else if (act === 'toggleFixThread') {
          vscode.postMessage({ type: 'toggleFixThread', threadId: el.getAttribute('data-thread-id') });
        } else if (act === 'dismissThread') {
          vscode.postMessage({ type: 'dismissThread', threadId: el.getAttribute('data-thread-id') });
        } else if (act === 'calibrateThread') {
          vscode.postMessage({ type: 'calibrateThread', threadId: el.getAttribute('data-thread-id') });
        } else if (act === 'openRawMarkdown') {
          vscode.postMessage({ type: 'openRawMarkdown' });
        } else if (act === 'runCommand') {
          vscode.postMessage({ type: 'runCommand', command: el.getAttribute('data-cmd') });
        }
      });

      if (initialAnchor) {
        const target = document.getElementById(initialAnchor);
        if (target) {
          if (target.tagName === 'DETAILS') target.open = true;
          setTimeout(() => target.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
        }
      }
    })();
  </script>
</body>
</html>`;
  }

  function updateCliffsNotesWebviewContent(sectionAnchor = '') {
    if (!cliffsNotesWebviewPanel) return;
    const session = loadActiveSession();
    const pr = (session && session.pr) || {};
    cliffsNotesWebviewPanel.title = pr.number
      ? `📖 CliffsNotes: PR #${pr.number}`
      : `📖 CliffsNotes: ${pr.title || 'Study Guide'}`;
    cliffsNotesWebviewPanel.webview.html = buildCliffsNotesWebviewHtml(
      cliffsNotesWebviewPanel.webview,
      session,
      sectionAnchor
    );
  }

  async function openInteractiveCliffsNotesPanel(sectionAnchor = '') {
    const session = loadActiveSession();
    const mdPath = getWindowCliffsNotesMdPath();
    if (!session && !fs.existsSync(mdPath)) {
      vscode.window.showInformationMessage(
        'No active CliffsNotes guide in this window yet. Load a PR URL or local branch first.'
      );
      return;
    }

    const targetColumn =
      (vscode.window.activeTextEditor && vscode.window.activeTextEditor.viewColumn) ||
      vscode.ViewColumn.Active ||
      vscode.ViewColumn.One;

    if (cliffsNotesWebviewPanel) {
      cliffsNotesWebviewPanel.reveal(cliffsNotesWebviewPanel.viewColumn || targetColumn, false);
      updateCliffsNotesWebviewContent(sectionAnchor);
      return;
    }

    const pr = (session && session.pr) || {};
    const panelTitle = pr.number
      ? `📖 CliffsNotes: PR #${pr.number}`
      : `📖 CliffsNotes: ${pr.title || 'Study Guide'}`;

    cliffsNotesWebviewPanel = vscode.window.createWebviewPanel(
      'quasarCliffsNotesGuide',
      panelTitle,
      { viewColumn: targetColumn, preserveFocus: false },
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.file(path.join(context.extensionPath, 'media'))],
      }
    );

    cliffsNotesWebviewPanel.onDidDispose(() => {
      cliffsNotesWebviewPanel = null;
    });

    cliffsNotesWebviewPanel.webview.onDidReceiveMessage(async (msg) => {
      if (!msg || !msg.type) return;
      const curSession = loadActiveSession();
      if (!curSession) return;

      if (msg.type === 'openStop') {
        const stops = getAllTourStops(curSession);
        const found = stops.find((s) => s.id === msg.stopId);
        if (found) await openTourStopInDiff(found, false);
      } else if (msg.type === 'toggleStopRead') {
        for (const ch of curSession.chapters || []) {
          for (const s of ch.stops || []) {
            if (s.id === msg.stopId) {
              s.read = !s.read;
            }
          }
        }
        saveActiveSession(curSession);
        refreshAllUI(false);
      } else if (msg.type === 'markChapterRead') {
        for (const ch of curSession.chapters || []) {
          if (ch.id === msg.chapterId) {
            for (const s of ch.stops || []) {
              s.read = Boolean(msg.read);
            }
          }
        }
        saveActiveSession(curSession);
        refreshAllUI(false);
      } else if (msg.type === 'markAllRead') {
        for (const ch of curSession.chapters || []) {
          for (const s of ch.stops || []) {
            s.read = Boolean(msg.read);
          }
        }
        saveActiveSession(curSession);
        refreshAllUI(false);
      } else if (msg.type === 'askAtStop') {
        const stops = getAllTourStops(curSession);
        const found = stops.find((s) => s.id === msg.stopId);
        if (found) {
          await vscode.commands.executeCommand('quasar.askAtLine', {
            path: found.path,
            line: found.startLine || 1,
            stopTitle: found.title,
          });
        }
      } else if (msg.type === 'jumpToQuery') {
        const matched = findBestTourStopForQuery(curSession, msg.query || '');
        if (matched) {
          await openTourStopInDiff(matched, false);
        } else {
          const stops = getAllTourStops(curSession);
          if (stops.length > 0) await openTourStopInDiff(stops[0], false);
        }
      } else if (msg.type === 'nextTourStop') {
        await vscode.commands.executeCommand('quasar.nextTourStop');
      } else if (msg.type === 'jumpToThread') {
        const t = (curSession.threads || []).find((th) => th.id === msg.threadId);
        if (t) await vscode.commands.executeCommand('quasar.jumpToThread', t);
      } else if (msg.type === 'toggleStageThread') {
        const t = (curSession.threads || []).find((th) => th.id === msg.threadId);
        if (t) {
          if (t.status === 'staged') {
            t.status = 'candidate';
            t.unstagedByUser = true;
          } else {
            t.status = 'staged';
            delete t.unstagedByUser;
          }
          saveActiveSession(curSession);
          refreshAllUI(false);
        }
      } else if (msg.type === 'toggleFixThread') {
        const t = (curSession.threads || []).find((th) => th.id === msg.threadId);
        if (t) {
          t.selectedForFix = !t.selectedForFix;
          saveActiveSession(curSession);
          refreshAllUI(false);
        }
      } else if (msg.type === 'dismissThread') {
        const t = (curSession.threads || []).find((th) => th.id === msg.threadId);
        if (t) {
          t.status = 'dismissed';
          t.unstagedByUser = true;
          t.selectedForFix = false;
          saveActiveSession(curSession);
          refreshAllUI(false);
        }
      } else if (msg.type === 'calibrateThread') {
        const t = (curSession.threads || []).find((th) => th.id === msg.threadId);
        if (t) {
          await vscode.commands.executeCommand('quasar.calibrateFindingPreference', {
            threadData: t,
          });
        }
      } else if (msg.type === 'openRawMarkdown') {
        const rawMd = getWindowCliffsNotesMdPath();
        if (fs.existsSync(rawMd)) {
          await vscode.window.showTextDocument(vscode.Uri.file(rawMd), {
            viewColumn: vscode.ViewColumn.Active,
          });
        }
      } else if (msg.type === 'runCommand' && msg.command) {
        await vscode.commands.executeCommand(msg.command);
      }
    });

    updateCliffsNotesWebviewContent(sectionAnchor);
  }

  async function executeCheckoutPrBranch(stashIfDirty = false) {
    const session = loadActiveSession();
    if (!session || !session.pr) return;
    const pr = session.pr;
    const headRef = String(pr.headBranch || pr.headRefName || '').replace(' + working tree', '').trim();
    if (!headRef) return;
    const collectScript = path.join(PLUGIN_SCRIPTS_DIR, 'collect_pr_context.py');
    const windowWsRoot = getWindowWorkspaceRoot();
    const args = [collectScript, '--checkout-pr-branch', '--workspace-dir', windowWsRoot];
    if (session.repoDir) {
      args.push('--repo-dir', session.repoDir);
    }
    if (stashIfDirty) {
      args.push('--stash-if-dirty');
    }

    await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: `Quasar: Switching ${path.basename(session.repoDir || windowWsRoot)} to branch '${headRef}'...`,
        cancellable: false,
      },
      () =>
        new Promise((resolve) => {
          execFile(
            'python3',
            args,
            { timeout: 30000, env: getQuasarScriptEnv() },
            async (err, stdout, stderr) => {
              let parsed = null;
              try {
                parsed = JSON.parse(stdout || '{}');
              } catch (_) {}
              if (err || !parsed || !parsed.ok) {
                const isDirty = parsed && parsed.hasUncommittedChanges;
                if (isDirty && !stashIfDirty) {
                  const stashChoice = await vscode.window.showWarningMessage(
                    `Uncommitted changes in ${path.basename(
                      session.repoDir || windowWsRoot
                    )} prevented switching to '${headRef}'. Stash your changes and switch now?`,
                    `Stash & Switch to '${headRef}'`,
                    'Cancel'
                  );
                  if (stashChoice === `Stash & Switch to '${headRef}'`) {
                    resolve();
                    await executeCheckoutPrBranch(true);
                    return;
                  }
                } else {
                  vscode.window.showErrorMessage(
                    `Failed to switch to branch '${headRef}': ${
                      (parsed && parsed.error) || (stderr && stderr.trim()) || (err && err.message) || 'Unknown error'
                    }`
                  );
                }
                resolve();
                return;
              }
              refreshAllUI(false);
              vscode.window.showInformationMessage(
                `🔀 Switched ${path.basename(
                  parsed.repoDir || session.repoDir || windowWsRoot
                )} from '${parsed.previousBranch || 'previous branch'}' to '${
                  parsed.checkedOutBranch || headRef
                }'${parsed.stashed ? ' (uncommitted changes auto-stashed)' : ''}.`
              );
              resolve();
            }
          );
        })
    );
  }

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.checkoutPrBranch', async () => {
      await executeCheckoutPrBranch(false);
    })
  );

  async function maybePromptToCheckoutOwnedPrBranch(session) {
    if (!session || !session.pr) return;
    const pr = session.pr;
    const sid = session.sessionId || `pr-${pr.number}`;
    if (!pr.isOwnedByUser || !pr.needsBranchSwitch || !pr.headRefName || !pr.number) return;
    if (promptedBranchSwitchSessionIds.has(sid)) return;
    promptedBranchSwitchSessionIds.add(sid);

    const headRef = String(pr.headBranch || pr.headRefName || '').replace(' + working tree', '').trim();
    const curBranch = pr.localCurrentBranch || 'another branch';
    const repoName = path.basename(session.repoDir || pr.repo || 'repository');
    const switchBtn = pr.hasUncommittedChanges
      ? `Stash Changes & Switch to '${headRef}'`
      : `Switch to '${headRef}'`;
    const stayBtn = `Stay on '${curBranch}'`;

    const choice = await vscode.window.showInformationMessage(
      `🔀 PR #${pr.number} is your PR, and your local '${repoName}' repo is currently on branch '${curBranch}'. Switch to '${headRef}'?`,
      switchBtn,
      stayBtn
    );
    if (choice === switchBtn) {
      await executeCheckoutPrBranch(Boolean(pr.hasUncommittedChanges));
    }
  }

  function refreshAllUI(autoOpenIfNew = false) {
    const session = loadActiveSession();
    cliffsProvider.refresh();
    commentsProvider.refresh();
    checksProvider.refresh();
    updateStatusBar(session);
    syncNativeCommentThreads(session);
    codeLensEmitter.fire();
    updateCliffsNotesWebviewContent();

    if (session && autoOpenIfNew) {
      const isNewSession = session.sessionId !== lastSessionId;
      const justFinishedAnalysis =
        lastAnalysisStatus === 'analyzing' && session.analysisStatus === 'ready';
      lastSessionId = session.sessionId;
      lastAnalysisStatus = session.analysisStatus;

      if (isNewSession) {
        maybePromptToCheckoutOwnedPrBranch(session);
      }

      if (isNewSession || justFinishedAnalysis) {
        const stops = getAllTourStops(session);
        if (stops.length > 0) {
          openTourStopInDiff(stops[0], true);
        }
        if (justFinishedAnalysis) {
          openInteractiveCliffsNotesPanel().catch(() => {});
        }
      }
    }
  }

  // Native Hover Provider: CliffsNotes on Hover (zero code clutter!)
  context.subscriptions.push(
    vscode.languages.registerHoverProvider('*', {
      provideHover(document, position) {
        const session = loadActiveSession();
        if (!session) return null;
        const line1 = position.line + 1;
        const stops = getAllTourStops(session);

        for (const stop of stops) {
          if (!matchesSessionPath(document.uri.fsPath, stop.path, session)) continue;
          const padStart = Math.max(1, (stop.startLine || 1) - 8);
          const padEnd = (stop.endLine || stop.startLine || 1) + 15;
          if (line1 >= padStart && line1 <= padEnd) {
            const md = new vscode.MarkdownString('', true);
            md.isTrusted = true;
            md.appendMarkdown(
              `### 📘 Quasar CliffsNotes — Stop ${stop.stopNumber} (${stop.chapterTitle})\n\n`
            );
            md.appendMarkdown(`**Summary**: ${stop.cliffsNotes || stop.description || ''}\n\n`);
            if (stop.whyItChanged) {
              md.appendMarkdown(`**Why It Changed**: ${stop.whyItChanged}\n\n`);
            }
            if (stop.callersAndInvariants) {
              md.appendMarkdown(`**Context & Invariants**: ${stop.callersAndInvariants}\n\n`);
            }
            const askArgs = encodeURIComponent(
              JSON.stringify({ path: stop.path, line: line1, stopTitle: stop.title })
            );
            md.appendMarkdown(
              `---\n[$(comment-discussion) Ask Quasar in Comment](command:quasar.askAtLine?${askArgs})  •  ` +
                `[$(book) Open CliffsNotes Guide](command:quasar.openCliffsNotesGuide)  •  ` +
                `[$(arrow-down) Next Tour Stop](command:quasar.nextTourStop)`
            );
            return new vscode.Hover(md);
          }
        }
        return null;
      },
    })
  );

  // Native CodeLens Provider at the start of each Tour Stop
  context.subscriptions.push(
    vscode.languages.registerCodeLensProvider('*', {
      onDidChangeCodeLenses: codeLensEmitter.event,
      provideCodeLenses(document) {
        const session = loadActiveSession();
        if (!session) return [];
        const lenses = [];
        const stops = getAllTourStops(session);
        for (const stop of stops) {
          if (!matchesSessionPath(document.uri.fsPath, stop.path, session)) continue;
          const line0 = Math.max(0, Math.min(document.lineCount - 1, (stop.startLine || 1) - 1));
          const range = new vscode.Range(line0, 0, line0, 0);
          lenses.push(
            new vscode.CodeLens(range, {
              title: `📘 CliffsNotes ${stop.stopNumber}: ${(stop.cliffsNotes || stop.description || '').slice(0, 75)}`,
              command: 'quasar.openCliffsNotesGuide',
            }),
            new vscode.CodeLens(range, {
              title: `💬 Ask Quasar`,
              command: 'quasar.askAtLine',
              arguments: [{ path: stop.path, line: stop.startLine || 1, stopTitle: stop.title }],
            }),
            new vscode.CodeLens(range, {
              title: `➡️ Next Stop`,
              command: 'quasar.nextTourStop',
            })
          );
        }
        return lenses;
      },
    })
  );

  // Watch ONLY this window's isolated workspace state directory for active_session.json updates,
  // and watch global REVIEW_STATE_DIR only for preferences.json / github_auth.json (without auto-opening diffs).
  let watchDebounce = null;
  let windowStateWatcher = null;
  function bindWindowStateWatcher() {
    if (windowStateWatcher) {
      try {
        windowStateWatcher.close();
      } catch (_) {}
      windowStateWatcher = null;
    }
    const myStateDir = getWindowStateDir();
    fs.mkdirSync(myStateDir, { recursive: true });
    try {
      windowStateWatcher = fs.watch(myStateDir, (_eventType, filename) => {
        if (!filename || filename === 'active_session.json') {
          if (watchDebounce) clearTimeout(watchDebounce);
          watchDebounce = setTimeout(() => refreshAllUI(true), 180);
        }
      });
    } catch (_) {}
  }
  bindWindowStateWatcher();

  context.subscriptions.push(
    vscode.workspace.onDidChangeWorkspaceFolders(() => {
      bindWindowStateWatcher();
      refreshAllUI(false);
    })
  );

  let globalPrefsDebounce = null;
  try {
    const globalWatcher = fs.watch(REVIEW_STATE_DIR, (_eventType, filename) => {
      if (filename === 'preferences.json' || filename === 'github_auth.json') {
        if (globalPrefsDebounce) clearTimeout(globalPrefsDebounce);
        globalPrefsDebounce = setTimeout(() => refreshAllUI(false), 180);
      }
    });
    context.subscriptions.push({ dispose: () => globalWatcher.close() });
  } catch (_) {}

  // ==========================================================================
  // Commands
  // ==========================================================================

  async function focusReviewSidebar() {
    try {
      await vscode.commands.executeCommand('quasar.cliffsNotesTree.focus');
    } catch (_) {
      try {
        await vscode.commands.executeCommand('workbench.view.extension.quasar');
      } catch (_) {}
    }
  }

  // 0. Quick Menu & Sidebar Launcher (bound to always-visible Status Bar item)
  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.openMenu', async () => {
      await focusReviewSidebar();
      const session = loadActiveSession();
      const prefs = loadReviewerPreferences();
      const rulesCount = (prefs.rules || []).length;
      const uncertainCount = session
        ? (session.threads || []).filter((t) => t.status === 'uncertain').length
        : 0;
      const hasSession = Boolean(session && session.pr);
      const pastReviews = listPastReviews();
      const items = [
        {
          label: '$(git-branch) Review Local Branch or Working-Tree Diff...',
          description: 'Pick a local git branch, commit range, or uncommitted changes',
          command: 'quasar.reviewLocalBranch',
        },
        {
          label: '$(git-pull-request) Review GitHub PR from URL or Number...',
          description: 'Paste a GitHub PR link or PR number',
          command: 'quasar.loadPrFromUrl',
        },
        {
          label: `$(history) Past Reviews / Switch Review Session (${pastReviews.length} saved)...`,
          description: 'Browse and reopen any previous PR or local branch review',
          command: 'quasar.switchPastReview',
        },
        {
          label: '$(sparkle) Import Premade Review Skill as Starting Point...',
          description: 'Seed Quasar with an existing SKILL.md or review guide without overwriting defaults',
          command: 'quasar.importReviewSkill',
        },
        {
          label: '$(settings-gear) Manage & Calibrate Reviewer Preferences...',
          description: `${rulesCount} rule(s)${
            uncertainCount > 0 ? ` • ${uncertainCount} uncertain finding(s) awaiting calibration` : ''
          }`,
          command: 'quasar.managePreferences',
        },
        {
          label: '$(lightbulb-sparkle) Teach Quasar / Give Feedback on Review...',
          description: 'Tell Quasar what to flag more of, what to skip, or how to adjust tone',
          command: 'quasar.giveMetaFeedback',
        },
        {
          label: (() => {
            const gh = loadStoredGithubAuth();
            return gh && gh.login
              ? `$(key) GitHub API Key: @${gh.login} (Update / Clear Token...)`
              : '$(key) Configure GitHub API Key / Personal Access Token...';
          })(),
          description: 'Publish reviews, comments, approvals & change requests to PRs as you',
          command: 'quasar.configureGithubToken',
        },
        {
          label: '$(layout-sidebar-left) Focus Quasar Sidebar',
          description: 'Opens the CliffsNotes & Reading Tour panel in the sidebar',
          command: 'focus-only',
        },
      ];
      if (hasSession) {
        const queuedFixCount = (session.threads || []).filter(
          (t) => t.selectedForFix && t.status !== 'fixed'
        ).length;
        const fixableCount = (session.threads || []).filter(
          (t) => t.status !== 'dismissed' && t.status !== 'fixed'
        ).length;
        if (fixableCount > 0 && (session.pr.isOwnedByUser !== false || queuedFixCount > 0)) {
          items.unshift({
            label:
              queuedFixCount > 0
                ? `$(wrench) Fix ${queuedFixCount} Checked Comment(s) with Agent...`
                : '$(wrench) Select & Fix Review Comments with Agent...',
            description: 'Apply fixes in your local repo working tree & mark comments Fixed',
            command: 'quasar.fixSelectedComments',
          });
        }
        if (uncertainCount > 0) {
          items.unshift({
            label: `$(question) Calibrate ${uncertainCount} Uncertain Finding(s)...`,
            description: 'Quasar has preference questions on borderline feedback in this review',
            command: 'quasar.calibrateFindingPreference',
          });
        }
        items.push(
          {
            label: '$(book) Open Full CliffsNotes Study Guide',
            description: 'Open markdown preview beside editor',
            command: 'quasar.openCliffsNotesGuide',
          },
          {
            label: '$(arrow-down) Next CliffsNotes Tour Stop',
            description: 'Step to the next logical stop in the diff editor (Alt+Down)',
            command: 'quasar.nextTourStop',
          },
          {
            label: '$(cloud-upload) Publish / Export Review to GitHub (as You)...',
            description: 'Publish review comments, approve, request changes, or create a pending draft on GitHub',
            command: 'quasar.exportPendingReview',
          },
          {
            label: '$(debug-restart) Reset / Re-Run Current Branch or PR Review...',
            description: 'Re-run fresh diff/analysis, reset tour & comment states, or clear review',
            command: 'quasar.resetReview',
          },
          {
            label: '$(close) Close Active Review & Clear IDE Annotations',
            description: 'Remove all inline comments, CodeLenses, hover cards & diff tabs from this window',
            command: 'quasar.closeReview',
          }
        );
      }
      const picked = await vscode.window.showQuickPick(items, {
        title: `Quasar (${path.basename(getWindowWorkspaceRoot())})`,
        placeHolder: 'Choose a review or preference calibration action for this window',
      });
      if (!picked || picked.command === 'focus-only') return;
      await vscode.commands.executeCommand(picked.command);
    })
  );

  // 1. Load PR from GitHub URL (or local branch name/path)
  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.loadPrFromUrl', async (providedUrl) => {
      const prUrl =
        providedUrl ||
        (await vscode.window.showInputBox({
          title: 'Quasar: GitHub PR Link or Local Branch',
          prompt: 'Paste a GitHub PR URL/number, or enter a local branch name (e.g. feature/my-branch or HEAD)',
          placeHolder: 'https://github.com/flutter/packages/pull/13265 or feature/my-branch',
        }));
      if (!prUrl || !prUrl.trim()) return;

      const trimmedUrl = prUrl.trim();
      const collectScript = path.join(PLUGIN_SCRIPTS_DIR, 'collect_pr_context.py');
      const workspaceRepo = await detectWorkspaceRepoDir();
      const windowWsRoot = getWindowWorkspaceRoot();
      const windowStateDir = getWindowStateDir();
      await focusReviewSidebar();

      await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: `Quasar: Loading ${trimmedUrl}...`,
          cancellable: false,
        },
        (progress) =>
          new Promise((resolve) => {
            progress.report({ message: 'Fetching diff, comments & materializing snapshots...' });
            const scriptArgs = [
              collectScript,
              '--pr',
              trimmedUrl,
              '--workspace-dir',
              windowWsRoot,
            ];
            if (workspaceRepo) {
              scriptArgs.push('--repo-dir', workspaceRepo);
            }
            execFile(
              'python3',
              scriptArgs,
              { timeout: 60000, env: getQuasarScriptEnv() },
              async (err) => {
                if (err) {
                  vscode.window.showErrorMessage(`Failed to fetch review context: ${err.message}`);
                  resolve();
                  return;
                }
                refreshAllUI(true);
                progress.report({
                  message: 'Initial tour ready! Dispatching Quasar multi-lens CliffsNotes synthesis...',
                });
                await sendPromptToQuasar(
                  `Review ${trimmedUrl} using the quasar skill for workspace "${windowWsRoot}" (context initialized in "${windowStateDir}/pr_context.json") and publish the CliffsNotes session via \`publish_review_session.py --workspace-dir "${windowWsRoot}"\`.`,
                  `Quasar: ${trimmedUrl}`
                );
                resolve();
              }
            );
          })
      );
    })
  );

  // 1b. Review Local Branch or Diff (Interactive Branch & Repo Picker)
  async function pickRepoFolder(currentRepoDir) {
    const discovered = discoverLocalGitRepos();
    const items = discovered.map((r) => ({
      label: `$(repo) ${path.basename(r)}`,
      description: r === currentRepoDir ? 'Current' : r,
      detail: r,
      repoDir: r,
    }));
    items.push({
      label: '$(folder-opened) Browse for another repository folder...',
      description: 'Open folder dialog',
      action: 'browse',
    });

    const picked = await vscode.window.showQuickPick(items, {
      title: 'Quasar: Select Local Git Repository',
      placeHolder: 'Choose which local repository to review',
    });
    if (!picked) return null;
    if (picked.action === 'browse') {
      const uris = await vscode.window.showOpenDialog({
        canSelectFiles: false,
        canSelectFolders: true,
        canSelectMany: false,
        openLabel: 'Select Git Repository',
      });
      if (!uris || uris.length === 0) return null;
      return (await resolveGitRoot(uris[0].fsPath)) || uris[0].fsPath;
    }
    return picked.repoDir;
  }

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.reviewLocalBranch', async (providedArgs) => {
      let repoDir =
        (providedArgs && providedArgs.repoDir) || (await detectWorkspaceRepoDir());
      if (!repoDir) {
        repoDir = await pickRepoFolder(null);
      }
      if (!repoDir) return;

      let targetBranch = providedArgs && (providedArgs.branch || (typeof providedArgs === 'string' ? providedArgs : null));
      let targetBase = providedArgs && providedArgs.base ? providedArgs.base : null;
      let committedOnly = Boolean(providedArgs && providedArgs.committedOnly);

      while (!targetBranch) {
        const repoName = path.basename(repoDir);
        const curBranchRes = await runGit(['branch', '--show-current'], repoDir);
        const currentBranch = curBranchRes.stdout || 'HEAD';
        const statusRes = await runGit(['status', '--porcelain', '--untracked-files=no'], repoDir);
        const hasUncommitted = Boolean(statusRes.stdout);
        const refsRes = await runGit(
          [
            'for-each-ref',
            '--sort=-committerdate',
            'refs/heads/',
            '--format=%(refname:short)|%(objectname:short)|%(contents:subject)|%(committerdate:relative)',
          ],
          repoDir
        );

        const branchItems = [];
        branchItems.push({
          label: `$(git-branch) Current Branch: ${currentBranch}`,
          description: hasUncommitted
            ? 'Includes uncommitted working-tree changes'
            : 'Latest committed state',
          detail: `Repository: ${repoName} (${repoDir})`,
          branch: currentBranch,
          committedOnly: false,
        });

        if (hasUncommitted) {
          branchItems.push({
            label: `$(git-commit) Current Branch (Committed Only): ${currentBranch}`,
            description: 'Ignore uncommitted working-tree edits',
            detail: `Only review committed changes on ${currentBranch}`,
            branch: currentBranch,
            committedOnly: true,
          });
        }

        const lines = (refsRes.stdout || '').split('\n').filter(Boolean);
        for (const line of lines) {
          const [bName, shortSha, subject, relDate] = line.split('|');
          if (!bName || bName === currentBranch) continue;
          branchItems.push({
            label: `$(git-branch) ${bName}`,
            description: `${shortSha || ''} • ${relDate || ''}`,
            detail: subject || '',
            branch: bName,
            committedOnly: true,
          });
        }

        branchItems.push(
          {
            label: '$(git-compare) Enter custom branch, ref, or base...head range...',
            description: 'e.g. origin/main...feature-x or HEAD~3',
            action: 'custom',
          },
          {
            label: `$(folder-opened) Switch repository folder (currently: ${repoName})...`,
            description: repoDir,
            action: 'switch-repo',
          }
        );

        const picked = await vscode.window.showQuickPick(branchItems, {
          title: `Quasar: Select Local Branch (${repoName})`,
          placeHolder: 'Choose a local branch or working-tree diff to review',
        });
        if (!picked) return;

        if (picked.action === 'switch-repo') {
          const nextRepo = await pickRepoFolder(repoDir);
          if (!nextRepo) return;
          repoDir = nextRepo;
          continue;
        }

        if (picked.action === 'custom') {
          const customInput = await vscode.window.showInputBox({
            title: `Custom Branch or Diff Range (${repoName})`,
            prompt: 'Enter a branch name, commit ref, or range (e.g. main...my-branch)',
            placeHolder: 'feature/my-branch or origin/main...HEAD',
          });
          if (!customInput || !customInput.trim()) return;
          targetBranch = customInput.trim();
          break;
        }

        targetBranch = picked.branch;
        committedOnly = Boolean(picked.committedOnly);
      }

      // Allow selecting base branch if not already specified in a range
      if (!targetBase && !targetBranch.includes('..')) {
        const baseCandidates = [
          {
            label: '$(sparkle) Auto-detect Base Branch (Recommended)',
            description: 'Automatically diffs against merge-base with upstream/main, origin/main, or main',
            base: 'auto',
          },
        ];
        for (const cand of ['upstream/main', 'origin/main', 'main', 'upstream/master', 'origin/master', 'master', 'HEAD~1']) {
          const check = await runGit(['rev-parse', '--verify', '--quiet', cand], repoDir);
          if (check.ok) {
            baseCandidates.push({
              label: `$(git-merge) ${cand}`,
              description: `Compare ${targetBranch} against ${cand}`,
              base: cand,
            });
          }
        }
        const pickedBase = await vscode.window.showQuickPick(baseCandidates, {
          title: `Compare "${targetBranch}" Against Base Ref`,
          placeHolder: 'Press Enter for automatic base detection, or choose a specific base ref',
        });
        if (!pickedBase) return;
        targetBase = pickedBase.base;
      }

      const collectScript = path.join(PLUGIN_SCRIPTS_DIR, 'collect_pr_context.py');
      const repoName = path.basename(repoDir);
      const windowWsRoot = getWindowWorkspaceRoot();
      const windowStateDir = getWindowStateDir();
      await focusReviewSidebar();

      await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: `Quasar: Loading branch ${targetBranch} in ${repoName}...`,
          cancellable: false,
        },
        (progress) =>
          new Promise((resolve) => {
            progress.report({ message: 'Computing branch diff & materializing snapshots...' });
            const scriptArgs = [
              collectScript,
              '--repo-dir',
              repoDir,
              '--workspace-dir',
              windowWsRoot,
              '--branch',
              targetBranch,
            ];
            if (targetBase && targetBase !== 'auto') {
              scriptArgs.push('--base', targetBase);
            }
            if (committedOnly) {
              scriptArgs.push('--committed-only');
            }

            execFile(
              'python3',
              scriptArgs,
              { timeout: 60000, env: getQuasarScriptEnv() },
              async (err, _stdout, stderr) => {
                if (err) {
                  vscode.window.showErrorMessage(
                    `Failed to collect local branch diff: ${stderr || err.message}`
                  );
                  resolve();
                  return;
                }
                refreshAllUI(true);
                progress.report({
                  message: 'Initial tour ready! Dispatching Quasar multi-lens CliffsNotes synthesis...',
                });
                const baseDesc = targetBase && targetBase !== 'auto' ? ` (base: ${targetBase})` : '';
                await sendPromptToQuasar(
                  `Review local branch "${targetBranch}"${baseDesc} in repository "${repoDir}" using the quasar skill for workspace "${windowWsRoot}". Context and snapshots are already initialized in "${windowStateDir}/active_session.json" (and "${windowStateDir}/pr_context.json" / "${windowStateDir}/pr.diff"). Run Phase 2 (parallel review lenses) and Phase 3 (\`publish_review_session.py --workspace-dir "${windowWsRoot}"\`).`,
                  `Quasar: ${repoName}@${targetBranch}`
                );
                resolve();
              }
            );
          })
      );
    })
  );

  // 2. Open Full Interactive CliffsNotes Study Guide beside editor
  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.openCliffsNotesGuide', async (sectionAnchor) => {
      await openInteractiveCliffsNotesPanel(
        typeof sectionAnchor === 'string' ? sectionAnchor : ''
      );
    })
  );

  // 3. Open specific Tour Stop
  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.openTourStop', async (stop) => {
      await openTourStopInDiff(stop, false);
    })
  );

  // 4. Next / Previous Tour Stop
  let currentStopIndex = 0;
  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.nextTourStop', async () => {
      const session = loadActiveSession();
      const stops = getAllTourStops(session);
      if (stops.length === 0) return;
      const nextUnreadIdx = stops.findIndex((s) => !s.read);
      currentStopIndex =
        nextUnreadIdx !== -1 ? nextUnreadIdx : (currentStopIndex + 1) % stops.length;
      await openTourStopInDiff(stops[currentStopIndex], false);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.prevTourStop', async () => {
      const session = loadActiveSession();
      const stops = getAllTourStops(session);
      if (stops.length === 0) return;
      currentStopIndex = (currentStopIndex - 1 + stops.length) % stops.length;
      await openTourStopInDiff(stops[currentStopIndex], false);
    })
  );

  // 5. Jump to Comment Thread
  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.jumpToThread', async (threadData) => {
      const session = loadActiveSession();
      if (!session || !threadData) return;
      const t = threadData.threadData || threadData;
      const baseUri = vscode.Uri.file(path.join(session.baseSnapshotRoot || '', t.path));
      const headUri = vscode.Uri.file(path.join(session.headSnapshotRoot || '', t.path));
      const line0 = Math.max(0, (t.startLine || 1) - 1);
      await vscode.commands.executeCommand(
        'vscode.diff',
        baseUri,
        headUri,
        `[Comment] ${path.basename(t.path)}:L${t.startLine || 1}`,
        {
          selection: new vscode.Range(line0, 0, line0, 0),
          preview: true,
        }
      );
    })
  );

  // 6. Ask Quasar Directly Inside a Native Comment Thread
  async function handleAskInThread(replyOrThread, explicitQuestion) {
    const session = loadActiveSession();
    if (!session) return;

    const thread = replyOrThread.thread || replyOrThread;
    const questionText = (explicitQuestion || replyOrThread.text || '').trim();
    if (!questionText) return;

    const docFsPath = thread.uri.fsPath;
    const relPath = thread.relPath || inferRelPathFromDocument(docFsPath, session) || path.basename(docFsPath);
    const startLine = (thread.range ? thread.range.start.line : 0) + 1;
    const endLine = (thread.range ? thread.range.end.line : startLine - 1) + 1;

    // Extract surrounding code snippet for precision
    let codeSnippet = '';
    try {
      const fileLines = fs.readFileSync(docFsPath, 'utf-8').split('\n');
      const s = Math.max(0, startLine - 8);
      const e = Math.min(fileLines.length, endLine + 8);
      codeSnippet = fileLines
        .slice(s, e)
        .map((l, idx) => `${s + idx + 1}: ${l}`)
        .join('\n');
    } catch (_) {}

    let threadId = thread.threadId;
    const nowIso = new Date().toISOString();

    if (!threadId) {
      threadId = `qa-${Date.now()}`;
      thread.threadId = threadId;
      session.threads = session.threads || [];
      session.threads.push({
        id: threadId,
        kind: 'user_qa',
        severity: 'QA',
        status: 'open',
        path: relPath,
        startLine,
        endLine,
        title: questionText.slice(0, 60),
        whyItMatters: 'Reviewer question asked in inline comment thread.',
        draftComment: questionText,
        messages: [
          {
            author: 'You',
            body: questionText,
            timestamp: nowIso,
          },
          {
            author: 'Quasar',
            body: '_⏳ Quasar is analyzing this code hunk..._',
            timestamp: nowIso,
            pending: true,
          },
        ],
      });
    } else {
      const existing = (session.threads || []).find((t) => t.id === threadId);
      if (existing) {
        existing.messages = existing.messages || [];
        existing.messages.push(
          {
            author: 'You',
            body: questionText,
            timestamp: nowIso,
          },
          {
            author: 'Quasar',
            body: '_⏳ Quasar is analyzing this code hunk..._',
            timestamp: nowIso,
            pending: true,
          }
        );
      }
    }

    saveActiveSession(session);
    refreshAllUI(false);

    const answerScript = path.join(PLUGIN_SCRIPTS_DIR, 'answer_comment_question.py');
    const prompt =
      `The reviewer asked an inline code review question in \`${relPath}\` (lines ${startLine}-${endLine}) on PR #${
        (session.pr && session.pr.number) || 'local'
      }:\n\n` +
      `Question: "${questionText}"\n\n` +
      `Code Context:\n\`\`\`\n${codeSnippet}\n\`\`\`\n\n` +
      `Please investigate the codebase if needed and reply directly into the inline comment thread by running:\n` +
      `\`python3 "${answerScript}" --thread-id "${threadId}" --answer "<YOUR_CONCISE_MARKDOWN_ANSWER>"\`\n` +
      `(If the reviewer asked to refine/update the draft GitHub comment, also pass \`--updated-draft "<NEW_DRAFT>"\`).`;

    await sendPromptToQuasar(prompt, `Comment Question: ${ path.basename(relPath) }:${startLine}`);
  }

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.askInComment', async (reply) => {
      await handleAskInThread(reply);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.askAtLine', async (args) => {
      const session = loadActiveSession();
      if (!session || !args) return;
      const q = await vscode.window.showInputBox({
        title: `Ask Quasar about ${path.basename(args.path)}:L${args.line}`,
        prompt: 'Your question & Antigravity\'s reply will appear as an inline comment thread on this line',
        placeHolder: 'e.g. Where is this method called, and can the argument be null?',
      });
      if (!q || !q.trim()) return;
      const headFilePath = path.join(session.headSnapshotRoot || session.repoDir || '', args.path);
      const fakeThread = {
        uri: vscode.Uri.file(headFilePath),
        relPath: args.path,
        range: new vscode.Range(Math.max(0, (args.line || 1) - 1), 0, Math.max(0, (args.line || 1) - 1), 0),
      };
      await handleAskInThread(fakeThread, q.trim());
    })
  );

  // 7. Stage a New User Comment Directly from the '+' Gutter Box
  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.stageNewComment', (reply) => {
      const session = loadActiveSession();
      if (!session || !reply) return;
      const thread = reply.thread;
      const text = (reply.text || '').trim();
      if (!text) return;

      const docFsPath = thread.uri.fsPath;
      const relPath = thread.relPath || inferRelPathFromDocument(docFsPath, session) || path.basename(docFsPath);
      const startLine = (thread.range ? thread.range.start.line : 0) + 1;
      const endLine = (thread.range ? thread.range.end.line : startLine - 1) + 1;
      const nowIso = new Date().toISOString();

      if (thread.threadId) {
        const existing = (session.threads || []).find((t) => t.id === thread.threadId);
        if (existing) {
          existing.draftComment = text;
          existing.status = 'staged';
          existing.kind = 'user_staged';
          existing.messages = existing.messages || [];
          existing.messages.push({
            author: 'You (Staged Draft)',
            body: `**Updated & Staged GitHub Comment**:\n> ${text}`,
            timestamp: nowIso,
          });
        }
      } else {
        const newId = `staged-${Date.now()}`;
        session.threads = session.threads || [];
        session.threads.push({
          id: newId,
          kind: 'user_staged',
          severity: 'REVIEW',
          status: 'staged',
          path: relPath,
          startLine,
          endLine,
          title: text.slice(0, 60),
          whyItMatters: 'Authored by reviewer.',
          draftComment: text,
          messages: [
            {
              author: 'You (Staged for GitHub)',
              body: `**Staged GitHub Comment**:\n> ${text}`,
              timestamp: nowIso,
            },
          ],
        });
      }

      saveActiveSession(session);
      refreshAllUI(false);
    })
  );

  // 8. Stage / Unstage / Dismiss / Edit Candidate Thread + Preference Learning
  function resolveThreadId(arg) {
    if (!arg) return null;
    if (typeof arg === 'string') return arg;
    if (arg.thread && arg.thread.threadId) return arg.thread.threadId;
    return arg.threadId || (arg.threadData && arg.threadData.id) || null;
  }

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.stageThread', async (arg) => {
      const session = loadActiveSession();
      const id = resolveThreadId(arg);
      if (!session || !id) return;
      const target = (session.threads || []).find((t) => t.id === id);
      if (target) {
        const wasUncertain = target.status === 'uncertain';
        target.status = 'staged';
        delete target.unstagedByUser;
        saveActiveSession(session);
        await runManagePreferences(['--record-feedback', '--thread-id', id, '--action', 'stage']);
        refreshAllUI(false);
        if (wasUncertain) {
          const choice = await vscode.window.showInformationMessage(
            `Staged "${target.title}". Should Quasar always flag similar issues in future reviews?`,
            'Always Flag Similar',
            'Just This Once'
          );
          if (choice === 'Always Flag Similar') {
            await runManagePreferences([
              '--record-feedback',
              '--thread-id',
              id,
              '--action',
              'calibrate_keep',
              '--category',
              target.preferenceCategory || 'general',
              '--learned-rule',
              `Flag issues similar to: ${target.title}`,
            ]);
            refreshAllUI(false);
          }
        } else {
          vscode.window.setStatusBarMessage(
            `✅ Staged comment on ${path.basename(target.path)}:L${target.startLine}`,
            3000
          );
        }
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.unstageThread', (arg) => {
      const session = loadActiveSession();
      const id = resolveThreadId(arg);
      if (!session || !id) return;
      const target = (session.threads || []).find((t) => t.id === id);
      if (target) {
        target.status = 'candidate';
        target.unstagedByUser = true;
        saveActiveSession(session);
        refreshAllUI(false);
        vscode.window.setStatusBarMessage(
          `Moved comment on ${path.basename(target.path)}:L${target.startLine} to Unstaged Drafts`,
          3000
        );
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.dismissThread', async (arg) => {
      const session = loadActiveSession();
      const id = resolveThreadId(arg);
      if (!session || !id) return;
      const target = (session.threads || []).find((t) => t.id === id);
      if (target) {
        target.status = 'dismissed';
        saveActiveSession(session);
        await runManagePreferences(['--record-feedback', '--thread-id', id, '--action', 'dismiss']);
        refreshAllUI(false);
        vscode.window
          .showInformationMessage(
            `Removed "${target.title}" from GitHub Review. Want to teach Quasar why so it adapts to your preferences?`,
            'Teach Quasar Why'
          )
          .then((choice) => {
            if (choice === 'Teach Quasar Why') {
              vscode.commands.executeCommand('quasar.downvoteFinding', { threadId: id });
            }
          });
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.editDraftComment', async (arg) => {
      const session = loadActiveSession();
      const id = resolveThreadId(arg);
      if (!session || !id) return;
      const target = (session.threads || []).find((t) => t.id === id);
      if (!target) return;
      const originalDraft = target.draftComment || '';
      const edited = await vscode.window.showInputBox({
        title: `Edit GitHub Draft Comment (${path.basename(target.path)}:L${target.startLine})`,
        value: originalDraft,
        prompt: 'Update the comment text (Quasar also learns your preferred phrasing & tone from your edits!)',
      });
      if (edited === undefined) return;
      target.draftComment = edited.trim();
      target.status = 'staged';
      target.messages = target.messages || [];
      target.messages.push({
        author: 'You (Edited & Staged)',
        body: `**Updated GitHub Comment**:\n> ${target.draftComment}`,
        timestamp: new Date().toISOString(),
      });
      saveActiveSession(session);
      await runManagePreferences([
        '--record-feedback',
        '--thread-id',
        id,
        '--action',
        'edit_draft',
        '--original-draft',
        originalDraft,
        '--updated-draft',
        target.draftComment,
      ]);
      refreshAllUI(false);
    })
  );

  // 8b. Uncertain Feedback Preference Calibration & Meta-Feedback ("Feedback on Feedback")
  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.calibrateFindingPreference', async (arg) => {
      const session = loadActiveSession();
      if (!session) return;
      let id = resolveThreadId(arg);
      let target = id ? (session.threads || []).find((t) => t.id === id) : null;

      if (!target) {
        const uncertainList = (session.threads || []).filter((t) => t.status === 'uncertain');
        if (uncertainList.length === 0) {
          vscode.window.showInformationMessage('No uncertain findings awaiting calibration in this session.');
          return;
        }
        if (uncertainList.length === 1) {
          target = uncertainList[0];
          id = target.id;
        } else {
          const pickedFinding = await vscode.window.showQuickPick(
            uncertainList.map((u) => ({
              label: `🤔 ${u.title}`,
              description: `${path.basename(u.path || '')}:L${u.startLine || 1}`,
              detail: u.uncertaintyQuestion || u.whyItMatters || '',
              finding: u,
            })),
            {
              title: 'Quasar: Select Uncertain Finding to Calibrate',
              placeHolder: 'Choose which borderline finding you want to calibrate',
            }
          );
          if (!pickedFinding) return;
          target = pickedFinding.finding;
          id = target.id;
        }
      }

      const question =
        target.uncertaintyQuestion ||
        `Do you want Quasar to flag issues like "${target.title}" in future reviews?`;
      const category = target.preferenceCategory || 'general';

      const options = [
        {
          label: '$(check) Yes — Stage this comment & flag similar cases in future reviews',
          description: 'Saves a "Prefer" rule so Quasar actively looks for this pattern',
          action: 'calibrate_keep',
          defaultRule: `Always flag ${category.replace(/_/g, ' ')} issues like: ${target.title}`,
        },
        {
          label: '$(circle-slash) No — Dismiss this & skip similar patterns in future reviews',
          description: 'Saves an "Avoid" rule so Quasar stops flagging this pattern',
          action: 'calibrate_skip',
          defaultRule: `Do not flag ${category.replace(/_/g, ' ')} issues like: ${target.title}`,
        },
        {
          label: '$(pin) Keep this comment for this PR only (do not save a global rule)',
          description: 'Moves finding to Candidate without changing long-term preferences',
          action: 'calibrate_once',
          defaultRule: '',
        },
        {
          label: '$(lightbulb-sparkle) Custom Preference — Tell Quasar the exact rule or nuance...',
          description: 'Write a custom rule or ask Quasar to adjust the comment phrasing',
          action: 'custom',
          defaultRule: '',
        },
      ];

      const picked = await vscode.window.showQuickPick(options, {
        title: `🤔 Calibrate Preference: ${target.title}`,
        placeHolder: question,
      });
      if (!picked) return;

      if (picked.action === 'custom') {
        await vscode.commands.executeCommand('quasar.giveMetaFeedback', { threadId: id });
        return;
      }

      let ruleText = picked.defaultRule;
      if (picked.action !== 'calibrate_once') {
        const refinedRule = await vscode.window.showInputBox({
          title: 'Confirm or Customize Learned Preference Rule',
          value: picked.defaultRule,
          prompt: `Question: "${question}" — Edit the rule Quasar will remember for future reviews (or press Enter to accept)`,
        });
        if (refinedRule === undefined) return;
        ruleText = refinedRule.trim() || picked.defaultRule;
      }

      await runManagePreferences([
        '--record-feedback',
        '--thread-id',
        id,
        '--action',
        picked.action,
        '--category',
        category,
        '--reason',
        question,
        '--learned-rule',
        ruleText,
      ]);
      refreshAllUI(false);
      vscode.window.showInformationMessage(
        picked.action === 'calibrate_keep'
          ? `✅ Staged comment & learned preference: "${ruleText}"`
          : picked.action === 'calibrate_skip'
          ? `🚫 Dismissed & learned suppression rule: "${ruleText}"`
          : `📌 Kept comment for this review without saving a global rule.`
      );
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.upvoteFinding', async (arg) => {
      const session = loadActiveSession();
      const id = resolveThreadId(arg);
      if (!session || !id) return;
      const target = (session.threads || []).find((t) => t.id === id);
      if (!target) return;

      const quickChoices = [
        {
          label: '$(thumbsup) High-Signal Catch — Flag more issues like this in future reviews',
          description: 'Reinforces this pattern and stages the comment',
          rule: `Prioritize findings like "${target.title}" (${target.whyItMatters || target.severity || 'high signal'})`,
          stage: true,
        },
        {
          label: '$(thumbsup) Good Catch (Keep as Candidate without staging yet)',
          description: 'Reinforces this pattern without staging right now',
          rule: `Prioritize findings like "${target.title}"`,
          stage: false,
        },
        {
          label: '$(pencil) Add a specific note on why this feedback was helpful...',
          description: 'Tell Quasar what you liked about this finding or phrasing',
          custom: true,
        },
      ];

      const picked = await vscode.window.showQuickPick(quickChoices, {
        title: `👍 Helpful Feedback: ${target.title}`,
        placeHolder: 'How should Quasar learn from this positive feedback?',
      });
      if (!picked) return;

      let learnedRule = picked.rule || '';
      if (picked.custom) {
        const note = await vscode.window.showInputBox({
          title: `👍 What made "${target.title}" helpful?`,
          prompt: 'Enter the preference rule Quasar should remember for future reviews',
          value: `Always flag issues like: ${target.title}`,
        });
        if (!note || !note.trim()) return;
        learnedRule = note.trim();
      }

      if (picked.stage) {
        target.status = 'staged';
        saveActiveSession(session);
      }

      await runManagePreferences([
        '--record-feedback',
        '--thread-id',
        id,
        '--action',
        'upvote',
        '--category',
        target.preferenceCategory || (target.severity || 'general').toLowerCase(),
        '--sentiment',
        'prefer',
        '--reason',
        learnedRule,
        '--learned-rule',
        learnedRule,
      ]);
      refreshAllUI(false);
      vscode.window.setStatusBarMessage(`👍 Quasar learned: "${learnedRule}"`, 4000);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.downvoteFinding', async (arg) => {
      const session = loadActiveSession();
      const id = resolveThreadId(arg);
      if (!session || !id) return;
      const target = (session.threads || []).find((t) => t.id === id);
      if (!target) return;

      const reasons = [
        {
          label: '$(circle-slash) Too nitpicky / low signal — stop flagging similar nits',
          description: 'Dismisses comment and teaches Quasar to skip this type of nit',
          category: 'nit_threshold',
          rule: `Skip low-signal nits similar to: ${target.title}`,
        },
        {
          label: '$(shield) False positive — already guaranteed safe by surrounding code',
          description: 'Dismisses comment and reminds Quasar to verify upstream invariants for this pattern',
          category: 'false_positive',
          rule: `Verify upstream callers/invariants before flagging: ${target.title}`,
        },
        {
          label: '$(repo) Intentional convention in this codebase — do not flag this pattern',
          description: 'Dismisses comment and saves a convention rule',
          category: 'repo_convention',
          rule: `In this codebase, treat "${target.title}" as an intentional convention and do not flag it.`,
        },
        {
          label: '$(comment) Keep the finding, but adjust the tone / phrasing...',
          description: 'Ask Quasar to rewrite this draft comment and learn your preferred tone',
          action: 'rewrite',
        },
        {
          label: '$(pencil) Write custom feedback to teach Quasar...',
          description: 'Explain in your own words why this feedback missed the mark',
          action: 'custom',
        },
      ];

      const picked = await vscode.window.showQuickPick(reasons, {
        title: `👎 Feedback on "${target.title}"`,
        placeHolder: 'Why was this comment not useful? Quasar will learn from your choice.',
      });
      if (!picked) return;

      if (picked.action === 'rewrite' || picked.action === 'custom') {
        await vscode.commands.executeCommand('quasar.giveMetaFeedback', {
          threadId: id,
          mode: picked.action,
        });
        return;
      }

      const customRule = await vscode.window.showInputBox({
        title: 'Confirm Suppression Rule for Future Reviews',
        value: picked.rule,
        prompt: 'Edit the rule Quasar will remember (or press Enter to save & dismiss)',
      });
      if (customRule === undefined) return;
      const finalRule = customRule.trim() || picked.rule;

      await runManagePreferences([
        '--record-feedback',
        '--thread-id',
        id,
        '--action',
        'downvote',
        '--category',
        picked.category || 'general',
        '--sentiment',
        'avoid',
        '--reason',
        finalRule,
        '--learned-rule',
        finalRule,
      ]);
      refreshAllUI(false);
      vscode.window.showInformationMessage(`🚫 Dismissed & taught Quasar: "${finalRule}"`);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.giveMetaFeedback', async (arg) => {
      const session = loadActiveSession();
      const id = resolveThreadId(arg);
      const target = session && id ? (session.threads || []).find((t) => t.id === id) : null;

      // Support typing feedback directly into the inline comment reply box and clicking "Teach Quasar"
      let feedbackText = arg && typeof arg.text === 'string' ? arg.text.trim() : '';
      if (!feedbackText) {
        const promptTitle = target
          ? `🧠 Teach Quasar About "${target.title}"`
          : '🧠 Teach Quasar Your Code Review Preferences';
        const promptText = target
          ? 'Give feedback on this comment (e.g. why to skip it, how to rephrase it, or a general preference rule)'
          : 'Tell Quasar what you want it to flag more of, what to ignore, or how to phrase review comments';
        const input = await vscode.window.showInputBox({
          title: promptTitle,
          prompt: promptText,
          placeHolder:
            'e.g. "Phrase comments as short questions instead of demands" or "Don\'t flag missing tests on private helpers"',
        });
        if (!input || !input.trim()) return;
        feedbackText = input.trim();
      }

      const manageScript = path.join(PLUGIN_SCRIPTS_DIR, 'manage_preferences.py');

      if (target) {
        await runManagePreferences([
          '--record-feedback',
          '--thread-id',
          target.id,
          '--action',
          'meta_feedback',
          '--category',
          target.preferenceCategory || 'general',
          '--reason',
          feedbackText,
          '--learned-rule',
          feedbackText,
        ]);
        refreshAllUI(false);

        const prompt =
          `The reviewer gave meta-feedback ("feedback on your feedback") on review finding \`${target.id}\` ("${target.title}" in \`${target.path}:L${target.startLine}\`):\n\n` +
          `Reviewer Meta-Feedback: "${feedbackText}"\n` +
          `Current Draft Comment: "${target.draftComment || ''}"\n\n` +
          `Please:\n` +
          `1. Distill this feedback into a clean, reusable preference rule (and/or style profile update) and persist it by running:\n` +
          `   \`python3 "${manageScript}" --record-feedback --thread-id "${target.id}" --action meta_feedback --sentiment <prefer|avoid|custom> --learned-rule "<DISTILLED_RULE>" [--updated-draft "<REFINED_DRAFT_IF_APPLICABLE>"]\`\n` +
          `2. If the reviewer asked to dismiss this finding, pass \`--action downvote\`; if they asked to rephrase or refine the comment, pass \`--updated-draft "<NEW_COMMENT>"\`.`;

        await sendPromptToQuasar(prompt, `Quasar Preference Feedback: ${target.title}`);
        vscode.window.showInformationMessage(
          `🧠 Recorded your feedback & dispatched to Quasar to refine its learned preferences!`
        );
      } else {
        await runManagePreferences([
          '--record-feedback',
          '--action',
          'meta_feedback',
          '--reason',
          feedbackText,
          '--learned-rule',
          feedbackText,
        ]);
        refreshAllUI(false);

        const prompt =
          `The reviewer shared a general code review preference for Quasar:\n\n` +
          `Reviewer Feedback: "${feedbackText}"\n\n` +
          `Please distill this into \`~/.gemini/quasar/preferences.json\` and \`~/.gemini/quasar/PREFERENCES.md\` using:\n` +
          `\`python3 "${manageScript}" --add-rule "<DISTILLED_RULE>" --sentiment <prefer|avoid|custom>\` (and/or \`--set-tone\`, \`--set-nit-threshold\`, \`--set-verbosity\`), and apply it to any active review findings if applicable.`;

        await sendPromptToQuasar(prompt, 'Quasar Preference Learning');
        vscode.window.showInformationMessage(`🧠 Saved preference rule: "${feedbackText}"`);
      }
    })
  );

  // 8c. Import Premade Review Skill as Starting Point & Manage Reviewer Preferences
  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.importReviewSkill', async (providedPath) => {
      const manageScript = path.join(PLUGIN_SCRIPTS_DIR, 'manage_preferences.py');
      const workspaceRepo = await detectWorkspaceRepoDir();

      let targetSkillPath = typeof providedPath === 'string' ? providedPath : null;
      let customSkillName = null;

      if (!targetSkillPath) {
        const discoverArgs = ['--discover-skills'];
        if (workspaceRepo) {
          discoverArgs.push('--workspace-dir', workspaceRepo);
        }
        const discoveredRes = await runManagePreferences(discoverArgs);
        const candidates = (discoveredRes && discoveredRes.candidates) || [];

        const quickItems = [];
        for (const c of candidates) {
          quickItems.push({
            label: `$(sparkle) ${c.name}`,
            description: 'Auto-discovered skill / review guide',
            detail: `${c.path}${c.description ? ` — ${c.description}` : ''}`,
            skillPath: c.path,
            skillName: c.name,
          });
        }

        quickItems.push(
          {
            label: '$(file-text) Browse for a SKILL.md or review guide file (.md / .txt)...',
            description: 'Select any markdown file containing review instructions or checklists',
            action: 'browse-file',
          },
          {
            label: '$(folder-opened) Browse for a Skill Folder (containing SKILL.md)...',
            description: 'Select a skill directory',
            action: 'browse-folder',
          },
          {
            label: '$(edit) Paste or type premade review skill / checklist markdown...',
            description: 'Paste an existing review prompt or team rubric directly',
            action: 'paste',
          }
        );

        const picked = await vscode.window.showQuickPick(quickItems, {
          title: '🌱 Quasar: Import Premade Review Skill as Starting Point',
          placeHolder:
            'Select a premade review skill to seed Quasar (keeps Quasar defaults & layers learned feedback on top)',
        });
        if (!picked) return;

        if (picked.skillPath) {
          targetSkillPath = picked.skillPath;
          customSkillName = picked.skillName;
        } else if (picked.action === 'browse-file') {
          const uris = await vscode.window.showOpenDialog({
            canSelectFiles: true,
            canSelectFolders: false,
            canSelectMany: false,
            openLabel: 'Import Review Skill as Starting Point',
            filters: {
              'Markdown & Skill Files': ['md', 'markdown', 'txt', 'yaml', 'yml'],
              'All Files': ['*'],
            },
          });
          if (!uris || uris.length === 0) return;
          targetSkillPath = uris[0].fsPath;
        } else if (picked.action === 'browse-folder') {
          const uris = await vscode.window.showOpenDialog({
            canSelectFiles: false,
            canSelectFolders: true,
            canSelectMany: false,
            openLabel: 'Select Skill Folder',
          });
          if (!uris || uris.length === 0) return;
          targetSkillPath = uris[0].fsPath;
        } else if (picked.action === 'paste') {
          const nameInput = await vscode.window.showInputBox({
            title: 'Name Your Starting-Point Review Skill',
            prompt: 'Give this review skill or rubric a short name',
            placeHolder: 'e.g. Team Flutter Review Rubric or Security Checklist',
          });
          if (!nameInput || !nameInput.trim()) return;
          customSkillName = nameInput.trim();

          const bodyInput = await vscode.window.showInputBox({
            title: `Paste Rules / Guidelines for "${customSkillName}"`,
            prompt: 'Paste your review guidelines, bullet points, or skill instructions',
            placeHolder: 'e.g. - Always verify error boundaries\n- Avoid flagging minor formatting nits',
          });
          if (!bodyInput || !bodyInput.trim()) return;

          const seedDir = path.join(REVIEW_STATE_DIR, 'seed_skills');
          fs.mkdirSync(seedDir, { recursive: true });
          const slug = customSkillName
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/^-|-$/g, '') || 'custom-skill';
          targetSkillPath = path.join(seedDir, `${slug}-input.md`);
          fs.writeFileSync(
            targetSkillPath,
            `---\nname: ${customSkillName}\ndescription: Custom imported review skill starting point\n---\n\n${bodyInput.trim()}\n`,
            'utf8'
          );
        }
      }

      if (!targetSkillPath) return;

      const importArgs = ['--import-skill', targetSkillPath];
      if (customSkillName) {
        importArgs.push('--skill-name', customSkillName);
      }
      const res = await runManagePreferences(importArgs);
      refreshAllUI(false);

      if (!res || !res.ok) {
        vscode.window.showErrorMessage(
          `Could not import review skill: ${(res && res.error) || 'Unknown error'}`
        );
        return;
      }

      const imported = res.importedSkill || {};
      const count = res.extractedRulesCount || 0;
      const choice = await vscode.window.showInformationMessage(
        `🌱 Imported "${imported.name || path.basename(targetSkillPath)}" as a starting point (${count} baseline rule(s) extracted; Quasar core defaults & your learned rules preserved).`,
        'Open PREFERENCES.md',
        'Ask Quasar to Deep-Distill Skill'
      );

      if (choice === 'Open PREFERENCES.md') {
        await vscode.commands.executeCommand('quasar.openPreferencesGuide');
      } else if (choice === 'Ask Quasar to Deep-Distill Skill') {
        const prompt =
          `I just imported a premade review skill ("${imported.name || ''}") from \`${imported.snapshotPath || targetSkillPath}\` as a starting point for Quasar's preference learning.\n\n` +
          `Please read \`${imported.snapshotPath || targetSkillPath}\` and \`~/.gemini/quasar/preferences.json\`. ` +
          `If there are any nuanced domain-specific review heuristics in that skill that weren't captured by bullet extraction, refine them in \`~/.gemini/quasar/preferences.json\` with \`origin: "seed_skill"\` (without overwriting Quasar's core defaults or any \`origin: "learned"\` rules) and regenerate \`PREFERENCES.md\` via \`python3 "${manageScript}" --show\`.`;
        await sendPromptToQuasar(prompt, `Quasar Seed Skill: ${imported.name || 'Import'}`);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.openPreferencesGuide', async () => {
      if (!fs.existsSync(PREFERENCES_MD_PATH)) {
        await runManagePreferences(['--show']);
      }
      if (!fs.existsSync(PREFERENCES_MD_PATH)) return;
      const uri = vscode.Uri.file(PREFERENCES_MD_PATH);
      try {
        await vscode.commands.executeCommand('markdown.showPreview', uri);
      } catch (_) {
        await vscode.window.showTextDocument(uri, { viewColumn: vscode.ViewColumn.Active });
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.managePreferences', async (clickedItem) => {
      if (clickedItem && clickedItem.isSeedSkill && clickedItem.seedSkill) {
        const sk = clickedItem.seedSkill;
        const skAction = await vscode.window.showQuickPick(
          [
            {
              label: '$(file-text) Open Imported Skill Snapshot',
              description: sk.snapshotPath || sk.sourcePath,
              action: 'open-snapshot',
            },
            {
              label: '$(sync) Re-sync / Re-import from Source File',
              description: sk.sourcePath,
              action: 'reimport',
            },
            {
              label: '$(notebook) Open Full PREFERENCES.md Guide',
              action: 'open-md',
            },
            {
              label: '$(trash) Remove This Starting-Point Seed Skill',
              description: 'Removes seed rules from this skill while keeping your learned preferences intact',
              action: 'remove-seed',
            },
          ],
          {
            title: `🌱 Seed Skill: ${sk.name || sk.slug}`,
            placeHolder: `${sk.extractedRuleCount || 0} baseline rules • Non-destructive starting point`,
          }
        );
        if (!skAction) return;
        if (skAction.action === 'open-snapshot') {
          const p = sk.snapshotPath && fs.existsSync(sk.snapshotPath) ? sk.snapshotPath : sk.sourcePath;
          if (p && fs.existsSync(p)) {
            await vscode.window.showTextDocument(vscode.Uri.file(p), {
              viewColumn: vscode.ViewColumn.Active,
            });
          }
        } else if (skAction.action === 'reimport' && sk.sourcePath) {
          await vscode.commands.executeCommand('quasar.importReviewSkill', sk.sourcePath);
        } else if (skAction.action === 'open-md') {
          await vscode.commands.executeCommand('quasar.openPreferencesGuide');
        } else if (skAction.action === 'remove-seed') {
          await runManagePreferences(['--remove-seed-skill', sk.slug]);
          refreshAllUI(false);
          vscode.window.showInformationMessage(
            `Removed starting-point skill "${sk.name || sk.slug}" (your learned preferences remain intact).`
          );
        }
        return;
      }

      if (clickedItem && clickedItem.id && clickedItem.rule) {
        const isSeed = clickedItem.origin === 'seed_skill';
        const action = await vscode.window.showQuickPick(
          [
            {
              label: '$(notebook) Open Full PREFERENCES.md Guide',
              action: 'open-md',
            },
            ...(isSeed
              ? [
                  {
                    label: '$(arrow-up) Promote & Customize as Explicit Learned Rule...',
                    description: 'Override/refine this starting-point rule into a permanent learned preference',
                    action: 'promote',
                  },
                ]
              : []),
            {
              label: `$(trash) Delete This ${isSeed ? 'Seed' : 'Learned'} Rule`,
              description: clickedItem.rule,
              action: 'delete',
            },
          ],
          {
            title: `Manage ${isSeed ? 'Seed Skill' : 'Learned'} Rule (${clickedItem.category || 'general'})`,
            placeHolder: clickedItem.rule,
          }
        );
        if (!action) return;
        if (action.action === 'open-md') {
          await vscode.commands.executeCommand('quasar.openPreferencesGuide');
        } else if (action.action === 'promote') {
          const edited = await vscode.window.showInputBox({
            title: 'Customize & Promote to Learned Rule',
            value: clickedItem.rule,
            prompt: 'Refine this baseline rule — your learned rule will supersede the seed rule',
          });
          if (!edited || !edited.trim()) return;
          await runManagePreferences(['--delete-rule-id', clickedItem.id]);
          await runManagePreferences([
            '--add-rule',
            edited.trim(),
            '--sentiment',
            clickedItem.sentiment || 'prefer',
            '--category',
            clickedItem.category || 'general',
          ]);
          refreshAllUI(false);
          vscode.window.showInformationMessage(`Promoted to learned rule: "${edited.trim()}"`);
        } else if (action.action === 'delete') {
          await runManagePreferences(['--delete-rule-id', clickedItem.id]);
          refreshAllUI(false);
          vscode.window.showInformationMessage(`Removed preference rule: "${clickedItem.rule}"`);
        }
        return;
      }

      const prefs = loadReviewerPreferences();
      const rules = prefs.rules || [];
      const seedSkills = prefs.seedSkills || [];
      const style = prefs.styleProfile || {};
      const session = loadActiveSession();
      const uncertainCount = session
        ? (session.threads || []).filter((t) => t.status === 'uncertain').length
        : 0;

      const menuItems = [];
      if (uncertainCount > 0) {
        menuItems.push({
          label: `$(question) Calibrate ${uncertainCount} Uncertain Finding(s) in Current Review...`,
          description: "Answer Quasar's preference questions on borderline feedback",
          action: 'calibrate-uncertain',
        });
      }
      menuItems.push(
        {
          label: '$(sparkle) Import Premade Review Skill as Starting Point...',
          description:
            seedSkills.length > 0
              ? `${seedSkills.length} seed skill(s) active — add or update a baseline SKILL.md without overwriting`
              : 'Provide a SKILL.md or review guide as a baseline for Quasar to grow from',
          action: 'import-skill',
        },
        {
          label: '$(add) Add a "Prefer / Always Flag" Rule...',
          description: 'Teach Quasar a pattern you always want it to catch',
          action: 'add-prefer',
        },
        {
          label: '$(circle-slash) Add an "Avoid / Suppress" Rule...',
          description: 'Teach Quasar a pattern or nit you want it to stop flagging',
          action: 'add-avoid',
        },
        {
          label: '$(comment-discussion) Configure Comment Tone & Strictness...',
          description: `Current tone: ${style.commentTone || 'collaborative'}`,
          action: 'set-style',
        },
        {
          label: '$(notebook) Open Learned Preferences Guide (PREFERENCES.md)',
          description: `${rules.length} active rule(s) saved in ~/.gemini/quasar/PREFERENCES.md`,
          action: 'open-guide',
        }
      );

      if (seedSkills.length > 0) {
        menuItems.push({
          label: `$(trash) Remove a Starting-Point Seed Skill (${seedSkills.length} active)...`,
          description: 'Removes seed rules from an imported skill while keeping your learned preferences',
          action: 'remove-seed-skill',
        });
      }

      if (rules.length > 0) {
        menuItems.push({
          label: `$(trash) Remove an Individual Rule (${rules.length} total)...`,
          description: 'View and delete saved preference rules',
          action: 'delete-rule',
        });
      }

      const picked = await vscode.window.showQuickPick(menuItems, {
        title: '🧠 Quasar: Manage & Calibrate Reviewer Preferences',
        placeHolder: 'Seed Quasar with a premade skill or teach it your review preferences',
      });
      if (!picked) return;

      if (picked.action === 'calibrate-uncertain') {
        await vscode.commands.executeCommand('quasar.calibrateFindingPreference');
      } else if (picked.action === 'import-skill') {
        await vscode.commands.executeCommand('quasar.importReviewSkill');
      } else if (picked.action === 'open-guide') {
        await vscode.commands.executeCommand('quasar.openPreferencesGuide');
      } else if (picked.action === 'remove-seed-skill') {
        const toRem = await vscode.window.showQuickPick(
          seedSkills.map((sk) => ({
            label: `🌱 ${sk.name || sk.slug}`,
            description: `${sk.extractedRuleCount || 0} baseline rules • ${sk.sourcePath || ''}`,
            slug: sk.slug,
            name: sk.name || sk.slug,
          })),
          {
            title: 'Select Starting-Point Seed Skill to Remove',
            placeHolder: 'Your explicit learned rules and Quasar defaults will remain intact',
          }
        );
        if (!toRem) return;
        await runManagePreferences(['--remove-seed-skill', toRem.slug]);
        refreshAllUI(false);
        vscode.window.showInformationMessage(
          `Removed starting-point skill "${toRem.name}" (learned preferences preserved).`
        );
      } else if (picked.action === 'add-prefer' || picked.action === 'add-avoid') {
        const isPrefer = picked.action === 'add-prefer';
        const ruleInput = await vscode.window.showInputBox({
          title: isPrefer
            ? '✅ Add "Prefer / Always Flag" Rule'
            : '🚫 Add "Avoid / Suppress" Rule',
          prompt: isPrefer
            ? 'What should Quasar always look for and flag in future reviews?'
            : 'What should Quasar stop flagging or ignore in future reviews?',
          placeHolder: isPrefer
            ? 'e.g. Always check that new public APIs have docstrings and error handling tests'
            : 'e.g. Do not flag missing unit tests on private helper functions',
        });
        if (!ruleInput || !ruleInput.trim()) return;
        await runManagePreferences([
          '--add-rule',
          ruleInput.trim(),
          '--sentiment',
          isPrefer ? 'prefer' : 'avoid',
        ]);
        refreshAllUI(false);
        vscode.window.showInformationMessage(`Saved preference rule: "${ruleInput.trim()}"`);
      } else if (picked.action === 'set-style') {
        const toneChoice = await vscode.window.showQuickPick(
          [
            {
              label: 'Collaborative & Socratic Questions (Default)',
              description: 'Phrases feedback as polite questions ("Should we guard against...?")',
              tone: 'constructive, collaborative questions rather than prescriptive demands',
            },
            {
              label: 'Direct & Ultra-Concise Action Items',
              description: 'Short 1-sentence imperative suggestions with minimal prose',
              tone: 'direct, ultra-concise 1-sentence action items without filler',
            },
            {
              label: 'High-Bar Senior Maintainer (Strict Bugs & Architecture Only, Zero Nits)',
              description: 'Suppresses minor polish nits and focuses strictly on bugs and design',
              tone: 'strict maintainer focus on correctness and architecture; suppress minor nits',
              nit: 'never flag minor style or naming nits; only flag correctness, API, and test gaps',
            },
            {
              label: 'Custom Style Description...',
              custom: true,
            },
          ],
          {
            title: 'Configure Quasar Comment Tone & Strictness',
            placeHolder: 'Choose how Quasar should phrase and filter review comments',
          }
        );
        if (!toneChoice) return;
        let toneVal = toneChoice.tone;
        if (toneChoice.custom) {
          const customTone = await vscode.window.showInputBox({
            title: 'Custom Comment Tone & Strictness',
            prompt: 'Describe how you want Quasar to write review comments',
            value: style.commentTone || '',
          });
          if (!customTone || !customTone.trim()) return;
          toneVal = customTone.trim();
        }
        const argsList = ['--set-tone', toneVal];
        if (toneChoice.nit) {
          argsList.push('--set-nit-threshold', toneChoice.nit);
        }
        await runManagePreferences(argsList);
        refreshAllUI(false);
        vscode.window.showInformationMessage(`Updated Quasar style profile: "${toneVal}"`);
      } else if (picked.action === 'delete-rule') {
        const toDelete = await vscode.window.showQuickPick(
          rules.map((r) => ({
            label: `${r.origin === 'seed_skill' ? '🌱' : r.sentiment === 'avoid' ? '🚫' : '✅'} ${r.rule}`,
            description: `${r.origin === 'seed_skill' ? `seed (${r.seedSkillName || 'skill'})` : 'learned'} • ${r.category || 'general'}`,
            ruleId: r.id,
          })),
          {
            title: 'Select Preference Rule to Delete',
            placeHolder: 'Choose a rule to remove from ~/.gemini/quasar/preferences.json',
          }
        );
        if (!toDelete) return;
        await runManagePreferences(['--delete-rule-id', toDelete.ruleId]);
        refreshAllUI(false);
        vscode.window.showInformationMessage('Deleted preference rule.');
      }
    })
  );

  // 9. Self-Review Fix Workflow: Queue Comments & Dispatch to Quasar Agent
  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.toggleSelectForFix', async (arg) => {
      const id = resolveThreadId(arg);
      if (!id) return;
      const res = await runManageFixes(['--toggle-select', '--thread-id', String(id)]);
      refreshAllUI(false);
      if (res && res.ok && Array.isArray(res.updated) && res.updated.length > 0) {
        const isQueued = res.updated[0].selectedForFix;
        vscode.window.setStatusBarMessage(
          isQueued
            ? `🔧 Queued comment for Quasar fix (${res.selectedCount || 1} total queued)`
            : `Removed comment from fix queue (${res.selectedCount || 0} total queued)`,
          3500
        );
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.fixSingleComment', async (arg) => {
      const id = resolveThreadId(arg);
      if (!id) return;
      await vscode.commands.executeCommand('quasar.fixSelectedComments', {
        singleThreadId: String(id),
      });
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.fixSelectedComments', async (providedArgs) => {
      const session = loadActiveSession();
      if (!session) {
        vscode.window.showWarningMessage('No active Quasar review session found.');
        return;
      }

      const fixableThreads = (session.threads || []).filter(
        (t) => t.status !== 'dismissed' && t.status !== 'fixed'
      );
      if (fixableThreads.length === 0) {
        vscode.window.showInformationMessage(
          'No open review comments or findings available to fix in this session.'
        );
        return;
      }

      let targetIds = [];
      const singleId =
        providedArgs && typeof providedArgs === 'object' ? providedArgs.singleThreadId : null;

      if (singleId) {
        targetIds = [String(singleId)];
      } else {
        const anyPreChecked = fixableThreads.some((t) => Boolean(t.selectedForFix));
        const quickItems = fixableThreads.map((t) => {
          const sev = (t.severity || '').toUpperCase();
          const ghTriage = t.kind === 'github_existing' ? inferExistingCommentTriage(t, session) : null;
          const badge =
            t.status === 'uncertain'
              ? '🤔 Uncertain'
              : t.kind === 'github_existing'
              ? (ghTriage && ghTriage.triageBadge) || '👀 GitHub Comment'
              : sev.includes('BLOCK')
              ? '🔴 Blocking'
              : sev.includes('DESIGN')
              ? '🟡 Design'
              : sev.includes('NIT')
              ? '🟢 Nit'
              : '💬 Comment';
          const defaultRecommended =
            t.kind === 'github_existing'
              ? Boolean(ghTriage && ghTriage.recommendedForFix)
              : t.kind === 'ai_candidate' && t.status !== 'uncertain';
          const rawCommentSnippet = (t.draftComment || '').replace(/\s+/g, ' ').slice(0, 120);
          const detailStr =
            ghTriage && ghTriage.triageSummary
              ? `${ghTriage.triageSummary} — "${rawCommentSnippet}"`
              : rawCommentSnippet;
          const cleanQuickTitle = String(t.title || path.basename(t.path || '')).replace(
            /^(?:🛠️|⚠️|✅|💬|❓|👀|🤔|🔴|🟡|🟢)\s*\[[^\]]+\]\s*/,
            ''
          );
          return {
            label: `${badge}: ${cleanQuickTitle}`,
            description: `${t.path || ''}:L${t.startLine || 1}`,
            detail: detailStr,
            picked: anyPreChecked ? Boolean(t.selectedForFix) : defaultRecommended,
            threadId: String(t.id),
          };
        });

        const selectedItems = await vscode.window.showQuickPick(quickItems, {
          canPickMany: true,
          title: `🔧 Quasar: Select Review Comments to Fix in ${path.basename(
            session.repoDir || 'Local Repo'
          )}`,
          placeHolder:
            'Recommended fixes (🛠️) are pre-checked; questions (❓) and disputed comments (⚠️) are left unchecked by default',
        });
        if (!selectedItems || selectedItems.length === 0) return;
        targetIds = selectedItems.map((item) => item.threadId);
      }

      const customInstructions = await vscode.window.showInputBox({
        title: `🔧 Fix ${targetIds.length} Comment(s) with Quasar Agent`,
        prompt:
          'Optional: Add any specific instructions or constraints for how Quasar should fix these issues (or press Enter to start immediately)',
        placeHolder:
          'e.g. "Prefer returning null instead of throwing" or "Also add a unit test covering this case"',
      });
      if (customInstructions === undefined) return;

      const prepArgs = ['--prepare-fix', '--thread-ids', targetIds.join(',')];
      if (customInstructions && customInstructions.trim()) {
        prepArgs.push('--instructions', customInstructions.trim());
      }

      const res = await runManageFixes(prepArgs);
      refreshAllUI(false);

      if (!res || !res.ok || !res.agentPrompt) {
        vscode.window.showErrorMessage(
          `Failed to prepare fix request: ${(res && res.error) || 'Unknown error'}`
        );
        return;
      }

      const dispatchRes = await sendPromptToQuasar(
        res.agentPrompt,
        `Quasar Fix (${targetIds.length} comment${targetIds.length === 1 ? '' : 's'})`,
        res.repoDir || session.repoDir || null
      );

      const prefNote =
        res.preferencesRecorded > 0
          ? ` (and recorded +${res.preferencesRecorded} positive preference signal${
              res.preferencesRecorded === 1 ? '' : 's'
            })`
          : '';
      if (dispatchRes && dispatchRes.success) {
        vscode.window.showInformationMessage(
          `🔧 Dispatched ${targetIds.length} comment(s) to Quasar to fix in ${path.basename(
            res.repoDir || session.repoDir || 'repo'
          )}${prefNote}!`
        );
      } else {
        await vscode.env.clipboard.writeText(res.agentPrompt);
        vscode.window.showWarningMessage(
          `Could not auto-dispatch via agentapi (${
            (dispatchRes && dispatchRes.error) || 'unknown error'
          }). Fix prompt has been copied to your clipboard — paste it into the Antigravity chat!`
        );
      }
    })
  );

  // 9b. Configure GitHub Personal Access Token (API Key) to Publish Reviews as User
  const GITHUB_CLASSIC_PUBLIC_PAT_URL =
    'https://github.com/settings/tokens/new?scopes=public_repo&description=Quasar+Code+Review';
  const GITHUB_CLASSIC_FULL_PAT_URL =
    'https://github.com/settings/tokens/new?scopes=repo&description=Quasar+Code+Review';
  const GITHUB_FINE_GRAINED_PAT_URL =
    'https://github.com/settings/personal-access-tokens/new?name=Quasar+Code+Review&description=Quasar+PR+reviews%2C+inline+comments%2C+and+diff+snapshots&pull_requests=write&contents=read&issues=read&metadata=read';

  async function promptAndConfigureGithubToken(options = {}) {
    const existing = loadStoredGithubAuth();
    if (options.openClassicPublicImmediately) {
      await vscode.env.openExternal(vscode.Uri.parse(GITHUB_CLASSIC_PUBLIC_PAT_URL));
    } else if (existing && existing.login && !options.forcePrompt) {
      const action = await vscode.window.showQuickPick(
        [
          {
            label: `$(key) Enter a New GitHub API Key / Personal Access Token...`,
            description: `Currently authenticated as @${existing.login} (${existing.tokenPreview || '***'})`,
            action: 'update',
          },
          {
            label:
              '$(globe) Create a Classic Token on GitHub (Recommended — pre-fills "public_repo" for all public orgs like flutter/*)...',
            description:
              'Opens github.com/settings/tokens/new?scopes=public_repo (minimum needed to post reviews on any public repo/org)',
            action: 'open_github_classic_public',
          },
          {
            label:
              '$(globe) Create a Classic Token on GitHub for Private + Public Repos (pre-fills "repo" scope)...',
            description: 'Opens github.com/settings/tokens/new?scopes=repo',
            action: 'open_github_classic_full',
          },
          {
            label:
              '$(globe) Create a Fine-Grained Token on GitHub (Single Resource Owner only)...',
            description:
              'Note: GitHub restricts fine-grained tokens to 1 Resource Owner (read-only on external orgs like flutter/* unless org is selected)',
            action: 'open_github_fine',
          },
          {
            label: `$(trash) Clear Saved GitHub API Key (@${existing.login})`,
            description: 'Removes ~/.gemini/quasar/github_auth.json from this machine',
            action: 'clear',
          },
        ],
        {
          title: `🔑 Quasar GitHub Account: @${existing.login}`,
          placeHolder: 'Manage your GitHub Personal Access Token for publishing PR reviews as you',
        }
      );
      if (!action) return false;
      if (action.action === 'clear') {
        await runPostReviewScript(['--clear-token']);
        refreshAllUI(false);
        vscode.window.showInformationMessage('Cleared saved GitHub API key from Quasar.');
        return false;
      }
      if (action.action === 'open_github_classic_public') {
        await vscode.env.openExternal(vscode.Uri.parse(GITHUB_CLASSIC_PUBLIC_PAT_URL));
      } else if (action.action === 'open_github_classic_full') {
        await vscode.env.openExternal(vscode.Uri.parse(GITHUB_CLASSIC_FULL_PAT_URL));
      } else if (action.action === 'open_github_fine') {
        await vscode.env.openExternal(vscode.Uri.parse(GITHUB_FINE_GRAINED_PAT_URL));
      }
    } else if (options.offerGithubLink !== false) {
      const choice = await vscode.window.showQuickPick(
        [
          {
            label:
              '$(globe) Generate a Classic Token on GitHub (Recommended — pre-fills "public_repo" for all public orgs like flutter/*)...',
            description:
              'Opens github.com/settings/tokens/new?scopes=public_repo (works across all public orgs & repos)',
            action: 'generate_classic_public',
          },
          {
            label:
              '$(globe) Generate a Classic Token for Private + Public Repos (pre-fills "repo" scope)...',
            description: 'Opens github.com/settings/tokens/new?scopes=repo',
            action: 'generate_classic_full',
          },
          {
            label: '$(key) Paste Existing GitHub Personal Access Token (ghp_... / github_pat_...)',
            description: 'Stored locally at ~/.gemini/quasar/github_auth.json (chmod 0600)',
            action: 'paste',
          },
          {
            label:
              '$(globe) Generate a Fine-Grained Token on GitHub (Single Resource Owner only)...',
            description:
              'Note: Fine-grained tokens are read-only on external orgs like flutter/* unless that org is selected as Resource Owner',
            action: 'generate_fine',
          },
        ],
        {
          title: '🔑 Connect Your GitHub Account to Quasar',
          placeHolder:
            'Provide your GitHub API key so Quasar can publish reviews to PRs directly as you',
        }
      );
      if (!choice) return false;
      if (choice.action === 'generate_classic_public') {
        await vscode.env.openExternal(vscode.Uri.parse(GITHUB_CLASSIC_PUBLIC_PAT_URL));
      } else if (choice.action === 'generate_classic_full') {
        await vscode.env.openExternal(vscode.Uri.parse(GITHUB_CLASSIC_FULL_PAT_URL));
      } else if (choice.action === 'generate_fine') {
        await vscode.env.openExternal(vscode.Uri.parse(GITHUB_FINE_GRAINED_PAT_URL));
      }
    }

    const tokenInput = await vscode.window.showInputBox({
      title: '🔑 Enter Your GitHub Personal Access Token (API Key)',
      prompt:
        'Paste your GitHub Personal Access Token (Classic "ghp_..." with "public_repo" or "repo" scope works across all orgs like flutter/*). Stored locally in ~/.gemini/quasar/github_auth.json (0600).',
      placeHolder: 'ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx or github_pat_...',
      password: true,
      ignoreFocusOut: true,
    });
    if (!tokenInput || !tokenInput.trim()) return false;

    const verifyRes = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: 'Quasar: Verifying GitHub API Key with api.github.com...',
        cancellable: false,
      },
      () => runPostReviewScript(['--set-token', tokenInput.trim()])
    );

    if (!verifyRes || !verifyRes.ok) {
      vscode.window.showErrorMessage(
        `GitHub API Key verification failed: ${(verifyRes && verifyRes.error) || 'Invalid token'}`
      );
      return false;
    }

    refreshAllUI(false);
    vscode.window.showInformationMessage(
      `🔑 Authenticated with GitHub as @${verifyRes.login}${
        verifyRes.name ? ` (${verifyRes.name})` : ''
      }! Quasar can now publish reviews to PRs as you.`
    );
    return true;
  }

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.configureGithubToken', async () => {
      await promptAndConfigureGithubToken();
    })
  );

  // 10. Publish / Export Review to GitHub (as Authenticated User)
  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.exportPendingReview', async () => {
      const session = loadActiveSession();
      if (!session) return;
      const staged = (session.threads || []).filter((t) => t.status === 'staged');
      const candidates = (session.threads || []).filter(
        (t) => t.kind === 'ai_candidate' && t.status === 'open'
      );

      // Check authentication status first
      let authStatus = await runPostReviewScript(['--auth-status']);
      const isLocalSession =
        session.pr && (session.pr.mode === 'local' || !session.pr.number || !session.pr.owner);

      if ((!authStatus || !authStatus.authenticated) && !isLocalSession) {
        const setupChoice = await vscode.window.showInformationMessage(
          'To publish this review to GitHub as you, please provide your GitHub Personal Access Token (API Key).',
          'Configure GitHub API Key...',
          'Export Local Markdown Instead'
        );
        if (!setupChoice) return;
        if (setupChoice === 'Configure GitHub API Key...') {
          const ok = await promptAndConfigureGithubToken({ forcePrompt: true });
          if (!ok) return;
          authStatus = await runPostReviewScript(['--auth-status']);
        }
      }

      const userHandle =
        (authStatus && authStatus.login && `@${authStatus.login}`) || 'your GitHub account';

      // If no comments are staged yet but there are open candidates, offer to stage or publish summary-only
      if (staged.length === 0 && candidates.length > 0) {
        const emptyChoice = await vscode.window.showQuickPick(
          [
            {
              label: `$(check-all) Stage All ${candidates.length} Open Quasar Candidate Comment(s) & Continue...`,
              description: 'Marks all open candidate findings as staged so they are included in your review',
              action: 'stage_all',
            },
            {
              label: `$(comment) Publish a Summary-Only Review (0 inline comments) as ${userHandle}...`,
              description: 'Approve, Request Changes, or post an overall review comment without inline comments',
              action: 'summary_only',
            },
          ],
          {
            title: 'No Inline Comments Staged Yet',
            placeHolder:
              'You have 0 staged inline comments. Would you like to stage open candidates or submit a summary review?',
          }
        );
        if (!emptyChoice) return;
        if (emptyChoice.action === 'stage_all') {
          for (const c of candidates) {
            c.status = 'staged';
          }
          saveActiveSession(session);
          refreshAllUI(false);
        }
      }

      const finalStaged = (session.threads || []).filter((t) => t.status === 'staged');

      const eventChoices = [
        {
          label: `$(comment-discussion) Publish Review Now as ${userHandle} (COMMENT)`,
          description: `Posts ${finalStaged.length} inline comment(s) immediately to the PR under ${userHandle}`,
          event: 'COMMENT',
        },
        {
          label: `$(check) Approve PR & Publish Review as ${userHandle} (APPROVE)`,
          description: `Approves the PR and publishes ${finalStaged.length} inline comment(s) under ${userHandle}`,
          event: 'APPROVE',
        },
        {
          label: `$(request-changes) Request Changes & Publish Review as ${userHandle} (REQUEST_CHANGES)`,
          description: `Requests changes on the PR and publishes ${finalStaged.length} inline comment(s) under ${userHandle}`,
          event: 'REQUEST_CHANGES',
        },
        {
          label: `$(clock) Create Draft Pending Review on GitHub as ${userHandle} (PENDING)`,
          description: `Uploads ${finalStaged.length} inline comment(s) as an unpublished Pending Review on GitHub so you can review before submitting`,
          event: 'PENDING',
        },
        {
          label: '$(key) Update / Configure GitHub API Key...',
          description: `Current auth: ${userHandle}`,
          event: 'CONFIGURE_TOKEN',
        },
      ];

      const pickedEvent = await vscode.window.showQuickPick(eventChoices, {
        title: `Publish Review to GitHub (${finalStaged.length} Staged Inline Comment${
          finalStaged.length === 1 ? '' : 's'
        })`,
        placeHolder: `Choose how to publish this review as ${userHandle}`,
      });
      if (!pickedEvent) return;

      if (pickedEvent.event === 'CONFIGURE_TOKEN') {
        const ok = await promptAndConfigureGithubToken({ forcePrompt: true });
        if (ok) {
          await vscode.commands.executeCommand('quasar.exportPendingReview');
        }
        return;
      }

      let summaryBody = '';
      if (pickedEvent.event !== 'PENDING' || finalStaged.length === 0) {
        const defaultBody =
          pickedEvent.event === 'APPROVE'
            ? 'LGTM!'
            : pickedEvent.event === 'COMMENT' && finalStaged.length === 0
            ? 'Reviewed with Quasar.'
            : '';
        const bodyInput = await vscode.window.showInputBox({
          title: `Review Summary (${pickedEvent.event} as ${userHandle})`,
          prompt:
            pickedEvent.event === 'REQUEST_CHANGES' ||
            (pickedEvent.event === 'COMMENT' && finalStaged.length === 0)
              ? 'Enter an overall summary message for this review (required by GitHub when requesting changes or posting 0 inline comments)'
              : 'Optional: Enter an overall top-level summary comment for your review (or press Enter to continue)',
          value: defaultBody,
          placeHolder: 'e.g. Looks great overall — left a few inline comments.',
          ignoreFocusOut: true,
        });
        if (bodyInput === undefined) return;
        summaryBody = bodyInput.trim();
      }

      const res = await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: `Quasar: Publishing ${pickedEvent.event} review to GitHub as ${userHandle}...`,
          cancellable: false,
        },
        () =>
          runPostReviewScript([
            '--from-session',
            '--event',
            pickedEvent.event,
            '--summary-body',
            summaryBody,
          ])
      );

      if (!res || !res.ok) {
        const rawErr = (res && res.error) || 'Failed to publish review to GitHub.';
        const cleanErr = rawErr.replace(/^Failed to publish review:\s*/i, '');
        const isFineGrained403 =
          /403/.test(cleanErr) &&
          (/Resource not accessible by personal access token/i.test(cleanErr) ||
            /Fine-Grained tokens/i.test(cleanErr));
        const primaryBtn = isFineGrained403
          ? 'Generate Classic Token (public_repo) & Update...'
          : 'Configure GitHub API Key...';
        const retryChoice = await vscode.window.showErrorMessage(
          `Failed to publish review: ${cleanErr}`,
          primaryBtn
        );
        if (retryChoice === 'Generate Classic Token (public_repo) & Update...') {
          await promptAndConfigureGithubToken({
            forcePrompt: true,
            openClassicPublicImmediately: true,
          });
        } else if (retryChoice === 'Configure GitHub API Key...') {
          await promptAndConfigureGithubToken({ forcePrompt: true });
        }
        return;
      }

      if (res.mode === 'local_markdown_export') {
        const openChoice = await vscode.window.showInformationMessage(
          `No open GitHub PR found for this local branch — exported ${
            res.comments_exported || finalStaged.length
          } comment(s) to ${res.markdown_path}`,
          'Open Exported Markdown'
        );
        if (openChoice === 'Open Exported Markdown' && res.markdown_path) {
          const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(res.markdown_path));
          await vscode.window.showTextDocument(doc, { preview: false });
        }
        return;
      }

      const actionLabel =
        res.event === 'APPROVE'
          ? 'Approved PR & published review'
          : res.event === 'REQUEST_CHANGES'
          ? 'Requested changes & published review'
          : res.event === 'COMMENT'
          ? 'Published review'
          : 'Created Draft Pending Review';
      const asWho = res.published_as ? ` as @${res.published_as}` : '';
      const choice = await vscode.window.showInformationMessage(
        `✅ ${actionLabel}${asWho} on GitHub (${res.comments_exported || 0} inline comment${
          res.comments_exported === 1 ? '' : 's'
        })!`,
        'Open Review on GitHub'
      );
      if (choice === 'Open Review on GitHub' && res.html_url) {
        vscode.env.openExternal(vscode.Uri.parse(res.html_url));
      }
    })
  );

  async function closeQuasarReviewTabs(session) {
    if (cliffsNotesWebviewPanel) {
      try {
        cliffsNotesWebviewPanel.dispose();
      } catch (_) {}
      cliffsNotesWebviewPanel = null;
    }
    try {
      const snapshotsDir = path.join(REVIEW_STATE_DIR, 'snapshots');
      const sessionSnapshotDir =
        (session && session.pr && session.pr.snapshotDir) ||
        (session && session.headSnapshotRoot ? path.dirname(session.headSnapshotRoot) : null) ||
        (session && session.sessionId ? path.join(snapshotsDir, String(session.sessionId)) : null);
      const cliffsMdPath = getWindowCliffsNotesMdPath();
      if (!vscode.window.tabGroups || !Array.isArray(vscode.window.tabGroups.all)) {
        return;
      }
      const tabsToClose = [];
      for (const group of vscode.window.tabGroups.all) {
        for (const tab of group.tabs || []) {
          const input = tab.input;
          const labelStr = String(tab.label || '');
          const candidateUris = [];
          if (input) {
            if (input.uri) candidateUris.push(input.uri);
            if (input.original) candidateUris.push(input.original);
            if (input.modified) candidateUris.push(input.modified);
          }
          const matchesQuasar =
            labelStr.includes('cliffs_notes.md') ||
            candidateUris.some((u) => {
              if (!u) return false;
              const fsPath = u.fsPath || '';
              const rawStr = String(u.toString ? u.toString() : '');
              if (fsPath === cliffsMdPath) return true;
              if (sessionSnapshotDir && fsPath.startsWith(sessionSnapshotDir)) return true;
              if (fsPath.startsWith(snapshotsDir)) return true;
              if (fsPath.startsWith(REVIEW_STATE_DIR)) return true;
              if (rawStr.includes('.gemini/quasar/')) return true;
              return false;
            });
          if (matchesQuasar) {
            tabsToClose.push(tab);
          }
        }
      }
      if (tabsToClose.length > 0) {
        await vscode.window.tabGroups.close(tabsToClose, true);
      }
    } catch (_) {}
  }

  function clearCurrentWindowSessionFiles(session, deleteSnapshots = false) {
    if (session) {
      archiveSessionToHistory(session, getWindowCliffsNotesMdPath());
    }
    const candidateDirs = new Set([getWindowStateDir()]);
    if (session && session.stateDir) {
      candidateDirs.add(session.stateDir);
    }
    const filesToRemove = [path.join(REVIEW_STATE_DIR, 'active_session.json')];
    for (const dir of candidateDirs) {
      filesToRemove.push(
        path.join(dir, 'active_session.json'),
        path.join(dir, 'cliffs_notes.md'),
        path.join(dir, 'pr_context.json'),
        path.join(dir, 'pr.diff'),
        path.join(dir, 'fix_request.json')
      );
    }
    for (const f of filesToRemove) {
      try {
        if (fs.existsSync(f)) fs.unlinkSync(f);
      } catch (_) {}
    }
    if (deleteSnapshots) {
      try {
        const snapshotsDir = path.join(REVIEW_STATE_DIR, 'snapshots');
        const snapDir =
          (session && session.pr && session.pr.snapshotDir) ||
          (session && session.headSnapshotRoot ? path.dirname(session.headSnapshotRoot) : null) ||
          (session && session.sessionId
            ? path.join(snapshotsDir, String(session.sessionId))
            : null);
        if (snapDir && fs.existsSync(snapDir) && snapDir.startsWith(snapshotsDir)) {
          fs.rmSync(snapDir, { recursive: true, force: true });
        }
      } catch (_) {}
    }
  }

  async function closeActiveReviewSession(silent = false) {
    const session = loadActiveSession();
    const pr = (session && session.pr) || {};
    const isLocal =
      pr.mode === 'local_branch' ||
      pr.number === 0 ||
      !pr.number ||
      String(pr.url || '').startsWith('local://');
    const targetLabel =
      session && session.pr
        ? isLocal
          ? `${pr.headRefName || 'HEAD'} → ${pr.baseRefName || 'main'}`
          : `PR #${pr.number || ''} (${pr.repo || ''})`
        : 'active review';

    await closeQuasarReviewTabs(session);
    clearCurrentWindowSessionFiles(session, false);
    for (const oldThread of activeCommentThreads.values()) {
      try {
        oldThread.dispose();
      } catch (_) {}
    }
    activeCommentThreads.clear();
    lastSessionId = null;
    lastAnalysisStatus = null;
    refreshAllUI(false);
    if (!silent) {
      vscode.window.showInformationMessage(
        `🧹 Closed Quasar review (${targetLabel}) and cleared all comments & IDE annotations. (Saved in Past Reviews)`
      );
    }
  }

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.closeReview', async () => {
      await closeActiveReviewSession(false);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.resetReview', async (arg) => {
      const session = loadActiveSession();
      if (!session || !session.pr) {
        const startPick = await vscode.window.showQuickPick(
          [
            {
              label: '$(git-branch) Review Local Branch or Uncommitted Changes...',
              description: 'Start a new branch / working-tree diff review in this window',
              command: 'quasar.reviewLocalBranch',
            },
            {
              label: '$(git-pull-request) Review GitHub PR from URL...',
              description: 'Start a new GitHub Pull Request review in this window',
              command: 'quasar.loadPrFromUrl',
            },
          ],
          {
            title: 'Quasar: No Active Review in This Window',
            placeHolder: 'Start a branch or PR review',
          }
        );
        if (startPick) {
          await vscode.commands.executeCommand(startPick.command);
        }
        return;
      }

      const pr = session.pr || {};
      const isLocal =
        pr.mode === 'local_branch' || pr.number === 0 || !pr.number || String(pr.url || '').startsWith('local://');
      const targetLabel = isLocal
        ? `${pr.headRefName || 'HEAD'} → ${pr.baseRefName || 'main'}`
        : `PR #${pr.number || ''} (${pr.repo || ''})`;

      let mode = typeof arg === 'string' ? arg : null;
      if (!mode) {
        const picked = await vscode.window.showQuickPick(
          [
            {
              label: '$(close) Close Active Review & Clear IDE Annotations',
              description: `Close review tabs and clear all comments/CodeLenses for ${targetLabel}`,
              detail:
                'Archives this review under Past Reviews, closes snapshot diff tabs, and removes all inline comments, CodeLenses, and hover cards from the IDE.',
              mode: 'clear',
            },
            {
              label: '$(debug-restart) Re-Run Fresh Review on Current Branch / PR',
              description: `Re-fetch latest diff for ${targetLabel} and run a fresh Quasar analysis`,
              detail:
                'Wipes the current review state & snapshots in this window, collects the latest commits/working-tree diff, and asks Quasar to review it from scratch.',
              mode: 'rerun',
            },
            {
              label: '$(history) Reset Tour Progress & Comment States (Fast)',
              description: 'Reset tour stops to 0/N unread and restore fixed/dismissed/checked comments',
              detail:
                'Keeps the existing CliffsNotes & findings without re-running the AI, resetting read stops, fix checkboxes, and comment statuses back to their initial state.',
              mode: 'soft_reset',
            },
          ],
          {
            title: `Reset or Close Quasar Review (${targetLabel})`,
            placeHolder: 'How would you like to reset or close this branch/PR review?',
          }
        );
        if (!picked) return;
        mode = picked.mode;
      }

      if (mode === 'soft_reset') {
        const chapters =
          session.chapters || (session.cliffsNotes && session.cliffsNotes.chapters) || [];
        let stopsReset = 0;
        for (const ch of chapters) {
          for (const stop of ch.stops || []) {
            if (stop.read) stopsReset++;
            stop.read = false;
          }
        }
        let threadsReset = 0;
        for (const t of session.threads || []) {
          const threadKind = t.kind || t.origin;
          if (t.selectedForFix || t.fixStatus) {
            t.selectedForFix = false;
            delete t.fixStatus;
            delete t.fixedAt;
            delete t.fixedSummary;
            threadsReset++;
          }
          if (threadKind === 'ai_candidate' || threadKind === 'user_staged') {
            const initialStatus = t.uncertain ? 'uncertain' : 'staged';
            delete t.unstagedByUser;
            if (t.status !== initialStatus) {
              t.status = initialStatus;
              threadsReset++;
            }
          } else if (threadKind === 'github_existing' && t.status === 'fixed') {
            t.status = 'open';
            threadsReset++;
          }
        }
        saveActiveSession(session);
        refreshAllUI(false);
        vscode.window.showInformationMessage(
          `🔄 Reset tour progress (${stopsReset} stop${
            stopsReset === 1 ? '' : 's'
          } marked unread) and restored ${threadsReset} comment state${
            threadsReset === 1 ? '' : 's'
          } for ${targetLabel}.`
        );
        return;
      }

      if (mode === 'clear') {
        await closeActiveReviewSession(false);
        return;
      }

      if (mode === 'rerun') {
        const savedRepoDir = session.repoDir || pr.localRepoDir || getWindowWorkspaceRoot();
        const savedBranch = pr.headRefName || 'HEAD';
        const savedBase = pr.baseRefName || 'main';
        const savedCommittedOnly = Boolean(pr.committedOnly);
        const savedPrUrl = pr.url;

        await closeQuasarReviewTabs(session);
        clearCurrentWindowSessionFiles(session, true);
        lastSessionId = null;
        lastAnalysisStatus = null;
        refreshAllUI(false);

        if (isLocal) {
          await vscode.commands.executeCommand('quasar.reviewLocalBranch', {
            repoDir: savedRepoDir,
            branch: savedBranch,
            base: savedBase,
            committedOnly: savedCommittedOnly,
          });
        } else if (savedPrUrl) {
          await vscode.commands.executeCommand('quasar.loadPrFromUrl', savedPrUrl);
        } else {
          await vscode.commands.executeCommand('quasar.loadPrFromUrl');
        }
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.switchPastReview', async (providedEntry) => {
      const pastReviews = listPastReviews();
      if (pastReviews.length === 0) {
        vscode.window.showInformationMessage('No past Quasar review sessions found yet.');
        return;
      }

      const currentSession = loadActiveSession();
      const currentId = currentSession ? getSessionArchiveId(currentSession) : null;

      let chosen =
        providedEntry && providedEntry.session ? providedEntry : null;

      if (!chosen) {
        const pickItems = pastReviews.map((entry) => {
          const s = entry.session || {};
          const p = s.pr || {};
          const isCurrent = Boolean(currentId && entry.id === currentId);
          const titleStr = p.number
            ? `PR #${p.number}: ${p.title || entry.id}`
            : p.title || `Branch: ${p.headRefName || entry.id}`;
          const stopsCount = (s.chapters || []).reduce(
            (acc, ch) => acc + ((ch.stops || []).length || 0),
            0
          );
          const threadsCount = (s.threads || []).filter((t) => t.status !== 'dismissed').length;
          const dateStr = entry.updatedMs
            ? new Date(entry.updatedMs).toLocaleString(undefined, {
                month: 'short',
                day: 'numeric',
                hour: 'numeric',
                minute: '2-digit',
              })
            : '';
          return {
            label: `${p.number ? '$(git-pull-request)' : '$(git-branch)'} ${titleStr}`,
            description: `${p.repo || ''}${dateStr ? ` • ${dateStr}` : ''}${
              isCurrent ? ' • (Active in this window)' : ''
            }`,
            detail: `${stopsCount} tour stop(s) • ${threadsCount} comment(s) • +${
              p.additions || 0
            }/-${p.deletions || 0}${
              s.cliffsNotes && s.cliffsNotes.tldr ? ` — ${s.cliffsNotes.tldr.slice(0, 120)}` : ''
            }`,
            entry,
          };
        });

        const selected = await vscode.window.showQuickPick(pickItems, {
          title: `🕘 Quasar: Past Reviews (${pastReviews.length} saved)`,
          placeHolder: 'Select any previous PR or local branch review to open in this window',
        });
        if (!selected) return;
        chosen = selected.entry;
      }

      if (currentSession) {
        archiveSessionToHistory(currentSession, getWindowCliffsNotesMdPath());
      }

      const nextSession = JSON.parse(JSON.stringify(chosen.session));
      const stateDir = getWindowStateDir();
      fs.mkdirSync(stateDir, { recursive: true });
      nextSession.workspaceDir = getWindowWorkspaceRoot();
      nextSession.stateDir = stateDir;
      fs.writeFileSync(
        getWindowActiveSessionPath(),
        JSON.stringify(nextSession, null, 2),
        'utf-8'
      );
      if (chosen.mdPath && fs.existsSync(chosen.mdPath)) {
        try {
          fs.copyFileSync(chosen.mdPath, getWindowCliffsNotesMdPath());
        } catch (_) {}
      }

      await focusReviewSidebar();
      refreshAllUI(true);
      const pr = nextSession.pr || {};
      const label = pr.number
        ? `PR #${pr.number}: ${pr.title || chosen.id}`
        : pr.title || chosen.id;
      vscode.window.showInformationMessage(`🕘 Loaded past Quasar review: ${label}`);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('quasar.refreshSession', () => {
      refreshAllUI(false);
    })
  );

  refreshAllUI(false);
}

function deactivate() {}

module.exports = { activate, deactivate };
