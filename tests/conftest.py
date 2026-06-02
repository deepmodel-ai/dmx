"""Shared test fixtures for dmx."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def tmp_skills_dir(tmp_path: Path) -> Path:
    """Return a temp directory pre-populated with valid and invalid skill files."""
    skills = tmp_path / "skills"
    skills.mkdir()

    # Valid skill with arguments
    (skills / "dmx-commit.md").write_text(
        "---\n"
        "name: dmx-commit\n"
        "title: Commit Changes\n"
        "description: Stage and commit with conventional format.\n"
        "arguments:\n"
        "  - name: message\n"
        "    description: Override generated summary.\n"
        "    required: false\n"
        "---\n\n"
        "Run `git add .` then commit with message: {{message}}\n",
        encoding="utf-8",
    )

    # Valid skill without arguments
    (skills / "dmx-status.md").write_text(
        "---\n"
        "name: dmx-status\n"
        "title: Development Status\n"
        "description: Show open tickets and PRs.\n"
        "---\n\n"
        "Show all in-progress tickets and open PRs.\n",
        encoding="utf-8",
    )

    # Valid skill in a sub-directory (recursive scan test)
    sub = skills / "workflow"
    sub.mkdir()
    (sub / "dmx-plan.md").write_text(
        "---\n"
        "name: dmx-plan\n"
        "title: Plan\n"
        "description: Generate phased task list.\n"
        "---\n\n"
        "Generate tasks.md for the active ticket.\n",
        encoding="utf-8",
    )

    return skills


@pytest.fixture()
def tmp_rules_dir(tmp_path: Path) -> Path:
    """Return a temp directory pre-populated with a valid rule file."""
    rules = tmp_path / "rules"
    rules.mkdir()

    (rules / "system-prompt.md").write_text(
        "---\n"
        "name: system-prompt\n"
        "title: AI SDLC — Engineering Persona\n"
        "description: Always-apply engineering persona and memory bank instructions.\n"
        "alwaysApply: true\n"
        "---\n\n"
        "# Persona\n\nI am a staff-level pair programmer.\n",
        encoding="utf-8",
    )

    return rules
