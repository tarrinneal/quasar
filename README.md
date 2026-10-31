# Quasar

A **comprehension-first, 100% native** GitHub PR & local branch code review tool for Antigravity.

Point **Quasar** at any **GitHub PR URL** or **local branch / working-tree diff** and it automatically:
1. Fetches the PR metadata or local branch commit history, unified diff, and existing comments.
2. Materializes clean local `base/` and `head/` file snapshots so native side-by-side diffs (`vscode.diff`) work immediately without dirtying your active branch.
3. Runs 4 parallel specialist review subagents (`review-architect`, `review-bug-hunter`, `review-test-critic`, `review-advocate`, plus any custom `review-*` subagents you add).
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
1. Installs the **Quasar** agent plugin (`SKILL.md`, Python scripts, and specialist review subagents) to `~/.gemini/config/plugins/quasar`.
2. Packages and installs the **Quasar** native IDE extension into `~/.antigravity/extensions` (and `~/.vscode/extensions`).
3. Initializes your persistent preference store at `~/.gemini/quasar/PREFERENCES.md`.
4. Runs the **Quasar Doctor** (`./install.sh --check`) and prints `✅` checks for every component.

### Step 3: Reload Your Window (Required Once!)
Because the IDE scans extensions at window startup, you **must reload the window once** after running `./install.sh`:
1. Press **`Cmd+Shift+P`** (macOS) or **`Ctrl+Shift+P`** (Windows/Linux).
2. Type **`Developer: Reload Window`** and press **Enter**.

### Step 4: Verify Where to Find Quasar in the UI
After reloading the window, you can open **Quasar** from **3 places**:
1. **Bottom-Left Status Bar (Easiest)**: Click **`$(git-pull-request) Quasar`** in the bottom-left status bar to open the Quasar Quick Menu & focus the sidebar.
2. **Command Palette (`Cmd+Shift+P`)**: Type **`Quasar`** to see all commands:
   - `Quasar: Open Quasar Sidebar & Menu...`
   - `Quasar: Start Interactive Tutorial (Sandbox PR)...`
   - `Quasar: Review Local Branch or Diff...`
   - `Quasar: Review PR from GitHub URL...`
   - `Quasar: Add / Manage Review Subagents (review-*)...`
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

## 📖 How to Start a Review (or Try the Interactive Tutorial)

### Option 0: 🎓 Try the Interactive Sandbox PR Tutorial (Zero Setup)
- Click **`Quasar`** in the bottom Status Bar $\rightarrow$ **`🎓 Start Interactive Tutorial (Sandbox PR)`** (or run **`Quasar: Start Interactive Tutorial (Sandbox PR)...`** from the Command Palette).
- Quasar instantly loads a self-contained 3-file sandbox PR (`PR #42: Add TTL token cache & auth middleware`) with a 5-step interactive walkthrough banner so you can explore the Study Guide, SVG flowchart, guided reading tour, and inline comment actions without touching your repo.

### Option A: Review a Local Branch or Working-Tree Diff
- **In Chat**:
  - `Review my current branch`
  - `Review my current branch (comments only)`
  - `Review local branch feature/my-branch`
  - `Review local branch main...feature/my-branch in /path/to/repo`
- **From the UI**:
  - Click **`Quasar`** in the bottom Status Bar $\rightarrow$ **`Review Local Branch or Working-Tree Diff...`** to interactively pick a repository, local branch (including uncommitted working-tree changes), base ref, effort level (`Low`, `Medium`, `High`), and review mode (**`📘 Full Review (Study Guide + Comments)`** or **`💬 Comments Only`**).

### Option B: Review a GitHub Pull Request
- **In Chat**:
  - `Review https://github.com/owner/repo/pull/123`
  - `Review https://github.com/owner/repo/pull/123 comments only`
- **From the UI**:
  - Click **`Quasar`** in the bottom Status Bar $\rightarrow$ **`Review GitHub PR from URL or Number...`**, paste the PR link, and choose your effort level and review mode (**Full Review** or **Comments Only**).

---

## ✨ Key Features

### 1. CliffsNotes-First Comprehension (or Fast Comments-Only Mode)
- **Native `CliffsNotes & Reading Tour` Sidebar TreeView**:
  - **At-a-Glance CliffsNotes**: Expandable `TL;DR`, `Character List (Key Symbols & Roles)`, and `Key Design Trade-offs` right in the sidebar.
  - **Logical Reading Chapters**: Groups files and hunks top-down (e.g., *Chapter 1: Core Interfaces & Models* $\rightarrow$ *Chapter 2: Implementation* $\rightarrow$ *Chapter 3: Verification & Unit Tests* $\rightarrow$ *Chapter 4: Skim / Generated Fixtures*).
  - **Step Through Tour Stops (`Alt+Down` / `Alt+Up`)**: Clicking any stop opens the native side-by-side `vscode.diff` editor scrolled directly to that hunk and marks the stop `✓ Read`.
- **Companion CliffsNotes Study Guide (`cliffs_notes.md`)**:
  - Click **Open Full CliffsNotes Study Guide** (`$(book)`) to view the formatted Markdown guide with Mermaid architecture diagrams and chapter-by-chapter breakdowns alongside your diff editor.
- **CliffsNotes on Hover & CodeLens**:
  - Hover over any changed hunk in the diff editor to see a **📘 Quasar CliffsNotes Hover Card** explaining *what* the hunk does, *why* it changed, and *how* it connects to callers/invariants—without adding comprehension threads into the code.
- **💬 Comments-Only Mode**:
  - Want just the inline review comments to publish to GitHub or fix with the agent without generating the study guide or reading tour? Select **💬 Comments Only** when launching a review (or say `"comments only"` in chat).

### 2. Ask Quasar & Revise Comments with the Agent (`vscode.comments`)
- Click the native `+` gutter icon on **any line** in the diff editor (or click `💬 Ask Quasar` in the CodeLens/Hover card).
- Type a question in the comment box and click **`$(sparkle) Ask Quasar`**:
  - Quasar analyzes the exact hunk and surrounding codebase and posts its answer **directly back into that native inline comment thread**.
- **Comment on Any Quasar Comment to Have the Agent Change It**:
  - Reply directly inside any **Quasar Candidate Comment Thread** (`🔴 Blocking`, `🟡 Design`, `🟢 Nit`, `🤔 Uncertain`) or click **`💬 Ask Agent to Change Comment...`** (available on inline diff threads, sidebar comment items, and Study Guide finding cards) to tell the agent how to revise the comment (e.g., *"Make this shorter"*, *"Remove the code suggestion"*, or *"Downgrade to Nit"*). Quasar rewrites the comment in-place and learns from your before/after phrasing.

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
  - Learned rules, seed skills, style profiles, and phrasing examples are automatically persisted and injected into `pr_context.json` and all review subagents on every future review.

### 4. Multi-Lens Review Subagents & Adding Custom Review Agents (`review-*`)
Quasar ships with 4 built-in specialist review subagents under `plugin/agents/`:
- **`review-architect`**: Architecture, API surface, backwards compatibility, and logical reading order.
- **`review-bug-hunter`**: Execution paths, nullability, concurrency/async hazards, resource leaks, and platform edge cases.
- **`review-test-critic`**: Test falsifiability, edge-case coverage matrix, generated file sync, and repo hygiene (`CHANGELOG.md`, `pubspec.yaml`).
- **`review-advocate`**: False-positive filter that verifies candidate findings against callers, types, and existing PR comments.

#### How to Add Your Own Review Subagents
You can add custom domain-, language-, or team-specific review subagents (e.g., `review-security`, `review-accessibility`, `review-sql-migrations`) and Quasar will automatically discover them (`available_review_agents` in `pr_context.json`) and spawn them in parallel during `medium` and `high` effort reviews:

- **Option A: Directly from the Extension UI (No GitHub Repo Needed)**
  1. Open the Command Palette (`Cmd+Shift+P` $\rightarrow$ **`Quasar: Add / Manage Review Subagents (review-*)...`**), click **`$(git-pull-request) Quasar`** in the Status Bar $\rightarrow$ **`Add / Manage Review Subagents (review-*)...`**, or expand **`Review Subagents`** in the **Checks & Learned Preferences** sidebar.
  2. Choose how to add your subagent:
     - **`$(add) Create New Custom Review Subagent (Guided Wizard)...`** — Prompts for name, specialty, install scope (`Quasar Plugin`, `Current Workspace (.gemini/agents/)`, or `Global User Agents`), and optional checklist items, then creates and opens `AGENT.md` in your editor.
     - **`$(sparkle) Ask Antigravity to Generate a Specialist Review Subagent...`** — Describe the domain or persona and let Antigravity write `AGENT.md` tailored to your repository.
     - **`$(file-text) Import Existing AGENT.md or Markdown Checklist as Review Subagent...`** — Pick any `.md` file on disk and install it as a parallel `review-*` subagent.

- **Option B: Create an `AGENT.md` File on Disk**
  1. **Create an `AGENT.md` file** in any of these automatically discovered locations:
     - **Directly in the installed Quasar plugin**: `~/.gemini/config/plugins/quasar/agents/review-<your-agent>/AGENT.md`
     - **Per-repository / workspace agents** (team-sharable in git): `<your-repo>/.gemini/agents/review-<your-agent>/AGENT.md`
     - **Global user agents**: `~/.gemini/config/agents/review-<your-agent>/AGENT.md`
     - **In the Quasar repo** (if installing from source): `plugin/agents/review-<your-agent>/AGENT.md` (then run `./install.sh`)
  2. **Use the standard read-only review subagent template**:
     ```markdown
     ---
     name: review-security
     description: "Security & Auth Reviewer. Evaluates authentication boundaries, secret handling, injection risks, and permission checks."
     tools:
       - view_file
       - grep_search
       - find_by_name
       - list_dir
     mainAgent: false
     subagent: true
     commandExecutionPolicy: off
     ---

     # Security & Auth Reviewer (`review-security`)

     You are a specialist code review subagent focused on security boundaries and safe data handling. You operate in read-only mode.

     ## What You Hunt For
     - Unvalidated external inputs, path traversal, or command/SQL injection risks.
     - Token/secret leakage in logs, error messages, or serialized state.
     - Missing authorization or permission checks on newly exposed endpoints or platform channels.

     ## Honor Learned Reviewer Preferences
     Check `~/.gemini/quasar/PREFERENCES.md` or the `reviewer_preferences` passed in your prompt. Skip patterns marked `Avoid` and match the reviewer's preferred comment tone.

     ## Output Format
     For each verified issue, return:
     - **Severity**: `BLOCKING`, `DESIGN`, or `NIT`
     - **Location**: `path/to/file:startLine-endLine`
     - **Title**: Short 1-line summary
     - **Why It Matters**: Concrete trace showing how the issue can be triggered
     - **Draft GitHub Comment**: Concise, collaborative 1–2 sentence review comment
     ```
  3. **Automatic Discovery & Execution**:
     - On the next review, `collect_pr_context.py` automatically detects all installed `review-*` subagents and lists them under `available_review_agents` in `pr_context.json` so Quasar spawns them alongside the built-in reviewers (or you can explicitly ask in chat: *"Review this PR using `review-security`"*).

### 5. Author Self-Review: Check Comments & Fix with Agent (`manage_fixes.py`)
- When reviewing a **local branch** or a **GitHub PR you own** (auto-detected from your git/GitHub identity or checked-out branch), Quasar enables **Author Self-Review / Fix Mode**:
  - **Check Any Comment in the Sidebar**: Every Quasar finding (`🔴 Blocking`, `🟡 Design`, `🟢 Nit`, `🤔 Uncertain`) **and** every **Existing GitHub PR Comment from teammates** (`👀`) has a native checkbox in the **Review Comments & Cart** tree.
  - **Queue or Fix Directly from Inline Diff Threads**: Click **`$(tools) Queue / Unqueue Comment for Agent Fix`** or **`$(wrench) Fix This Issue with Quasar Now...`** on any inline comment thread in the diff editor.
  - **Batch Dispatch with Optional Instructions**: Click **`$(wrench) Fix Selected Comments with Agent...`** in the sidebar header (or the top action node in **Review Comments & Cart**) to confirm your checklist, add optional custom instructions, and pass a structured fix manifest (`~/.gemini/quasar/fix_request.json`) to the agent.
  - **Working-Tree Edits, Live Diff Sync & Preference Learning**:
    - The agent edits the real working-tree files in your local repo (without auto-committing), syncs the updated files into Quasar's diff snapshot so your open `vscode.diff` view updates immediately, marks the threads **`✅ Fixed`**, and records a positive preference signal on any Quasar findings you chose to fix.

### 6. Publish Reviews Directly to GitHub (as You) via GitHub API Key
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
