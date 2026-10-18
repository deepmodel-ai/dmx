"""GH-121: PR titles, branch names and commit types follow the repo and the change."""

from __future__ import annotations

from pathlib import Path

import pytest

SKILLS = Path(__file__).parent.parent / "src" / "dmx" / "skills"


def _skill(relative: str) -> str:
    return (SKILLS / relative).read_text(encoding="utf-8")


def test_pr_titles_follow_the_repo_convention() -> None:
    step = (
        _skill("workflow/5-ship/dmx-create-pr.md").split("## Step 10", 1)[1].split("## Step 11")[0]
    )

    assert "**The repo's own convention wins.**" in step
    for source in ("`CONTRIBUTING.md`", "`AGENTS.md`", "`CLAUDE.md`", "PR template"):
        assert source in step
    assert "Say in the output which convention you followed." in step


@pytest.mark.parametrize(
    "relative",
    [
        "workflow/1-triage/dmx-create-ticket.md",
        "workflow/1-triage/dmx-derive-ticket.md",
        "workflow/1-triage/dmx-hotfix.md",
        "utility/dmx-create-branch.md",
    ],
)
def test_branch_slugs_stop_at_40_characters(relative: str) -> None:
    text = _skill(relative)

    assert "at most 40 characters at a hyphen" in text
    assert "60 char" not in text


def test_commit_type_comes_from_what_changed() -> None:
    step = _skill("utility/dmx-commit.md").split("## Step 7", 1)[1].split("## Step 8")[0]

    assert step.index("look at what is staged first") < step.index("`feature-` → `feat`")
    assert "or docs (`*.md`, `docs/`) → `docs`" in step
    assert "only `.dmx/jobs/` loop state (`*.json`) → `chore`" in step
