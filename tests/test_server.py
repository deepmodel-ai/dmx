"""Tests for dmx.server — create_app factory and skill handler."""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING

from dmx.catalog import SkillArgument, SkillDefinition
from dmx.server import _build_skill_handler, create_app

if TYPE_CHECKING:
    from pathlib import Path


class TestCreateApp:
    """Integration tests for the create_app() factory."""

    def test_registers_all_bundled_skills(self) -> None:
        app = create_app()
        prompts = asyncio.run(app.list_prompts())
        assert len(prompts) == 23

    def test_all_prompt_names_are_valid_slugs(self) -> None:
        import re  # noqa: PLC0415

        slug_re = re.compile(r"^[a-z0-9_-]+$")
        app = create_app()
        prompts = asyncio.run(app.list_prompts())
        for p in prompts:
            assert slug_re.match(p.name), f"{p.name!r} is not a valid slug"

    def test_parameterized_prompt_exposes_arguments(self) -> None:
        app = create_app()
        prompts = asyncio.run(app.list_prompts())
        # commit has 5 arguments; verify at least 1 is registered
        commit = next(p for p in prompts if p.name == "commit")
        assert commit.arguments is not None
        assert len(commit.arguments) >= 1

    def test_zero_arg_prompt_registers(self) -> None:
        app = create_app()
        prompts = asyncio.run(app.list_prompts())
        init = next((p for p in prompts if p.name == "init"), None)
        assert init is not None
        assert not init.arguments  # no arguments

    def test_tools_are_registered(self) -> None:
        app = create_app()
        tools = asyncio.run(app.list_tools())
        names = {t.name for t in tools}
        assert "detect_invoking_ide" in names
        assert "setup_ide_rules" in names

    def test_custom_skills_and_rules_dir(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "dmx-custom.md").write_text(
            "---\ntitle: Custom\ndescription: A custom skill.\n---\n\nHello!\n",
            encoding="utf-8",
        )
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()

        app = create_app(skills_dir=skills_dir, rules_dir=rules_dir)
        prompts = asyncio.run(app.list_prompts())
        assert len(prompts) == 1
        assert prompts[0].name == "dmx-custom"

    def test_prompts_sorted_by_name(self) -> None:
        app = create_app()
        prompts = asyncio.run(app.list_prompts())
        names = [p.name for p in prompts]
        assert names == sorted(names)


class TestBuildSkillHandler:
    """Unit tests for _build_skill_handler — the explicit-signature factory."""

    def _skill(
        self,
        name: str = "dmx-test",
        body: str = "Hello {{greeting}}!",
        args: list[tuple[str, bool]] | None = None,
    ) -> SkillDefinition:
        arguments = tuple(
            SkillArgument(name=n, required=req) for n, req in (args or [("greeting", False)])
        )
        return SkillDefinition(
            name=name, title="Test", description="d", arguments=arguments, body=body
        )

    def test_optional_arg_is_substituted(self) -> None:
        handler = _build_skill_handler(self._skill())
        result = asyncio.run(handler(greeting="world"))
        assert result == "Hello world!"

    def test_missing_optional_arg_resolves_to_empty_string(self) -> None:
        handler = _build_skill_handler(self._skill())
        result = asyncio.run(handler(greeting=None))
        assert result == "Hello !"

    def test_multiple_args_all_substituted(self) -> None:
        skill = self._skill(body="{{a}} and {{b}}", args=[("a", False), ("b", False)])
        handler = _build_skill_handler(skill)
        result = asyncio.run(handler(a="foo", b="bar"))
        assert result == "foo and bar"

    def test_required_arg_has_no_default_in_signature(self) -> None:
        skill = self._skill(args=[("greeting", True)])
        handler = _build_skill_handler(skill)
        sig = inspect.signature(handler)
        assert sig.parameters["greeting"].default is inspect.Parameter.empty

    def test_optional_arg_has_none_default_in_signature(self) -> None:
        skill = self._skill(args=[("greeting", False)])
        handler = _build_skill_handler(skill)
        sig = inspect.signature(handler)
        assert sig.parameters["greeting"].default is None

    def test_signature_contains_no_var_keyword(self) -> None:
        """FastMCP rejects **kwargs; verify the exposed signature has none."""
        handler = _build_skill_handler(self._skill())
        sig = inspect.signature(handler)
        kinds = {p.kind for p in sig.parameters.values()}
        assert inspect.Parameter.VAR_KEYWORD not in kinds

    def test_annotations_match_signature_names(self) -> None:
        skill = self._skill(args=[("greeting", False), ("name", False)])
        handler = _build_skill_handler(skill)
        sig = inspect.signature(handler)
        annotations = getattr(handler, "__annotations__", {})
        for param_name in sig.parameters:
            assert param_name in annotations, f"{param_name!r} missing from __annotations__"

    def test_handler_name_equals_skill_name(self) -> None:
        skill = self._skill(name="dmx-custom", args=[("x", False)])
        handler = _build_skill_handler(skill)
        assert handler.__name__ == "dmx-custom"

    def test_body_captured_per_skill(self) -> None:
        """Verify closure captures the right body for each skill (no late-binding bug)."""
        skills = [
            self._skill(name=f"dmx-s{i}", body=f"skill {i} {{{{x}}}}", args=[("x", False)])
            for i in range(3)
        ]
        handlers = [_build_skill_handler(s) for s in skills]
        for i, h in enumerate(handlers):
            assert asyncio.run(h(x="v")) == f"skill {i} v"

    def test_return_type_annotation_is_str(self) -> None:
        handler = _build_skill_handler(self._skill())
        sig = inspect.signature(handler)
        assert sig.return_annotation is str
