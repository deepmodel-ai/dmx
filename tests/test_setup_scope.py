"""GH-130: test commands copy CI's setup; recorded dmx setup changes aren't scope creep."""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "dmx"


def _read(relative: str) -> str:
    return (SRC / relative).read_text(encoding="utf-8")


def test_init_copies_the_ci_test_setup() -> None:
    step = (
        _read("skills/workflow/0-init/dmx-init.md").split("## Step 9", 1)[1].split("## Step 10")[0]
    )

    assert "**Copy CI's test setup.**" in step
    assert "`.github/workflows/*.yml`" in step
    assert "Tell the developer which settings came from CI." in step


def test_add_component_copies_the_ci_test_setup() -> None:
    skill = _read("skills/utility/dmx-add-component.md")

    assert "Copy the environment variables and flags of the CI job" in skill


def test_the_rule_records_setup_changes_in_the_spec() -> None:
    rule = _read("rules/system-prompt.md")
    section = rule.split("## dmx setup changes during a ticket", 1)[1].split("\n## ", 1)[0]

    for path in ("`.dmx/config.md`", "`.dmx/components/*`", "`.dmx/loops/*`"):
        assert path in section
    assert "in its own commit" in section
    assert "add a line to the ticket's `spec.md` Scope right away" in section


def test_validate_counts_recorded_setup_changes_as_scope() -> None:
    skill = _read("skills/workflow/4-validate/dmx-validate.md")
    scope = skill.split("**dmx setup changes** (GH-130).", 1)[1].split("\n\n", 1)[0]

    assert "list it as a covered scope item, not scope creep" in scope
    assert "When the spec doesn't record it, it is scope creep" in scope
    assert "`.dmx/jobs/`" in scope
