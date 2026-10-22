# Antigravity Code Review

A **comprehension-first, 100% native (zero-webview)** GitHub PR review tool for **Antigravity**.

Paste a single GitHub PR link and Antigravity automatically:
1. Fetches the PR metadata, unified diff, and existing GitHub review comments.
2. Materializes clean local `base/` and `head/` file snapshots so native side-by-side diffs (`vscode.diff`) work immediately without dirtying your active branch.
3. Runs 4 parallel specialist review subagents (`review-architect`, `review-bug-hunter`, `review-test-critic`, `review-advocate`).
4. Populates a **CliffsNotes Study Guide**, a **Guided Reading Tour** ordered by logical chapters (instead of alphabetically), **CliffsNotes Hover Cards & CodeLenses** (keeping your code completely free of inline comprehension clutter), and **Native Inline Comment Threads** where you can ask Antigravity questions directly on any line of code or stage comments for a **GitHub Pending Review**.

---

## Key Features

### 1. CliffsNotes-First Comprehension (No Code Clutter)
- **Native `CliffsNotes & Reading Tour` Sidebar TreeView**:
  - **At-a-Glance CliffsNotes**: Expandable `TL;DR`, `Character List (Key Symbols & Roles)`, and `Key Design Trade-offs` right in the Activity Bar.
  - **Logical Reading Chapters**: Groups files and hunks top-down (e.g., *Chapter 1: Core Interfaces & Models* $\rightarrow$ *Chapter 2: Implementation* $\rightarrow$ *Chapter 3: Verification & Unit Tests* $\rightarrow$ *Chapter 4: Skim / Generated Fixtures*).
  - **Step Through Tour Stops (`Alt+Down` / `Alt+Up`)**: Clicking any stop opens the native side-by-side `vscode.diff` editor scrolled directly to that hunk and marks the stop `✓ Read`.
- **Companion CliffsNotes Study Guide (`cliffs_notes.md`)**:
  - Click **Open Full CliffsNotes Study Guide** (`$(book)`) to view the formatted Markdown guide with Mermaid architecture diagrams and chapter-by-chapter breakdowns alongside your diff editor.
- **CliffsNotes on Hover & CodeLens**:
  - Hover over any changed hunk in the diff editor to see a **📘 CliffsNotes Hover Card** explaining *what* the hunk does, *why* it changed, and *how* it connects to callers/invariants—without adding comprehension threads into the code.

### 2. Ask Antigravity Directly in Inline Comment Threads (`vscode.comments`)
- Click the native `+` gutter icon on **any line** in the diff editor (or click `💬 Ask Antigravity` in the CodeLens/Hover card).
- Type a question in the comment box and click **`$(sparkle) Ask Antigravity`**:
  - Antigravity analyzes the exact hunk and surrounding codebase and posts its answer **directly back into that native inline comment thread**.
  - You can also reply inside any **AI Candidate Comment Thread** (`🔴 Blocking`, `🟡 Design`, `🟢 Nit`) to ask follow-up questions or ask Antigravity to refine the draft comment.

### 3. 1-Click GitHub Pending Review Export
- Stage any AI candidate finding (`✓ Stage for GitHub`) or write your own inline comment and click **`Stage Review Comment`**.
- Click **`$(cloud-upload) Export Staged Comments to GitHub Pending Review`** in the **Review Comments & Cart** panel to push all staged comments to GitHub as a **Pending Review** (draft) so you can do a final check in the browser before submitting.

---

## Installation

```bash
./install.sh
```

Then reload the Antigravity window (`Cmd+Shift+P` $\rightarrow$ `Developer: Reload Window`) to activate the **Antigravity Code Review** icon in the Activity Bar.

---

## How to Start a Review

Simply paste a GitHub PR link in Antigravity Chat:
> `Review https://github.com/flutter/packages/pull/13265`

Or click the **`$(git-pull-request) Review PR from GitHub URL...`** button at the top of the **Antigravity Code Review** sidebar and paste the PR link.
