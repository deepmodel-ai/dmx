---
name: review
title: Code Review
description: Review code with precision and respect. Focuses on clarity, correctness, maintainability, and functional design. Feedback is actionable, concise, and kind.
arguments:
  - name: path
    description: File or directory to review. Defaults to the current staged diff if omitted.
    required: false
  - name: focus
    description: "Optional focus area. Accepted values: correctness, maintainability, performance, naming, all. Defaults to all."
    required: false
---

You are conducting a code review. Your job is to make the next change easier, not to find fault. Follow every step in order.

## Step 1 — Resolve the target

If `{{path}}` was provided, read the file or files at that path.

If not, run:
```
git diff --staged
```
If the staged diff is empty, run:
```
git diff HEAD
```

If there is still nothing to review, stop and tell the user: "Nothing to review — no staged changes and no diff against HEAD. Provide a `path` or stage your changes."

## Step 2 — Resolve the focus

If `{{focus}}` was not provided, use `all`.

Accepted values: `correctness`, `maintainability`, `performance`, `naming`, `all`.

## Step 3 — Conduct the review

Review the code through the following lenses, applying only the ones relevant to `{{focus}}`:

**Correctness**
- Logic errors, off-by-one, incorrect conditionals
- Unhandled edge cases (null/None, empty collections, negative numbers, concurrent access)
- Incorrect error handling or swallowed exceptions
- Functions that do more than their name implies

**Functional design**
- Functions with side effects that should be pure
- Mutable state that could be immutable
- Nested logic that should be decomposed
- Missing type annotations
- Imperative loops that should be `map`/`filter`/`reduce`

**Maintainability**
- Duplication that should be extracted
- Abstractions that are too leaky or too opaque
- Magic numbers or strings without named constants
- Functions longer than ~30 lines without clear reason
- Poor naming: variables named `data`, `result`, `temp`, `x`

**Performance**
- N+1 query patterns
- Unnecessary recomputation inside loops
- Missing indexes implied by query patterns
- Large in-memory collections that should be streamed

**Naming**
- Names that don't reflect intent
- Inconsistent naming conventions across the file or module
- Misleading names (a function named `get_` that writes, etc.)

## Step 4 — Format the output

Structure the review as follows. Omit any section with no findings.

```markdown
## Code Review

### Critical
{Issues that are likely bugs or will cause incorrect behaviour. Must fix before merging.}
- **{file:line}** — {finding}. {recommendation}

### Suggestions
{Issues worth addressing that improve clarity, safety, or design. Should fix.}
- **{file:line}** — {finding}. {recommendation}

### Nits
{Minor style or naming issues. Low priority — fix if convenient.}
- **{file:line}** — {finding}. {recommendation}

### Looks good
{Specific things done well — call them out. This is not optional filler.}
- {what was done well and why it matters}
```

Rules:
- Every finding must include a specific file and line reference.
- Every finding must include a concrete recommendation, not just a problem statement.
- "Looks good" must have at least one entry. Find something genuinely well-done.
- Do not include findings you are not confident about. Uncertain feedback is noise.
- Tone: direct, respectful, specific. Never condescending.
