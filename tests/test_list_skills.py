"""list_skills lists the skill get_skill_definition would load, and no other."""

from __future__ import annotations

import importlib.resources as pkg
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client

from dmx.loop_tools import _resolve_skill, list_skills
from dmx.server import create_app

if TYPE_CHECKING:
    from pathlib import Path


def _write(root: Path, relative: str, body: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _sources(root: Path, sources: list[tuple[str, str]]) -> None:
    lines = ["shared_sources:"]
    for name, source in sources:
        lines.append(f"  - name: {name}")
        lines.append(f'    source: "{source}"')
    _write(root, ".dmx/shared-sources.yaml", "\n".join(lines) + "\n")


def _skill(description: str | None, body: str = "Do the thing.\n") -> str:
    if description is None:
        return body
    return f"---\ndescription: {description}\n---\n\n{body}"


class TestListSkills:
    def test_empty_catalog(self, tmp_path: Path) -> None:
        assert list_skills(tmp_path) == "No local or shared skills."

    def test_vendored_flat_skill_is_listed_and_loadable(self, tmp_path: Path) -> None:
        _sources(tmp_path, [("acme", "git::https://x//?ref=v1")])
        _write(
            tmp_path,
            ".dmx/vendor/acme/skills/helm_update.md",
            _skill("Update Helm charts", "Run the helm update.\n"),
        )

        listed = list_skills(tmp_path)
        assert listed == "helm_update (acme): Update Helm charts"
        resolved = _resolve_skill("helm_update", tmp_path)
        assert resolved is not None
        assert "Run the helm update." in resolved.raw

    def test_vendored_folder_skill_is_listed_and_loadable(self, tmp_path: Path) -> None:
        _sources(tmp_path, [("acme", "git::https://x//?ref=v1")])
        _write(
            tmp_path,
            ".dmx/vendor/acme/skills/helm_update/SKILL.md",
            _skill("Update Helm charts", "Run the folder skill.\n"),
        )

        assert list_skills(tmp_path) == "helm_update (acme): Update Helm charts"
        resolved = _resolve_skill("helm_update", tmp_path)
        assert resolved is not None
        assert "Run the folder skill." in resolved.raw

    def test_app_repo_wins_and_the_shadowed_description_is_omitted(self, tmp_path: Path) -> None:
        _sources(tmp_path, [("acme", "git::https://x//?ref=v1")])
        _write(tmp_path, ".dmx/skills/helm_update.md", _skill("from the app"))
        _write(tmp_path, ".dmx/vendor/acme/skills/helm_update.md", _skill("from the source"))

        listed = list_skills(tmp_path)
        assert listed == "helm_update (app repo): from the app"
        assert "from the source" not in listed

    def test_dmx_prefix_and_folder_shape_collapse_to_one_name(self, tmp_path: Path) -> None:
        _sources(tmp_path, [("acme", "git::https://x//?ref=v1")])
        _write(tmp_path, ".dmx/skills/dmx-helm_update.md", _skill("prefixed flat"))
        _write(tmp_path, ".dmx/skills/helm_update/SKILL.md", _skill("app folder"))
        _write(tmp_path, ".dmx/vendor/acme/skills/helm_update/SKILL.md", _skill("shared folder"))

        listed = list_skills(tmp_path)
        assert listed == "helm_update (app repo): prefixed flat"
        assert "app folder" not in listed
        assert "shared folder" not in listed

    def test_declared_source_order_picks_the_winner(self, tmp_path: Path) -> None:
        _sources(
            tmp_path,
            [
                ("first", "git::https://x//?ref=v1"),
                ("second", "git::https://y//?ref=v1"),
            ],
        )
        _write(tmp_path, ".dmx/vendor/first/skills/helm_update.md", _skill("from first"))
        _write(tmp_path, ".dmx/vendor/second/skills/helm_update.md", _skill("from second"))

        assert list_skills(tmp_path) == "helm_update (first): from first"

    def test_missing_description_is_empty(self, tmp_path: Path) -> None:
        _write(tmp_path, ".dmx/skills/plain.md", "Just a body.\n")
        _write(tmp_path, ".dmx/skills/broken.md", "---\ndescription: [\n---\n\nbody\n")

        assert list_skills(tmp_path) == "broken (app repo):\nplain (app repo):"

    def test_description_may_contain_a_dash(self, tmp_path: Path) -> None:
        _sources(tmp_path, [("platform", "git::https://x//?ref=v1")])
        _write(
            tmp_path,
            ".dmx/vendor/platform/skills/helm_update.md",
            _skill("Bump the helm chart — and redeploy"),
        )

        listed = list_skills(tmp_path)
        assert listed == "helm_update (platform): Bump the helm chart — and redeploy"
        assert listed.split(": ", 1)[1] == "Bump the helm chart — and redeploy"

    def test_bundled_override_is_marked(self, tmp_path: Path) -> None:
        _write(tmp_path, ".dmx/skills/commit.md", _skill("Our own commit flow"))

        assert list_skills(tmp_path) == (
            "commit (app repo) "
            "(overrides bundled /dmx/commit for loops and get_skill_definition): "
            "Our own commit flow"
        )

    def test_invalid_name_is_not_listed(self, tmp_path: Path) -> None:
        _write(tmp_path, ".dmx/skills/-skip.md", _skill("secret skip"))
        _write(tmp_path, ".dmx/skills/ok.md", _skill("visible"))

        listed = list_skills(tmp_path)
        assert listed == "ok (app repo): visible"
        assert "secret skip" not in listed

    def test_bundled_skills_are_absent(self, tmp_path: Path) -> None:
        _write(tmp_path, ".dmx/skills/local_only.md", _skill("local"))
        listed = list_skills(tmp_path)
        assert "create-ticket" not in listed
        assert "implement-next-phase" not in listed
        assert listed == "local_only (app repo): local"

    def test_lines_are_sorted_by_name(self, tmp_path: Path) -> None:
        _write(tmp_path, ".dmx/skills/zeta.md", _skill("last"))
        _write(tmp_path, ".dmx/skills/alpha.md", _skill("first"))
        assert list_skills(tmp_path) == "alpha (app repo): first\nzeta (app repo): last"

    def test_malformed_shared_sources_returns_the_error_string(self, tmp_path: Path) -> None:
        _write(tmp_path, ".dmx/shared-sources.yaml", "shared_sources: nope\n")
        message = list_skills(tmp_path)
        assert message.startswith("Error reading .dmx/shared-sources.yaml:")
        assert "must be a list" in message


def _between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_skill_discovery_rule() -> None:
    rule = (pkg.files("dmx") / "rules/system-prompt.md").read_text(encoding="utf-8")
    assert "helm_update" not in rule
    discovery = _between(rule, "**Discovery question.**", "**Task request, no loop active.**")
    task = _between(rule, "**Task request, no loop active.**", "**During a loop.**")
    during = _between(rule, "**During a loop.**", "## Loop runtime")

    assert "name, description, and source" in discovery
    assert "I do not call `get_skill_definition`" in discovery
    assert "I do not run any skill" in discovery
    assert "slash commands" in discovery
    assert "command table" in discovery

    assert "`get_skill_definition`" in task
    assert "exact name" in task
    assert "which one to run" in task

    assert "listing" in during
    assert "does not consult the catalog" in during


class TestListSkillsTool:
    @pytest.mark.asyncio
    async def test_tool_lists_a_skill_and_get_skill_definition_loads_it(
        self, tmp_path: Path
    ) -> None:
        _sources(tmp_path, [("acme", "git::https://x//?ref=v1")])
        _write(
            tmp_path,
            ".dmx/vendor/acme/skills/helm_update.md",
            _skill("Update Helm charts", "Run the helm update.\n"),
        )

        app = create_app()
        async with Client(app) as client:
            listed = await client.call_tool("list_skills", {"workspace_root": str(tmp_path)})
            loaded = await client.call_tool(
                "get_skill_definition",
                {"workspace_root": str(tmp_path), "name": "helm_update"},
            )

        assert listed.data == "helm_update (acme): Update Helm charts"
        assert "Run the helm update." in loaded.data

    @pytest.mark.asyncio
    async def test_malformed_shared_sources_does_not_raise(self, tmp_path: Path) -> None:
        _write(tmp_path, ".dmx/shared-sources.yaml", "shared_sources: nope\n")

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool("list_skills", {"workspace_root": str(tmp_path)})

        assert str(result.data).startswith("Error reading .dmx/shared-sources.yaml:")
