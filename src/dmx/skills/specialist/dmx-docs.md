---
name: docs
title: Write Documentation
description: Write clear, human-first documentation. Covers purpose, parameters, return values, and small useful examples. Uses the idiomatic docstring style for the language. Avoids unnecessary words.
arguments:
  - name: path
    description: File or module to document. Required.
    required: true
  - name: style
    description: "Documentation style. Accepted values: docstrings (default), readme, api, all."
    required: false
  - name: audience
    description: "Intended reader. Accepted values: developer (default), user, both."
    required: false
---

You are writing documentation. Write for humans first. Every word must earn its place. Follow every step in order.

## Step 1 — Resolve defaults

- If `{{style}}` was not provided, use `docstrings`.
- If `{{audience}}` was not provided, use `developer`.

## Step 2 — Read the target

Read the file at `{{path}}`. Understand:
- What the module, class, or function does at a high level
- What each parameter means and what values it accepts
- What is returned and in what shape
- What errors can be raised and when
- Any non-obvious behaviour or side effects

Also read the call sites and usages if accessible, to understand how the code is actually used in practice — this often reveals the real purpose better than the implementation itself.

## Step 3 — Detect the language and docstring convention

| Language | Convention |
|---|---|
| Python | Google-style docstrings (Args, Returns, Raises, Example) |
| TypeScript / JavaScript | TSDoc (`@param`, `@returns`, `@throws`, `@example`) |
| Kotlin | KDoc (`@param`, `@return`, `@throws`, `@sample`) |
| Scala | Scaladoc (`@param`, `@return`, `@throws`, `@example`) |
| Other | Follow the dominant convention in the file |

## Step 4 — Write the documentation

Apply these rules regardless of style:

**What to document**
- Every public function, method, and class.
- Every parameter: name, type, what it means, valid values or constraints.
- Return value: what it is, its shape, when it can be null/None/empty.
- Raised exceptions: which ones, under what conditions.
- Side effects that are non-obvious (writes to DB, sends email, mutates shared state).

**What not to document**
- What the code obviously does (`# increment counter` above `count += 1`).
- Implementation details that will change (refer to behaviour, not mechanism).
- Internal/private functions that are stable and self-explanatory.

**Examples**
- Include a minimal, runnable example for any function with non-trivial usage.
- Examples must be correct — test them mentally before writing.
- Keep examples short: show the most common use case, not every possible combination.

**Tone**
- Present tense, active voice: "Returns the user by ID" not "This function will return the user".
- One sentence for the summary line — make it count.
- Avoid filler phrases: "This function...", "This method is used to...", "Note that...".

**Python (Google-style) example:**
```python
def get_user_by_id(user_id: str, include_deleted: bool = False) -> User | None:
    """Fetch a user record by their unique ID.

    Args:
        user_id: The UUID of the user to retrieve.
        include_deleted: If True, soft-deleted users are included in the lookup.
            Defaults to False.

    Returns:
        The matching User instance, or None if no user exists with that ID.

    Raises:
        ValueError: If user_id is an empty string.

    Example:
        user = get_user_by_id("abc-123")
        if user:
            print(user.email)
    """
```

**TSDoc example:**
```typescript
/**
 * Fetch a user record by their unique ID.
 *
 * @param userId - The UUID of the user to retrieve.
 * @param includeDeleted - When true, soft-deleted users are included. Defaults to false.
 * @returns The matching user, or null if not found.
 * @throws {Error} If userId is an empty string.
 *
 * @example
 * const user = await getUserById('abc-123');
 * if (user) console.log(user.email);
 */
```

## Step 5 — Apply the documentation

Write the docstrings/comments directly into the source file. Do not create a separate file for `docstrings` style.

For `readme` or `api` style, create or update the appropriate markdown file.

## Step 6 — Output a summary

```
Documentation written: {file path}

Documented:
{list each function/class documented}

Skipped (private or self-explanatory):
{list anything intentionally not documented}
```

## Guards

- Do not document what the code obviously does. If writing a docstring makes you repeat the function name in prose, the function name is probably good enough.
- If a function is so complex it needs a long explanation, note that the function itself may need to be simplified — good documentation reveals bad design.
- Do not fabricate parameter descriptions. If you are unsure what a parameter does, say so and leave a `TODO:` placeholder rather than guessing.
