"""Tests for dmx.catalog — load_skills, load_rules, substitute_args."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from dmx.catalog import (
    MAX_SKILL_BYTES,
    RuleDefinition,
    SkillArgument,
    SkillDefinition,
    load_rules,
    load_skills,
    substitute_args,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestLoadSkills:
    def test_loads_all_skills_recursively(self, tmp_skills_dir: Path) -> None:
        skills = load_skills(tmp_skills_dir)
        names = {s.name for s in skills}
        assert "dmx-commit" in names
        assert "dmx-status" in names
        assert "dmx-plan" in names  # nested in workflow/

    def test_returns_tuple(self, tmp_skills_dir: Path) -> None:
        result = load_skills(tmp_skills_dir)
        assert isinstance(result, tuple)

    def test_parsed_skill_has_correct_fields(self, tmp_skills_dir: Path) -> None:
        skills = load_skills(tmp_skills_dir)
        commit = next(s for s in skills if s.name == "dmx-commit")
        assert commit.title == "Commit Changes"
        assert commit.description == "Stage and commit with conventional format."
        assert len(commit.arguments) == 1
        assert commit.arguments[0].name == "message"
        assert commit.arguments[0].required is False

    def test_skill_with_no_arguments(self, tmp_skills_dir: Path) -> None:
        skills = load_skills(tmp_skills_dir)
        status = next(s for s in skills if s.name == "dmx-status")
        assert status.arguments == ()

    def test_empty_directory_returns_empty_tuple(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        result = load_skills(empty)
        assert result == ()

    def test_invalid_name_is_skipped(self, tmp_path: Path) -> None:
        d = tmp_path / "skills"
        d.mkdir()
        (d / "INVALID NAME.md").write_text(
            "---\nname: INVALID NAME\ntitle: Bad\ndescription: x\n---\n\nbody\n",
            encoding="utf-8",
        )
        skills = load_skills(d)
        assert skills == ()

    def test_oversized_file_is_skipped(self, tmp_path: Path) -> None:
        d = tmp_path / "skills"
        d.mkdir()
        big = d / "huge-skill.md"
        # Write frontmatter + oversized body
        header = "---\nname: huge-skill\ntitle: Big\ndescription: x\n---\n\n"
        big.write_bytes(header.encode() + b"x" * (MAX_SKILL_BYTES + 1))
        skills = load_skills(d)
        assert skills == ()

    def test_skill_body_is_stored(self, tmp_skills_dir: Path) -> None:
        skills = load_skills(tmp_skills_dir)
        status = next(s for s in skills if s.name == "dmx-status")
        assert "in-progress tickets" in status.body

    def test_frozen_dataclass_is_immutable(self, tmp_skills_dir: Path) -> None:
        skills = load_skills(tmp_skills_dir)
        skill = skills[0]
        with pytest.raises((TypeError, AttributeError)):
            skill.name = "modified"  # type: ignore[misc]

    def test_malformed_frontmatter_is_skipped(self, tmp_path: Path) -> None:
        d = tmp_path / "skills"
        d.mkdir()
        (d / "bad-skill.md").write_text("---\n: bad yaml: [unclosed\n---\nbody\n", encoding="utf-8")
        # Bad files are skipped with a warning; the directory load should not raise.
        skills = load_skills(d)
        assert skills == ()


class TestLoadRules:
    def test_loads_rule(self, tmp_rules_dir: Path) -> None:
        rules = load_rules(tmp_rules_dir)
        assert len(rules) == 1
        rule = rules[0]
        assert rule.name == "system-prompt"
        assert rule.always_apply is True

    def test_returns_tuple(self, tmp_rules_dir: Path) -> None:
        result = load_rules(tmp_rules_dir)
        assert isinstance(result, tuple)

    def test_empty_directory_returns_empty_tuple(self, tmp_path: Path) -> None:
        empty = tmp_path / "rules"
        empty.mkdir()
        assert load_rules(empty) == ()

    def test_rule_body_is_stored(self, tmp_rules_dir: Path) -> None:
        rules = load_rules(tmp_rules_dir)
        assert "staff-level pair programmer" in rules[0].body

    def test_frozen_rule_is_immutable(self, tmp_rules_dir: Path) -> None:
        rules = load_rules(tmp_rules_dir)
        with pytest.raises((TypeError, AttributeError)):
            rules[0].name = "modified"  # type: ignore[misc]


class TestSubstituteArgs:
    def test_single_substitution(self) -> None:
        result = substitute_args("Hello {{name}}!", {"name": "world"})
        assert result == "Hello world!"

    def test_multiple_substitutions(self) -> None:
        result = substitute_args("{{a}} and {{b}}", {"a": "foo", "b": "bar"})
        assert result == "foo and bar"

    def test_missing_key_resolves_to_empty_string(self) -> None:
        result = substitute_args("Hello {{missing}}!", {})
        assert result == "Hello !"

    def test_none_value_resolves_to_empty_string(self) -> None:
        result = substitute_args("Hello {{name}}!", {"name": None})
        assert result == "Hello !"

    def test_no_placeholders_returns_unchanged(self) -> None:
        template = "No placeholders here."
        assert substitute_args(template, {"x": "y"}) == template

    def test_whitespace_in_placeholder_name(self) -> None:
        result = substitute_args("{{ name }}", {"name": "trimmed"})
        assert result == "trimmed"

    def test_empty_template_returns_empty(self) -> None:
        assert substitute_args("", {"a": "b"}) == ""

    def test_repeated_placeholder(self) -> None:
        result = substitute_args("{{x}} {{x}}", {"x": "hi"})
        assert result == "hi hi"


class TestSkillDefinitionDataClass:
    def test_default_arguments_is_empty_tuple(self) -> None:
        skill = SkillDefinition(name="x", title="X", description="desc")
        assert skill.arguments == ()

    def test_default_body_is_empty_string(self) -> None:
        skill = SkillDefinition(name="x", title="X", description="desc")
        assert skill.body == ""

    def test_arguments_stored_as_tuple(self) -> None:
        arg = SkillArgument(name="a")
        skill = SkillDefinition(name="x", title="X", description="d", arguments=(arg,))
        assert isinstance(skill.arguments, tuple)
        assert skill.arguments[0].name == "a"


class TestRuleDefinitionDataClass:
    def test_always_apply_defaults_to_true(self) -> None:
        rule = RuleDefinition(name="r", title="R")
        assert rule.always_apply is True

    def test_ides_defaults_to_empty_tuple(self) -> None:
        rule = RuleDefinition(name="r", title="R")
        assert rule.ides == ()

    def test_globs_defaults_to_none(self) -> None:
        rule = RuleDefinition(name="r", title="R")
        assert rule.globs is None

    def test_ides_scalar_string_is_normalised_to_list(self, tmp_path: Path) -> None:
        """A YAML scalar `ides: cursor` must parse as ("cursor",), not individual chars."""
        d = tmp_path / "rules"
        d.mkdir()
        (d / "my-rule.md").write_text("---\ntitle: R\nides: cursor\n---\nbody\n", encoding="utf-8")
        rules = load_rules(d)
        assert rules[0].ides == ("cursor",)

    def test_ides_list_parses_correctly(self, tmp_path: Path) -> None:
        d = tmp_path / "rules"
        d.mkdir()
        (d / "my-rule.md").write_text(
            "---\ntitle: R\nides:\n  - cursor\n  - claude\n---\nbody\n",
            encoding="utf-8",
        )
        rules = load_rules(d)
        assert set(rules[0].ides) == {"cursor", "claude"}


class TestBundledSkills:
    def test_all_bundled_skills_load(self) -> None:
        """Every skill in src/dmx/skills/ must parse without error."""
        import importlib.resources as pkg
        from pathlib import Path as _Path

        skills_dir = _Path(str(pkg.files("dmx") / "skills"))
        skills = load_skills(skills_dir)
        assert len(skills) == 23, (
            f"Expected 23 bundled skills but got {len(skills)}. "
            "A skill file may have broken YAML frontmatter."
        )


class TestBranchRoleSkillContent:
    """Bundled skills must use config branch roles — not hardcoded master git refs."""

    @pytest.fixture()
    def bundled_skill_bodies(self) -> dict[str, str]:
        import importlib.resources as pkg
        from pathlib import Path as _Path

        skills_dir = _Path(str(pkg.files("dmx") / "skills"))
        return {skill.name: skill.body for skill in load_skills(skills_dir)}

    def test_create_pr_hotfix_base_auto_detect(self, bundled_skill_bodies: dict[str, str]) -> None:
        body = bundled_skill_bodies["create-pr"]
        assert "spec frontmatter `type` is `hotfix`" in body
        assert "contains `**Type:** hotfix`" in body
        assert "**Resolve `production_branch` when needed**" in body
        assert "do not override an explicit argument" in body

    def test_hotfix_spec_includes_type_in_frontmatter(
        self, bundled_skill_bodies: dict[str, str]
    ) -> None:
        body = bundled_skill_bodies["hotfix"]
        assert "type: hotfix" in body

    def test_release_merge_targets_production_branch(
        self, bundled_skill_bodies: dict[str, str]
    ) -> None:
        body = bundled_skill_bodies["release-merge"]
        assert "base:                  {config.production_branch}" in body
        assert "base:                  master" not in body

    def test_create_release_tags_production_branch(
        self, bundled_skill_bodies: dict[str, str]
    ) -> None:
        body = bundled_skill_bodies["create-release"]
        assert "git checkout {config.production_branch}" in body
        assert "--target {config.production_branch}" in body
        assert "git checkout master" not in body

    def test_hotfix_branches_from_production_branch(
        self, bundled_skill_bodies: dict[str, str]
    ) -> None:
        body = bundled_skill_bodies["hotfix"]
        assert "from_branch: {config.production_branch}" in body
        assert "from_branch: master" not in body

    def test_no_hardcoded_master_git_refs_in_branch_role_skills(
        self, bundled_skill_bodies: dict[str, str]
    ) -> None:
        forbidden = (
            "base:                  master",
            "from_branch: master",
            "git checkout master",
            "--target master",
            "head:   master",
        )
        for skill_name in (
            "create-pr",
            "release-merge",
            "create-release",
            "hotfix",
            "close-ticket",
        ):
            body = bundled_skill_bodies[skill_name]
            for pattern in forbidden:
                assert pattern not in body, (
                    f"{skill_name} contains hardcoded master git ref: {pattern!r}"
                )


class TestSystemPromptBranchRoles:
    """Always-apply system prompt must describe config branch roles in the commands table."""

    @pytest.fixture()
    def system_prompt_body(self) -> str:
        import importlib.resources as pkg
        from pathlib import Path as _Path

        rules_dir = _Path(str(pkg.files("dmx") / "rules"))
        rules = load_rules(rules_dir)
        rule = next(r for r in rules if r.name == "system-prompt")
        return rule.body

    def test_hotfix_command_uses_production_branch(self, system_prompt_body: str) -> None:
        assert "| `/dmx/hotfix` | Branch from `production_branch`" in system_prompt_body

    def test_release_commands_use_branch_roles(self, system_prompt_body: str) -> None:
        assert "`branch_base` → `production_branch`" in system_prompt_body
        assert "Tag `production_branch`" in system_prompt_body

    def test_sync_branch_uses_integration_branch(self, system_prompt_body: str) -> None:
        assert "Rebase onto latest integration branch (`branch_base`)" in system_prompt_body
        assert "Rebase onto latest base branch" not in system_prompt_body
