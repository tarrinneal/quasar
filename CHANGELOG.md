# Changelog

All notable changes to **Quasar** are documented in this file.

## [1.4.0] - 2026-11-12

### Added
- **Inline Subagent Attribution (`sourceAgents` / `sourceAgent`)**:
  - Every finding now records which specialist `review-*` lens(es) surfaced it (preserving multiple lenses when duplicate findings are merged) and displays `🤖 <subagent>` badges across native VS Code inline `CommentThread` banners and author headers, sidebar items, interactive Study Guide cards, `quasar-web`, and the standalone Flutter app.
- **Per-Subagent Continuous Learning & `AGENT.md` Evolution (`quasar.teachSubagent` & `--subagent`)**:
  - Added **🎓 Teach / Tune Review Subagent...** directly inside inline comment cards, comment thread toolbars, sidebar context menus, the **Review Subagents** tree, `quasar-web`, and the Flutter app—plus 1-click subagent-only scoping inside **👍 Helpful**, **👎 Not Useful**, and **🤔 Calibrate Preference**.
  - Added `--subagent <review-*>` support, `subagentRules`, and `subagentStats` telemetry in `manage_preferences.py` (`~/.gemini/quasar/preferences.json` and `PREFERENCES.md`), automatic `learnedDirectives` injection per lens in `review_lenses.py` / `pr_context.json`, and 1-click Antigravity subagent evolution to refine a lens's `AGENT.md` alongside its upgrade-safe learned directives.

### Changed
- **High Effort by Default (`high`)**:
  - Updated the default review effort level from `medium` to `high` across `bin/quasar`, the VS Code / Antigravity extension, `manage_preferences.py`, `collect_pr_context.py`, `publish_review_session.py`, `quasar-web`, and the standalone Flutter app.

### Fixed
- **Deterministic Sidebar-to-Comment Scrolling (`revealRangeInVisibleEditor` & `jumpToCommentThread`)**:
  - Clicking a comment in the sidebar now prefers `vscode.window.activeTextEditor`, centers `range.end.line` on multi-line threads (where VS Code anchors inline `CommentThread` widgets), and verifies `editor.visibleRanges` across async diff folding and ZoneWidget layout passes so comments consistently land in view.

## [1.3.0] - 2026-11-10

### Added
- **Standalone Flutter App (Zero-Install Web + Native Desktop) Powered by Antigravity CLI (`agy`)**:
  - Added [`app/`](app/) (Flutter Web & Desktop client) and [`bin/quasar`](bin/quasar) launcher so Quasar runs completely standalone—either in any browser at `http://127.0.0.1:49341` (with zero IDE or desktop install required) or as a native desktop app (`./bin/quasar --desktop`).
  - Upgraded `plugin/sidecars/quasar-web/server.py` with direct headless Antigravity CLI (`agy --print --output-format stream-json`) auto-dispatch and live NDJSON progress streaming whenever no IDE language server is active, plus static serving of compiled `app/build/web` (`flutter_web`) and `/api/reply-thread` + `/api/open-in-editor` endpoints.
- **Dual-Artifact Release Packaging & One-Line Standalone Installer**:
  - Added [`install-standalone.sh`](install-standalone.sh) and `--standalone-only` / `--vscode-only` modes in [`install.sh`](install.sh) so releases publish both `quasar-<version>.vsix` (VS Code / Antigravity IDE) and `quasar-standalone-<version>.tar.gz` (precompiled Web App + `quasar` CLI).

### Fixed
- **Smooth Comment Navigation & Zero-Jump Inline Q&A Updates**:
  - Clicking any comment in the sidebar or Study Guide now expands collapsed threads and re-centers the diff editor viewport after Monaco finishes laying out diff hunks and inline `CommentThread` widgets (`revealRangeInVisibleEditor`).
  - `syncNativeCommentThreads` now updates `CommentThread` instances in place (reusing native `'+'` gutter widgets, preserving user expand/collapse state, initializing `lastSessionId` on startup, and suppressing self-triggered `fs.watch` bounces) so asking inline questions and receiving agent replies never tear down widgets or jump your scroll position.

## [1.2.5] - 2026-11-09

### Added
- **Structured Quasar Follow-Ups & Code-Level Verification on Existing GitHub PR Comments**:
  - Every pulled GitHub PR comment (`github_existing`) is now followed immediately by a structured **🤖 Quasar Follow-Up** message detailing **✅ What's Done**, **⚠️ What Isn't Done**, and **👉 What Should Be Done** across native VS Code inline comment threads, Study Guide cards, sidebar hover tooltips, CliffsNotes Markdown, and `quasar-web`.
  - Added a first-class `"partially_addressed"` (`🟡 [Partially Addressed]`) triage status so comments where code was updated in HEAD—but an edge case or cleaner refinement remains—explicitly credit what is already done instead of appearing unfixed.
  - Added direct HEAD snapshot & diff-hunk code checks (`inspect_comment_code_state` and `enrich_comments_with_code_state` in `collect_pr_context.py`) to verify reverted/untouched files, already-applied ````suggestion``` blocks, and post-comment line edits automatically.

### Fixed
- **VS Code `agy --hub` Agent Auto-Dispatch Discovery**:
  - Updated `discoverAntigravityLsEnv` (`extension/extension.js`) and `discover_ls_env` (`plugin/sidecars/quasar-web/server.py`) to discover VS Code's `agy --hub` processes (`--csrf_token=...`, `--hub-port=...`, and `--add-dir=...` per-workspace routing) in addition to standalone `language_server` processes.
- **Top-Level Review Comment Scoop-Up & Inline Action Bar Placement**:
  - `build_combined_top_level_comment` and `sync_top_level_comments_file` now exclude `github_existing` and non-`staged` threads so existing GitHub comments on files outside the current diff are never merged into new top-level review drafts.
  - Inline solution options and fix controls on `github_existing` threads now attach to Quasar's follow-up message rather than the reviewer's original GitHub comment, showing `✅ No Code Changes Needed` on already-addressed comments.

## [1.2.4] - 2026-11-09

### Added
- **New general-purpose review lenses**: `review-security`, `review-performance`, `review-concurrency`, `review-compat`, `review-error-handling`, `review-docs`, `review-readability`, `review-dependencies`, `review-accessibility`, and opt-in `review-newcomer`. Each declares default effort levels (`quasarEfforts`) and an applicability gate (`quasarAppliesWhen`) so irrelevant lenses skip diffs they don't apply to.
- **Configurable lenses per effort level**: `Quasar: Configure Review Lenses per Effort Level...` (sidebar, Preferences menu, Command Palette) and `review_lenses.py` CLI. Overrides persist as `lensEfforts` in `preferences.json`; `pr_context.json` now includes `review_lenses` (current effort) and `effort_lenses` (all levels), and the review pipeline spawns only the configured lenses.
- **Solution options on user-owned reviews**: findings and existing comments with multiple valid fixes carry selectable `solutionOptions` in all Quasar UIs, and fixes follow the chosen approach.

### Changed
- `medium` effort now includes `review-security` by default (gated to security-relevant diffs).
- Removed the built-in domain-specific lens from the shipped plugin; Quasar's built-in lenses are now general-purpose. Domain lenses belong in `~/.gemini/config/agents/` or `<repo>/.gemini/agents/`.

## [1.2.3] - 2026-10-30

### Added
- **Proxied & Remote Sandbox Support for GitHub Token Verification & Review Publishing**:
  - `submitToken()` in `quasar-web` now verifies GitHub Personal Access Tokens against `https://api.github.com/user` directly from the browser first and sends the verified `login` and `name` to `/api/github-token` (with graceful `RemoteDisconnected` fallback on the backend when outbound DLP proxies drop `ghp_...` / `github_pat_...` headers).
  - `post_pending_review.py` and `/api/publish-github` now return a hunk-aligned `needs_browser_publish` (`clientSidePublish: true`) payload when backend `POST` requests to `api.github.com` are blocked by outbound proxies (`RemoteDisconnected`), allowing the browser to `POST` the review directly to GitHub via CORS.
  - Added clean simulated review publishing in **Tutorial Sandbox** mode (`status: "tutorial_simulated"`).

### Fixed
- **Public PR Fetching Behind DLP Header Scanners**:
  - `github_api_get()` and `fetch_raw_file_content()` in `collect_pr_context.py` automatically retry without the `Authorization` header if a backend `GET` request carrying a stored token is dropped by an outbound proxy.
- **Sidecar Port Contention in `install.sh`**:
  - `install.sh` now skips launching `--standalone-daemon` when `SidecarManager` is active so they do not race for port `49341`.

## [1.2.2] - 2026-10-30

### Added
- **Quasar Web UI Sidecar & Standalone Browser App (`quasar-web`)**:
  - Added `plugin/sidecars/quasar-web` (`sidecar://quasar/quasar-web/` and `http://127.0.0.1:49341`) plus self-contained `quasar_review.html` export for Antigravity Web, Desktop Aux/Full Panes, and standalone browser sessions.
  - Supports interactive Study Guides, clickable SVG flowcharts, side-by-side & unified diffs, inline line Q&A, comment revision with the agent, batch comment fixing, and 1-click GitHub review publishing.
  - Added a **Live Agent Activity & Progress Indicator** (`#agentPill` & `#agentActivityBanner`) that tracks pipeline phases (`Phase 1/3` → `Phase 2/3` → `Phase 3/3`), active subagents, live tool steps, and elapsed time in real time.
- **Update Existing Pending Reviews on GitHub**:
  - Automatically detects and replaces any existing `PENDING` review owned by you on the PR when pushing updated staged comments (`delete_existing_pending_reviews` in `post_pending_review.py`).
- **New Quasar App & Activity Bar Icons**:
  - Added custom Quasar extension icon (`media/quasar-icon.png`), activity bar SVG (`media/review-icon.svg`), and Web UI favicon.

### Fixed
- **Top-Level PR Comment Preservation on Pending Reviews**:
  - Automatically copies the top-level review summary (`TOP_LEVEL_PR_COMMENTS.md`) to the clipboard when creating/updating a GitHub `PENDING` review (since GitHub discards `PENDING` review bodies in the web submit box) and fixed duplicate intro lines in `overflow_notes`.
- **Antigravity Web Sidecar Auth & AgentAPI Context Resolution**:
  - Added `/preload.js`, `X-Sidecar-Token` forwarding, cross-project `ANTIGRAVITY_PROJECT_ID` / `rootConversationId` resolution in `server.py`, and automatic plugin enablement (`JetboxWriteState` + `GetAllPlugins`) in `install.sh`.

## [1.2.0] - 2026-10-29

### Added
- **Interactive Sandbox PR Tutorial (`quasar.startTutorial`)**:
  - Added a zero-setup **🎓 Interactive Tutorial (Sandbox PR)** (`collect_pr_context.py --tutorial`) accessible from the Quasar Quick Menu, Command Palette, and empty-state welcome view.
  - Creates a realistic 3-file sandbox PR (`src/token_cache.py`, `src/auth_middleware.py`, `tests/test_token_cache.py`) with an interactive 5-step walkthrough banner in the Study Guide, clickable SVG architecture flowchart, guided reading tour stops, and realistic `🔴 Blocking`, `🟡 Design`, `🟢 Nit`, and `🤔 Uncertain` threads to test every Quasar feature safely.
- **Comments-Only Review Mode (`--comments-only`)**:
  - Added a **💬 Comments Only (Skip Study Guide & Tour)** scope selector when starting any GitHub PR or local branch review (as well as `--comments-only` in `collect_pr_context.py` and `publish_review_session.py`, plus `"commentsOnly"` in `SKILL.md`).
  - Skips generating the CliffsNotes study guide, Mermaid flowcharts, and reading tour chapters/stops—producing only high-signal inline review comments ready to publish to GitHub or fix with the agent.
- **Comment on Quasar Comments to Revise with Agent (`quasar.commentOnComment`)**:
  - Added **💬 Ask Agent to Change Comment...** across inline diff comment threads (both typing directly in the reply box and via thread action buttons), sidebar comment context menus, and Study Guide finding cards (`answer_comment_question.py --revise-finding`).
  - Instructs the agent to rewrite the comment's `title`, `body`, `suggestion`, or `severity` in-place based on your feedback and records a before/after phrasing example in your learned preferences.

### Changed
- **Concise-by-Default Comment Style & Optional Code Suggestions**:
  - Tuned `SKILL.md`, all 4 review subagents (`review-architect`, `review-bug-hunter`, `review-test-critic`, `review-advocate`), and default style preferences in `manage_preferences.py` so comments lead with the core issue in 1–3 concise sentences without filler.
  - Made `suggestion` blocks optional—only included when a concrete replacement snippet adds clear value over stating the issue plainly.
- **Cleaner Sidebar by Default (`quasar.toggleChecksTree`)**:
  - Hid the **Checks & Learned Preferences** sidebar section (`quasar.checksTree`) by default so the sidebar stays focused on **CliffsNotes & Reading Tour** and **Review Comments & Cart**, with a **Show / Hide Checks & Learned Preferences Panel** toggle in the Quasar Quick Menu and Command Palette.

### Fixed
- **Strict Per-Window Session Isolation**:
  - Locked each active review session to the exact VS Code / Antigravity window that activated it using per-window `windowId` (`vscode.env.sessionId`) and extension host `windowPid` (`process.pid`) ownership verification, plus window focus registration (`windows.json`).
  - Removed parent `.git` monorepo walk-up and open-document fallback in `resolveWorkspaceRootSync` / `resolve_workspace_root`, preventing reviews or tutorials in one window from ever leaking into other open windows.

## [1.1.2] - 2026-10-28

### Fixed
- **Conversation Routing for Review Actions (`Fix`, `Ask Quasar`, `Calibrate`)**:
  - Pinned the review session's owning `conversationId` in `active_session.json` (`publish_review_session.py` & `detectActiveConversationId`) so follow-up actions always route to the exact conversation that ran the review instead of another recent conversation tab in the workspace.
  - Tightened workspace path boundary matching when scanning conversation transcripts.
- **Effort Selector UI Polish**:
  - Fixed `$(pulse)` codicon rendering for Medium Effort in QuickPick, normalized sidebar `ThemeIcon` IDs, and added `timeEstimate` labels in the sidebar tree.

## [1.1.1] - 2026-10-28

### Added
- **Configurable Review Effort Levels (`Low`, `Medium`, `High`)**:
  - **⚡ Low Effort (Fast Single-Pass)**: Zero subagents, direct single-pass diff synthesis using `flash` for fast CliffsNotes tours and obvious high-confidence blockers (~20–35s).
  - **⚖️ Medium Effort (Balanced — Default)**: Spawns 2 parallel `flash` subagents (`review-architect` + `review-bug-hunter`) in a single wave and filters directly without a second sequential subagent wave (~45–75s).
  - **🔬 High Effort (Deep 4-Lens Pipeline)**: Runs the full parallel 3-subagent wave (`review-architect`, `review-bug-hunter`, `review-test-critic`) plus thorough false-positive verification for complex PRs.
  - Added quick-pick effort selector when launching PR or local branch reviews, a `Quasar: Set Review Effort Level (Low / Medium / High)...` command (`quasar.selectEffortLevel`), sidebar/header effort badges, and persistent `defaultEffort` preference in `~/.gemini/quasar/preferences.json`.

### Changed
- **Single-Column Study Guide View**:
  - Opening the Interactive CliffsNotes Study Guide now opens in the current active editor column (`vscode.ViewColumn.Active`) instead of forcing a side-by-side split window (`vscode.ViewColumn.Beside`).

## [0.1.0] - Initial Release

### Added
- **Interactive CliffsNotes Study Guide**:
  - Concise 1-sentence plain-English explanations by default with expandable `<details>` drawers for deeper context, invariants, and live line-numbered code snippets.
  - Built-in zero-dependency SVG Flowchart engine (`mermaid-preview.js`) with clickable nodes that jump directly to the relevant diff hunk.
  - Chapter & Tour Stop reading progress tracker (`✓ Read` checkboxes, progress ring, and `Next Unread Stop` navigation).
- **Multi-Lens Parallel Code Review Pipeline**:
  - 4 specialist subagents (`review-architect`, `review-bug-hunter`, `review-test-critic`, `review-advocate`) for high-signal, false-positive-filtered code review.
- **Native IDE Diff & Inline Comment Experience**:
  - Side-by-side diffs (`vscode.diff`) backed by clean local `base/` and `head/` file snapshots.
  - CliffsNotes Hover Cards & CodeLenses on changed hunks.
  - Inline `Ask Quasar` comment threads on any line of code.
  - Review findings staged by default for 1-click GitHub Review publishing (`COMMENT`, `APPROVE`, `REQUEST_CHANGES`, or `PENDING`) and easily removable/dismissable.
- **Preference Calibration & Continuous Learning**:
  - Interactive calibration for uncertain/borderline findings (`status: "uncertain"`).
  - Non-destructive 3-layer preference hierarchy (Core Defaults, Imported Seed Skills, and Learned Preferences) persisted to `~/.gemini/quasar/PREFERENCES.md`.
- **Author Self-Review & Branch Switching**:
  - Auto-detects when a reviewed GitHub PR belongs to the current user (`gh`, `GITHUB_TOKEN`, git remotes, or git author identity) and prompts to switch to the PR branch.
  - 1-click `Fix Selected Comments with Agent` workflow for addressing review findings and teammate PR comments directly in the working tree.
- **Session Management**:
  - Per-window isolated review sessions, `Past Reviews` history archive & switcher, and `Close / Clear Current Review` to cleanly remove all review UI and inline comment decorations.
