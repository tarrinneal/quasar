---
name: code-review
description: >-
  Use this skill whenever the user provides a GitHub PR link/number or asks to review a PR, branch,
  or diff, or asks an inline code review question. Runs the Antigravity CliffsNotes Code Review
  pipeline: (1) Auto-fetches PR context & Base/Head file snapshots, (2) Parallel Multi-Lens Subagent
  Analysis (Architecture, Bug Hunter, Test Critic, Advocate Filter), (3) Publishes interactive
  CliffsNotes chapters, tour stops, hover context, and inline candidate comment threads to the
  native Antigravity Code Review extension, and (4) Answers inline comment thread questions or
  exports staged comments to a GitHub Pending Review.
---

# Antigravity Code Review Workflow (`code-review`)

Your primary goal is **deep reviewer comprehension first**, followed by **high-signal, verified review feedback**—all rendered natively inside Antigravity without webviews or code clutter.

Whenever the user provides a GitHub PR URL (or PR number), **initiate the entire workflow automatically without asking for confirmation**.

---

## Phase 1: Auto-Fetch PR Context & Materialize Base/Head Snapshots

Run `collect_pr_context.py` immediately. This fetches PR metadata, existing GitHub comments, and unified diffs, materializes exact `base/` and `head/` file snapshots on local disk for native side-by-side diffs (`vscode.diff`), and initializes `~/.gemini/antigravity-code-review/active_session.json` so the native **Antigravity Code Review** sidebar updates right away:

```bash
# When given a GitHub PR URL or number:
python3 ~/.gemini/config/plugins/code-review/skills/code-review/scripts/collect_pr_context.py \
  --pr <PR_URL_OR_NUMBER> \
  --output-dir <SCRATCH_DIR>

# Or when reviewing a local branch diff:
python3 ~/.gemini/config/plugins/code-review/skills/code-review/scripts/collect_pr_context.py \
  --base origin/main \
  --repo-dir <REPO_ROOT> \
  --output-dir <SCRATCH_DIR>
```

Inspect the `core_source` files from the categorized summary and relevant sections of `pr.diff`. Separate **hand-written source-of-truth files** from **generated code / test fixtures** (`generated_or_fixture`).

---

## Phase 2: Parallel Multi-Lens Subagents

For medium or large PRs, spawn the three specialist review subagents **in parallel** using a single `invoke_subagent` call (`Model: "inherit"`). (For tiny diffs under ~50 lines, you may apply these three lenses directly):

1. **`review-architect`** (`Role: "Architecture & API Reviewer"`):
   - Pass the PR title/description, list of changed `core_source` files, and path to `<SCRATCH_DIR>/pr.diff`.
   - Ask it to build the architectural mental model, identify key symbols ("Character List"), evaluate API boundaries & backwards compatibility, and suggest the logical top-down reading order.
2. **`review-bug-hunter`** (`Role: "Bug & Edge-Case Hunter"`):
   - Pass the changed `core_source` files and `<SCRATCH_DIR>/pr.diff`.
   - Ask it to trace execution paths, nullability, state mutations, resource lifecycle, and language-boundary/generator edge cases.
3. **`review-test-critic`** (`Role: "Test & Verification Critic"`):
   - Pass the changed `core_source`, `test`, `generated_or_fixture`, and `docs_or_config` files.
   - Ask it to build a coverage matrix, evaluate test falsifiability, verify generated file sync, and check repo hygiene (`CHANGELOG.md`, `pubspec.yaml`).

### Filtering False Positives (`review-advocate`)
Once the lenses report back, verify every candidate finding against the actual codebase callers/callees and `existing_comments` (either via `review-advocate` or direct inspection). **Drop** any finding that is:
- Guaranteed safe by upstream checks/types
- Pre-existing untouched code
- Already commented on in `existing_comments`
- Purely subjective style bike-shedding

---

## Phase 3: Publish the CliffsNotes Session to the Native Antigravity Extension

Write a JSON file `<SCRATCH_DIR>/review_synthesis.json` and publish it using `publish_review_session.py`. This populates:
1. **The Native `CliffsNotes & Reading Tour` TreeView** (logical chapters, CliffsNotes bullet summaries, key symbol character list, and numbered tour stops that open `vscode.diff`).
2. **Clean Native Hover Cards & CodeLenses** (so hovering over any changed hunk shows its CliffsNotes summary, why it changed, and callers/invariants—without injecting comprehension comments into the code).
3. **Native Inline Comment Threads (`vscode.comments`)** for verified candidate review comments (`🔴 Blocking`, `🟡 Design`, `🟢 Nit`).
4. **The Companion CliffsNotes Study Guide** (`cliffs_notes.md` and conversation artifact `code_review_briefing.md`).

### `review_synthesis.json` Schema

```json
{
  "analysisStatus": "ready",
  "cliffsNotes": {
    "tldr": "One-sentence summary of what the PR does and why.",
    "whatAndWhy": "Concise paragraph explaining the problem solved and the core mechanism.",
    "architectureDiagram": "flowchart LR\n  A[\"Caller\"] --> B[\"Generator\"]",
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

Run the publisher script and also copy the study guide to your conversation's artifact directory (`<appDataDir>/brain/<conversation-id>/code_review_briefing.md`):

```bash
python3 ~/.gemini/config/plugins/code-review/skills/code-review/scripts/publish_review_session.py \
  --input <SCRATCH_DIR>/review_synthesis.json \
  --artifact-out <ARTIFACT_DIR>/code_review_briefing.md
```

---

## Phase 4: Answering Inline Comment Questions & Exporting Pending Reviews

### 1. When the Reviewer Asks a Question in a Native Comment Thread
When you receive a prompt triggered from an inline comment thread (`--thread-id`), investigate the code and post your answer directly back into that inline thread so it updates live inside the diff editor:

```bash
python3 ~/.gemini/config/plugins/code-review/skills/code-review/scripts/answer_comment_question.py \
  --thread-id "<THREAD_ID>" \
  --answer "<CONCISE_MARKDOWN_EXPLANATION>" \
  [--updated-draft "<OPTIONAL_UPDATED_GITHUB_COMMENT>"]
```

### 2. Exporting Staged Comments to a GitHub Pending Review
The reviewer can click the **`$(cloud-upload) Export Staged Comments to GitHub Pending Review`** button in the **Review Comments & Cart** panel at any time, or ask you in chat to export selected comments:

```bash
python3 ~/.gemini/config/plugins/code-review/skills/code-review/scripts/post_pending_review.py \
  --from-session
```
