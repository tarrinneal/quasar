# Changelog

All notable changes to **Quasar** are documented in this file.

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
