---
name: test
title: Write Tests
description: Write tests that enable change. Prioritises unit tests, covers edge cases and failure modes, uses property-based tests where useful. Tests are fast, isolated, and reliable.
arguments:
  - name: path
    description: File or module to write tests for. Required.
    required: true
  - name: style
    description: "Testing style. Accepted values: unit (default), integration, property, all."
    required: false
  - name: framework
    description: Test framework to use. Auto-detected from the project if omitted (e.g. pytest, jest, vitest).
    required: false
---

You are writing tests. Your goal is to write tests that give the developer confidence to change the code without fear. Follow every step in order.

## Step 1 — Resolve defaults

- If `{{style}}` was not provided, use `unit`.
- If `{{framework}}` was not provided, auto-detect from the project:
  - Look for `pytest` in `requirements.txt`, `pyproject.toml` → use pytest
  - Look for `jest` in `package.json` → use Jest
  - Look for `vitest` in `package.json` → use Vitest
  - If unclear, default to pytest for Python, Jest for TypeScript/JavaScript.

## Step 2 — Read the target

Read the file at `{{path}}`. Understand:
- What each function/method does
- What the inputs, outputs, and side effects are
- What the failure modes are
- What invariants must always hold

Also read any related files that are called or imported, to understand integration points.

## Step 3 — Identify test cases

For each function or public method, identify:

**Happy path cases**
- Typical inputs that should produce the expected output

**Edge cases**
- Empty inputs (empty string, empty list, zero, None/null)
- Boundary values (min/max, off-by-one)
- Large inputs (if performance is a concern)
- Concurrent invocations (if async or shared state)

**Failure cases**
- Invalid input types or values
- Downstream failures (service throws, DB unavailable)
- Partial failures (first item succeeds, second fails)
- Permission/auth failures

**Invariants (for property-based tests)**
- Properties that must hold for any valid input (e.g. "serialise then deserialise returns the original")

## Step 4 — Write the tests

Apply these rules:

**Structure**
- One test file per source file, co-located or in a parallel `tests/` directory matching the source tree.
- One test function per behaviour, not per function. Name tests as `test_{what}_{when}_{expected}`.
- Use `arrange / act / assert` structure within each test, with blank lines between sections.

**Isolation**
- Tests must not depend on external services, databases, or network calls. Use fakes or in-memory implementations over mocks where possible.
- If mocking is unavoidable, mock at the boundary (the external call), not at internal functions.
- Each test must be runnable independently — no shared mutable state between tests.

**Functional style**
- Prefer pure function tests: given input X, assert output Y. No setup/teardown when avoidable.
- Parametrize repeated patterns rather than duplicating test functions.

**Property-based tests** (when `{{style}}` is `property` or `all`)
- Use `hypothesis` for Python, `fast-check` for TypeScript.
- Write at least one property test for any function with a large input domain.

## Step 5 — Output the tests

Write the test file(s) to disk at the appropriate path.

Then output:
```
Tests written: {file path}

Coverage:
{list each tested function and the cases covered}

Not covered (requires integration test or manual verification):
{list anything intentionally excluded and why}
```

## Guards

- Do not write tests that only test the framework (e.g. `assert True`).
- Do not test private/internal functions directly — test them through the public interface.
- If the code under test has no clear interface (e.g. it's all side effects with no return value), note this and write tests that assert on observable state changes instead.
- If the code is untestable as written (e.g. hardcoded dependencies, no DI), note the refactor needed before proceeding with a partial test suite.
