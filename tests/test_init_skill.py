"""GH-119: init checks prerequisites first and lands the setup on the base branch."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent.parent
SKILL = (ROOT / "src/dmx/skills/workflow/0-init/dmx-init.md").read_text(encoding="utf-8")


def _step(number: int) -> str:
    return SKILL.split(f"## Step {number} — ", 1)[1].split("\n## Step ", 1)[0]


def test_github_access_is_checked_before_any_question() -> None:
    assert _step(3).startswith("Check GitHub access")
    assert "before asking anything" in _step(3)
    assert _step(4).startswith("Ask: workflow mode")
    assert _step(5).startswith("Ask: ticketing system")
    assert SKILL.index("gh auth status") < SKILL.index("How do you want to work on this project?")
    assert "Steps 4 and 5 require the developer's input" in SKILL


def test_atlassian_is_checked_right_after_the_jira_answers() -> None:
    ticketing = _step(5)

    assert ticketing.index("Record as `project_key`") < ticketing.index("atlassianUserInfo")
    assert "Verify MCP server availability" not in SKILL


def test_every_stop_message_covers_each_ide() -> None:
    for name in ("user-github", "user-atlassian"):
        block = SKILL.split(f"as {name}" if name == "user-atlassian" else "gh auth login", 1)[1]
        block = block.split("```", 1)[0]
        for ide in ("Claude Code:", "Cursor:", "VS Code:"):
            assert ide in block, (name, ide)


def test_ignored_rule_files_get_an_offer() -> None:
    rules = _step(6)

    assert "git check-ignore -v" in rules
    assert "`!.claude/rules/`" in rules
    assert "run `git check-ignore` again" in rules


def test_the_setup_lands_on_the_base_branch() -> None:
    output = _step(10)

    assert "**Commit the setup.**" in output
    assert "Commit it on {branch_base} and push" in output
    assert "chore-dmx-setup" in output
    assert "merge the PR before starting a loop" in output


def test_the_readme_names_the_github_prerequisite() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quick_start = readme.split("## Quick start", 1)[1]

    assert quick_start.index("**Before you start:**") < quick_start.index("**1. Add dmx")
    assert "GitHub MCP server" in quick_start
    assert "`gh auth login`" in quick_start
