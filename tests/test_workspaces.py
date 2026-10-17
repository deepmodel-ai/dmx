"""GH-86: ``/dmx/init`` finds components in pnpm, npm, yarn, go.work, uv and Cargo workspaces."""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client

from dmx.components import load_components
from dmx.server import create_app
from dmx.workspaces import apply_detected_components, detect_components

if TYPE_CHECKING:
    from pathlib import Path


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _package(root: Path, folder: str, name: str, description: str | None = None) -> None:
    manifest: dict[str, str] = {"name": name}
    if description:
        manifest["description"] = description
    _write(root, f"{folder}/package.json", json.dumps(manifest))


def _fresh(reference_monorepo: Path) -> Path:
    """The reference monorepo before anyone declared components."""
    shutil.rmtree(reference_monorepo / ".dmx" / "components")
    return reference_monorepo


def _found(root: Path) -> list[tuple[str, str, str]]:
    return [(c.id, c.path, c.workspace) for c in detect_components(root).components]


class TestReferenceMonorepo:
    def test_every_workspace_kind(self, reference_monorepo: Path) -> None:
        root = _fresh(reference_monorepo)

        detected = detect_components(root)

        assert detected.workspaces == ("go.work", "pnpm", "uv", "cargo")
        assert [(c.id, c.path, c.workspace, c.language) for c in detected.components] == [
            ("billing-ui", "packages/billing-ui", "pnpm", "node"),
            ("hasher", "crates/hasher", "cargo", "rust"),
            ("payments", "services/payments", "go.work", "go"),
            ("pyutils", "libs/pyutils", "uv", "python"),
        ]
        assert detected.ask is True

    def test_descriptions_come_from_the_manifests(self, reference_monorepo: Path) -> None:
        by_id = {c.id: c for c in detect_components(_fresh(reference_monorepo)).components}

        assert by_id["billing-ui"].description == "Billing dashboard"
        assert by_id["pyutils"].description == "Shared Python helpers"
        assert by_id["hasher"].description == "Stable content hashes"
        assert by_id["payments"].description == "Go module example.com/mono/services/payments"

    def test_the_root_module_is_never_a_component(self, reference_monorepo: Path) -> None:
        paths = [c.path for c in detect_components(_fresh(reference_monorepo)).components]

        assert "." not in paths
        assert "services/ledger" not in paths

    def test_applying_writes_loadable_component_folders(self, reference_monorepo: Path) -> None:
        root = _fresh(reference_monorepo)

        result = apply_detected_components(root, ids=["payments", "hasher"])

        assert result.written == (
            ".dmx/components/hasher/component.yaml",
            ".dmx/components/payments/component.yaml",
        )
        loaded = load_components(root)
        assert loaded.ids == ("hasher", "payments")
        assert loaded.skipped == ()
        payments = loaded.get("payments")
        assert payments is not None
        assert payments.paths == ("services/payments/**",)
        assert payments.depends_on == ()
        assert payments.test is None
        assert result.codeowners == (
            "/.dmx/                          @<platform-team>",
            "/.dmx/components/hasher/        @<hasher-team>",
            "/.dmx/components/payments/      @<payments-team>",
            "/.dmx/jobs/",
        )

    def test_existing_components_are_not_offered_again(self, reference_monorepo: Path) -> None:
        detected = detect_components(reference_monorepo)

        assert detected.components == ()
        assert detected.ask is False

    @pytest.mark.parametrize(
        "existing",
        ["description: mine\npaths: [x]\n", "paths: [x]\n"],
        ids=["valid", "broken"],
    )
    def test_an_existing_folder_is_never_overwritten(
        self, reference_monorepo: Path, existing: str
    ) -> None:
        root = _fresh(reference_monorepo)
        _write(root, ".dmx/components/hasher/component.yaml", existing)

        result = apply_detected_components(root)

        assert ".dmx/components/crates-hasher/component.yaml" in result.written
        assert (root / ".dmx/components/hasher/component.yaml").read_text(
            encoding="utf-8"
        ) == existing


class TestNode:
    def test_npm_workspaces(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", json.dumps({"workspaces": ["apps/*", "libs/ui"]}))
        _package(tmp_path, "apps/web", "web", "Storefront")
        _package(tmp_path, "apps/admin", "@acme/admin")
        _package(tmp_path, "libs/ui", "@acme/ui")

        assert _found(tmp_path) == [
            ("admin", "apps/admin", "npm"),
            ("ui", "libs/ui", "npm"),
            ("web", "apps/web", "npm"),
        ]

    def test_yarn_is_told_apart_by_its_lock_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", json.dumps({"workspaces": {"packages": ["pkgs/*"]}}))
        _write(tmp_path, "yarn.lock", "")
        _package(tmp_path, "pkgs/a", "a")
        _package(tmp_path, "pkgs/b", "b")

        assert {workspace for _id, _path, workspace in _found(tmp_path)} == {"yarn"}

    def test_pnpm_negated_patterns(self, tmp_path: Path) -> None:
        _write(
            tmp_path, "pnpm-workspace.yaml", "packages:\n  - 'packages/*'\n  - '!packages/old'\n"
        )
        _write(tmp_path, "package.json", json.dumps({"workspaces": ["ignored/*"]}))
        for name in ("a", "b", "old"):
            _package(tmp_path, f"packages/{name}", name)
        _package(tmp_path, "ignored/c", "c")

        assert _found(tmp_path) == [("a", "packages/a", "pnpm"), ("b", "packages/b", "pnpm")]

    def test_a_folder_without_a_manifest_is_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", json.dumps({"workspaces": ["packages/*"]}))
        _package(tmp_path, "packages/a", "a")
        _package(tmp_path, "packages/b", "b")
        (tmp_path / "packages" / "docs").mkdir()
        _package(tmp_path, "packages/a/node_modules/dep", "dep")

        assert [path for _id, path, _ws in _found(tmp_path)] == ["packages/a", "packages/b"]


class TestGoPythonRust:
    def test_go_work_single_and_block_use(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "go.work",
            'go 1.22\n\nuse ./tools/gen // generators\n\nuse (\n\t./svc/a\n\t"./svc/b"\n)\n',
        )
        for folder, module in (("tools/gen", "gen"), ("svc/a", "x.io/svc/a"), ("svc/b", "b")):
            _write(tmp_path, f"{folder}/go.mod", f"module {module}\n\ngo 1.22\n")

        assert _found(tmp_path) == [
            ("a", "svc/a", "go.work"),
            ("b", "svc/b", "go.work"),
            ("gen", "tools/gen", "go.work"),
        ]

    def test_uv_members_and_exclude(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "pyproject.toml",
            '[tool.uv.workspace]\nmembers = ["libs/*"]\nexclude = ["libs/scratch"]\n',
        )
        for name in ("core", "cli", "scratch"):
            _write(tmp_path, f"libs/{name}/pyproject.toml", f'[project]\nname = "acme-{name}"\n')

        assert _found(tmp_path) == [
            ("acme-cli", "libs/cli", "uv"),
            ("acme-core", "libs/core", "uv"),
        ]

    def test_cargo_members_and_exclude(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "Cargo.toml",
            '[workspace]\nmembers = ["crates/*", "bins/tool"]\nexclude = ["crates/wip"]\n',
        )
        for folder, name in (
            ("crates/parse", "parse"),
            ("crates/wip", "wip"),
            ("bins/tool", "tool"),
        ):
            _write(tmp_path, f"{folder}/Cargo.toml", f'[package]\nname = "{name}"\n')

        assert _found(tmp_path) == [
            ("parse", "crates/parse", "cargo"),
            ("tool", "bins/tool", "cargo"),
        ]

    def test_a_broken_manifest_does_not_stop_detection(self, tmp_path: Path) -> None:
        _write(tmp_path, "Cargo.toml", "[workspace\nmembers = [")
        _write(tmp_path, "package.json", json.dumps({"workspaces": ["p/*"]}))
        _package(tmp_path, "p/a", "a")
        _package(tmp_path, "p/b", "b")
        _write(tmp_path, "p/c/package.json", "{not json")

        # A member whose manifest can't be read is named after its folder.
        assert [component_id for component_id, _p, _w in _found(tmp_path)] == ["a", "b", "c"]

    def test_a_pattern_outside_the_repo_is_ignored(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _package(tmp_path, "sibling", "sibling")
        _write(repo, "package.json", json.dumps({"workspaces": ["../*", "p/*"]}))
        _package(repo, "p/a", "a")

        assert _found(repo) == [("a", "p/a", "npm")]


class TestIds:
    def test_a_clash_uses_the_folder_paths(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", json.dumps({"workspaces": ["apps/*", "services/*"]}))
        _package(tmp_path, "apps/api", "api")
        _package(tmp_path, "services/api", "@acme/api")
        _package(tmp_path, "services/web", "web")

        assert _found(tmp_path) == [
            ("apps-api", "apps/api", "npm"),
            ("services-api", "services/api", "npm"),
            ("web", "services/web", "npm"),
        ]

    def test_an_id_already_taken_by_a_component(self, tmp_path: Path) -> None:
        _write(tmp_path, ".dmx/components/web/component.yaml", "description: W\npaths: [site]\n")
        _write(tmp_path, "package.json", json.dumps({"workspaces": ["apps/*"]}))
        _package(tmp_path, "apps/web", "web")
        _package(tmp_path, "apps/api", "api")

        assert [component_id for component_id, _p, _w in _found(tmp_path)] == ["api", "apps-web"]

    def test_names_become_valid_ids(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", json.dumps({"workspaces": ["p/*"]}))
        _package(tmp_path, "p/one", "@Acme/Billing_UI.v2")
        _package(tmp_path, "p/two", "two")

        assert _found(tmp_path)[0][0] == "billing_ui-v2"


class TestWhenToAsk:
    def test_a_single_product_repo_is_not_asked(self, tmp_path: Path) -> None:
        _write(tmp_path, "go.mod", "module example.com/app\n")
        _write(tmp_path, "package.json", json.dumps({"name": "app"}))

        detected = detect_components(tmp_path)

        assert detected.workspaces == ()
        assert detected.components == ()
        assert detected.ask is False

    def test_a_workspace_with_one_member_is_not_asked(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", json.dumps({"workspaces": ["packages/*"]}))
        _package(tmp_path, "packages/only", "only")

        detected = detect_components(tmp_path)

        assert len(detected.components) == 1
        assert detected.ask is False


class TestTool:
    @pytest.mark.asyncio
    async def test_plan_then_apply(self, reference_monorepo: Path) -> None:
        root = _fresh(reference_monorepo)

        async with Client(create_app()) as client:
            planned = await client.call_tool("detect_components", {"workspace_root": str(root)})
            applied = await client.call_tool(
                "detect_components",
                {"workspace_root": str(root), "apply": True, "ids": ["billing-ui"]},
            )

        assert planned.data["ask"] is True
        assert [c["id"] for c in planned.data["components"]] == [
            "billing-ui",
            "hasher",
            "payments",
            "pyutils",
        ]
        first = planned.data["components"][0]
        assert first["component_yaml"] == (
            'description: Billing dashboard\npaths: ["packages/billing-ui/**"]\n'
        )
        assert not (root / ".dmx/components/hasher").exists()
        assert applied.data["written"] == [".dmx/components/billing-ui/component.yaml"]
        assert applied.data["unknown_ids"] == []
        assert load_components(root).ids == ("billing-ui",)

    @pytest.mark.asyncio
    async def test_an_unknown_id_is_reported(self, reference_monorepo: Path) -> None:
        root = _fresh(reference_monorepo)

        async with Client(create_app()) as client:
            applied = await client.call_tool(
                "detect_components",
                {"workspace_root": str(root), "apply": True, "ids": ["ledgr"]},
            )

        assert applied.data["written"] == []
        assert applied.data["unknown_ids"] == ["ledgr"]


class TestInitSkill:
    def test_init_asks_only_in_a_workspace_and_never_guesses_dependencies(self) -> None:
        from pathlib import Path

        skill = (
            Path(__file__).parent.parent / "src/dmx/skills/workflow/0-init/dmx-init.md"
        ).read_text(encoding="utf-8")
        step = skill.split("## Step 9 — Components (monorepos only)", 1)[1].split("## Step 10", 1)[
            0
        ]

        assert "Call `detect_components`" in step
        assert "**If `ask` is false**, skip this step without saying anything." in step
        assert "`apply` true and `ids`" in step
        assert "dmx does not guess dependencies" in step
        assert "`codeowners`" in step
        assert "Show the move as a diff" in step
        assert "## Step 10 — Output the result" in skill
