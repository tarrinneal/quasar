---
name: quasar
description: >-
  Use this skill whenever the user provides a GitHub PR link/number or asks to review a PR, branch,
  or diff, asks an inline code review question, or gives feedback on Quasar's review comments/preferences.
  Runs the Quasar CliffsNotes Code Review pipeline: (1) Auto-fetches PR/branch context, snapshots,
  and learned reviewer preferences, (2) Parallel Multi-Lens Subagent Analysis (Architecture, Bug Hunter,
  Test Critic, Advocate Filter), (3) Publishes interactive CliffsNotes chapters, tour stops, hover context,
  candidate comments, and uncertain preference-calibration findings to the native Quasar extension,
  and (4) Proactively calibrates uncertain findings with the user and learns continuously from feedback on its feedback.
---

# Quasar Code Review Workflow (`quasar`)

Your primary goal is **deep reviewer comprehension first**, followed by **high-signal, verified review feedback** that **adapts to and learns the reviewer's personal preferences over time**—all rendered natively inside Antigravity without webviews or code clutter.

Whenever the user provides a GitHub PR URL (or PR number), OR asks to review a **local branch**, current working tree, or git diff, **initiate the entire workflow automatically without asking for confirmation**.

---

## Phase 1: Auto-Fetch PR or Local Branch Context, Snapshots & Learned Preferences

Run `collect_pr_context.py` immediately, passing `--workspace-dir <ACTIVE_WORKSPACE_PATH>` (the active workspace path of the current window from `<user_information>`) so the Quasar session is isolated to the current window (`~/.gemini/quasar/workspaces/<workspaceKey>/active_session.json`) and never pops up in other open project windows:

```bash
# 1. When given a GitHub PR URL or number:
python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/collect_pr_context.py \
  --pr <PR_URL_OR_NUMBER> \
  [--effort <low|medium|high>] \
  [--comments-only] \
  --workspace-dir <ACTIVE_WORKSPACE_PATH> \
  --output-dir <SCRATCH_DIR>

# 2. When reviewing a local branch (or current checked-out branch + working-tree edits):
python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/collect_pr_context.py \
  --branch <BRANCH_NAME_OR_HEAD> \
  [--base <BASE_REF_OR_AUTO>] \
  [--repo-dir <REPO_ROOT>] \
  [--effort <low|medium|high>] \
  [--comments-only] \
  --workspace-dir <ACTIVE_WORKSPACE_PATH> \
  [--committed-only] \
  --output-dir <SCRATCH_DIR>
```

Inspect `effort_level` (`"low"`, `"medium"`, or `"high"`), `comments_only` (`true` or `false`), and `reviewer_preferences` inside `pr_context.json` (or view `~/.gemini/quasar/PREFERENCES.md` if it exists) so you know:
- Which **Effort Level** to run (`"low"`, `"medium"`, or `"high"` — if the user explicitly asked for `"low effort"` / `"quick"`, `"medium effort"`, or `"high effort"` / `"deep"` in their prompt, pass `--effort <level>` to `collect_pr_context.py` or honor their requested level).
- Whether **Comments-Only Mode (`comments_only: true` / `--comments-only`)** is active (either via `--comments-only` flag, `comments_only: true` in `pr_context.json`, or because the user asked for comments only / no study guide).
- What patterns the reviewer **prefers to flag** (`preferRules`)
- What patterns the reviewer **wants suppressed/skipped** (`avoidRules`)
- Their preferred **comment tone, verbosity, and phrasing examples** (`styleProfile` & `recentEditedDrafts`).

### Owned PR Git Branch Check (`isOwnedByUser` & `needsBranchSwitch`)
When the user provides a GitHub PR link or number, inspect `pr_metadata` in `pr_context.json`:
- If `pr_metadata.isOwnedByUser` is `true` and `pr_metadata.needsBranchSwitch` is `true` (meaning the PR belongs to the user, a matching local git repository was found at `repo_dir`, and it is currently checked out on `pr_metadata.localCurrentBranch` instead of the PR's head branch `pr_metadata.headRefName`):
  - **Ask the user** (using `ask_question`) whether they would like you to switch their local git repository (`repo_dir`) from `<localCurrentBranch>` to the PR branch `<headRefName>` (offering options: `(Recommended) Yes, switch to branch '<headRefName>'` and `No, stay on '<localCurrentBranch>'`).
  - If the user chooses to switch to `<headRefName>`, run:
    ```bash
    python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/collect_pr_context.py \
      --checkout-pr-branch \
      [--stash-if-dirty] \
      --workspace-dir <ACTIVE_WORKSPACE_PATH>
    ```

---

## Phase 2: Effort-Scaled Analysis (`low`, `medium`, `high`) & Preference-Aware Filtering

> **💬 Comments-Only Mode (`comments_only: true`)**:
> When `comments_only` is `true` (or the user requested a comments-only review):
> - **Do NOT build the CliffsNotes Study Guide** (`cliffsNotes.tldr`, `whatAndWhy`, `architectureDiagram`, `keySymbols`, `designTradeoffs`) and **do NOT generate reading tour `chapters` or `stops`**.
> - Focus 100% on finding verified, high-signal review comments (`findings`) and triaging `existing_comments` (`existingCommentAssessments`) so the user can immediately publish them to GitHub or queue them for Agent fixes.
> - In `medium` effort Comments-Only mode, you only need to spawn `review-bug-hunter` (`Model: "flash"`) or ask subagents strictly for candidate review findings (skipping reading-tour & architecture-diagram synthesis).

Choose the analysis pipeline below that matches `effort_level` (`"low"`, `"medium"`, or `"high"`):

### ⚡ Option A: `low` Effort (Fast / Quick Scan — Single-Pass, 0 Subagents)
- **Do NOT spawn any subagents (`invoke_subagent`)**.
- Read `<SCRATCH_DIR>/pr_context.json` and `<SCRATCH_DIR>/pr.diff` directly in a single fast pass (only view individual files if a diff hunk is ambiguous).
- Build a concise CliffsNotes Study Guide (`tldr`, `architectureDiagram`, logical `chapters` & `stops` — **skip this step if `comments_only` is `true`**), triage any `existing_comments`, and include only **obvious, high-confidence bugs or blockers** (skipping deep cross-file hunting and minor nits).
- Proceed immediately to **Phase 3** (`publish_review_session.py`).

### ⚖️ Option B: `medium` Effort (Balanced — Default — 2 Fast `flash` Subagents in 1 Wave)
- Spawn **2 subagents in parallel** in a single `invoke_subagent` call with **`Model: "flash"`**:
  1. **`review-architect`** (`Model: "flash"`, `Role: "Architecture & API Reviewer"`): Pass the PR title/description, changed `core_source` files, `<SCRATCH_DIR>/pr.diff`, and `reviewer_preferences`. *(If `comments_only` is `true`, instruct `review-architect` to only return API/design `findings` and skip building a reading tour or mental model.)*
  2. **`review-bug-hunter`** (`Model: "flash"`, `Role: "Bug & Edge-Case Hunter"`): Pass the changed `core_source` files, `<SCRATCH_DIR>/pr.diff`, and `reviewer_preferences`.
- **Do NOT spawn a second sequential `review-advocate` wave** in `medium` effort — once the 2 `flash` subagents report back, verify and filter their findings directly in the main agent and proceed immediately to **Phase 3**.

### 🔬 Option C: `high` Effort (Deep / Thorough — Full 4-Lens Pipeline)
- Spawn all **3 specialist review subagents in parallel** using a single `invoke_subagent` call (`Model: "inherit"`). Pass each subagent the active `reviewer_preferences` summary (or instruct them to read `~/.gemini/quasar/PREFERENCES.md`):
  1. **`review-architect`** (`Model: "inherit"`, `Role: "Architecture & API Reviewer"`): Build the architectural mental model, identify key symbols ("Character List"), evaluate API boundaries & backwards compatibility, and suggest the logical top-down reading order *(if `comments_only` is `true`, skip the reading tour & mental model and focus strictly on architectural/API findings)*.
  2. **`review-bug-hunter`** (`Model: "inherit"`, `Role: "Bug & Edge-Case Hunter"`): Trace execution paths, nullability, state mutations, resource lifecycle, and language-boundary/generator edge cases.
  3. **`review-test-critic`** (`Model: "inherit"`, `Role: "Test & Verification Critic"`): Build a coverage matrix, evaluate test falsifiability, verify generated file sync, and check repo hygiene (`CHANGELOG.md`, `pubspec.yaml`).

### Filtering False Positives, Flagging Uncertain Preferences, & Triaging Existing GitHub Comments
Verify every candidate finding against the actual codebase callers/callees, `existing_comments`, and `reviewer_preferences` (in `high` effort you may invoke `review-advocate` or verify directly; in `low`/`medium` effort always filter directly):
- **Drop** any finding that is guaranteed safe by upstream checks/types, pre-existing untouched code, already commented on, or suppressed by `avoidRules` in `reviewer_preferences`.
- **Keep (`"status": "candidate"`)** verified bugs, regressions, and design issues that clearly align with high-signal review standards or `preferRules`.
- **Triage Existing GitHub Comments (`"existingCommentAssessments"`)**: For each inline comment in `existing_comments`, evaluate it against the codebase and classify it so the user immediately sees whether Quasar thinks the suggested fix should be made or if it requires human attention:
  - `"triageStatus": "recommended_fix"` (`"triageBadge": "🛠️ Good Catch — Should Fix"`): The comment points out a valid bug, warning, edge case, or clean improvement (`suggestion` block, naming/readability/doc fix) that should be applied.
  - `"triageStatus": "needs_attention"` (`"triageBadge": "❓ Question — Needs Answer"` or `"👀 Needs Look (Design)"`): The comment asks an open question that needs answering or raises an architectural/design trade-off that requires human decision.
  - `"triageStatus": "addressed_or_refuted"` (`"triageBadge": "⚠️ Disputed / No Fix"` or `"💬 Author Replied"`): The comment was already addressed, replied to by the PR author, or relies on a mistaken premise.
- **Mark as Uncertain (`"status": "uncertain"`)** 1–3 borderline or preference-sensitive findings where the technical observation is valid, but whether to post it depends on the reviewer's taste/convention (e.g., private-helper unit test strictness, API surface trade-offs, defensive guards on internal callers, or borderline nits) and is **not yet covered** by `reviewer_preferences`. Each uncertain finding MUST include:
  - `"status": "uncertain"`
  - `"preferenceCategory"`: e.g. `"test_strictness"`, `"api_design"`, `"nit_threshold"`, `"null_defensiveness"`, `"comment_tone"`, `"repo_hygiene"`
  - `"uncertaintyQuestion"`: A crisp 1-sentence question asking the reviewer's general preference on this kind of feedback.
  - `"preferenceOptions"`: 2–3 actionable choices (e.g. `["Flag this & similar cases in future reviews", "Skip this pattern in future reviews", "Keep this comment once, don't save a rule"]`).

---

## Phase 3: Publish the CliffsNotes Session & Calibrate Uncertain Preferences

Write `<SCRATCH_DIR>/review_synthesis.json` and publish it using `publish_review_session.py` (when `comments_only` is `true`, set `"commentsOnly": true` and omit `"cliffsNotes"` and `"chapters"`):

```json
{
  "analysisStatus": "ready",
  "commentsOnly": false,
  "cliffsNotes": {
    "tldr": "One-sentence summary of what the PR does and why.",
    "whatAndWhy": "Concise paragraph explaining the problem solved and the core mechanism.",
    "architectureDiagram": "flowchart LR\n  A[\"Caller (Entry Point)\"] --> B[\"Generator - Emits Code\"]",
    "keySymbols": [
      { "name": "KotlinGenerator._writeHeader", "role": "Emits the package declaration and file-level annotations." }
    ],
    "designTradeoffs": [
      "Trade-off 1 that deserves human reviewer judgment."
    ]
  },
  "chapters": [
    {
      "id": "ch-1",
      "title": "Chapter 1: Core Interfaces & Models",
      "summary": "Defines the new options and AST representation.",
      "cliffsNotesBullets": [
        "Adds nullable `package` field to `KotlinOptions`.",
        "Preserves backwards compatibility when omitted."
      ],
      "stops": [
        {
          "stopNumber": "1.1",
          "title": "kotlin_generator.dart:140-185",
          "path": "packages/pigeon/lib/src/kotlin/kotlin_generator.dart",
          "startLine": 140,
          "endLine": 185,
          "cliffsNotes": "Emits the Kotlin package header and file-level suppression annotations.",
          "whyItChanged": "Needed to support custom package namespaces in generated output.",
          "callersAndInvariants": "Called once per generated file from `KotlinGenerator.generate`."
        }
      ]
    }
  ],
  "findings": [
    {
      "id": "finding-1",
      "severity": "BLOCKING",
      "status": "candidate",
      "path": "packages/pigeon/lib/src/kotlin/kotlin_generator.dart",
      "startLine": 142,
      "endLine": 148,
      "title": "Null-safety crash when kotlinOptions.package is omitted",
      "whyItMatters": "`options.package` is nullable in `KotlinOptions`, and callers do not default it.",
      "draftComment": "If `KotlinOptions.package` isn't specified by the caller, `options.package!` here will throw at generation time. Should we guard this with `if (options.package != null)`?"
    },
    {
      "id": "finding-2",
      "severity": "NIT",
      "status": "uncertain",
      "preferenceCategory": "test_strictness",
      "uncertaintyQuestion": "Do you want Quasar to flag missing unit tests for private helper methods when the public generator output is already tested?",
      "preferenceOptions": [
        "Flag this & similar cases in future reviews",
        "Skip this pattern in future reviews",
        "Keep this comment once, don't save a rule"
      ],
      "path": "packages/pigeon/lib/src/kotlin/kotlin_generator.dart",
      "startLine": 170,
      "endLine": 185,
      "title": "No direct unit test for private _sanitizePackage helper",
      "whyItMatters": "`_sanitizePackage` is only exercised indirectly through `generate()`.",
      "draftComment": "Consider adding a direct unit test for edge cases in `_sanitizePackage` (e.g. reserved Kotlin keywords)."
    }
  ],
  "coverageMatrix": [
    {
      "behavior": "Custom Kotlin package header emission",
      "status": "✅ Covered",
      "notes": "Verified in kotlin_generator_test.dart:L184"
    }
  ],
  "repoChecks": [
    { "name": "CHANGELOG.md & pubspec.yaml", "passed": true, "detail": "Version bumped and changelog updated" }
  ],
  "dismissedChecks": [
    "Checked whether empty string `package: ''` can reach `_writeHeader` — already rejected by parser validation."
  ]
}
```

> **Progressive Disclosure Study Guide Rules (Keep It Simple & Scannable!)**:
> - **`cliffsNotes.tldr`**, each chapter's **`summary`**, and each tour stop's **`cliffsNotes`** are rendered up front in the default view: keep each one to **1 short, plain-English sentence (under ~140 characters)** without long parenthetical type lists.
> - Put deeper mechanics, callers, edge cases, and symbol breakdowns into **`cliffsNotes.whatAndWhy`**, **`cliffsNotesBullets`**, **`whyItChanged`**, and **`callersAndInvariants`** — Quasar automatically places those inside expandable `▸ Context & code snippet` drawers alongside a live code preview.
>
> **Draft Comment Style & Verbosity (`draftComment`)**:
> - **Lean less verbose without losing important information**: Keep `draftComment` focused on the core issue and essential context (typically 1–2 concise sentences). Put extra background or step-by-step trace details into `whyItMatters` (for the reviewer) so the GitHub comment itself stays crisp.
> - **Suggestions are optional, not mandatory**: Not every comment needs a code suggestion or prescribed fix. Often, simply pointing out the issue or asking a brief question is enough—include a suggestion only when the fix is non-obvious or genuinely helpful.
>
> **Mermaid Flowchart Rules (`architectureDiagram`)**:
> - Always quote every node and subgraph label (`ID["Label Text"]`, `subgraph sg_id ["Title"]`).
> - Keep node labels short (`Title - 3-5 word subtitle` max) and on a **single line** — never put raw newlines, `\n`, `<br/>`, or `<tag>` angle brackets inside labels.
> - Use standard `-->` or `-.->` arrows (one edge per line; avoid `<-->` or chained `A --> B --> C`).

Publish the session to the current window's isolated workspace instance:

```bash
python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/publish_review_session.py \
  --input <SCRATCH_DIR>/review_synthesis.json \
  --workspace-dir <ACTIVE_WORKSPACE_PATH> \
  --artifact-out <ARTIFACT_DIR>/code_review_briefing.md
```

### Interactive Preference Calibration (`ask_question`)
If your review produced any `"status": "uncertain"` findings (or if you have 1–2 high-leverage questions about the reviewer's preferences for this repo/style), **call the `ask_question` tool** right after publishing the session!
- Present each uncertain finding's `uncertaintyQuestion` (linking to the file/line) with options like:
  1. `"Yes — stage this comment and flag similar cases in future reviews"`
  2. `"No — dismiss this and skip similar patterns in future reviews"`
  3. `"Keep this comment once for this PR, but don't save a global rule"`
- As soon as the user responds to `ask_question`, persist their preference and update the live session by running `manage_preferences.py`:

```bash
# If the user chose to keep & prefer this pattern:
python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/manage_preferences.py \
  --record-feedback \
  --thread-id "<FINDING_ID>" \
  --action calibrate_keep \
  --category "<PREFERENCE_CATEGORY>" \
  --learned-rule "<CLEAR_GENERALIZED_PREFERENCE_RULE>"

# If the user chose to dismiss & avoid this pattern in the future:
python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/manage_preferences.py \
  --record-feedback \
  --thread-id "<FINDING_ID>" \
  --action calibrate_skip \
  --category "<PREFERENCE_CATEGORY>" \
  --learned-rule "<CLEAR_GENERALIZED_AVOID_RULE>"
```

---

## Phase 4: Answering Inline Questions, Learning from Feedback on Feedback, & Exporting

### 1. When the Reviewer Gives Feedback on Quasar's Feedback (Meta-Feedback / Preference Learning)
The reviewer can click **👍 Helpful**, **👎 Not Useful**, **🤔 Calibrate Preference**, or **🧠 Teach Quasar / Give Feedback on This Feedback** on any inline comment thread in the editor, or tell you in chat how they want Quasar to adjust its feedback.
When you receive a meta-feedback prompt:
1. Distill the user's critique into a clear, reusable preference rule (or tone/verbosity update).
2. Run `manage_preferences.py` to persist the learned rule into `~/.gemini/quasar/preferences.json` and `~/.gemini/quasar/PREFERENCES.md`, and update the inline thread in `active_session.json` (including `--updated-draft` if the user asked to rephrase or adjust the comment):

```bash
python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/manage_preferences.py \
  --record-feedback \
  --thread-id "<THREAD_ID>" \
  --action meta_feedback \
  --category "<CATEGORY>" \
  --sentiment "<prefer|avoid|custom>" \
  --reason "<USER_RAW_FEEDBACK>" \
  --learned-rule "<DISTILLED_REUSABLE_PREFERENCE_RULE>" \
  [--updated-draft "<REFINED_GITHUB_DRAFT_COMMENT>"] \
  [--set-tone "<NEW_TONE_IF_APPLICABLE>"]
```

### 2. When the Reviewer Provides a Premade Review Skill as a Starting Point
The reviewer can provide an existing `SKILL.md`, review rubric, or markdown guide via **`Quasar: Import Premade Review Skill as Starting Point...`** or in chat.
- **Never overwrite Quasar's core defaults (`styleProfile` / CliffsNotes pipeline) or existing user-learned preferences (`origin: "learned"`)**.
- Instead, import the skill as a **non-destructive starting point** (`origin: "seed_skill"`, saved under `~/.gemini/quasar/seed_skills/<slug>.md`):
```bash
python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/manage_preferences.py \
  --import-skill "/path/to/SKILL.md" \
  [--skill-name "Optional Custom Name"]
```
- **3-Layer Precedence Hierarchy**:
  1. **Quasar Core Defaults**: Built-in CliffsNotes comprehension tour, multi-lens verification, and collaborative tone.
  2. **Imported Premade Seed Skills (`origin: "seed_skill"`)**: Baseline preferences and domain checklists extracted from the user's premade skill.
  3. **Evolved Learned Preferences (`origin: "learned"`, Highest Priority)**: Rules learned from the user's explicit calibrations, 👍/👎 ratings, and meta-feedback automatically supersede any conflicting seed skill rules so Quasar grows with the user over time.

### 3. When the Reviewer Comments on a Quasar Comment (to Change/Revise It) or Asks an Inline Question
When the reviewer comments on a Quasar comment to have the agent change/revise it (or asks a question inside a native comment thread with `--thread-id`), update the comment and reply directly in place so the inline diff thread, sidebar, and study guide update live:

```bash
python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/answer_comment_question.py \
  --thread-id "<THREAD_ID>" \
  --workspace-dir "<ACTIVE_WORKSPACE_PATH>" \
  [--updated-draft "<REVISED_GITHUB_COMMENT>"] \
  [--answer "<CONCISE_EXPLANATION_OR_CONFIRMATION>"] \
  [--updated-title "<OPTIONAL_NEW_TITLE>"] \
  [--updated-why "<OPTIONAL_NEW_RATIONALE>"] \
  [--updated-severity "<BLOCKING|DESIGN|NIT>"] \
  [--start-line <N> --end-line <M>] \
  [--stage | --unstage | --dismiss] \
  [--learned-rule "<OPTIONAL_REUSABLE_PREFERENCE_RULE>" --sentiment <prefer|avoid|custom>]
```
*(When `--updated-draft` is passed, `answer_comment_question.py` updates both `draftComment` and the primary finding card at the top of the thread, and automatically records the before/after phrasing example in `preferences.json`.)*

### 4. Publishing Reviews to GitHub as the User (`post_pending_review.py`)
The reviewer can provide their GitHub Personal Access Token (API Key) via **`🔑 Configure GitHub API Key / Personal Access Token...`** in the Quasar UI (or by giving it to you in chat) and publish reviews directly to the PR under their own GitHub account (`@login`) without needing `gh` CLI installed:
- **Save & verify a GitHub Personal Access Token (`~/.gemini/quasar/github_auth.json`, mode `0600`)**:
  ```bash
  python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/post_pending_review.py \
    --set-token "<GITHUB_PAT>"
  ```
- **Publish or export the review to the GitHub PR** (`--event` supports `COMMENT` to publish comments immediately as the user, `APPROVE` to approve the PR, `REQUEST_CHANGES` to request changes, or `PENDING` to create a draft pending review):
  ```bash
  python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/post_pending_review.py \
    --from-session \
    --event "<COMMENT|APPROVE|REQUEST_CHANGES|PENDING>" \
    [--summary-body "<OVERALL_REVIEW_SUMMARY>"]
  ```

### 5. When the PR/Branch Owner Selects Comments to Fix (`manage_fixes.py`)
When reviewing a local branch or user-owned PR, the user can check boxes next to any Quasar findings or existing GitHub PR comments in the **Review Comments & Cart** sidebar (or click **`$(wrench) Fix Selected Comments with Agent...`** / **`$(wrench) Fix This Issue with Quasar Now...`** on an inline thread).
When you receive a fix request prompt (referencing `~/.gemini/quasar/fix_request.json`):
1. Read `~/.gemini/quasar/fix_request.json` if needed and apply the requested fixes directly to the **real working-tree files** under `repoDir` (never edit only the snapshot copies, and do **not** create a git commit unless explicitly asked).
2. Run relevant static analysis or unit tests in `repoDir` to verify the fix.
3. Run `manage_fixes.py --complete-fix` so Quasar automatically copies your updated working-tree files into `headSnapshotRoot` (updating the native side-by-side `vscode.diff` editor) and marks the fixed threads as `✅ Fixed` in `active_session.json`:

```bash
python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/manage_fixes.py \
  --complete-fix \
  --thread-ids "<COMMA_SEPARATED_THREAD_IDS>" \
  --summary "<CONCISE_SUMMARY_OF_WHAT_YOU_FIXED>"
```


