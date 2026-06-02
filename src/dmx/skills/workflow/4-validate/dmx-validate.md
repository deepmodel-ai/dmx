---
name: validate
title: Validate — Pre-PR Quality Gate
description: Three-part quality gate before opening a PR. Checks ticket completeness (spec, acceptance criteria, tasks), code quality (codebase patterns, style, naming), and security (vulnerabilities introduced by the diff). Produces a structured report with a clear READY FOR PR or NEEDS WORK verdict.
arguments:
  - name: ticket_id
    description: Ticket identifier. Auto-detected from activeContext.md if omitted.
    required: false
---

You are running the pre-PR validation gate for an active ticket. Follow every step in order. Do not skip checks. Do not open a PR.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `ticketing`, `branch_base`, `owner`, `repo`

If not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init first."

## Step 2 — Resolve the active ticket

If `{{ticket_id}}` was provided, use it.

If not, read `.dmx/activeContext.md` and extract the ticket ID from `## Active Ticket`.

If no active ticket is found, stop: "No active ticket found. Provide `ticket_id` or run `/dmx/create-ticket` first."

## Step 3 — Read ticket files and memory bank

Read all of the following:
- `.dmx/tickets/active/{ticket_id}/spec.md`
- `.dmx/tickets/active/{ticket_id}/tasks.md`
- `.dmx/systemPatterns.md` — for code quality reference
- `.dmx/techContext.md` — for stack and constraint reference

If spec.md or tasks.md is missing, stop: "Spec or tasks not found for {ticket_id}. Run `/dmx/plan` first."

## Step 4 — Read the diff

Run:
```
git diff origin/{config.branch_base}..HEAD
git diff origin/{config.branch_base}..HEAD --stat
```

If the diff is empty, stop: "No changes found against {config.branch_base}. Nothing to validate."

## Step 5 — Check 1: Ticket Completeness

Evaluate whether the implementation satisfies the ticket.

**Tasks coverage:**
Scan `tasks.md` for unchecked tasks (`- [ ]`). Every task must be checked for the ticket to be complete.

**Acceptance criteria:**
Extract the `## Acceptance Criteria` section from `spec.md`. For each criterion, evaluate whether the diff satisfies it. Mark each as:
- `[x]` Satisfied — evidence visible in the diff
- `[ ]` Not satisfied — no evidence in the diff
- `[~]` Partially satisfied — implementation present but incomplete

**Scope:**
Check whether the diff stays within the `## Scope` defined in spec.md. Flag anything in the diff that falls outside declared scope.

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
## Validation Report: {ticket_id}

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

## Guards

- Omit sections with no findings (except "Looks good" in Code Quality — always include at least one).
- Never open a PR. Never make code changes. Report only.
- If there are unchecked tasks in tasks.md, the verdict is always NEEDS WORK regardless of other findings.
- If there are Critical findings in Code Quality or Security, the verdict is always NEEDS WORK.
