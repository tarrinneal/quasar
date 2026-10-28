# Quasar

A **comprehension-first, 100% native (zero-webview)** GitHub PR & local branch code review tool for Jetski / Antigravity.

Point **Quasar** at any **GitHub PR URL** or **local branch / working-tree diff** and it automatically:
1. Fetches the PR metadata or local branch commit history, unified diff, and existing comments.
2. Materializes clean local `base/` and `head/` file snapshots so native side-by-side diffs (`vscode.diff`) work immediately without dirtying your active branch.
3. Runs 4 parallel specialist review subagents (`review-architect`, `review-bug-hunter`, `review-test-critic`, `review-advocate`).
4. Populates a **CliffsNotes Study Guide**, a **Guided Reading Tour** ordered by logical chapters (instead of alphabetically), **CliffsNotes Hover Cards & CodeLenses** (keeping your code completely free of inline comprehension clutter), and **Native Inline Comment Threads** where you can ask Quasar questions directly on any line of code, calibrate your review preferences, or stage comments for a **GitHub Pending Review**.

---

## 🚀 Foolproof Human Setup Guide (2 Minutes)

### Step 1: Check Prerequisites
- **Required**: **`git`** and **`python3`** (both pre-installed on macOS). **Local branches and public GitHub PRs work immediately with zero extra setup.**
- **Optional** (`gh` CLI or `GITHUB_TOKEN`): *Only needed if you want to review **private** GitHub PRs or use 1-click **Export Staged Comments to GitHub Pending Review***:
  ```bash
  brew install gh && gh auth login
  ```

### Step 2: Run the Installer
Open a terminal in this repository and run:
```bash
git clone https://github.com/tarrinneal/quasar.git quasar   # if not already cloned
cd quasar
chmod +x install.sh
./install.sh
```
`./install.sh` automatically:
1. Installs the **Quasar** agent plugin (`SKILL.md`, Python scripts, and 4 specialist review subagents) to `~/.gemini/config/plugins/quasar`.
2. Packages and installs the **Quasar** native IDE extension into `~/.jetski/extensions` (and `~/.vscode/extensions`).
3. Initializes your persistent preference store at `~/.gemini/quasar/PREFERENCES.md`.
4. Runs the **Quasar Doctor** (`./install.sh --check`) and prints `✅` checks for every component.

### Step 3: Reload Your Window (Required Once!)
Because Jetski scans extensions at window startup, you **must reload the window once** after running `./install.sh`:
1. Press **`Cmd+Shift+P`** (macOS) or **`Ctrl+Shift+P`** (Windows/Linux).
2. Type **`Developer: Reload Window`** and press **Enter**.

### Step 4: Verify Where to Find Quasar in the UI
After reloading the window, you can open **Quasar** from **3 places**:
1. **Bottom-Left Status Bar (Easiest)**: Click **`$(git-pull-request) Quasar`** in the bottom-left status bar to open the Quasar Quick Menu & focus the sidebar.
2. **Command Palette (`Cmd+Shift+P`)**: Type **`Quasar`** to see all commands:
   - `Quasar: Open Quasar Sidebar & Menu...`
   - `Quasar: Review Local Branch or Diff...`
   - `Quasar: Review PR from GitHub URL...`
   - `Quasar: Import Premade Review Skill as Starting Point...`
   - `Quasar: Manage & Calibrate Reviewer Preferences...`
3. **Source Control (`Git`) Sidebar Header**: Click the **branch** or **PR** icon buttons at the top-right of the Source Control panel.

---

## 🤖 Agent Setup Playbook (Copy-Paste Instructions for an AI Agent)

If you want an AI coding agent to install, verify, or configure Quasar for you, paste this prompt into chat:

> **Prompt for Agent**:
> `"Set up and verify Quasar from /path/to/quasar following the Agent Setup Playbook in README.md."`

### Agent Execution Steps (Non-Interactive)
When an agent is asked to set up Quasar, it should execute these exact steps:

1. **Run the installer & built-in health check**:
   ```bash
   chmod +x /path/to/quasar/install.sh
   /path/to/quasar/install.sh
   /path/to/quasar/install.sh --check
   ```
2. **Verify GitHub CLI authentication (if reviewing GitHub PRs)**:
   ```bash
   gh auth status
   ```
   - If `gh` is not authenticated, inform the user they can either run `gh auth login` for GitHub PR support or immediately use local branch reviews (`Review my current branch`).
3. **(Optional) Seed a premade review skill if the user has one**:
   - Check if the user has existing review skills or guidelines:
     ```bash
     python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/manage_preferences.py --discover-skills
     ```
   - If the user asks to use a specific `SKILL.md` or review guide as a starting point, import it non-destructively:
     ```bash
     python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/manage_preferences.py --import-skill "/path/to/SKILL.md"
     ```
4. **Prompt the user for the final UI reload**:
   - Tell the user: *"Quasar is installed and verified! Please press `Cmd+Shift+P` -> `Developer: Reload Window` once to activate the native Quasar sidebar and status bar."*

---

## 📖 How to Start a Review

### Option A: Review a Local Branch or Working-Tree Diff
- **In Chat**:
  - `Review my current branch`
  - `Review local branch feature/my-branch`
  - `Review local branch main...feature/my-branch in /path/to/repo`
- **From the UI**:
  - Click **`Quasar`** in the bottom Status Bar $\rightarrow$ **`Review Local Branch or Working-Tree Diff...`** to interactively pick a repository, local branch (including uncommitted working-tree changes), and base ref.

### Option B: Review a GitHub Pull Request
- **In Chat**:
  - `Review https://github.com/owner/repo/pull/123`
- **From the UI**:
  - Click **`Quasar`** in the bottom Status Bar $\rightarrow$ **`Review GitHub PR from URL or Number...`** and paste the PR link.

---

## ✨ Key Features

### 1. CliffsNotes-First Comprehension (No Code Clutter)
- **Native `CliffsNotes & Reading Tour` Sidebar TreeView**:
  - **At-a-Glance CliffsNotes**: Expandable `TL;DR`, `Character List (Key Symbols & Roles)`, and `Key Design Trade-offs` right in the sidebar.
  - **Logical Reading Chapters**: Groups files and hunks top-down (e.g., *Chapter 1: Core Interfaces & Models* $\rightarrow$ *Chapter 2: Implementation* $\rightarrow$ *Chapter 3: Verification & Unit Tests* $\rightarrow$ *Chapter 4: Skim / Generated Fixtures*).
  - **Step Through Tour Stops (`Alt+Down` / `Alt+Up`)**: Clicking any stop opens the native side-by-side `vscode.diff` editor scrolled directly to that hunk and marks the stop `✓ Read`.
- **Companion CliffsNotes Study Guide (`cliffs_notes.md`)**:
  - Click **Open Full CliffsNotes Study Guide** (`$(book)`) to view the formatted Markdown guide with Mermaid architecture diagrams and chapter-by-chapter breakdowns alongside your diff editor.
- **CliffsNotes on Hover & CodeLens**:
  - Hover over any changed hunk in the diff editor to see a **📘 Quasar CliffsNotes Hover Card** explaining *what* the hunk does, *why* it changed, and *how* it connects to callers/invariants—without adding comprehension threads into the code.

### 2. Ask Quasar Directly in Inline Comment Threads (`vscode.comments`)
- Click the native `+` gutter icon on **any line** in the diff editor (or click `💬 Ask Quasar` in the CodeLens/Hover card).
- Type a question in the comment box and click **`$(sparkle) Ask Quasar`**:
  - Quasar analyzes the exact hunk and surrounding codebase and posts its answer **directly back into that native inline comment thread**.
  - You can also reply inside any **Quasar Candidate Comment Thread** (`🔴 Blocking`, `🟡 Design`, `🟢 Nit`) to ask follow-up questions or ask Quasar to refine the draft comment.

### 3. Preference Calibration, Premade Skill Seeding & Continuous Learning
- **🌱 Import a Premade Review Skill as a Starting Point**:
  - Have an existing `SKILL.md`, team review guide, or checklist you already like? Run **`Quasar: Import Premade Review Skill as Starting Point...`** (or click **`Import Premade Review Skill as Starting Point...`** in the **Checks & Learned Preferences** panel).
  - **Non-Destructive 3-Layer Hierarchy**: Importing a skill never overwrites Quasar's built-in defaults or your learned rules. Instead, Quasar layers preferences in priority order:
    1. **Quasar Core Defaults** (CliffsNotes reading tour, multi-lens verification, and collaborative tone).
    2. **Starting-Point Seed Skills (`origin: "seed_skill"`)** (baseline rules and domain checklists extracted from your premade skill, stored in `~/.gemini/quasar/seed_skills/`).
    3. **Evolved Learned Preferences (`origin: "learned"`, Highest Priority)** (rules learned from your calibrations, 👍/👎 ratings, and inline feedback automatically supersede conflicting baseline rules so Quasar grows with you over time).
- **🤔 Uncertain-Feedback Calibration**:
  - When Quasar encounters borderline or preference-sensitive feedback (e.g., private-helper unit test strictness, API visibility trade-offs, or polish nits not yet covered by your preferences), it marks the finding as **`🤔 Uncertain — Calibrate Your Preference`** and asks how you want to handle that pattern.
  - Click **`$(question) Calibrate Preference`** on the inline thread (or answer the interactive prompt in chat) to either **Stage & Always Flag Similar**, **Dismiss & Skip Pattern in Future Reviews**, or **Keep Once**.
- **👍 / 👎 / 🧠 Feedback on Feedback**:
  - Every inline comment thread and sidebar finding includes **`$(thumbsup) Helpful (More Like This)`**, **`$(thumbsdown) Not Useful (Less Like This...)`**, and **`$(lightbulb-sparkle) Teach Quasar / Give Feedback on This Feedback...`**.
  - You can also type feedback directly into any inline comment reply box and click **`Teach Quasar`**, or edit a draft comment (Quasar automatically records before/after phrasing examples to learn your tone).
- **Persistent Preference Memory (`~/.gemini/quasar/PREFERENCES.md` & `preferences.json`)**:
  - Learned rules, seed skills, style profiles, and phrasing examples are automatically persisted and injected into `pr_context.json` and all 4 review subagents on every future review.

### 4. Author Self-Review: Check Comments & Fix with Agent (`manage_fixes.py`)
- When reviewing a **local branch** or a **GitHub PR you own** (auto-detected from your git/GitHub identity or checked-out branch), Quasar enables **Author Self-Review / Fix Mode**:
  - **Check Any Comment in the Sidebar**: Every Quasar finding (`🔴 Blocking`, `🟡 Design`, `🟢 Nit`, `🤔 Uncertain`) **and** every **Existing GitHub PR Comment from teammates** (`👀`) has a native checkbox in the **Review Comments & Cart** tree.
  - **Queue or Fix Directly from Inline Diff Threads**: Click **`$(tools) Queue / Unqueue Comment for Agent Fix`** or **`$(wrench) Fix This Issue with Quasar Now...`** on any inline comment thread in the diff editor.
  - **Batch Dispatch with Optional Instructions**: Click **`$(wrench) Fix Selected Comments with Agent...`** in the sidebar header (or the top action node in **Review Comments & Cart**) to confirm your checklist, add optional custom instructions, and pass a structured fix manifest (`~/.gemini/quasar/fix_request.json`) to the agent.
  - **Working-Tree Edits, Live Diff Sync & Preference Learning**:
    - The agent edits the real working-tree files in your local repo (without auto-committing), syncs the updated files into Quasar's diff snapshot so your open `vscode.diff` view updates immediately, marks the threads **`✅ Fixed`**, and records a positive preference signal on any Quasar findings you chose to fix.

### 5. Publish Reviews Directly to GitHub (as You) via GitHub API Key
- **No `gh` CLI Required**: Click **`$(key) Configure GitHub API Key / Personal Access Token...`** in the **Review Comments & Cart** header (or in the **Checks & Learned Preferences** tree / Quasar menu) and paste a GitHub Personal Access Token (`repo` scope or Fine-Grained `Pull requests: Read & write`).
  - Quasar verifies the token against `https://api.github.com/user`, displays your `@login`, and stores it locally at `~/.gemini/quasar/github_auth.json` (`chmod 0600`).
- **Publish as `@your-username` in Any Mode**:
  - Click **`$(cloud-upload) Publish / Export Review to GitHub (as You)...`** in the **Review Comments & Cart** header to submit your staged inline comments (and an optional top-level review summary) as:
    - **`COMMENT`** — Publish review comments immediately to the PR under your GitHub account.
    - **`APPROVE`** — Approve the PR and publish your inline comments/summary as you.
    - **`REQUEST_CHANGES`** — Request changes on the PR and publish your inline comments/summary as you.
    - **`PENDING`** — Create an unpublished Draft Pending Review on GitHub so you can inspect it in the browser before submitting.

---

## 🛠️ Troubleshooting

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| **`Cmd+Shift+P` -> `Quasar` shows no commands** | Window hasn't been reloaded since running `./install.sh`, or extension cache is stale. | Run `./install.sh` in terminal, then press `Cmd+Shift+P` -> `Developer: Reload Window`. |
| **Where is the Quasar sidebar icon?** | Activity bar icons can be hidden or collapsed inside the `...` overflow menu if many extensions are installed. | Click **`$(git-pull-request) Quasar`** in the **bottom-left Status Bar**, or run `Cmd+Shift+P` -> `Quasar: Open Quasar Sidebar & Menu...` to automatically reveal and focus the sidebar. |
| **Private GitHub PR fails to fetch or publish** | No GitHub API Key or `gh` CLI auth configured yet. | Click **`$(key) Configure GitHub API Key / Personal Access Token...`** in Quasar and paste your GitHub token (or run `gh auth login`). |
| **How do I check if everything is installed properly?** | Want a quick health check. | Run `./install.sh --check` in your terminal. |
