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
  --workspace-dir <ACTIVE_WORKSPACE_PATH> \
  --output-dir <SCRATCH_DIR>

# 2. When reviewing a local branch (or current checked-out branch + working-tree edits):
python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/collect_pr_context.py \
  --branch <BRANCH_NAME_OR_HEAD> \
  [--base <BASE_REF_OR_AUTO>] \
  [--repo-dir <REPO_ROOT>] \
  --workspace-dir <ACTIVE_WORKSPACE_PATH> \
  [--committed-only] \
  --output-dir <SCRATCH_DIR>
```

Inspect `reviewer_preferences` inside `pr_context.json` (or view `~/.gemini/quasar/PREFERENCES.md` if it exists) so you know:
- What patterns the reviewer **prefers to flag** (`preferRules`)
- What patterns the reviewer **wants suppressed/skipped** (`avoidRules`)
- Their preferred **comment tone, verbosity, and phrasing examples** (`styleProfile` & `recentEditedDrafts`).

### Owned PR Git Branch Check (`isOwnedByUser` & `needsBranchSwitch`)
When the user provides a GitHub PR link or number, inspect `pr_metadata` in `pr_context.json`:
- If `pr_metadata.isOwnedByUser` is `true` and `pr_metadata.needsBranchSwitch` is `true` (meaning the PR belongs to the user, a matching local git repository was found at `repo_dir`, and it is currently checked out on `pr_metadata.localCurrentBranch` instead of the PR's head branch `pr_metadata.headRefName`):
  - **Ask the user** (using `ask_question`) whether they would like you to switch their local git repository (`repo_dir`) from `<localCurrentBranch>` to the PR branch `<headRefName>` (offering options: `(Recommended) Yes, switch to branch '<headRefName>'` and `No, stay on '<localCurrentBranch>'`).
  - You can launch the Phase 2 subagents first so analysis runs concurrently in the background while waiting for the user's answer, or ask immediately.
  - If the user chooses to switch to `<headRefName>`, run:
    ```bash
    python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/collect_pr_context.py \
      --checkout-pr-branch \
      [--stash-if-dirty] \
      --workspace-dir <ACTIVE_WORKSPACE_PATH>
    ```

---

## Phase 2: Parallel Multi-Lens Subagents & Preference-Aware Filtering

For medium or large PRs, spawn the three specialist review subagents **in parallel** using a single `invoke_subagent` call (`Model: "inherit"`). Pass each subagent the active `reviewer_preferences` summary (or instruct them to read `~/.gemini/quasar/PREFERENCES.md`):

1. **`review-architect`** (`Role: "Architecture & API Reviewer"`):
   - Pass the PR title/description, list of changed `core_source` files, `<SCRATCH_DIR>/pr.diff`, and `reviewer_preferences`.
   - Ask it to build the architectural mental model, identify key symbols ("Character List"), evaluate API boundaries & backwards compatibility, and suggest the logical top-down reading order.
2. **`review-bug-hunter`** (`Role: "Bug & Edge-Case Hunter"`):
   - Pass the changed `core_source` files, `<SCRATCH_DIR>/pr.diff`, and `reviewer_preferences`.
   - Ask it to trace execution paths, nullability, state mutations, resource lifecycle, and language-boundary/generator edge cases.
3. **`review-test-critic`** (`Role: "Test & Verification Critic"`):
   - Pass the changed `core_source`, `test`, `generated_or_fixture`, and `docs_or_config` files, plus `reviewer_preferences`.
   - Ask it to build a coverage matrix, evaluate test falsifiability, verify generated file sync, and check repo hygiene (`CHANGELOG.md`, `pubspec.yaml`).

### Filtering False Positives, Flagging Uncertain Preferences, & Triaging Existing GitHub Comments (`review-advocate`)
Once the lenses report back, verify every candidate finding against the actual codebase callers/callees, `existing_comments`, and `reviewer_preferences` (either via `review-advocate` or direct inspection):
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

Write `<SCRATCH_DIR>/review_synthesis.json` and publish it using `publish_review_session.py`:

```json
{
  "analysisStatus": "ready",
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

### 3. When the Reviewer Asks a Question in a Native Comment Thread
When you receive a prompt triggered from an inline comment thread (`--thread-id`), investigate the code and post your answer directly back into that inline thread so it updates live inside the diff editor:

```bash
python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/answer_comment_question.py \
  --thread-id "<THREAD_ID>" \
  --answer "<CONCISE_MARKDOWN_EXPLANATION>" \
  [--updated-draft "<OPTIONAL_UPDATED_GITHUB_COMMENT>"]
```
*(If their question also expresses a review preference, record it via `manage_preferences.py` as well!)*

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


