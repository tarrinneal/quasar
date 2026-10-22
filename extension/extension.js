const vscode = require('vscode');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { execFile } = require('node:child_process');

const REVIEW_STATE_DIR = path.join(os.homedir(), '.gemini', 'antigravity-code-review');
const ACTIVE_SESSION_PATH = path.join(REVIEW_STATE_DIR, 'active_session.json');
const CLIFFS_NOTES_MD_PATH = path.join(REVIEW_STATE_DIR, 'cliffs_notes.md');
const PLUGIN_SCRIPTS_DIR = path.join(
  os.homedir(),
  '.gemini',
  'config',
  'plugins',
  'code-review',
  'skills',
  'code-review',
  'scripts'
);

/**
 * Resolves the `agentapi` CLI binary used to communicate with Antigravity.
 */
function getAgentApiBinary() {
  if (process.env.ANTIGRAVITY_AGENTAPI_EXE && fs.existsSync(process.env.ANTIGRAVITY_AGENTAPI_EXE)) {
    return process.env.ANTIGRAVITY_AGENTAPI_EXE;
  }
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
  return 'agentapi';
}

/**
 * Finds the most recent active Antigravity conversation ID so in-code comment questions
 * can be routed to the current session (or starts a new conversation if none is found).
 */
function detectActiveConversationId() {
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
  let bestId = null;
  let bestMtime = 0;

  for (const brainDir of brainDirs) {
    try {
      const entries = fs.readdirSync(brainDir, { withFileTypes: true });
      for (const entry of entries) {
        if (!entry.isDirectory()) continue;
        const tPath = path.join(brainDir, entry.name, '.system_generated', 'logs', 'transcript.jsonl');
        if (!fs.existsSync(tPath)) continue;
        const stat = fs.statSync(tPath);
        if (stat.mtimeMs > bestMtime) {
          bestMtime = stat.mtimeMs;
          bestId = entry.name;
        }
      }
    } catch (_) {}
  }
  return bestId;
}

function sendPromptToAntigravity(prompt, title = 'Antigravity Code Review') {
  return new Promise((resolve) => {
    const exe = getAgentApiBinary();
    const convId = detectActiveConversationId();
    const args = convId
      ? ['send-message', `--title=${title}`, convId, prompt]
      : ['new-conversation', `--title=${title}`, prompt];

    execFile(exe, args, { timeout: 10000 }, (err, stdout, stderr) => {
      if (err) {
        resolve({ success: false, error: err.message, stderr });
      } else {
        resolve({ success: true, stdout: (stdout || '').trim() });
      }
    });
  });
}

function loadActiveSession() {
  try {
    if (!fs.existsSync(ACTIVE_SESSION_PATH)) return null;
    const raw = fs.readFileSync(ACTIVE_SESSION_PATH, 'utf-8');
    return JSON.parse(raw);
  } catch (_) {
    return null;
  }
}

function saveActiveSession(session) {
  try {
    fs.mkdirSync(REVIEW_STATE_DIR, { recursive: true });
    session.updatedAt = new Date().toISOString();
    fs.writeFileSync(ACTIVE_SESSION_PATH, JSON.stringify(session, null, 2), 'utf-8');
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
    if (!session) {
      const emptyItem = new vscode.TreeItem(
        'Click $(git-pull-request) to load a GitHub PR URL...',
        vscode.TreeItemCollapsibleState.None
      );
      emptyItem.command = {
        command: 'antigravityReview.loadPrFromUrl',
        title: 'Review PR from GitHub URL',
      };
      emptyItem.iconPath = new vscode.ThemeIcon('git-pull-request');
      return [emptyItem];
    }

    if (!element) {
      const rootItems = [];
      const pr = session.pr || {};
      const prLabel = pr.number ? `PR #${pr.number}: ${pr.title || ''}` : pr.title || 'Active Review';
      const headerItem = new vscode.TreeItem(prLabel, vscode.TreeItemCollapsibleState.None);
      headerItem.description = `@${pr.author || 'author'} • +${pr.additions || 0}/-${pr.deletions || 0}`;
      headerItem.iconPath = new vscode.ThemeIcon(
        session.analysisStatus === 'analyzing' ? 'sync~spin' : 'book'
      );
      const mdTooltip = new vscode.MarkdownString(
        `### 📖 ${prLabel}\n\n` +
          `**Author**: \`@${pr.author || 'unknown'}\`  |  **Changes**: \`+${pr.additions || 0} / -${pr.deletions || 0}\`\n\n` +
          `${(session.cliffsNotes && session.cliffsNotes.tldr) || ''}\n\n` +
          `*Click to open the full CliffsNotes Study Guide beside your editor.*`
      );
      headerItem.tooltip = mdTooltip;
      headerItem.command = {
        command: 'antigravityReview.openCliffsNotesGuide',
        title: 'Open CliffsNotes Study Guide',
      };
      rootItems.push(headerItem);

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
        command: 'antigravityReview.openCliffsNotesGuide',
        title: 'Open Full CliffsNotes',
      };
      items.push(tldrItem);

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
        item.description = sym.role;
        item.iconPath = new vscode.ThemeIcon('symbol-method');
        item.tooltip = new vscode.MarkdownString(`**\`${sym.name}\`**\n\n${sym.role}`);
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
          command: 'antigravityReview.openTourStop',
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

    if (!element) {
      const staged = threads.filter((t) => t.status === 'staged');
      const candidates = threads.filter((t) => t.kind === 'ai_candidate' && t.status !== 'staged');
      const qaThreads = threads.filter((t) => t.kind === 'user_qa' && t.status !== 'staged');
      const existingGh = threads.filter((t) => t.kind === 'github_existing');

      const groups = [
        {
          id: 'staged',
          label: `Staged for GitHub Pending Review (${staged.length})`,
          icon: 'cloud-upload',
          items: staged,
          expanded: true,
        },
        {
          id: 'candidates',
          label: `Antigravity Candidate Findings (${candidates.length})`,
          icon: 'sparkle',
          items: candidates,
          expanded: true,
        },
        {
          id: 'qa',
          label: `In-Code Q&A Threads (${qaThreads.length})`,
          icon: 'comment-discussion',
          items: qaThreads,
          expanded: qaThreads.length > 0,
        },
        {
          id: 'existing',
          label: `Existing GitHub PR Comments (${existingGh.length})`,
          icon: 'github',
          items: existingGh,
          expanded: false,
        },
      ];

      return groups.map((g) => {
        const node = new vscode.TreeItem(
          g.label,
          g.expanded
            ? vscode.TreeItemCollapsibleState.Expanded
            : vscode.TreeItemCollapsibleState.Collapsed
        );
        node.iconPath = new vscode.ThemeIcon(g.icon);
        node.groupItems = g.items;
        return node;
      });
    }

    if (element.groupItems) {
      return element.groupItems.map((t) => {
        const sev = (t.severity || '').toUpperCase();
        const prefix = sev.includes('BLOCK')
          ? '🔴'
          : sev.includes('DESIGN')
          ? '🟡'
          : sev.includes('NIT')
          ? '🟢'
          : '💬';
        const item = new vscode.TreeItem(
          `${prefix} ${t.title || path.basename(t.path || '')}`,
          vscode.TreeItemCollapsibleState.None
        );
        item.description = `${path.basename(t.path || '')}:L${t.startLine || 1}`;
        item.contextValue =
          t.status === 'staged'
            ? 'thread-staged'
            : t.kind === 'ai_candidate' || t.kind === 'user_qa'
            ? 'thread-candidate'
            : 'thread-existing';
        item.threadId = t.id;
        item.threadData = t;
        const tip = new vscode.MarkdownString(
          `### ${prefix} ${t.title || 'Comment'}\n` +
            `**File**: \`${t.path}:L${t.startLine}-${t.endLine || t.startLine}\`\n\n` +
            (t.whyItMatters ? `*CliffsNotes Context*: ${t.whyItMatters}\n\n` : '') +
            `**Draft Comment**:\n> ${t.draftComment || ''}`
        );
        item.tooltip = tip;
        item.command = {
          command: 'antigravityReview.jumpToThread',
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
// 3. TreeDataProvider: Test Coverage & Repo Hygiene Checks
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
    const session = loadActiveSession();
    if (!session) return [];
    if (element) return [];

    const items = [];
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

    if (items.length === 0) {
      const placeholder = new vscode.TreeItem(
        session.analysisStatus === 'analyzing'
          ? 'Evaluating test coverage & repo hygiene...'
          : 'No coverage issues flagged',
        vscode.TreeItemCollapsibleState.None
      );
      placeholder.iconPath = new vscode.ThemeIcon('info');
      items.push(placeholder);
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

  vscode.window.registerTreeDataProvider('antigravityReview.cliffsNotesTree', cliffsProvider);
  vscode.window.registerTreeDataProvider('antigravityReview.commentsTree', commentsProvider);
  vscode.window.registerTreeDataProvider('antigravityReview.checksTree', checksProvider);

  // Status Bar Item
  const statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 95);
  statusBarItem.command = 'antigravityReview.nextTourStop';
  context.subscriptions.push(statusBarItem);

  // Native Comment Controller (for Review Comments + Ask-in-Comment Q&A only)
  const commentController = vscode.comments.createCommentController(
    'antigravity-code-review',
    'Antigravity Code Review'
  );
  commentController.options = {
    prompt: 'Ask Antigravity about these lines, or type a review comment to stage for GitHub...',
    placeHolder: 'e.g. "Is this null-safe when called from Web?" or "nit: Prefer final here"',
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
      statusBarItem.hide();
      return;
    }
    const stops = getAllTourStops(session);
    const readCount = stops.filter((s) => s.read).length;
    const stagedCount = (session.threads || []).filter((t) => t.status === 'staged').length;
    const prNum = session.pr.number ? `#${session.pr.number}` : 'Diff';
    const spin = session.analysisStatus === 'analyzing' ? '$(sync~spin) ' : '$(book) ';
    statusBarItem.text = `${spin}PR ${prNum} • Tour ${readCount}/${stops.length} • ${stagedCount} Staged`;
    statusBarItem.tooltip = 'Click to step to the next CliffsNotes Tour Stop (Alt+Down)';
    statusBarItem.show();
  }

  /**
   * Synchronizes the native vscode.CommentThread instances in the diff editor
   * with `session.threads` (Review Findings, Staged Comments, In-Code Q&A, Existing GitHub Comments).
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

      const comments = (t.messages || []).map((m) => {
        const md = new vscode.MarkdownString(m.body || '');
        md.isTrusted = true;
        return {
          author: { name: m.author || 'Antigravity' },
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

      if (t.status === 'staged') {
        thread.contextValue = 'staged';
        thread.label = `✅ Staged for GitHub Pending Review — ${t.title || ''}`;
        thread.collapsibleState = vscode.CommentThreadCollapsibleState.Collapsed;
      } else if (t.kind === 'ai_candidate') {
        thread.contextValue = 'candidate';
        const sev = (t.severity || '').toUpperCase();
        const badge = sev.includes('BLOCK')
          ? '🔴 Blocking'
          : sev.includes('DESIGN')
          ? '🟡 Design'
          : '🟢 Nit';
        thread.label = `${badge} Candidate — ${t.title || ''}`;
        thread.collapsibleState = vscode.CommentThreadCollapsibleState.Expanded;
      } else if (t.kind === 'user_qa') {
        thread.contextValue = 'qa';
        thread.label = `💬 Antigravity Q&A — ${t.title || ''}`;
        thread.collapsibleState = vscode.CommentThreadCollapsibleState.Expanded;
      } else {
        thread.contextValue = 'existing';
        thread.label = `👀 Existing GitHub Comment`;
        thread.collapsibleState = vscode.CommentThreadCollapsibleState.Collapsed;
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

    if (session && autoOpenIfNew) {
      const isNewSession = session.sessionId !== lastSessionId;
      const justFinishedAnalysis =
        lastAnalysisStatus === 'analyzing' && session.analysisStatus === 'ready';
      lastSessionId = session.sessionId;
      lastAnalysisStatus = session.analysisStatus;

      if (isNewSession || justFinishedAnalysis) {
        const stops = getAllTourStops(session);
        if (stops.length > 0) {
          openTourStopInDiff(stops[0], true);
        }
        if (justFinishedAnalysis && fs.existsSync(CLIFFS_NOTES_MD_PATH)) {
          vscode.commands
            .executeCommand('markdown.showPreviewToSide', vscode.Uri.file(CLIFFS_NOTES_MD_PATH))
            .then(undefined, () => {});
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
              `### 📘 Antigravity CliffsNotes — Stop ${stop.stopNumber} (${stop.chapterTitle})\n\n`
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
              `---\n[$(comment-discussion) Ask Antigravity in Comment](command:antigravityReview.askAtLine?${askArgs})  •  ` +
                `[$(book) Open CliffsNotes Guide](command:antigravityReview.openCliffsNotesGuide)  •  ` +
                `[$(arrow-down) Next Tour Stop](command:antigravityReview.nextTourStop)`
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
              command: 'antigravityReview.openCliffsNotesGuide',
            }),
            new vscode.CodeLens(range, {
              title: `💬 Ask Antigravity`,
              command: 'antigravityReview.askAtLine',
              arguments: [{ path: stop.path, line: stop.startLine || 1, stopTitle: stop.title }],
            }),
            new vscode.CodeLens(range, {
              title: `➡️ Next Stop`,
              command: 'antigravityReview.nextTourStop',
            })
          );
        }
        return lenses;
      },
    })
  );

  // Watch active_session.json for live updates from Antigravity
  let watchDebounce = null;
  fs.watch(REVIEW_STATE_DIR, (_eventType, filename) => {
    if (!filename || filename === 'active_session.json') {
      if (watchDebounce) clearTimeout(watchDebounce);
      watchDebounce = setTimeout(() => refreshAllUI(true), 180);
    }
  });

  // ==========================================================================
  // Commands
  // ==========================================================================

  // 1. Load PR from GitHub URL (1-Link Auto-Initiation)
  context.subscriptions.push(
    vscode.commands.registerCommand('antigravityReview.loadPrFromUrl', async (providedUrl) => {
      const prUrl =
        providedUrl ||
        (await vscode.window.showInputBox({
          title: 'Antigravity Code Review: Paste GitHub PR Link',
          prompt: 'Paste a GitHub PR URL or PR number to initiate a CliffsNotes review session',
          placeHolder: 'https://github.com/flutter/packages/pull/13265',
        }));
      if (!prUrl || !prUrl.trim()) return;

      const trimmedUrl = prUrl.trim();
      const collectScript = path.join(PLUGIN_SCRIPTS_DIR, 'collect_pr_context.py');

      await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: `Antigravity Review: Loading ${trimmedUrl}...`,
          cancellable: false,
        },
        (progress) =>
          new Promise((resolve) => {
            progress.report({ message: 'Fetching PR diff, comments & materializing snapshots...' });
            execFile('python3', [collectScript, '--pr', trimmedUrl], { timeout: 60000 }, async (err) => {
              if (err) {
                vscode.window.showErrorMessage(`Failed to fetch PR context: ${err.message}`);
                resolve();
                return;
              }
              refreshAllUI(true);
              progress.report({
                message: 'Initial tour ready! Dispatching Antigravity multi-lens CliffsNotes synthesis...',
              });
              await sendPromptToAntigravity(
                `Review ${trimmedUrl} using the code-review skill and publish the CliffsNotes session via publish_review_session.py.`,
                `Code Review: ${trimmedUrl}`
              );
              resolve();
            });
          })
      );
    })
  );

  // 2. Open Full CliffsNotes Study Guide beside editor
  context.subscriptions.push(
    vscode.commands.registerCommand('antigravityReview.openCliffsNotesGuide', async () => {
      if (!fs.existsSync(CLIFFS_NOTES_MD_PATH)) {
        vscode.window.showInformationMessage('No active CliffsNotes guide yet. Load a PR URL first.');
        return;
      }
      const uri = vscode.Uri.file(CLIFFS_NOTES_MD_PATH);
      try {
        await vscode.commands.executeCommand('markdown.showPreviewToSide', uri);
      } catch (_) {
        await vscode.window.showTextDocument(uri, { viewColumn: vscode.ViewColumn.Beside });
      }
    })
  );

  // 3. Open specific Tour Stop
  context.subscriptions.push(
    vscode.commands.registerCommand('antigravityReview.openTourStop', async (stop) => {
      await openTourStopInDiff(stop, false);
    })
  );

  // 4. Next / Previous Tour Stop
  let currentStopIndex = 0;
  context.subscriptions.push(
    vscode.commands.registerCommand('antigravityReview.nextTourStop', async () => {
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
    vscode.commands.registerCommand('antigravityReview.prevTourStop', async () => {
      const session = loadActiveSession();
      const stops = getAllTourStops(session);
      if (stops.length === 0) return;
      currentStopIndex = (currentStopIndex - 1 + stops.length) % stops.length;
      await openTourStopInDiff(stops[currentStopIndex], false);
    })
  );

  // 5. Jump to Comment Thread
  context.subscriptions.push(
    vscode.commands.registerCommand('antigravityReview.jumpToThread', async (threadData) => {
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

  // 6. Ask Antigravity Directly Inside a Native Comment Thread
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
            author: 'Antigravity',
            body: '_⏳ Antigravity is analyzing this code hunk..._',
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
            author: 'Antigravity',
            body: '_⏳ Antigravity is analyzing this code hunk..._',
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

    await sendPromptToAntigravity(prompt, `Comment Question: ${ path.basename(relPath) }:${startLine}`);
  }

  context.subscriptions.push(
    vscode.commands.registerCommand('antigravityReview.askInComment', async (reply) => {
      await handleAskInThread(reply);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('antigravityReview.askAtLine', async (args) => {
      const session = loadActiveSession();
      if (!session || !args) return;
      const q = await vscode.window.showInputBox({
        title: `Ask Antigravity about ${path.basename(args.path)}:L${args.line}`,
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
    vscode.commands.registerCommand('antigravityReview.stageNewComment', (reply) => {
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

  // 8. Stage / Unstage / Dismiss / Edit Candidate Thread
  function resolveThreadId(arg) {
    if (!arg) return null;
    if (typeof arg === 'string') return arg;
    return arg.threadId || (arg.threadData && arg.threadData.id) || null;
  }

  context.subscriptions.push(
    vscode.commands.registerCommand('antigravityReview.stageThread', (arg) => {
      const session = loadActiveSession();
      const id = resolveThreadId(arg);
      if (!session || !id) return;
      const target = (session.threads || []).find((t) => t.id === id);
      if (target) {
        target.status = 'staged';
        saveActiveSession(session);
        refreshAllUI(false);
        vscode.window.setStatusBarMessage(`✅ Staged comment on ${path.basename(target.path)}:L${target.startLine}`, 3000);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('antigravityReview.unstageThread', (arg) => {
      const session = loadActiveSession();
      const id = resolveThreadId(arg);
      if (!session || !id) return;
      const target = (session.threads || []).find((t) => t.id === id);
      if (target) {
        target.status = 'candidate';
        saveActiveSession(session);
        refreshAllUI(false);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('antigravityReview.dismissThread', (arg) => {
      const session = loadActiveSession();
      const id = resolveThreadId(arg);
      if (!session || !id) return;
      const target = (session.threads || []).find((t) => t.id === id);
      if (target) {
        target.status = 'dismissed';
        saveActiveSession(session);
        refreshAllUI(false);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('antigravityReview.editDraftComment', async (arg) => {
      const session = loadActiveSession();
      const id = resolveThreadId(arg);
      if (!session || !id) return;
      const target = (session.threads || []).find((t) => t.id === id);
      if (!target) return;
      const edited = await vscode.window.showInputBox({
        title: `Edit GitHub Draft Comment (${path.basename(target.path)}:L${target.startLine})`,
        value: target.draftComment || '',
        prompt: 'Update the comment text that will be exported to your GitHub Pending Review',
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
      refreshAllUI(false);
    })
  );

  // 9. Export Staged Comments to GitHub Pending Review
  context.subscriptions.push(
    vscode.commands.registerCommand('antigravityReview.exportPendingReview', async () => {
      const session = loadActiveSession();
      if (!session) return;
      const staged = (session.threads || []).filter((t) => t.status === 'staged');
      if (staged.length === 0) {
        vscode.window.showWarningMessage(
          'No staged comments to export yet. Click $(check) on any candidate comment or add a comment in the diff gutter first.'
        );
        return;
      }

      const postScript = path.join(PLUGIN_SCRIPTS_DIR, 'post_pending_review.py');
      execFile('python3', [postScript, '--from-session'], { timeout: 30000 }, (err, stdout, stderr) => {
        if (err) {
          vscode.window.showErrorMessage(`Failed to export Pending Review: ${stderr || err.message}`);
          return;
        }
        try {
          const res = JSON.parse(stdout);
          vscode.window
            .showInformationMessage(
              `Exported ${res.comments_exported || staged.length} staged comment(s) to GitHub Pending Review!`,
              'Open PR on GitHub'
            )
            .then((choice) => {
              if (choice === 'Open PR on GitHub' && res.html_url) {
                vscode.env.openExternal(vscode.Uri.parse(res.html_url));
              }
            });
        } catch (_) {
          vscode.window.showInformationMessage('Created GitHub Pending Review!');
        }
      });
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('antigravityReview.refreshSession', () => {
      refreshAllUI(false);
    })
  );

  refreshAllUI(false);
}

function deactivate() {}

module.exports = { activate, deactivate };
