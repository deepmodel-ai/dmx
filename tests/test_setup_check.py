"""GH-91: ``dmx check`` validates the setup and shows what a branch will load and run."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from fastmcp import Client

from dmx.cli import main
from dmx.component_tests import plan_tests
from dmx.context_tools import build_context
from dmx.server import create_app
from dmx.setup_check import branch_plan, check_setup, render

if TYPE_CHECKING:
    from pathlib import Path

_BRANCH = "feature-gh-7"
_IDS = ("billing-ui", "hasher", "ledger", "lua-lint", "payments", "pyutils")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def clean(reference_monorepo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The reference monorepo with CODEOWNERS, a test_command, and every toolchain."""
    root = reference_monorepo
    lines = "".join(f"/.dmx/components/{cid}/  @acme/{cid}\n" for cid in _IDS)
    _write(root, ".github/CODEOWNERS", f"/.dmx/  @acme/platform\n{lines}/.dmx/jobs/\n")
    _write(root, ".dmx/config.md", "branch_base: main\ntest_command: make test\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "setup")
    monkeypatch.setattr("dmx.setup_check.shutil.which", lambda tool: f"/usr/bin/{tool}")
    _git(root, "checkout", "-q", "-b", _BRANCH)
    return root


def _problems(root: Path) -> list[tuple[str, str, str | None]]:
    return [(f.level, f.text, f.fix) for f in check_setup(root) if f.level != "ok"]


def _has(root: Path, level: str, text: str, fix: str | None) -> None:
    assert (level, text, fix) in _problems(root), _problems(root)


class TestSetup:
    def test_a_clean_setup(self, clean: Path) -> None:
        findings = check_setup(clean)

        assert [(f.level, f.text) for f in findings] == [("ok", "6 components")]

    def test_a_broken_component_file(self, clean: Path) -> None:
        _write(clean, ".dmx/components/fraud/component.yaml", "paths: [services/fraud]\n")

        _has(
            clean,
            "error",
            ".dmx/components/fraud/component.yaml: `description` is missing",
            "fix .dmx/components/fraud/component.yaml",
        )

    def test_an_unknown_dependency(self, clean: Path) -> None:
        _write(
            clean,
            ".dmx/components/payments/component.yaml",
            "description: P\npaths: ['services/payments/**']\ndepends_on: [ledgr]\n",
        )

        _has(
            clean,
            "error",
            'payments: depends_on "ledgr" is not a component',
            "fix depends_on in .dmx/components/payments/component.yaml",
        )

    def test_a_dependency_cycle(self, clean: Path) -> None:
        _write(
            clean,
            ".dmx/components/ledger/component.yaml",
            "description: L\npaths: ['services/ledger/**']\ndepends_on: [payments]\n",
        )

        _has(
            clean,
            "error",
            "depends_on cycle: ledger → payments → ledger",
            "remove one depends_on in the cycle",
        )

    def test_identical_paths(self, clean: Path) -> None:
        _write(
            clean,
            ".dmx/components/web/component.yaml",
            "description: W\npaths: ['packages/billing-ui/**']\n",
        )
        _write(clean, ".github/CODEOWNERS", "/.dmx/components/  @acme/all\n")

        _has(
            clean,
            "error",
            'billing-ui and web both own "packages/billing-ui/**"',
            "give each component its own paths",
        )

    def test_paths_that_match_no_files(self, clean: Path) -> None:
        _git(clean, "mv", "packages/billing-ui", "apps")

        _has(
            clean,
            "error",
            "billing-ui: paths match no files (moved?)",
            "update paths in .dmx/components/billing-ui/component.yaml",
        )

    def test_a_component_with_no_test_command(self, clean: Path) -> None:
        _write(
            clean,
            ".dmx/components/lua-lint/component.yaml",
            "description: L\npaths: ['tools/lua-lint/**']\n",
        )

        _has(
            clean,
            "error",
            "lua-lint: no test command",
            "set test in .dmx/components/lua-lint/component.yaml",
        )

    def test_a_missing_toolchain(self, clean: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "dmx.setup_check.shutil.which",
            lambda tool: None if tool == "cargo" else f"/usr/bin/{tool}",
        )

        _has(clean, "warn", "hasher: `cargo` not found on PATH", "install cargo")

    def test_a_workspace_member_without_a_component(self, clean: Path) -> None:
        _write(clean, "packages/checkout/package.json", '{"name": "checkout"}')

        _has(
            clean,
            "warn",
            "packages/checkout has no component",
            "run /dmx/add-component packages/checkout",
        )

    def test_a_component_without_a_codeowners_line(self, clean: Path) -> None:
        _write(clean, ".github/CODEOWNERS", "/.dmx/components/ledger/  @acme/ledger\n")

        problems = _problems(clean)

        assert (
            "warn",
            "payments: no CODEOWNERS line",
            'add "/.dmx/components/payments/  @<team>" to .github/CODEOWNERS',
        ) in problems
        assert not [p for p in problems if p[1] == "ledger: no CODEOWNERS line"]

    def test_a_glob_codeowners_line_covers_components(self, clean: Path) -> None:
        _write(clean, ".github/CODEOWNERS", "# owners\n/.dmx/components/*/  @acme/teams\n")

        assert not [p for p in _problems(clean) if "CODEOWNERS" in p[1]]

    def test_no_codeowners_file(self, clean: Path) -> None:
        (clean / ".github" / "CODEOWNERS").unlink()

        problems = [p for p in _problems(clean) if "CODEOWNERS" in p[1]]

        assert problems == [
            (
                "warn",
                "no CODEOWNERS file",
                "add .github/CODEOWNERS with a line per component; /dmx/init suggests them",
            )
        ]

    def test_no_test_command(self, clean: Path) -> None:
        _write(clean, ".dmx/config.md", "branch_base: main\n")

        _has(
            clean,
            "warn",
            "no test_command in .dmx/config.md: changes outside every component run every "
            "component's tests",
            "set test_command in .dmx/config.md",
        )

    def test_a_stray_dmx_folder(self, clean: Path) -> None:
        _write(clean, "packages/billing-ui/.dmx/jobs/x/spec.md", "old\n")

        _has(
            clean,
            "warn",
            "packages/billing-ui/.dmx/ has no config.md",
            "restart any runs from the repo root, then delete it",
        )

    def test_custom_code_naming_old_paths(self, clean: Path) -> None:
        _write(clean, "validators/legacy.py", "SPEC = '.dmx/spec.md'\n")

        _has(
            clean,
            "warn",
            "validators/legacy.py:1 names an old path: SPEC = '.dmx/spec.md'",
            "point it at loop_context's spec_path, then run /dmx/upgrade",
        )

    def test_lock_mismatches(self, clean: Path) -> None:
        _write(
            clean,
            ".dmx/components/ledger/shared-sources.yaml",
            "shared_sources:\n  - name: team\n    source: git::https://example.com/t.git?ref=main\n",
        )
        _write(
            clean,
            ".dmx/shared-sources.lock",
            "shared_sources:\n  old:\n    source: git::https://example.com/o.git?ref=v1\n"
            "    commit: abc\n    hash: sha256:0\n",
        )

        _has(
            clean,
            "error",
            'ledger: shared source "team" is not locked',
            "run /dmx/update-shared-sources ledger",
        )
        _has(
            clean,
            "error",
            'repo-wide: "old" is locked but no longer declared',
            "run /dmx/update-shared-sources",
        )

    def test_a_lock_from_another_address(self, clean: Path) -> None:
        _write(
            clean,
            ".dmx/shared-sources.yaml",
            "shared_sources:\n  - name: org\n    source: git::https://example.com/new.git?ref=main\n",
        )
        _write(
            clean,
            ".dmx/shared-sources.lock",
            "shared_sources:\n  org:\n    source: git::https://example.com/old.git?ref=main\n"
            "    commit: abc\n    hash: sha256:0\n",
        )

        _has(
            clean,
            "error",
            'repo-wide: shared source "org" is locked from a different address',
            "run /dmx/update-shared-sources",
        )

    def test_learnings_for_a_component_that_doesnt_exist(self, clean: Path) -> None:
        for job in ("GH-3", "GH-4"):
            _write(clean, f".dmx/jobs/{job}/activeContext.md", "## Open Learnings\n- [ledgr] x\n")

        _has(
            clean,
            "error",
            "learnings tagged [ledgr] in GH-3, GH-4, but ledgr is not a component",
            "re-tag them, or mark them folded with mark_learnings_folded",
        )

    def test_waiting_learnings(self, clean: Path) -> None:
        _write(
            clean,
            ".dmx/jobs/GH-3/activeContext.md",
            "## Open Learnings\n- [ledger] a\n- [ledger] b\n- [ledger] c → ledger, #1\n",
        )

        _has(clean, "info", "ledger: 2 learnings waiting", "run /dmx/update-memory ledger")

    def test_a_ticket_default_outside_the_allowed_list(self, clean: Path) -> None:
        _write(
            clean,
            ".dmx/config.md",
            "branch_base: main\ntest_command: make test\nticketing: jira\nproject_key: DM\n"
            "jira_projects: [LEDG]\n",
        )
        _write(
            clean,
            ".dmx/components/ledger/component.yaml",
            "description: L\npaths: ['services/ledger/**']\nticket: {jira_project: LEDGR}\n",
        )

        _has(
            clean,
            "warn",
            "ledger's ticket.jira_project `LEDGR` is not in jira_projects",
            "fix ticket in its component.yaml, or allow it in .dmx/config.md",
        )

    def test_it_never_changes_a_file(self, clean: Path) -> None:
        _write(clean, "services/ledger/new.go", "package ledger\n")
        before = _git(clean, "status", "--porcelain", "--ignored")

        render(check_setup(clean), branch_plan(clean))

        assert _git(clean, "status", "--porcelain", "--ignored") == before


class TestBranchPlan:
    def test_matches_what_the_loops_use(self, clean: Path) -> None:
        _write(clean, "services/ledger/ledger.go", "package ledger\n\n// changed\n")
        _write(clean, "Makefile", "test:\n")

        plan = branch_plan(clean)
        context = build_context(clean)

        assert plan["branch"] == _BRANCH
        assert plan["components"] == [
            ("ledger", "changed files"),
            ("payments", "depends on ledger"),
        ]
        assert plan["repo_wide_files"] == ["Makefile"]
        assert plan["loads"] == context["memory_files"]
        assert plan["runs"] == [run.label for run in plan_tests(clean).runs]
        assert plan["validators"]["validate"] == [
            "run_tests",
            "spec_adherence",
            "ledger/no_float_money",
        ]

    def test_component_validators_are_listed(self, clean: Path) -> None:
        _write(
            clean,
            ".dmx/components/ledger/component.yaml",
            "description: L\npaths: ['services/ledger/**']\nvalidators:\n  validate: [no_floats]\n",
        )
        _write(clean, "services/ledger/ledger.go", "package ledger\n\n// changed\n")

        plan = branch_plan(clean)

        assert plan["validators"]["validate"] == ["run_tests", "spec_adherence", "ledger/no_floats"]

    def test_the_report(self, clean: Path) -> None:
        _write(clean, "services/ledger/ledger.go", "package ledger\n\n// changed\n")

        text = render(check_setup(clean), branch_plan(clean))

        assert text.splitlines()[:2] == ["Setup", "  ✓ 6 components"]
        assert f"This branch ({_BRANCH})" in text
        assert "  Components: ledger (changed files), payments (depends on ledger)" in text
        assert "  Runs:       ledger — go test ./services/ledger/..." in text
        assert "              payments — go test ./... in services/payments" in text


class TestWithoutComponents:
    def test_a_short_report(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-q", "-b", "main")
        _write(tmp_path, ".dmx/config.md", "branch_base: main\n")
        _write(tmp_path, "Makefile", "test:\n\techo ok\n")

        text = render(check_setup(tmp_path), branch_plan(tmp_path))

        assert "  ✓ no components (.dmx/components/ not set up)" in text
        assert "CODEOWNERS" not in text
        assert "test_command" not in text
        assert "  Runs:       make test" in text


class TestCommands:
    def test_the_cli_exit_codes(self, clean: Path) -> None:
        ok = CliRunner().invoke(main, ["check", "--workspace-root", str(clean)])
        _write(
            clean,
            ".dmx/components/payments/component.yaml",
            "description: P\npaths: ['services/payments/**']\ndepends_on: [ledgr]\n",
        )
        bad = CliRunner().invoke(main, ["check", "--workspace-root", str(clean)])

        assert ok.exit_code == 0, ok.output
        assert bad.exit_code == 1
        assert '✗ payments: depends_on "ledgr" is not a component' in bad.output

    @pytest.mark.asyncio
    async def test_the_tool(self, clean: Path) -> None:
        async with Client(create_app()) as client:
            result = await client.call_tool("check_setup", {"workspace_root": str(clean)})

        text: Any = result.data
        assert text.startswith("Setup\n  ✓ 6 components")


def test_the_skill_shows_the_report_verbatim() -> None:
    from pathlib import Path  # noqa: PLC0415

    skill = (Path(__file__).parent.parent / "src/dmx/skills/utility/dmx-check.md").read_text(
        encoding="utf-8"
    )

    assert "name: check" in skill
    assert "Call `check_setup`" in skill
    assert "Show the report verbatim" in skill
    assert "Do not change any file." in skill
