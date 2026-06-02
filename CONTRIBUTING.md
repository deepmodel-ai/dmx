# Contributing to dmx

Thank you for your interest in contributing. This document explains how to contribute and what we expect from contributors.

## Contributor License Agreement (CLA)

**Before your first pull request can be merged, you must sign the [Contributor License Agreement](CLA.md).**

We use [CLA Assistant](https://cla-assistant.io) — it will prompt you to sign directly in the PR when you open one. The CLA assigns copyright to Deepmodel while granting you the right to retain and use your contributions. This allows Deepmodel to offer `dmx` under AGPL-3.0 alongside a commercial license.

## Development setup

```bash
git clone https://github.com/deepmodel/dmx.git
cd dmx
uv sync --extra dev
uv run pre-commit install  # optional but recommended
```

## Running checks locally

```bash
uv run ruff check src/ tests/        # lint
uv run ruff format --check src/ tests/  # format
uv run mypy src/                     # type check
uv run python -m pytest              # tests
```

All four must pass before opening a PR. CI runs the same commands on Python 3.11, 3.12, and 3.13 on Ubuntu and macOS.

## Adding or editing skills

Skills are Markdown files under `src/dmx/skills/`. Each file requires YAML frontmatter:

```yaml
---
name: dmx-my-skill
title: Short human-readable title
description: One-sentence description shown in the IDE command palette.
arguments:
  - name: my_arg
    description: What this argument controls.
    required: false
---

Your skill prompt body here.
```

- `name` must match `^[a-z0-9_-]+$`
- `title` and `description` are shown in the IDE; keep them concise
- Skill files live under `src/dmx/skills/<category>/<order>-<slug>/` — order prefix controls sort order

## Adding or editing rules

Rules are Markdown files under `src/dmx/rules/`. Each file requires YAML frontmatter:

```yaml
---
name: my-rule
title: Short title
description: One-sentence description of what this rule enforces.
---

Rule body here (written as an instruction to the AI).
```

## Pull request checklist

- [ ] Tests added or updated for all changed behaviour
- [ ] `CHANGELOG.md` entry added under `[Unreleased]`
- [ ] All new functions and classes have type annotations
- [ ] `ruff check`, `ruff format --check`, `mypy`, and `pytest` all pass
- [ ] CLA signed (CLA Assistant will prompt on first PR)

## Reporting bugs and feature requests

Open a [GitHub issue](https://github.com/deepmodel/dmx/issues). For security issues, see [SECURITY.md](SECURITY.md).
