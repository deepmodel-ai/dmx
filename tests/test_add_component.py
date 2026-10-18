"""GH-87: ``/dmx/add-component <path>`` makes one folder a component."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastmcp import Client

from dmx.components import load_components
from dmx.server import create_app
from dmx.workspaces import add_component, plan_component


def _fresh(root: Path) -> Path:
    shutil.rmtree(root / ".dmx" / "components")
    return root


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestLanguages:
    @pytest.mark.parametrize(
        ("path", "component_id", "language", "description"),
        [
            ("services/ledger", "ledger", "go", "Go package example.com/mono/services/ledger"),
            (
                "services/payments",
                "payments",
                "go",
                "Go module example.com/mono/services/payments",
            ),
            ("packages/billing-ui", "billing-ui", "node", "Billing dashboard"),
            ("libs/pyutils", "pyutils", "python", "Shared Python helpers"),
            ("crates/hasher", "hasher", "rust", "Stable content hashes"),
        ],
    )
    def test_one_command_per_language(
        self,
        reference_monorepo: Path,
        path: str,
        component_id: str,
        language: str,
        description: str,
    ) -> None:
        root = _fresh(reference_monorepo)

        plan = add_component(root, path)

        assert plan.blocked is None
        assert (plan.id, plan.language, plan.description) == (component_id, language, description)
        assert plan.has_default_test is True
        assert plan.written == f".dmx/components/{component_id}/component.yaml"
        component = load_components(root).get(component_id)
        assert component is not None
        assert component.paths == (f"{path}/**",)
        assert component.test is None
        assert plan.codeowners == f"/.dmx/components/{component_id}/".ljust(32) + (
            f"@<{component_id}-team>"
        )

    def test_another_language_is_named_after_its_folder(self, reference_monorepo: Path) -> None:
        root = _fresh(reference_monorepo)

        plan = add_component(root, "tools/lua-lint")

        assert (plan.id, plan.language) == ("lua-lint", "other")
        assert plan.description == "lua-lint (tools/lua-lint)"
        assert plan.has_default_test is False
        text = (root / ".dmx/components/lua-lint/component.yaml").read_text(encoding="utf-8")
        assert "# test:\n#   cmd: <your test command>\n#   cwd: tools/lua-lint\n" in text
        component = load_components(root).get("lua-lint")
        assert component is not None
        assert component.test is None

    def test_a_trailing_slash_or_absolute_path(self, reference_monorepo: Path) -> None:
        root = _fresh(reference_monorepo)

        assert plan_component(root, "crates/hasher/").path == "crates/hasher"
        assert plan_component(root, str(root / "crates" / "hasher")).path == "crates/hasher"

    def test_a_go_folder_without_go_files_is_another_language(
        self, reference_monorepo: Path
    ) -> None:
        root = _fresh(reference_monorepo)
        _write(root, "docs/guide/index.md", "# Guide\n")

        assert plan_component(root, "docs/guide").language == "other"


class TestIdsAndDescriptions:
    def test_the_developers_id_and_description(self, reference_monorepo: Path) -> None:
        root = _fresh(reference_monorepo)

        plan = add_component(root, "services/payments", component_id="pay", description="Pay: v2")

        assert plan.id == "pay"
        component = load_components(root).get("pay")
        assert component is not None
        assert component.description == "Pay: v2"

    def test_a_taken_id_asks_for_another(self, reference_monorepo: Path) -> None:
        _write(reference_monorepo, "apps/payments/package.json", '{"name": "payments"}')

        plan = add_component(reference_monorepo, "apps/payments")

        assert plan.blocked == (
            "`payments` is already a component. Pick another id, such as `apps-payments`."
        )
        assert plan.suggested_id == "apps-payments"
        assert plan.written is None

    def test_a_folder_with_a_broken_file_counts_as_taken(self, reference_monorepo: Path) -> None:
        root = _fresh(reference_monorepo)
        _write(root, ".dmx/components/hasher/component.yaml", "paths: [x]\n")

        plan = add_component(root, "crates/hasher")

        assert plan.blocked is not None
        assert "`hasher` is already a component" in plan.blocked
        assert (root / ".dmx/components/hasher/component.yaml").read_text(
            encoding="utf-8"
        ) == "paths: [x]\n"

    def test_an_invalid_id(self, reference_monorepo: Path) -> None:
        plan = plan_component(_fresh(reference_monorepo), "crates/hasher", component_id="Hash Er")

        assert plan.blocked is not None
        assert "not a valid component id" in plan.blocked


class TestPaths:
    @pytest.mark.parametrize(
        ("path", "reason"),
        [
            ("services/missing", "is not a folder in this repo"),
            ("go.mod", "is not a folder in this repo"),
            (".", "the repo root holds the repo-wide files"),
            ("", "the repo root holds the repo-wide files"),
            (".dmx/components", "is dmx's own folder"),
            ("../elsewhere", "is outside this repo"),
        ],
    )
    def test_refused(self, reference_monorepo: Path, path: str, reason: str) -> None:
        (reference_monorepo.parent / "elsewhere").mkdir(exist_ok=True)

        plan = plan_component(reference_monorepo, path)

        assert plan.blocked is not None
        assert reason in plan.blocked

    def test_the_same_paths_as_an_existing_component(self, reference_monorepo: Path) -> None:
        plan = plan_component(reference_monorepo, "services/ledger", component_id="ledger2")

        assert plan.blocked == "`ledger` already covers exactly `services/ledger/**`."

    def test_a_folder_inside_a_component_is_carved_out(self, reference_monorepo: Path) -> None:
        _write(reference_monorepo, "services/payments/sdk/client.go", "package sdk\n")

        plan = add_component(reference_monorepo, "services/payments/sdk")

        assert plan.blocked is None
        assert (plan.id, plan.language) == ("sdk", "go")
        assert plan.carved_from == "payments"
        owner = load_components(reference_monorepo).component_for("services/payments/sdk/client.go")
        assert owner is not None
        assert owner.id == "sdk"


class TestTool:
    @pytest.mark.asyncio
    async def test_dry_run_then_apply(self, reference_monorepo: Path) -> None:
        root = _fresh(reference_monorepo)

        async with Client(create_app()) as client:
            planned = await client.call_tool(
                "add_component", {"path": "tools/lua-lint", "workspace_root": str(root)}
            )
            assert not (root / ".dmx/components/lua-lint").exists()
            applied = await client.call_tool(
                "add_component",
                {"path": "tools/lua-lint", "dry_run": False, "workspace_root": str(root)},
            )

        assert planned.data["applied"] is False
        assert planned.data["id"] == "lua-lint"
        assert planned.data["has_default_test"] is False
        assert planned.data["component_yaml"].startswith(
            'description: lua-lint (tools/lua-lint)\npaths: ["tools/lua-lint/**"]\n'
        )
        assert applied.data["applied"] is True
        assert applied.data["written"] == ".dmx/components/lua-lint/component.yaml"

    @pytest.mark.asyncio
    async def test_a_refusal_is_reported(self, reference_monorepo: Path) -> None:
        async with Client(create_app()) as client:
            result = await client.call_tool(
                "add_component",
                {"path": ".", "dry_run": False, "workspace_root": str(reference_monorepo)},
            )

        assert result.data["applied"] is False
        assert "repo root" in result.data["blocked"]


class TestSkill:
    def test_the_skill_plans_asks_applies_and_offers_the_memory_move(self) -> None:
        skill = (
            Path(__file__).parent.parent / "src/dmx/skills/utility/dmx-add-component.md"
        ).read_text(encoding="utf-8")

        assert "name: add-component" in skill
        assert "Call `add_component`" in skill
        assert "`dry_run` false" in skill
        assert "`suggested_id`" in skill
        assert "Show the move as a diff" in skill
        assert "`codeowners`" in skill
        assert "fill in `test: {cmd: ..., cwd: ...}`" in skill
        assert "runs the test paths its `paths` name" in skill
