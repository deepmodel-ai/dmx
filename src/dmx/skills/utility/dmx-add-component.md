---
name: add-component
title: Add Component
description: Make one folder of a monorepo a component in .dmx/components/<id>/. Recognises a Go module or package, a Node package, a Python project and a Rust crate; any other folder is named after itself and gets a test command to fill in. Offers to move repo-wide memory about it into its folder and prints the CODEOWNERS line.
arguments:
  - name: path
    description: The folder to make a component, relative to the repo root, e.g. services/fraud.
    required: true
  - name: id
    description: "The component id. Defaults to the package, module, project or crate name, else the folder name."
    required: false
  - name: description
    description: One line about what the component is. Defaults to the manifest's description.
    required: false
---

You are making one folder of this repo a component: a service or package with its own folder in `.dmx/components/<id>/`, whose `paths` decide which files belong to it. Follow every step in order. Do not commit.

## Step 1 — Plan

Call `add_component` on `user-dmx` with `path` set to `{{path}}`, plus `id` set to `{{id}}` and `description` set to `{{description}}` when they were provided. Leave `dry_run` at its default (true), so nothing is written yet.

**If `blocked` is set:**
- When `suggested_id` is set, the id is taken, for example by a second folder named `api`. Ask: "`{id}` is already a component. Which id should `{{path}}` use? (suggested: `{suggested_id}`)". Wait for the answer, then call `add_component` again with that `id`, still a dry run.
- Otherwise show the reason and stop. A path that is outside the repo, the repo root, under `.dmx/`, not a folder, or exactly another component's paths can't be added.

## Step 2 — Confirm

Show the plan and ask once:
```
Add component `{id}` ({language}) for {path}/**:

{component_yaml}
{if carved_from:} {path} is inside `{carved_from}`. Its files will belong to `{id}`, the more specific match.

Create it? (yes / no / a different id or description)
```

Wait for the answer. On **no**, stop. On a different id or description, plan again with it (Step 1). On **yes**, call `add_component` again with the same arguments and `dry_run` false.

## Step 3 — Move memory about it

Read the repo-wide memory files (`.dmx/projectbrief.md`, `.dmx/productContext.md`, `.dmx/systemPatterns.md`, `.dmx/techContext.md`, and any other `.dmx/*.md` except `config.md`). Find the parts that are only about this component: its architecture, dependencies, constraints, conventions. If there are none, skip this step.

Show the move as a diff: removed from `.dmx/{file}`, added to `.dmx/components/{id}/{file}` under the same heading. Ask once. Apply only what the developer accepts. Content about more than one component stays repo-wide.

## Step 4 — Return the result

Use the `codeowners` line from the tool's reply, and its `has_default_test`.

```
Added component `{id}`: .dmx/components/{id}/component.yaml (paths: {path}/**)
{if memory moved:} Moved memory: {files}

Next:
  {if has_default_test is false:} - dmx has no default test command for this folder's language: fill in `test: {cmd: ..., cwd: ...}` in .dmx/components/{id}/component.yaml (`cwd` is relative to the repo root and defaults to it).
  {if the folder's tests live elsewhere, such as a root tests/ folder:} - Add those test paths to `paths`, or set `test: {cmd: ..., cwd: ...}` to run them. A Python component with no tests in its own folder runs the test paths its `paths` name, from the repo root.
  {if you suggest a `test` command:} - Copy the environment variables and flags of the CI job that runs these tests (from `.github/workflows/`) into it, and say which came from CI.
  - Add `depends_on` there if `{id}` uses another component, so its tests run when that one changes.
  - Add this line to .github/CODEOWNERS with the owning team:
      {codeowners}
  - Commit .dmx/components/{id}/ on a branch and open a PR.
```

## Guards

- Never write `component.yaml` yourself. `add_component` writes it, and never overwrites an existing one.
- Never reuse an id that is already a component.
- Never move memory the developer did not accept.
