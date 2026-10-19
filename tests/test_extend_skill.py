"""GH-135: an authoring skill, so the agent doesn't read dmx's source to learn the formats."""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "dmx"
SKILL = (SRC / "skills" / "utility" / "dmx-extend.md").read_text(encoding="utf-8")


def test_the_skill_covers_each_format() -> None:
    for heading in (
        "## Step 2 — Decide where it goes",
        "## Step 3 — Write a skill",
        "## Step 4 — Write a loop",
        "### A component's loop and its own validators",
        "## Step 5 — Write a validator",
        "## Step 6 — Add checks to a standard loop for one component",
        "## Step 7 — Check the result",
    ):
        assert heading in SKILL, heading


def test_the_validator_contract_matches_the_runner() -> None:
    for key in (
        "job_id",
        "job_dir",
        "spec_path",
        "tasks_path",
        "workspace_root",
        "timeout_seconds",
    ):
        assert f'"{key}"' in SKILL, key
    assert "Exit 0 when it passes and 1 when it fails" in SKILL
    assert "the same Python that runs dmx" in SKILL
    assert '"findings"' in SKILL
    assert "reworkable: false" in SKILL
    assert "default 630" in SKILL


def test_the_loop_fields_match_the_schema() -> None:
    from dmx.loop_schema import LoopConfig  # noqa: PLC0415

    loop = SKILL.split("## Step 4 — Write a loop", 1)[1].split("```yaml", 1)[1].split("```", 1)[0]
    for field in LoopConfig.model_fields:
        if field in {"repeat_until", "require_branch"}:
            continue
        assert f"{field}:" in loop, field


def test_a_component_loop_names_its_validator_plainly() -> None:
    section = SKILL.split("### A component's loop and its own validators", 1)[1].split("##", 1)[0]

    assert "plainly (`tool: refund_findings`)" in section
    assert "owner" not in section


def test_the_rule_points_to_it() -> None:
    rule = (SRC / "rules" / "system-prompt.md").read_text(encoding="utf-8")

    assert "| `/dmx/extend` |" in rule
    assert "I call `get_skill_definition` with `extend` and follow it" in rule
