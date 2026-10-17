"""GH-89: run each touched component's tests, plus the components that depend on them."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from dmx.component_tests import plan_tests
from dmx.components import load_components
from dmx.validators.run_tests import run
from dmx.workspaces import default_test_command

if TYPE_CHECKING:
    from pathlib import Path

_BRANCH = "feature-gh-9"
# CI sets this so a missing toolchain fails instead of skipping (#81).
_REQUIRE_TOOLCHAINS = os.environ.get("DMX_REQUIRE_TOOLCHAINS") == "1"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _touch(root: Path, *rels: str) -> None:
    for rel in rels:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n")


def _on_branch(root: Path) -> Path:
    _git(root, "checkout", "-q", "-b", _BRANCH)
    return root


def _component(root: Path, component_id: str, body: str) -> None:
    _write(root, f".dmx/components/{component_id}/component.yaml", body)


def _runs(root: Path) -> list[tuple[tuple[str, ...], str, str]]:
    return [(r.components, r.command, r.cwd) for r in plan_tests(root).runs]


def _need(tool: str) -> None:
    if shutil.which(tool) is None:
        if _REQUIRE_TOOLCHAINS:
            pytest.fail(f"`{tool}` is required in CI (DMX_REQUIRE_TOOLCHAINS=1)")
        pytest.skip(f"`{tool}` not installed")


class TestDefaultCommands:
    @pytest.mark.parametrize(
        ("component_id", "command", "cwd"),
        [
            ("ledger", "go test ./services/ledger/...", "."),
            ("payments", "go test ./...", "services/payments"),
            ("billing-ui", "pnpm --filter @acme/billing-ui test", "."),
            ("pyutils", "pytest -q", "libs/pyutils"),
            ("hasher", "cargo test -p hasher", "."),
        ],
    )
    def test_the_reference_monorepo(
        self, reference_monorepo: Path, component_id: str, command: str, cwd: str
    ) -> None:
        component = load_components(reference_monorepo).get(component_id)
        assert component is not None

        found = default_test_command(reference_monorepo, component)

        assert found is not None
        assert (" ".join(found.argv), found.cwd) == (command, cwd)

    def test_another_language_has_no_default(self, reference_monorepo: Path) -> None:
        component = load_components(reference_monorepo).get("lua-lint")
        assert component is not None

        assert default_test_command(reference_monorepo, component) is None

    @pytest.mark.parametrize(
        ("files", "command", "cwd"),
        [
            (
                {
                    "package.json": '{"workspaces": ["apps/*"]}',
                    "apps/web/package.json": '{"name": "web"}',
                },
                "npm test -w web",
                ".",
            ),
            (
                {
                    "package.json": '{"workspaces": ["apps/*"]}',
                    "yarn.lock": "",
                    "apps/web/package.json": '{"name": "web"}',
                },
                "yarn workspace web test",
                ".",
            ),
            (
                {"apps/web/package.json": '{"name": "web", "scripts": {"test": "jest"}}'},
                "npm test --silent",
                "apps/web",
            ),
            (
                {"uv.lock": "", "apps/web/pyproject.toml": '[project]\nname = "web"\n'},
                "uv run pytest -q",
                "apps/web",
            ),
            (
                {
                    "Cargo.toml": '[workspace]\nmembers = ["apps/*"]\n',
                    "apps/web/Cargo.toml": '[package]\nname = "web-crate"\n',
                },
                "cargo test -p web-crate",
                ".",
            ),
            (
                {"apps/web/Cargo.toml": '[package]\nname = "web"\n'},
                "cargo test",
                "apps/web",
            ),
        ],
        ids=["npm", "yarn", "standalone-npm", "uv", "cargo-member", "cargo-standalone"],
    )
    def test_each_form(self, tmp_path: Path, files: dict[str, str], command: str, cwd: str) -> None:
        for rel, text in files.items():
            _write(tmp_path, rel, text)
        _component(tmp_path, "web", "description: W\npaths: ['apps/web/**']\n")
        component = load_components(tmp_path).get("web")
        assert component is not None

        found = default_test_command(tmp_path, component)

        assert found is not None
        assert (" ".join(found.argv), found.cwd) == (command, cwd)

    def test_a_package_without_a_test_script_has_no_default(self, tmp_path: Path) -> None:
        _write(tmp_path, "apps/web/package.json", '{"name": "web"}')
        _component(tmp_path, "web", "description: W\npaths: ['apps/web/**']\n")
        component = load_components(tmp_path).get("web")
        assert component is not None

        assert default_test_command(tmp_path, component) is None

    def test_paths_starting_with_a_wildcard_have_no_default(self, tmp_path: Path) -> None:
        _write(tmp_path, "protos/go.mod", "module protos\n")
        _component(tmp_path, "protos", "description: P\npaths: ['**/*.proto']\n")
        component = load_components(tmp_path).get("protos")
        assert component is not None

        assert default_test_command(tmp_path, component) is None


class TestPlan:
    def test_a_ledger_change_runs_ledger_and_its_dependents(self, reference_monorepo: Path) -> None:
        root = _on_branch(reference_monorepo)
        _touch(root, "services/ledger/ledger.go")

        plan = plan_tests(root)

        assert _runs(root) == [
            (("ledger",), "go test ./services/ledger/...", "."),
            (("payments",), "go test ./...", "services/payments"),
        ]
        assert plan.reasons == {"ledger": "changed files", "payments": "depends on ledger"}
        assert plan.unconfigured == ()

    def test_a_payments_change_runs_payments_only(self, reference_monorepo: Path) -> None:
        root = _on_branch(reference_monorepo)
        _touch(root, "services/payments/payments.go")

        assert _runs(root) == [(("payments",), "go test ./...", "services/payments")]

    def test_a_dependency_chain_is_followed(self, reference_monorepo: Path) -> None:
        root = _on_branch(reference_monorepo)
        _component(
            root,
            "checkout",
            "description: C\npaths: ['services/checkout/**']\ndepends_on: [payments]\n"
            "test: {cmd: make test}\n",
        )
        _touch(root, "services/ledger/ledger.go")

        assert plan_tests(root).reasons == {
            "ledger": "changed files",
            "payments": "depends on ledger",
            "checkout": "depends on payments",
        }

    def test_a_cycle_ends(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-q", "-b", "main")
        _write(tmp_path, ".dmx/config.md", "branch_base: main\n")
        _component(
            tmp_path, "a", "description: A\npaths: [a]\ndepends_on: [b]\ntest: {cmd: 'true'}\n"
        )
        _component(
            tmp_path, "b", "description: B\npaths: [b]\ndepends_on: [a]\ntest: {cmd: 'true'}\n"
        )
        _touch(tmp_path, "a/x")

        assert set(plan_tests(tmp_path).reasons) == {"a", "b"}

    def test_a_go_mod_bump_without_test_command_runs_everything(
        self, reference_monorepo: Path
    ) -> None:
        root = _on_branch(reference_monorepo)
        _touch(root, "go.mod")

        plan = plan_tests(root)

        assert plan.repo_wide_files == ("go.mod",)
        assert {cid for run in plan.runs for cid in run.components} == {
            "billing-ui",
            "hasher",
            "ledger",
            "lua-lint",
            "payments",
            "pyutils",
        }
        assert set(plan.reasons.values()) == {"repo-wide change"}

    def test_a_lock_file_bump_with_test_command_runs_it(self, reference_monorepo: Path) -> None:
        root = _on_branch(reference_monorepo)
        _write(root, ".dmx/config.md", "branch_base: main\ntest_command: make test\n")
        _touch(root, "pnpm-lock.yaml", "services/payments/payments.go")

        assert _runs(root) == [
            (("payments",), "go test ./...", "services/payments"),
            (("repo-wide",), "make test", "."),
        ]

    def test_no_changes_never_runs_zero_tests(self, reference_monorepo: Path) -> None:
        plan = plan_tests(_on_branch(reference_monorepo))

        assert len(plan.runs) == 6

    def test_a_component_override_runs_through_sh(self, reference_monorepo: Path) -> None:
        root = _on_branch(reference_monorepo)
        _touch(root, "tools/lua-lint/lint.lua")

        [lua] = plan_tests(root).runs

        assert lua.argv == ("sh", "-c", "./test.sh")
        assert (lua.command, lua.cwd) == ("./test.sh", "tools/lua-lint")

    def test_a_component_without_a_command_is_unconfigured(self, reference_monorepo: Path) -> None:
        root = _on_branch(reference_monorepo)
        _component(root, "lua-lint", "description: L\npaths: ['tools/lua-lint/**']\n")
        _touch(root, "tools/lua-lint/lint.lua")

        plan = plan_tests(root)

        assert plan.runs == ()
        assert plan.unconfigured == ("lua-lint",)

    def test_identical_commands_run_once(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-q", "-b", "main")
        _write(tmp_path, ".dmx/config.md", "branch_base: main\n")
        for cid in ("a", "b"):
            _component(tmp_path, cid, f"description: X\npaths: [{cid}]\ntest: {{cmd: make test}}\n")
        _touch(tmp_path, "a/x", "b/y")

        assert _runs(tmp_path) == [(("a", "b"), "make test", ".")]


def _stub_repo(root: Path, components: dict[str, str], config: str = "") -> Path:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _write(root, ".dmx/config.md", f"branch_base: main\n{config}")
    for cid, test in components.items():
        _component(root, cid, f"description: {cid}\npaths: ['{cid}/**']\n{test}")
        _write(root, f"{cid}/file.txt", "x\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    _git(root, "checkout", "-q", "-b", _BRANCH)
    for cid in components:
        _touch(root, f"{cid}/file.txt")
    return root


class TestValidator:
    def test_every_run_passes(self, tmp_path: Path) -> None:
        root = _stub_repo(tmp_path, {"a": "test: {cmd: 'echo ok'}\n", "b": "test: {cmd: 'true'}\n"})

        result = run(root)

        assert result["pass"] is True
        assert result["message"].splitlines() == [
            "Tests passed — 2 runs",
            "✓ a — echo ok (exit 0)",
            "✓ b — true (exit 0)",
        ]
        assert result["checks"] == [{"name": "tests_pass", "pass": True, "message": ""}]

    def test_a_failure_names_the_component_and_the_test(self, tmp_path: Path) -> None:
        root = _stub_repo(
            tmp_path,
            {
                "a": "test: {cmd: 'true'}\n",
                "b": "test: {cmd: 'echo FAIL TestCharge; exit 3', cwd: b}\n",
            },
        )

        result = run(root)

        assert result["pass"] is False
        lines = result["message"].splitlines()
        assert lines[0] == "Tests failed — 1 of 2 runs failed"
        assert "✗ b — echo FAIL TestCharge; exit 3 in b (exit 3)" in lines
        [check] = result["checks"]
        assert check["pass"] is False
        assert check["message"].startswith("b — echo FAIL TestCharge; exit 3 in b (exit 3):\n")
        assert "FAIL TestCharge" in check["message"]
        assert [r["status"] for r in result["runs"]] == ["passed", "failed"]

    def test_an_unconfigured_component_fails(self, tmp_path: Path) -> None:
        root = _stub_repo(tmp_path, {"a": "test: {cmd: 'true'}\n", "lua": ""})

        result = run(root)

        assert result["pass"] is False
        assert (
            "✗ lua — not configured: set `test` in .dmx/components/lua/component.yaml"
            in result["message"]
        )
        assert "lua — not configured" in result["checks"][0]["message"]

    def test_a_missing_toolchain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _stub_repo(tmp_path, {"crate": ""})
        _write(root, "crate/Cargo.toml", '[package]\nname = "crate"\n')
        tools = tmp_path / "bin"
        tools.mkdir()
        for tool in ("git", "sh"):
            found = shutil.which(tool)
            assert found is not None
            (tools / tool).symlink_to(found)
        monkeypatch.setenv("PATH", str(tools))

        result = run(root)

        assert result["pass"] is False
        assert "✗ crate — `cargo` not found on PATH" in result["message"]

    def test_the_time_budget_is_shared(self, tmp_path: Path) -> None:
        root = _stub_repo(tmp_path, {"a": "test: {cmd: 'sleep 5'}\n", "b": "test: {cmd: 'true'}\n"})

        result = run(root, timeout_seconds=2)

        assert result["pass"] is False
        assert "✗ a — sleep 5 timed out" in result["message"]
        assert "✗ b — true not run: the time budget was used up" in result["message"]

    def test_a_repo_without_components_runs_the_root_command(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-q", "-b", "main")
        _write(tmp_path, "Makefile", "test:\n\t@echo root tests\n")

        result = run(tmp_path)

        assert result["pass"] is True
        assert result["message"] == "Tests passed — ran `make test` (exit 0)"


@pytest.fixture
def hermetic_toolchains(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the developer's Go settings out, but let the repo's own ``go.work`` apply."""
    monkeypatch.setenv("GOENV", "off")
    monkeypatch.setenv("GOWORK", "")
    monkeypatch.setenv("GOFLAGS", "")
    monkeypatch.setenv("GOTOOLCHAIN", "local")
    monkeypatch.setenv("GOCACHE", str(tmp_path_factory.getbasetemp() / "go-cache"))
    monkeypatch.setenv("CARGO_TARGET_DIR", str(tmp_path_factory.getbasetemp() / "cargo-target"))


@pytest.mark.usefixtures("hermetic_toolchains")
class TestReferenceMonorepoToolchains:
    """The reference monorepo's own tests, run through ``run_tests``."""

    @pytest.mark.parametrize(
        ("tool", "changed", "expected"),
        [
            ("go", "services/ledger/ledger.go", ["ledger", "payments"]),
            ("pnpm", "packages/billing-ui/test/format.test.js", ["billing-ui"]),
            ("pytest", "libs/pyutils/tests/test_cents.py", ["pyutils"]),
            ("cargo", "crates/hasher/src/lib.rs", ["hasher"]),
            ("sh", "tools/lua-lint/lint.lua", ["lua-lint"]),
        ],
    )
    def test_each_language(
        self, reference_monorepo: Path, tool: str, changed: str, expected: list[str]
    ) -> None:
        _need(tool)
        root = _on_branch(reference_monorepo)
        _touch(root, changed)

        result = run(root, timeout_seconds=600)

        assert result["pass"] is True, result["message"]
        assert sorted(c for r in result["runs"] for c in r["components"]) == expected


class TestTestCommand:
    @pytest.mark.parametrize(
        ("line", "command"),
        [
            ("test_command: make test", "make test"),
            ("test_command:   go test ./... && make lint  ", "go test ./... && make lint"),
            ('test_command: "make test"', "make test"),
            ("test_command:", None),
        ],
    )
    def test_the_whole_line_is_the_command(
        self, reference_monorepo: Path, line: str, command: str | None
    ) -> None:
        root = _on_branch(reference_monorepo)
        _write(root, ".dmx/config.md", f"branch_base: main\n{line}\n")
        _touch(root, "Makefile")

        repo_wide = [r for r in plan_tests(root).runs if r.components == ("repo-wide",)]

        assert [r.command for r in repo_wide] == ([command] if command else [])
