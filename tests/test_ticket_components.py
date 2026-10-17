"""GH-86: which components the current ticket covers."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from dmx.ticket_components import changed_files, spec_components, ticket_components

if TYPE_CHECKING:
    from pathlib import Path

_BRANCH = "feature-gh-7-ledger"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _spec(root: Path, components: str | None, job: str = "GH-7") -> Path:
    folder = root / ".dmx" / "jobs" / job
    folder.mkdir(parents=True, exist_ok=True)
    line = f"components: {components}\n" if components is not None else ""
    spec = folder / "spec.md"
    spec.write_text(f"---\nticket: {job}\nbranch: {_BRANCH}\n{line}---\n# Spec\n", encoding="utf-8")
    return spec


def _ids(root: Path, job: str | None = "GH-7", folder: Path | None = None) -> list[tuple[str, str]]:
    found = ticket_components(root, job, folder=folder)
    return [(entry.id, entry.reason) for entry in found.entries]


class TestSpecComponents:
    def test_a_flow_list(self, tmp_path: Path) -> None:
        assert spec_components(_spec(tmp_path, "[ledger, payments]")) == ("ledger", "payments")

    def test_a_block_list(self, tmp_path: Path) -> None:
        spec = _spec(tmp_path, "\n  - ledger\n  - payments")

        assert spec_components(spec) == ("ledger", "payments")

    def test_one_name(self, tmp_path: Path) -> None:
        assert spec_components(_spec(tmp_path, "ledger")) == ("ledger",)

    def test_an_empty_list_is_kept(self, tmp_path: Path) -> None:
        assert spec_components(_spec(tmp_path, "[]")) == ()

    def test_no_key_or_no_file(self, tmp_path: Path) -> None:
        assert spec_components(_spec(tmp_path, None)) is None
        assert spec_components(tmp_path / "missing.md") is None


class TestChangedFiles:
    def test_committed_staged_unstaged_and_untracked(self, reference_monorepo: Path) -> None:
        root = reference_monorepo
        _git(root, "checkout", "-q", "-b", _BRANCH)
        (root / "services/ledger/ledger.go").write_text("package ledger\n", encoding="utf-8")
        _git(root, "commit", "-qam", "committed")
        (root / "crates/hasher/src/lib.rs").write_text("// staged\n", encoding="utf-8")
        _git(root, "add", "crates/hasher/src/lib.rs")
        (root / "go.mod").write_text("module example.com/mono\n\ngo 1.23\n", encoding="utf-8")
        (root / "tools/lua-lint/new.lua").write_text("-- untracked\n", encoding="utf-8")
        _spec(root, "[ledger]")

        assert changed_files(root) == (
            "crates/hasher/src/lib.rs",
            "go.mod",
            "services/ledger/ledger.go",
            "tools/lua-lint/new.lua",
        )

    def test_a_rename_counts_both_sides(self, reference_monorepo: Path) -> None:
        root = reference_monorepo
        _git(root, "checkout", "-q", "-b", _BRANCH)
        _git(root, "mv", "services/ledger/ledger.go", "services/payments/ledger.go")
        _git(root, "commit", "-qm", "move")

        assert changed_files(root) == ("services/ledger/ledger.go", "services/payments/ledger.go")

    def test_an_uncommitted_rename_counts_both_sides(self, reference_monorepo: Path) -> None:
        root = reference_monorepo
        _git(root, "checkout", "-q", "-b", _BRANCH)
        _git(root, "mv", "services/ledger/ledger.go", "services/payments/ledger.go")

        assert changed_files(root) == ("services/ledger/ledger.go", "services/payments/ledger.go")

    def test_nothing_on_the_base_branch(self, reference_monorepo: Path) -> None:
        assert changed_files(reference_monorepo) == ()

    def test_a_dmx_folder_below_the_git_top(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-q", "-b", "main")
        _git(tmp_path, "config", "user.email", "t@example.com")
        _git(tmp_path, "config", "user.name", "T")
        app = tmp_path / "apps" / "shop"
        (app / ".dmx").mkdir(parents=True)
        (app / ".dmx" / "config.md").write_text("branch_base: main\n", encoding="utf-8")
        (tmp_path / "other.txt").write_text("x", encoding="utf-8")
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-qm", "init")
        _git(tmp_path, "checkout", "-q", "-b", _BRANCH)
        (app / "cart.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "other.txt").write_text("y", encoding="utf-8")

        assert changed_files(app) == ("cart.py",)


class TestTicketComponents:
    def test_from_the_spec(self, reference_monorepo: Path) -> None:
        _git(reference_monorepo, "checkout", "-q", "-b", _BRANCH)
        _spec(reference_monorepo, "[payments, ledger]")

        assert _ids(reference_monorepo) == [("ledger", "spec"), ("payments", "spec")]

    def test_changed_files_add_a_component(self, reference_monorepo: Path) -> None:
        root = reference_monorepo
        _git(root, "checkout", "-q", "-b", _BRANCH)
        _spec(root, "[ledger]")
        (root / "packages/billing-ui/index.js").write_text("// new\n", encoding="utf-8")
        (root / "Makefile").write_text("test:\n", encoding="utf-8")

        found = ticket_components(root, "GH-7")

        assert [(e.id, e.reason) for e in found.entries] == [
            ("billing-ui", "changed files"),
            ("ledger", "spec"),
        ]
        assert found.repo_wide_files == ("Makefile",)

    def test_editing_the_spec_corrects_the_list(self, reference_monorepo: Path) -> None:
        _git(reference_monorepo, "checkout", "-q", "-b", _BRANCH)
        _spec(reference_monorepo, "[ledger]")
        assert _ids(reference_monorepo) == [("ledger", "spec")]

        _spec(reference_monorepo, "[ledger, billing-ui]")

        assert _ids(reference_monorepo) == [("billing-ui", "spec"), ("ledger", "spec")]

    def test_an_unknown_id_is_kept_and_flagged(self, reference_monorepo: Path) -> None:
        _git(reference_monorepo, "checkout", "-q", "-b", _BRANCH)
        _spec(reference_monorepo, "[ledgr]")

        found = ticket_components(reference_monorepo, "GH-7")

        [entry] = found.entries
        assert entry.id == "ledgr"
        assert entry.component is None
        assert found.line() == "Components: ledgr (not a component)"

    def test_the_ide_folder_without_a_spec(self, reference_monorepo: Path) -> None:
        _git(reference_monorepo, "checkout", "-q", "-b", _BRANCH)
        folder = reference_monorepo / "services" / "payments"

        assert _ids(reference_monorepo, None, folder) == [("payments", "IDE folder")]
        assert _ids(reference_monorepo, None, reference_monorepo) == []

    def test_the_ide_folder_is_ignored_when_the_spec_names_components(
        self, reference_monorepo: Path
    ) -> None:
        _git(reference_monorepo, "checkout", "-q", "-b", _BRANCH)
        _spec(reference_monorepo, "[]")
        folder = reference_monorepo / "services" / "payments"

        assert _ids(reference_monorepo, "GH-7", folder) == []

    def test_a_spec_without_the_key_falls_back_to_the_ide_folder(
        self, reference_monorepo: Path
    ) -> None:
        _git(reference_monorepo, "checkout", "-q", "-b", _BRANCH)
        _spec(reference_monorepo, None)
        folder = reference_monorepo / "crates" / "hasher" / "src"

        assert _ids(reference_monorepo, "GH-7", folder) == [("hasher", "IDE folder")]

    def test_the_line(self, reference_monorepo: Path) -> None:
        _git(reference_monorepo, "checkout", "-q", "-b", _BRANCH)
        _spec(reference_monorepo, "[payments, ledger]")

        assert ticket_components(reference_monorepo, "GH-7").line() == (
            "Components: ledger (.dmx/components/ledger/), payments (.dmx/components/payments/)"
        )

    def test_the_line_for_a_ticket_with_none(self, reference_monorepo: Path) -> None:
        _git(reference_monorepo, "checkout", "-q", "-b", _BRANCH)
        _spec(reference_monorepo, "[]")

        assert ticket_components(reference_monorepo, "GH-7").line() == (
            "Components: none (repo-wide)"
        )
