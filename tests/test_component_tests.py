"""GH-89: run each touched component's tests, plus the components that depend on them."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dmx.component_tests import plan_tests
from dmx.components import load_components
from dmx.validators.run_tests import run
from dmx.workspaces import default_test_command

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
                {
                    "uv.lock": "",
                    "apps/web/pyproject.toml": '[project]\nname = "web"\n',
                    "apps/web/tests/test_web.py": "",
                },
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
        assert check["findings"] == [
            "b — echo FAIL TestCharge; exit 3 in b (exit 3)",
            "FAIL TestCharge",
        ]
        assert "reworkable" not in check

    def test_an_unconfigured_component_fails(self, tmp_path: Path) -> None:
        root = _stub_repo(tmp_path, {"a": "test: {cmd: 'true'}\n", "lua": ""})

        result = run(root)

        assert result["pass"] is False
        assert (
            "✗ lua — not configured: set `test: {cmd: ..., cwd: ...}` in "
            ".dmx/components/lua/component.yaml" in result["message"]
        )
        assert "lua — not configured" in result["checks"][0]["message"]
        assert result["checks"][0]["reworkable"] is False

    def test_a_missing_toolchain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _stub_repo(tmp_path, {"crate": ""})
        _write(root, "crate/Cargo.toml", '[package]\nname = "crate"\n')
        # Only git's own folder: git still works, cargo isn't there on any OS.
        git = shutil.which("git")
        assert git is not None
        monkeypatch.setenv("PATH", os.path.dirname(git))

        result = run(root)

        assert result["pass"] is False
        assert "✗ crate — `cargo` not found on PATH" in result["message"]
        assert result["checks"][0]["reworkable"] is False

    def test_the_time_budget_is_shared(self, tmp_path: Path) -> None:
        root = _stub_repo(tmp_path, {"a": "test: {cmd: 'sleep 5'}\n", "b": "test: {cmd: 'true'}\n"})

        result = run(root, timeout_seconds=2)

        assert result["pass"] is False
        assert "✗ a — sleep 5 timed out" in result["message"]
        assert "✗ b — true not run: the time budget was used up" in result["message"]
        assert result["checks"][0]["reworkable"] is False

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


_NODE_TEST = 'const test = require("node:test");\ntest("ok", () => {});\n'


def _workspace_repo(root: Path, files: dict[str, str], component: str = "web") -> Path:
    """A small repo with one component at ``apps/web``, changed on a branch."""
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _write(root, ".dmx/config.md", "branch_base: main\n")
    _component(root, component, "description: Web\npaths: ['apps/web/**']\n")
    for rel, text in files.items():
        _write(root, rel, text)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    _git(root, "checkout", "-q", "-b", _BRANCH)
    _touch(root, "apps/web/changed.txt")
    return root


@pytest.mark.usefixtures("hermetic_toolchains")
class TestOtherDefaultForms:
    """The default commands the reference monorepo doesn't use, run for real."""

    def _passes(self, root: Path, command: str) -> None:
        [run_] = plan_tests(root).runs
        assert run_.command == command
        result = run(root, timeout_seconds=600)
        assert result["pass"] is True, (result["message"], result["checks"][0]["message"])

    def test_npm_workspace(self, tmp_path: Path) -> None:
        _need("npm")
        root = _workspace_repo(
            tmp_path,
            {
                "package.json": '{"private": true, "workspaces": ["apps/*"]}',
                "apps/web/package.json": '{"name": "web", "scripts": {"test": "node --test"}}',
                "apps/web/test/ok.test.js": _NODE_TEST,
            },
        )

        self._passes(root, "npm test -w web")

    def test_yarn_workspace(self, tmp_path: Path) -> None:
        _need("yarn")
        root = _workspace_repo(
            tmp_path,
            {
                "package.json": '{"private": true, "workspaces": ["apps/*"]}',
                "yarn.lock": "",
                # Yarn 1 ignores a workspace package without a version.
                "apps/web/package.json": (
                    '{"name": "web", "version": "0.1.0", "scripts": {"test": "node --test"}}'
                ),
                "apps/web/test/ok.test.js": _NODE_TEST,
            },
        )
        # A real checkout has been installed; yarn won't run workspace scripts before.
        yarn = shutil.which("yarn")  # yarn.cmd on Windows
        assert yarn is not None
        subprocess.run([yarn, "install"], cwd=root, check=True, capture_output=True)

        self._passes(root, "yarn workspace web test")

    def test_a_standalone_npm_package(self, tmp_path: Path) -> None:
        _need("npm")
        root = _workspace_repo(
            tmp_path,
            {
                "apps/web/package.json": '{"name": "web", "scripts": {"test": "node --test"}}',
                "apps/web/test/ok.test.js": _NODE_TEST,
            },
        )

        self._passes(root, "npm test --silent")

    def test_a_uv_project(self, tmp_path: Path) -> None:
        _need("uv")
        root = _workspace_repo(
            tmp_path,
            {
                "apps/web/pyproject.toml": (
                    '[project]\nname = "web"\nversion = "0.1.0"\nrequires-python = ">=3.11"\n\n'
                    '[dependency-groups]\ndev = ["pytest"]\n'
                ),
                "apps/web/tests/test_ok.py": "def test_ok() -> None:\n    assert True\n",
            },
        )
        subprocess.run(["uv", "lock", "-q"], cwd=root / "apps" / "web", check=True)

        self._passes(root, "uv run pytest -q")

    def test_a_standalone_crate(self, tmp_path: Path) -> None:
        _need("cargo")
        root = _workspace_repo(
            tmp_path,
            {
                "apps/web/Cargo.toml": (
                    '[package]\nname = "web"\nversion = "0.1.0"\nedition = "2021"\n'
                ),
                "apps/web/src/lib.rs": (
                    "#[cfg(test)]\nmod tests {\n    #[test]\n    fn ok() {}\n}\n"
                ),
            },
        )

        self._passes(root, "cargo test")


def _root_tests_repo(root: Path) -> Path:
    """A uv workspace shaped like pydantic-ai: members hold no tests, the root ``tests/`` does."""
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _write(
        root,
        "pyproject.toml",
        "[project]\nname = 'mono'\n[tool.uv.workspace]\nmembers = ['pkg_a', 'pkg_b']\n"
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\n",
    )
    _write(root, "uv.lock", "")
    _write(root, "pkg_a/pyproject.toml", "[project]\nname = 'pkg-a'\n")
    _write(root, "pkg_a/pkg_a/__init__.py", "")
    _write(root, "pkg_a/pkg_a/test.py", "# a module named test, not a test file\n")
    _write(root, "pkg_b/pyproject.toml", "[project]\nname = 'pkg-b'\n")
    _write(root, "pkg_b/pkg_b/__init__.py", "")
    _write(root, "tests/test_top.py", "def test_top():\n    pass\n")
    _write(root, "tests/conftest.py", "")
    _write(root, "tests/models/test_model.py", "def test_model():\n    pass\n")
    _write(root, "tests/cassettes/recording.yaml", "x: 1\n")
    _write(root, "tests/pkg_b/test_b.py", "def test_b():\n    pass\n")
    _write(root, ".dmx/config.md", "branch_base: main\n")
    _component(
        root,
        "pkg-a",
        "description: A\n"
        "paths: ['pkg_a/**', 'tests/*.py', 'tests/models/**', 'tests/cassettes/**']\n",
    )
    _component(root, "pkg-b", "description: B\npaths: ['pkg_b/**', 'tests/pkg_b/**']\n")
    _component(root, "pkg-c", "description: C\npaths: ['pkg_c/**']\n")
    _write(root, "pkg_c/pyproject.toml", "[project]\nname = 'pkg-c'\n")
    _write(root, "pkg_c/pkg_c/__init__.py", "")
    return root


class TestTestsOutsideThePackage:
    """GH-117: a member without tests runs the root test paths its component maps."""

    def test_a_member_without_tests_runs_its_mapped_root_paths(self, tmp_path: Path) -> None:
        root = _root_tests_repo(tmp_path)
        component = load_components(root).get("pkg-a")
        assert component is not None

        command = default_test_command(root, component)

        assert command is not None
        assert command.cwd == "."
        assert command.argv == ("uv", "run", "pytest", "-q", "tests/models", "tests/test_top.py")

    def test_a_folder_of_tests_is_passed_whole(self, tmp_path: Path) -> None:
        root = _root_tests_repo(tmp_path)
        component = load_components(root).get("pkg-b")
        assert component is not None

        command = default_test_command(root, component)

        assert command is not None
        assert command.argv == ("uv", "run", "pytest", "-q", "tests/pkg_b")

    def test_a_member_with_no_tests_anywhere_has_no_command(self, tmp_path: Path) -> None:
        root = _root_tests_repo(tmp_path)
        component = load_components(root).get("pkg-c")
        assert component is not None

        assert default_test_command(root, component) is None

    def test_a_member_with_its_own_tests_is_unchanged(self, tmp_path: Path) -> None:
        root = _root_tests_repo(tmp_path)
        _write(root, "pkg_b/tests/test_inside.py", "def test_inside():\n    pass\n")
        component = load_components(root).get("pkg-b")
        assert component is not None

        command = default_test_command(root, component)

        assert command is not None
        assert command.argv == ("uv", "run", "pytest", "-q")
        assert command.cwd == "pkg_b"

    def test_the_reference_monorepo_python_library_is_unchanged(
        self, reference_monorepo: Path
    ) -> None:
        component = load_components(reference_monorepo).get("pyutils")
        assert component is not None

        command = default_test_command(reference_monorepo, component)

        assert command is not None
        assert (command.argv, command.cwd) == (("pytest", "-q"), "libs/pyutils")

    def test_dmx_check_says_how_to_set_a_test_command(self, tmp_path: Path) -> None:
        from dmx.setup_check import check_setup  # noqa: PLC0415

        root = _root_tests_repo(tmp_path)

        [finding] = [f for f in check_setup(root) if f.text.startswith("pkg-c:")]
        assert finding.level == "error"
        assert "no tests in its folder or paths" in finding.text
        assert finding.fix is not None
        assert "test: {cmd: ..., cwd: ...}" in finding.fix

    def test_nothing_collected_is_a_setup_failure(self, tmp_path: Path) -> None:
        root = _stub_repo(tmp_path, {"a": "test: {cmd: 'echo no tests ran; exit 5'}\n"})

        result = run(root)

        assert result["pass"] is False
        [check] = result["checks"]
        assert check["reworkable"] is False
        assert "collected no tests" in result["message"]
        assert "test: {cmd: ..., cwd: ...}" in check["message"]

    def test_the_validator_collects_the_root_tests(self, tmp_path: Path) -> None:
        root = _root_tests_repo(tmp_path)
        (root / "uv.lock").unlink()  # plain pytest, from this test run's environment
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "init")
        _git(root, "checkout", "-q", "-b", _BRANCH)
        _touch(root, "pkg_a/pkg_a/__init__.py")

        result = run(root)

        assert result["pass"] is True, result["message"]
        assert "✓ pkg-a — pytest -q tests/models tests/test_top.py (exit 0)" in result["message"]


def _failing(*lines: str, code: int = 1) -> str:
    """A ``test:`` entry whose command prints *lines* and exits *code*."""
    echoes = "; ".join(f"echo '{line}'" for line in lines)
    return f'test:\n  cmd: "{echoes}; exit {code}"\n'


class TestKnownFailures:
    """GH-118: listed failures don't block the gate; anything else still does."""

    def test_a_run_whose_only_failures_are_listed_passes(self, tmp_path: Path) -> None:
        entry = _failing(
            "FAILED tests/test_display.py::test_banner - OSError",
            "1 failed, 40 passed",
        )
        entry += "  known_failures: [tests/test_display.py::test_banner]\n"
        root = _stub_repo(tmp_path, {"a": entry})

        result = run(root)

        assert result["pass"] is True, result["message"]
        assert "✓ a — " in result["message"]
        assert "known failures only" in result["message"]
        assert "Known failures: tests/test_display.py::test_banner" in result["message"]

    def test_an_unlisted_failure_still_fails(self, tmp_path: Path) -> None:
        entry = _failing(
            "FAILED tests/test_display.py::test_banner - OSError",
            "FAILED tests/test_refund.py::test_partial - AssertionError",
        )
        entry += "  known_failures: [tests/test_display.py::test_banner]\n"
        root = _stub_repo(tmp_path, {"a": entry})

        result = run(root)

        assert result["pass"] is False
        [check] = result["checks"]
        assert check["findings"][-1] == "FAILED tests/test_refund.py::test_partial - AssertionError"
        assert "FAILED tests/test_display.py::test_banner - OSError" not in check["findings"]
        assert "can be listed under `known_failures`" in result["message"]

    def test_a_failure_with_no_test_lines_still_fails(self, tmp_path: Path) -> None:
        entry = _failing("ImportError: no module named x") + "  known_failures: [anything]\n"
        root = _stub_repo(tmp_path, {"a": entry})

        assert run(root)["pass"] is False

    def test_a_listed_test_that_did_not_fail_is_noted(self, tmp_path: Path) -> None:
        entry = "test:\n  cmd: 'echo 3 passed'\n  known_failures: [tests/test_x.py::test_gone]\n"
        root = _stub_repo(tmp_path, {"a": entry})

        result = run(root)

        assert result["pass"] is True
        assert (
            "Listed known failures that didn't fail: tests/test_x.py::test_gone "
            "(remove them from known_failures if they pass now)"
        ) in result["message"]

    @pytest.mark.parametrize(
        ("line", "known"),
        [
            ("--- FAIL: TestCharge (0.00s)", "TestCharge"),
            ("FAIL src/fees.test.ts", "src/fees.test.ts"),
            ("ERROR tests/test_setup.py - ImportError", "tests/test_setup.py"),
        ],
    )
    def test_go_jest_and_pytest_errors_match(self, tmp_path: Path, line: str, known: str) -> None:
        entry = _failing(line) + f"  known_failures: ['{known}']\n"
        root = _stub_repo(tmp_path, {"a": entry})

        assert run(root)["pass"] is True

    def test_the_repo_wide_command_reads_config(self, tmp_path: Path) -> None:
        root = _stub_repo(
            tmp_path,
            {"a": "test: {cmd: 'true'}\n"},
            config=(
                "test_command: echo 'FAILED tests/test_lock.py::test_x'; exit 1\n"
                "test_known_failures: [tests/test_lock.py::test_x]\n"
            ),
        )
        _write(root, "uv.lock", "changed\n")

        result = run(root)

        assert result["pass"] is True, result["message"]
        assert "Known failures: tests/test_lock.py::test_x" in result["message"]
        from dmx.setup_check import check_setup  # noqa: PLC0415

        texts = [(f.level, f.text) for f in check_setup(root)]
        assert ("info", "test_command: 1 known failure (tests/test_lock.py::test_x)") in texts

    def test_known_failures_must_be_a_list_of_strings(self, tmp_path: Path) -> None:
        root = _stub_repo(tmp_path, {"a": "test:\n  cmd: 'true'\n  known_failures: x\n"})

        components = load_components(root)

        assert components.get("a") is None
        assert "known_failures" in components.skipped[0].reason

    def test_dmx_check_and_get_context_show_them(self, tmp_path: Path) -> None:
        from dmx.component_tests import component_test  # noqa: PLC0415
        from dmx.setup_check import check_setup  # noqa: PLC0415

        entry = "test:\n  cmd: 'true'\n  known_failures: [t1, t2]\n"
        root = _stub_repo(tmp_path, {"a": entry})
        component = load_components(root).get("a")
        assert component is not None

        assert component_test(root, component) == {
            "command": "true",
            "cwd": ".",
            "source": "test",
            "known_failures": ["t1", "t2"],
        }
        texts = [(f.level, f.text) for f in check_setup(root)]
        assert ("info", "a: 2 known failures in its tests (t1, t2)") in texts


_PRETTY = Path(__file__).parent / "fixtures" / "pytest-pretty"
_LONG = "test_stream_source_closed_when_cancelled_before_the_first_item_arrives"


class TestPytestPretty:
    """GH-129: pytest-pretty replaces the FAILED lines with a table."""

    def test_the_wide_table_gives_full_ids(self) -> None:
        from dmx.validators.run_tests import _failing_tests  # noqa: PLC0415

        lines = _failing_tests((_PRETTY / "wide.txt").read_text(encoding="utf-8"))

        assert lines == [
            "ERROR test_error_fixture",
            f"FAILED tests/models/test_model_function.py::{_LONG}",
            "FAILED tests/models/test_model_function.py::TestSentenceTransformers::test_embed",
            "FAILED tests/models/test_model_function.py::test_param[2]",
        ]

    def test_a_narrow_table_still_names_every_test(self) -> None:
        from dmx.validators.run_tests import _failing_tests  # noqa: PLC0415

        lines = _failing_tests((_PRETTY / "narrow.txt").read_text(encoding="utf-8"))

        assert lines == [
            "ERROR test_error_fixture",
            f"FAILED {_LONG}",
            "FAILED TestSentenceTransformers::test_embed",
            "FAILED test_param[2]",
        ]

    def test_known_failures_match_pretty_output(self, tmp_path: Path) -> None:
        fixture = (_PRETTY / "wide.txt").as_posix()
        entry = (
            f"test:\n  cmd: \"cat '{fixture}'; exit 1\"\n"
            "  known_failures:\n"
            "    - tests/models/test_model_function.py::TestSentenceTransformers\n"
            "    - test_param\n"
            f"    - {_LONG}\n"
            "    - test_error_fixture\n"
        )
        root = _stub_repo(tmp_path, {"a": entry})

        result = run(root)

        assert result["pass"] is True, result["message"]
        assert "known failures only" in result["message"]

    def test_an_unlisted_pretty_failure_still_fails(self, tmp_path: Path) -> None:
        fixture = (_PRETTY / "wide.txt").as_posix()
        entry = f"test:\n  cmd: \"cat '{fixture}'; exit 1\"\n  known_failures: [test_param]\n"
        root = _stub_repo(tmp_path, {"a": entry})

        result = run(root)

        assert result["pass"] is False
        assert (
            "FAILED tests/models/test_model_function.py::test_param[2]"
            not in (result["checks"][0]["findings"])
        )
        assert "ERROR test_error_fixture" in result["checks"][0]["findings"]


class TestKnownFailureHints:
    """GH-129: say how entries match, and why a list couldn't match."""

    def test_the_hint_explains_substring_matching(self, tmp_path: Path) -> None:
        root = _stub_repo(tmp_path, {"a": _failing("FAILED tests/test_x.py::TestA::test_b")})

        message = run(root)["message"]

        assert "matches every failing test whose line contains it" in message
        assert "a file, a class or a test name" in message

    def test_no_recognisable_lines_with_a_list_says_why(self, tmp_path: Path) -> None:
        entry = _failing("something broke, no test names here") + "  known_failures: [test_x]\n"
        root = _stub_repo(tmp_path, {"a": entry})

        result = run(root)

        assert result["pass"] is False
        assert "No failing-test lines were recognised" in result["message"]
        assert "known_failures couldn't match" in result["message"]
