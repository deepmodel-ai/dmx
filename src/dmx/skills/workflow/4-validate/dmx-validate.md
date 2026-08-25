---
name: validate
title: Validate — Pre-PR Quality Gate
description: Three-part quality gate before opening a PR. Checks ticket completeness (spec, acceptance criteria, tasks), code quality (codebase patterns, style, naming), and security (vulnerabilities introduced by the diff). Produces a structured report with a clear READY FOR PR or NEEDS WORK verdict.
---

You are running the pre-PR validation gate for the current branch. Follow every step in order. Do not skip checks. Do not open a PR.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `ticketing`, `branch_base`, `owner`, `repo`

If not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init first."

## Step 2 — Read spec.md

Read `.dmx/spec.md`.

If the file does not exist, stop: "spec.md not found. Run `/dmx/create-ticket` or `/dmx/create-branch` first."

Extract from the YAML frontmatter:
- `ticket` — ticket reference (may be empty)
- `branch` — branch name
- `summary` — one-line summary
- `ticketing` — ticketing provider

## Step 3 — Read tasks.md and memory bank

Read all of the following:
- `.dmx/tasks.md`
- `.dmx/systemPatterns.md` — for code quality reference
- `.dmx/techContext.md` — for stack and constraint reference

If `tasks.md` is missing, stop: "tasks.md not found. Run `/dmx/plan` first."

## Step 4 — Read the diff

Run:
```
git diff origin/{config.branch_base}..HEAD
git diff origin/{config.branch_base}..HEAD --stat
git rev-parse HEAD
```

If the diff is empty, stop: "No changes found against {config.branch_base}. Nothing to validate."

Keep the `git rev-parse HEAD` output — you'll need it in Step 9.

## Step 5 — Check 1: Ticket Completeness

Evaluate whether the implementation satisfies the spec.

**Tasks coverage:**
Scan `tasks.md` for unchecked tasks (`- [ ]`). Every task must be checked for the ticket to be complete.

**Acceptance criteria:**
Look for an `## Acceptance Criteria` section in `spec.md`. If present, evaluate each criterion against the diff and mark as:
- `[x]` Satisfied — evidence visible in the diff
- `[ ]` Not satisfied — no evidence in the diff
- `[~]` Partially satisfied — implementation present but incomplete

If no `## Acceptance Criteria` section exists, fall back to evaluating the `## Scope` bullet list as implicit acceptance criteria.

**Scope:**
Check whether the diff stays within the `## Scope` defined in spec.md. Flag anything in the diff that falls outside declared scope.

For every scope item (or acceptance criterion), find the specific file(s)/line(s) or diff hunk that satisfy it. If you cannot find any evidence in the diff for an item, mark it `missing` — not `partial` — only when you are certain there is genuinely nothing there; use `partial` for anything ambiguous or incomplete. You'll record these verdicts in Step 9.

**Regressions:**
Check whether the diff breaks any previously-working behavior — removed functionality with no replacement, or changes that contradict an existing test's expectations. Base this only on what you find in the diff and test suite, never on words like "regression"/"broke" appearing in commit messages or descriptions — a description that accurately reports a *fixed* regression is not itself a regression.

## Step 6 — Check 2: Code Quality

Evaluate the diff against the codebase's established standards.

**Codebase patterns (from systemPatterns.md and techContext.md):**
- Do the new files and functions follow the architectural patterns described in systemPatterns.md?
- Are the same layers, abstractions, and component boundaries respected?
- Are new dependencies consistent with techContext.md constraints?

**Functional style:**
- Are functions small and single-purpose?
- Is mutable state avoided where immutable alternatives are available?
- Are side effects isolated at the edges?
- Are type annotations present?
- Are imperative loops used where `map`/`filter`/`reduce` would be clearer?

**Naming and structure:**
- Do names reflect intent clearly?
- Is naming consistent with the conventions in the affected module?
- Are there magic numbers, unexplained constants, or misleading names?

**Maintainability:**
- Is there duplication that should be extracted?
- Are functions longer than ~30 lines without clear reason?
- Are there unhandled edge cases (null, empty collection, concurrent access)?

## Step 7 — Check 3: Security

Evaluate the diff for vulnerabilities.

Check for:
- **Injection** — SQL, command, template, or path injection risks
- **Authentication / authorisation** — missing auth checks, privilege escalation, insecure token handling
- **Data exposure** — secrets, credentials, or PII in code, logs, or responses
- **Input validation** — unvalidated or unsanitised user inputs reaching sensitive operations
- **Cryptography** — weak algorithms, hardcoded keys, insecure randomness
- **Dependency risks** — new packages with known vulnerabilities or unnecessary permissions
- **Race conditions** — shared state accessed without synchronisation

Rate each finding: `Critical` | `High` | `Medium` | `Low`.

## Step 8 — Assemble the report

```markdown
## Validation Report: {summary from spec.md}

**Branch:** {branch from spec frontmatter}
{if ticket is set} **Ticket:** {ticket}

### 1. Ticket Completeness

**Tasks:** {N}/{M} complete
{list any unchecked tasks}

**Acceptance Criteria:**
- {[x] / [ ] / [~]} {criterion}
...

**Scope:** {In scope / Out-of-scope changes detected: {list}}

---

### 2. Code Quality

**Critical**
{issues that are likely bugs or will cause incorrect behaviour — must fix}
- **{file:line}** — {finding}. {recommendation}

**Suggestions**
{issues worth addressing — should fix}
- **{file:line}** — {finding}. {recommendation}

**Nits**
{minor style or naming — fix if convenient}
- **{file:line}** — {finding}. {recommendation}

**Looks good**
{specific things done well — required, not optional filler}
- {what was done well and why it matters}

---

### 3. Security

**Critical / High**
- **{file:line}** — {risk}. {remediation}

**Medium / Low**
- **{file:line}** — {risk}. {remediation}

{If no findings: "No security issues found in this diff."}

---

### Verdict

{READY FOR PR}
  All tasks complete. Acceptance criteria satisfied. No critical issues.
  Run /dmx/create-pr to open the pull request.

{or}

{NEEDS WORK}
  {summary of what must be addressed before the PR is opened}
  Fix the issues above, then re-run /dmx/validate.
```

## Step 9 — Write the structured validation report

The report in Step 8 is for the developer to read. This step writes a machine-readable copy of your scope/regression/edge-case findings, so the `spec_adherence` validator grades your actual diff analysis instead of re-parsing prose.

Determine `job_id`: use the value given in the loop runtime instruction that invoked this skill (the line reading `` Job: `{job_id}` ``). If you were invoked directly (not through the loop runtime), resolve it the same way the runtime does: the `ticket` field from `spec.md` frontmatter (Step 2), else the current branch name, else `unknown`.

Write `.dmx/jobs/{job_id}/validation-report.json` (create the directory if it doesn't exist):

```json
{
  "commit": "{full commit SHA from Step 4's `git rev-parse HEAD`}",
  "scope_items": [
    {"item": "{scope bullet or acceptance criterion text}", "verdict": "covered", "evidence": "{file:line}"}
  ],
  "scope_creep": [
    {"description": "{out-of-scope change}", "evidence": "{file:line}"}
  ],
  "regressions": [
    {"description": "{what broke}", "evidence": "{file:line}"}
  ],
  "edge_cases": [
    {"description": "{edge case}", "addressed": true, "evidence": "{file:line}"}
  ]
}
```

Population rules:
- `scope_items` — one entry per scope bullet / acceptance criterion from Step 5. `verdict` is `"covered"`, `"partial"`, or `"missing"` — use the verdicts you already determined in Step 5. Only `"missing"` blocks the loop, so reserve it for items with genuinely zero evidence in the diff.
- `scope_creep` — only changes clearly outside declared scope, from Step 5. Empty array if none.
- `regressions` — only your own findings from Step 5's regression check. Empty array if none. Never populate this from keyword matches on prose.
- `edge_cases` — only cases you actually evaluated in Step 6. `addressed: false` only for a specific, clear gap.
- Empty arrays (`[]`) are valid and expected when a section has no findings — do not omit the key.

## Guards

- Omit sections with no findings (except "Looks good" in Code Quality — always include at least one).
- Never open a PR. Never make code changes. Report only.
- If there are unchecked tasks in tasks.md, the verdict is always NEEDS WORK regardless of other findings.
- If there are Critical findings in Code Quality or Security, the verdict is always NEEDS WORK.
- Always complete Step 9, even when the verdict is NEEDS WORK — the validator reads `validation-report.json`, not this chat response.
