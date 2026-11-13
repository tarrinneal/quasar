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

Inspect `effort_level` (`"low"`, `"medium"`, or `"high"`), `comments_only` (`true` or `false`), `available_review_agents`, and `reviewer_preferences` inside `pr_context.json` (or view `~/.gemini/quasar/PREFERENCES.md` if it exists) so you know:
- Which **Effort Level** to run (`"low"`, `"medium"`, or `"high"` — if the user explicitly asked for `"low effort"` / `"quick"`, `"medium effort"`, or `"high effort"` / `"deep"` in their prompt, pass `--effort <level>` to `collect_pr_context.py` or honor their requested level).
- Whether **Comments-Only Mode (`comments_only: true` / `--comments-only`)** is active (either via `--comments-only` flag, `comments_only: true` in `pr_context.json`, or because the user asked for comments only / no study guide).
- Which **Review Lenses run at this effort level (`review_lenses`)**. `review_lenses` is the user-configured list of `review-*` subagents enabled for the current `effort_level` (resolved from each lens's `quasarEfforts` frontmatter plus the user's `lensEfforts` overrides in `~/.gemini/quasar/preferences.json`; see `effort_lenses` for all levels and `available_review_agents` for every installed lens/helper). Each entry includes an `appliesWhen` gate describing which diffs it is relevant to.
- What patterns the reviewer **prefers to flag** (`preferRules`)
- What patterns the reviewer **wants suppressed/skipped** (`avoidRules`)
- Their preferred **comment tone, verbosity, and phrasing examples** (`styleProfile` & `recentEditedDrafts`).

### Owned PR Git Branch Check (`isOwnedByUser` & `needsBranchSwitch`)
When the user provides a GitHub PR link or number, inspect `pr_metadata` in `pr_context.json`:
- If `pr_metadata.isOwnedByUser` is `true` and `pr_metadata.needsBranchSwitch` is `true` (meaning the PR belongs to the user, a matching local git repository was found at `repo_dir`, and it is currently checked out on `pr_metadata.localCurrentBranch` instead of the PR's head branch `pr_metadata.headRefName`):
  - **Do NOT interrupt the review with `ask_question`**. The native Quasar extension automatically prompts the user with a non-blocking notification toast to switch branches, and you can mention in your final post-review summary that they can switch `<repo_dir>` from `<localCurrentBranch>` to `<headRefName>` (or ask you to run `collect_pr_context.py --checkout-pr-branch [--stash-if-dirty] --workspace-dir <ACTIVE_WORKSPACE_PATH>`).

---

## Phase 2: Effort-Scaled Analysis (`low`, `medium`, `high`) & Preference-Aware Filtering

> **💬 Comments-Only Mode (`comments_only: true`)**:
> When `comments_only` is `true` (or the user requested a comments-only review):
> - **Do NOT build the CliffsNotes Study Guide** (`cliffsNotes.tldr`, `whatAndWhy`, `architectureDiagram`, `keySymbols`, `designTradeoffs`) and **do NOT generate reading tour `chapters` or `stops`**.
> - Focus 100% on finding verified, high-signal review comments (`findings`) and triaging `existing_comments` (`existingCommentAssessments`) so the user can immediately publish them to GitHub or queue them for Agent fixes.
> - In `medium` effort Comments-Only mode, spawn only the lenses in `review_lenses` (with `Model: "flash"`), asking them strictly for candidate review findings (skipping reading-tour & architecture-diagram synthesis).

### 🧩 Choosing Which Lenses to Spawn (`review_lenses`)
Users configure which lenses run at each effort level (Quasar sidebar → **Configure Review Lenses per Effort**, or `python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/review_lenses.py --set-effort-lenses <level> --lenses a,b,c`). **Always honor `review_lenses` from `pr_context.json`** instead of a hard-coded list:
- Spawn **every lens in `review_lenses` whose `appliesWhen` gate matches this diff** (e.g. skip `review-accessibility` for a diff with no UI code, skip `review-dependencies` when no manifest/lockfile changed). Lenses with `appliesWhen: "Always applicable."` always run. When unsure whether a lens applies, spawn it — each lens self-gates and returns quickly when irrelevant.
- **Never spawn lenses that are not in `review_lenses`** unless the user explicitly asks for them by name in their prompt (e.g. "also run the security lens"); in that case add them for this review only.
- `review-advocate` is a false-positive filter helper (`role: "filter"`), not a lens — it is never listed in `review_lenses`.
- If `review_lenses` is empty for the current effort, do not spawn any subagents (single-pass review).
- Pass each lens the PR title/description, changed `core_source` files, `<SCRATCH_DIR>/pr.diff`, global `reviewer_preferences`, **and that lens's own `learnedDirectives`** (`review_lenses[i].learnedDirectives`, or its `### 🤖 \`<lens-name>\`` section in `~/.gemini/quasar/PREFERENCES.md`), and use `Role` = the lens's description title (e.g. `"Security Auditor"`).
- **Preserve Subagent Attribution (`"sourceAgents"`)**: Record which subagent(s) surfaced every finding in `"sourceAgents"` (e.g. `["review-bug-hunter"]`, or `["review-bug-hunter", "review-security"]` when merging duplicates across lenses, or `["quasar-single-pass"]` when running without subagents) so inline comments show which lens suggested each comment and let the reviewer teach that specific subagent.

Choose the analysis pipeline below that matches `effort_level` (`"low"`, `"medium"`, or `"high"`):

### ⚡ Option A: `low` Effort (Fast / Quick Scan — Single-Pass)
- By default `review_lenses` is empty at `low` effort: **do NOT spawn any subagents**. (If the user enabled lenses for `low`, spawn only those, with `Model: "flash"`, in one parallel `invoke_subagent` call.)
- Read `<SCRATCH_DIR>/pr_context.json` and `<SCRATCH_DIR>/pr.diff` directly in a single fast pass (only view individual files if a diff hunk is ambiguous).
- Build a concise CliffsNotes Study Guide (`tldr`, `architectureDiagram`, logical `chapters` & `stops` — **skip this step if `comments_only` is `true`**), triage any `existing_comments`, and include only **obvious, high-confidence bugs or blockers** (skipping deep cross-file hunting and minor nits).
- Proceed immediately to **Phase 3** (`publish_review_session.py`).

### ⚖️ Option B: `medium` Effort (Balanced — Fast `flash` Subagents in 1 Wave)
- Spawn the applicable lenses from `review_lenses` **in parallel** in a single `invoke_subagent` call with **`Model: "flash"`** (defaults: `review-architect`, `review-bug-hunter`, `review-security`, plus any custom lenses enabled for `medium`).
  - If `review-architect` is spawned, pass it the PR title/description too. *(If `comments_only` is `true`, instruct `review-architect` to only return API/design `findings` and skip building a reading tour or mental model.)*
- **Do NOT spawn a second sequential `review-advocate` wave** in `medium` effort — once the `flash` subagents report back, verify and filter their findings directly in the main agent and proceed immediately to **Phase 3**.

### 🔬 Option C: `high` Effort (Deep / Thorough — Default — Full Multi-Lens Pipeline)
- Spawn the applicable lenses from `review_lenses` **in parallel** using a single `invoke_subagent` call (`Model: "inherit"`). Pass each subagent the active `reviewer_preferences` summary (or instruct them to read `~/.gemini/quasar/PREFERENCES.md`). Defaults include:
  1. **`review-architect`**: Build the architectural mental model, identify key symbols ("Character List"), evaluate API boundaries & backwards compatibility, and suggest the logical top-down reading order *(if `comments_only` is `true`, skip the reading tour & mental model and focus strictly on architectural/API findings)*.
  2. **`review-bug-hunter`**: Trace execution paths, nullability, state mutations, resource lifecycle, and language-boundary/generator edge cases.
  3. **`review-test-critic`**: Build a coverage matrix, evaluate test falsifiability, verify generated file sync, and check repo hygiene (changelog, version metadata).
  4. **Gated specialist lenses** (spawned only when their `appliesWhen` matches): `review-security`, `review-performance`, `review-concurrency`, `review-compat`, `review-error-handling`, `review-docs`, `review-readability`, `review-dependencies`, `review-accessibility`.
  5. **Custom lenses** (`isCustom: true`) enabled for `high`.
- When several lenses report the same issue, merge them into a single finding (keep the most specific location and the clearest draft comment, and list **all** contributing lens names in `"sourceAgents"`, e.g. `["review-bug-hunter", "review-security"]`).

### Filtering False Positives, Flagging Uncertain Preferences, & Triaging Existing GitHub Comments
Verify every candidate finding against the actual codebase callers/callees, `existing_comments`, and `reviewer_preferences` (in `high` effort you may invoke `review-advocate` or verify directly; in `low`/`medium` effort always filter directly):
- **Drop** any finding that is guaranteed safe by upstream checks/types, pre-existing untouched code, already commented on, or suppressed by `avoidRules` in `reviewer_preferences`.
- **Keep (`"status": "candidate"`)** verified bugs, regressions, and design issues that clearly align with high-signal review standards or `preferRules`.
- **Triage Existing GitHub Comments Against Current Code (`"existingCommentAssessments"`)**: For **every** inline comment in `existing_comments`, inspect the actual code in `head_snapshot_root` (and compare against `diff_hunk` / `code_check` / `base_snapshot_root` — do **not** rely solely on GitHub thread replies or resolved checkboxes!) so Quasar appends a structured follow-up comment right below each GitHub comment stating **what is done**, **what isn't done**, and **what should be done**:
  - `"triageStatus": "addressed_or_refuted"` (`"triageCategory": "addressed"` or `"refuted"`, `"triageBadge": "✅ Addressed in Code"` or `"⚠️ Disputed / No Fix"`): Current HEAD code already resolves the comment cleanly (or the premise does not apply).
  - `"triageStatus": "partially_addressed"` (`"triageBadge": "🟡 Partially Addressed — Edge Case Left"` or `"🟡 Could Be Resolved Better"`): Code was changed toward the comment, **but** a secondary question, edge case, or cleaner resolution still remains. Always acknowledge what the author already changed in `"whatIsDone"` so it never looks like Quasar missed their fix!
  - `"triageStatus": "recommended_fix"` (`"triageBadge": "🛠️ Not Addressed — Should Fix"`): The code at HEAD has **not** yet addressed a valid bug, warning, edge case, or concrete improvement.
  - `"triageStatus": "needs_attention"` (`"triageBadge": "❓ Question — Needs Answer"` or `"👀 Needs Look (Design)"`): The comment asks an open question or design trade-off that still needs an answer or human decision.
  - Every entry in `"existingCommentAssessments"` MUST include:
    - `"id"`: The existing comment ID (e.g. `"gh-inline-123456"`).
    - `"triageStatus"`, `"triageCategory"` (optional), and `"triageBadge"`.
    - `"whatIsDone"`: Concrete 1-sentence summary of what has already been changed in HEAD or replied in thread (cite `file:line`, or state `"No code changes at this location yet"`).
    - `"whatIsNotDone"`: Concrete 1-sentence summary of what remains unaddressed or what edge case / improvement is still open (or `"None — fully resolved in current code"`).
    - `"whatShouldBeDone"`: Specific recommended action (e.g. exact code refinement, reply to leave, or `"No further code changes needed"`).
- **Present Selectable Solution Options on User-Owned Reviews (`"solutionOptions"` when `pr_metadata.isOwnedByUser` is `true`)**:
  - Whenever `pr_metadata.isOwnedByUser` is `true` (a local branch review or a GitHub PR authored by the user), check each candidate/uncertain finding (`findings`) and each triaged existing GitHub comment (`existingCommentAssessments`) to see whether there are **multiple genuinely distinct, valid ways to solve it** (2–4 reasonable implementation approaches, API shapes, or error-handling strategies — do **not** invent artificial options for single-way trivial fixes like typos or obvious 1-line suggestions).
  - When 2+ distinct valid solutions exist on a user-owned review, include `"solutionOptions"` on that finding or `existingCommentAssessment` so the user can select their preferred approach directly in the Quasar UI instead of hoping the agent guesses how they want it solved:
    - Each option should have `"id"` (`"opt-1"`, `"opt-2"`, ...), `"label"` (concise action summary), `"description"` (1-sentence trade-off/impact), and `"recommended": true` on the recommended option when one is preferable.
- **Flag as Uncertain (`"status": "uncertain"`) for Post-Review Inspection in the UI** 1–3 borderline, questionable, or preference-sensitive findings where the technical observation is valid, but whether to post it depends on the reviewer's taste/convention or closer code inspection (e.g., private-helper unit test strictness, API surface trade-offs, defensive guards on internal callers, or borderline nits) and is **not yet covered** by `reviewer_preferences`. **Never interrupt the review with `ask_question` in chat** — instead, flag these findings in `review_synthesis.json` so the user can view the code and comments together in the Quasar UI after the review completes. Each uncertain finding MUST include:
  - `"status": "uncertain"`
  - `"preferenceCategory"`: e.g. `"test_strictness"`, `"api_design"`, `"nit_threshold"`, `"null_defensiveness"`, `"comment_tone"`, `"repo_hygiene"`
  - `"uncertaintyQuestion"`: A crisp 1-sentence question explaining what needs the reviewer's judgment or asking their general preference on this kind of feedback.
  - `"preferenceOptions"`: 2–3 actionable choices (e.g. `["Flag this & similar cases in future reviews", "Skip this pattern in future reviews", "Keep this comment once, don't save a rule"]`).

---

## Phase 3: Publish the CliffsNotes Session & Highlight Flagged Comments for UI Review

Write `<SCRATCH_DIR>/review_synthesis.json` and publish it using `publish_review_session.py` (when `comments_only` is `true`, set `"commentsOnly": true` and omit `"cliffsNotes"` and `"chapters"`):

```json
{
  "analysisStatus": "ready",
  "commentsOnly": false,
  "cliffsNotes": {
    "tldr": "One-sentence summary of what the PR does and why.",
    "whatAndWhy": "Concise paragraph explaining the problem solved and the core mechanism.",
    "architectureDiagram": "flowchart LR\n  A[\"AuthMiddleware - Entry Point\"] --> B[\"TokenCache - Lookup & Refresh\"]",
    "keySymbols": [
      { "name": "TokenCache.getOrRefresh", "role": "Returns a cached access token or refreshes it before expiry." }
    ],
    "designTradeoffs": [
      "Trade-off 1 that deserves human reviewer judgment."
    ]
  },
  "chapters": [
    {
      "id": "ch-1",
      "title": "Chapter 1: Core Interfaces & Models",
      "summary": "Defines the cache configuration options and token entry schema.",
      "cliffsNotesBullets": [
        "Adds optional `namespace` field to `CacheOptions`.",
        "Preserves backwards compatibility when omitted."
      ],
      "stops": [
        {
          "stopNumber": "1.1",
          "title": "token_cache.ts:140-185",
          "path": "src/auth/token_cache.ts",
          "startLine": 140,
          "endLine": 185,
          "cliffsNotes": "Builds the cache key prefix and TTL metadata for stored tokens.",
          "whyItChanged": "Needed to isolate cached tokens across multi-tenant namespaces.",
          "callersAndInvariants": "Called once per lookup from `TokenCache.getOrRefresh`."
        }
      ]
    }
  ],
  "topLevelComment": "Optional overall top-level PR review comment to leave on the review (automatically combined with any `isTopLevel: true` untouched-file comments into `TOP_LEVEL_PR_COMMENTS.md` and the GitHub review summary `body`).",
  "findings": [
    {
      "id": "finding-1",
      "severity": "BLOCKING",
      "status": "candidate",
      "sourceAgents": ["review-bug-hunter"],
      "path": "src/auth/token_cache.ts",
      "startLine": 142,
      "endLine": 148,
      "title": "Null dereference when CacheOptions.namespace is omitted",
      "whyItMatters": "`options.namespace` is optional in `CacheOptions`, and existing callers do not pass it.",
      "draftComment": "If `options.namespace` isn't specified by the caller, `options.namespace.trim()` here will throw at runtime. Should we guard this with `if (options.namespace)`?",
      "solutionOptions": [
        {
          "id": "opt-1",
          "label": "Omit the namespace prefix when `options.namespace` is undefined",
          "description": "Preserves backwards compatibility for existing single-tenant callers.",
          "recommended": true
        },
        {
          "id": "opt-2",
          "label": "Default `CacheOptions.namespace` to `\"default\"` on initialization",
          "description": "Guarantees every cache entry uses an explicit two-part key format."
        }
      ]
    },
    {
      "id": "finding-2",
      "severity": "NIT",
      "status": "uncertain",
      "sourceAgents": ["review-test-critic"],
      "preferenceCategory": "test_strictness",
      "uncertaintyQuestion": "Do you want Quasar to flag missing direct unit tests for private helper methods when the public API is already tested?",
      "preferenceOptions": [
        "Flag this & similar cases in future reviews",
        "Skip this pattern in future reviews",
        "Keep this comment once, don't save a rule"
      ],
      "path": "src/auth/token_cache.ts",
      "startLine": 170,
      "endLine": 185,
      "title": "No direct unit test for private sanitizeNamespace helper",
      "whyItMatters": "`sanitizeNamespace` is only exercised indirectly through `getOrRefresh()`.",
      "draftComment": "Consider adding a direct unit test for edge cases in `sanitizeNamespace` (e.g. colons or whitespace)."
    }
  ],
  "existingCommentAssessments": [
    {
      "id": "gh-inline-123456",
      "triageStatus": "partially_addressed",
      "triageBadge": "🟡 Partially Addressed — Edge Case Left",
      "whatIsDone": "Added `if (options.namespace != null)` guard in `token_cache.ts:142`.",
      "whatIsNotDone": "An empty string `namespace: \"\"` still produces a leading colon `:user-123` key.",
      "whatShouldBeDone": "Check `options.namespace && options.namespace.trim().length > 0` instead."
    }
  ],
  "coverageMatrix": [
    {
      "behavior": "Custom cache namespace key isolation",
      "status": "✅ Covered",
      "notes": "Verified in token_cache.test.ts:L184"
    }
  ],
  "repoChecks": [
    { "name": "CHANGELOG.md & package.json", "passed": true, "detail": "Version bumped and changelog updated" }
  ],
  "dismissedChecks": [
    "Checked whether negative `ttlSeconds` can reach `buildEntry` — already rejected by constructor validation."
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
> - **Files not included in the PR diff → Top-Level PR Comment (`isTopLevel: true`)**: When a finding is about a file that was **not modified in the PR diff** (for example, a missing unit test in an untouched test file), do **not** anchor it inline on a random changed file in the PR. Instead, set `"isTopLevel": true`, `"path": ""`, and `"targetFile": "<path/to/untouched_file>"` (or pass the untouched file path in `"path"`, which `publish_review_session.py` automatically promotes to a top-level PR review comment when the file is not in `headSnapshotRoot`). When published to GitHub via `post_pending_review.py`, top-level comments are automatically included in the top-level PR review summary `body`.
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

> **Antigravity Web & Browser UI Link**:
> In your final response after publishing a review, always include the interactive Quasar Web UI pill so users on **Antigravity Web** (or without the VS Code extension) can open the full interactive Study Guide, SVG Flowchart, and Side-by-Side Diff Viewer right inside Antigravity or their browser:
> - `[📖 Open Quasar Review](sidecar://quasar/quasar-web/)` (opens the Quasar Web UI sidecar pane)
> - Direct browser URL: `http://127.0.0.1:49341` (or the exported `quasar_review.html` artifact)

### Flagging Uncertain Comments for Post-Review Inspection (Do NOT Call `ask_question` During Review)
- **Never call `ask_question` while conducting or finishing a review.** Always let the review complete and publish to the Quasar UI first so the user can view the code diff and comments in the UI while considering any flagged items (and selecting among `solutionOptions` on user-owned reviews).
- Any findings with `"status": "uncertain"` are automatically flagged in the Quasar UI under **🤔 Flagged to Review / Calibrate**, inside the Study Guide, and inline on the diff lines with **🤔 Calibrate**, **Stage**, and **🗑️ Dismiss** buttons.
- In your final response after `publish_review_session.py` completes, briefly list any flagged `"status": "uncertain"` comments (with clickable file/line links and their `uncertaintyQuestion`) and—when `isOwnedByUser` is `true`—mention any comments that include selectable `solutionOptions` so the user knows they can pick how they want them solved in the Quasar UI (or reply in chat).
- Whenever the user later tells you their decision on a flagged/uncertain comment in chat, persist their preference and update the live session by running `manage_preferences.py`:

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

### 1. When the Reviewer Gives Feedback on Quasar's Feedback or Teaches a Specific Subagent
The reviewer can click **👍 Helpful**, **👎 Not Useful**, **🤔 Calibrate Preference**, **🎓 Teach / Tune Review Subagent**, or **🧠 Teach Quasar / Give Feedback on This Feedback** on any inline comment thread in the editor, or tell you in chat how they want Quasar (or a specific `review-*` subagent) to adjust its feedback.
When you receive a meta-feedback or subagent-teaching prompt:
1. Distill the user's critique into a clear, reusable preference rule (or tone/verbosity update).
2. Run `manage_preferences.py` to persist the learned rule into `~/.gemini/quasar/preferences.json` and `~/.gemini/quasar/PREFERENCES.md` (passing `--subagent <lens-name>` when the feedback targets a specific `review-*` subagent so only that lens's learned directives are altered, or omitting `--subagent` for a global preference), and update the inline thread in `active_session.json` (including `--updated-draft` if the user asked to rephrase or adjust the comment):

```bash
python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/manage_preferences.py \
  --record-feedback \
  --thread-id "<THREAD_ID>" \
  --action meta_feedback \
  [--subagent "<review-lens-name>"] \
  --category "<CATEGORY>" \
  --sentiment "<prefer|avoid|custom>" \
  --reason "<USER_RAW_FEEDBACK>" \
  --learned-rule "<DISTILLED_REUSABLE_PREFERENCE_RULE>" \
  [--updated-draft "<REFINED_GITHUB_DRAFT_COMMENT>"] \
  [--set-tone "<NEW_TONE_IF_APPLICABLE>"]
```
3. If the prompt also asks to refine a subagent's `AGENT.md` checklist or applicability gate (`quasarAppliesWhen`), update that subagent's `AGENT.md` file cleanly while preserving valid YAML frontmatter (`name`, `subagent: true`, `commandExecutionPolicy: off`).

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
  [--solution-options '<OPTIONAL_JSON_ARRAY_OF_SOLUTION_OPTIONS>'] \
  [--select-solution "<OPTIONAL_CHOSEN_SOLUTION_ID_OR_TEXT>"] \
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

### 5. When the PR/Branch Owner Selects Comments to Fix (`manage_fixes.py` or Chat Fix Requests)
When reviewing a local branch or user-owned PR (`isOwnedByUser: true`), the user can pick among `solutionOptions` on any multi-solution comment and check boxes next to any Quasar findings or existing GitHub PR comments in the **Review Comments & Cart** sidebar (or click **`$(wrench) Fix Selected Comments with Agent...`** / **`$(wrench) Fix This Issue with Quasar Now...`** on an inline thread), or ask you directly in chat to address review comments.
When you receive a fix request on a user-owned review:
1. **Honor Selected Solutions & Present Options When Multiple Valid Fixes Exist**:
   - Inspect `fix_request.json` (or `active_session.json`) and the relevant working-tree code.
   - If the user already chose a `selectedSolution` (or gave explicit instructions choosing the approach), implement that **exact** chosen solution.
   - If a comment does **not** have a `selectedSolution` chosen yet AND there are **multiple meaningfully different ways to solve it** (either listed in `solutionOptions` or discovered when inspecting the code—e.g., returning a fallback vs. throwing, changing a signature vs. handling internally, or resolving an open design question from a reviewer), **do NOT guess or silently pick one**. Use the `ask_question` tool to present the concrete solution options (marking `(Recommended)` first when appropriate) so the user can select how they want each ambiguous comment solved before you edit the files.
2. Apply the fixes directly to the **real working-tree files** under `repoDir` (never edit only the snapshot copies, and do **not** create a git commit unless explicitly asked).
3. Run relevant static analysis or unit tests in `repoDir` to verify the fix.
4. Run `manage_fixes.py --complete-fix` so Quasar automatically copies your updated working-tree files into `headSnapshotRoot` (updating the native side-by-side `vscode.diff` editor) and marks the fixed threads as `✅ Fixed` in `active_session.json`:

```bash
python3 ~/.gemini/config/plugins/quasar/skills/quasar/scripts/manage_fixes.py \
  --complete-fix \
  --thread-ids "<COMMA_SEPARATED_THREAD_IDS>" \
  --workspace-dir "<ACTIVE_WORKSPACE_PATH>" \
  --summary "<CONCISE_SUMMARY_OF_WHAT_YOU_FIXED>"
```



