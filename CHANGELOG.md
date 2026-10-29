# Changelog

All notable changes to **Quasar** are documented in this file.

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
