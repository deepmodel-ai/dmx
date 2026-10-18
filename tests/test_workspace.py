"""Tests for dmx.workspace — resolve_workspace_root and validate_workspace_root."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from dmx import workspace
from dmx.exceptions import WorkspaceRootInvalid
from dmx.loop_state import write_initial_state
from dmx.workspace import (
    find_dmx_root,
    ide_folder,
    resolve_workspace_root,
    validate_workspace_root,
)

# ---------------------------------------------------------------------------
# resolve_workspace_root — resolution chain
# ---------------------------------------------------------------------------


class TestResolveWorkspaceRoot:
    """Unit tests for resolve_workspace_root's explicit → roots → cwd chain."""

    @pytest.mark.asyncio
    async def test_returns_explicit_path_without_marker_check(self, tmp_path: Path) -> None:
        # Explicit paths skip the .git/.dmx marker check — e.g. a brand-new
        # project before `git init` — but still get the dangerous-path check.
        empty_dir = tmp_path / "brand-new-project"
        empty_dir.mkdir()
        ctx = MagicMock()

        result = await resolve_workspace_root(ctx, str(empty_dir))
        assert result == empty_dir
        ctx.list_roots.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_mcp_root_file_uri(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        root = MagicMock()
        root.uri = f"file://{tmp_path}"
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[root])

        result = await resolve_workspace_root(ctx, None)
        assert result == tmp_path

    @pytest.mark.asyncio
    async def test_decodes_percent_encoded_uri(self, tmp_path: Path) -> None:
        project = tmp_path / "my project"
        project.mkdir()
        (project / ".dmx").mkdir()
        root = MagicMock()
        root.uri = f"file://{tmp_path}/my%20project"
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[root])

        result = await resolve_workspace_root(ctx, None)
        assert result == project

    @pytest.mark.asyncio
    async def test_falls_back_to_cwd_when_no_roots(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[])

        result = await resolve_workspace_root(ctx, None)
        assert result == Path(os.getcwd())

    @pytest.mark.asyncio
    async def test_falls_back_to_cwd_when_list_roots_raises(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        (tmp_path / ".dmx").mkdir()
        monkeypatch.chdir(tmp_path)
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(side_effect=RuntimeError("not supported"))

        result = await resolve_workspace_root(ctx, None)
        assert result == Path(os.getcwd())

    @pytest.mark.asyncio
    async def test_explicit_takes_precedence_over_roots(self, tmp_path: Path) -> None:
        root = MagicMock()
        root.uri = "file:///from/roots"
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[root])

        result = await resolve_workspace_root(ctx, str(tmp_path))
        assert result == tmp_path
        ctx.list_roots.assert_not_called()

    @pytest.mark.asyncio
    async def test_expands_tilde_in_explicit_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Path.expanduser() reads $HOME directly (not Path.home()).
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))  # what Windows reads
        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        ctx = MagicMock()

        result = await resolve_workspace_root(ctx, "~/project")
        assert result == project


# ---------------------------------------------------------------------------
# resolve_workspace_root — validation failures
# ---------------------------------------------------------------------------


class TestResolveWorkspaceRootValidation:
    """Auto-detected roots that fail validation must raise, not silently succeed."""

    @pytest.mark.asyncio
    async def test_rejects_filesystem_root_from_roots(self) -> None:
        root = MagicMock()
        root.uri = "file:///"
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[root])

        with pytest.raises(WorkspaceRootInvalid):
            await resolve_workspace_root(ctx, None)

    @pytest.mark.asyncio
    async def test_rejects_home_directory_from_roots(self) -> None:
        root = MagicMock()
        root.uri = Path.home().as_uri()
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[root])

        with pytest.raises(WorkspaceRootInvalid):
            await resolve_workspace_root(ctx, None)

    @pytest.mark.asyncio
    async def test_rejects_cwd_fallback_without_markers(self, tmp_path: Path, monkeypatch) -> None:
        # No .git, no .dmx — this is the "container folder" case: dmx should
        # fail loudly rather than silently writing into the wrong directory.
        monkeypatch.chdir(tmp_path)
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[])

        with pytest.raises(WorkspaceRootInvalid):
            await resolve_workspace_root(ctx, None)

    @pytest.mark.asyncio
    async def test_rejects_explicit_dangerous_path_even_though_explicit(self) -> None:
        ctx = MagicMock()

        with pytest.raises(WorkspaceRootInvalid):
            await resolve_workspace_root(ctx, "/")

    @pytest.mark.asyncio
    async def test_rejects_relative_explicit_path(self, tmp_path: Path, monkeypatch) -> None:
        # A relative explicit path would just reintroduce the cwd-ambiguity
        # this module exists to eliminate — reject it instead of silently
        # resolving against the server process's cwd.
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        ctx = MagicMock()

        with pytest.raises(WorkspaceRootInvalid):
            await resolve_workspace_root(ctx, "./some-project")

    @pytest.mark.asyncio
    async def test_rejects_dot_as_explicit_path(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        ctx = MagicMock()

        with pytest.raises(WorkspaceRootInvalid):
            await resolve_workspace_root(ctx, ".")


# ---------------------------------------------------------------------------
# resolve_workspace_root — require_markers=False (e.g. setup_ide_rules)
# ---------------------------------------------------------------------------


class TestResolveWorkspaceRootWithoutMarkers:
    """Callers that may legitimately run before `.dmx`/`.git` exist."""

    @pytest.mark.asyncio
    async def test_accepts_cwd_fallback_without_markers(self, tmp_path: Path, monkeypatch) -> None:
        # Regression check: `/dmx-init` calls setup_ide_rules on a project
        # that may not be a git repo yet — this must not be rejected.
        monkeypatch.chdir(tmp_path)
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[])

        result = await resolve_workspace_root(ctx, None, require_markers=False)
        assert result == tmp_path

    @pytest.mark.asyncio
    async def test_accepts_mcp_root_without_markers(self, tmp_path: Path) -> None:
        root = MagicMock()
        root.uri = f"file://{tmp_path}"
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[root])

        result = await resolve_workspace_root(ctx, None, require_markers=False)
        assert result == tmp_path

    @pytest.mark.asyncio
    async def test_still_rejects_filesystem_root_when_markers_not_required(self) -> None:
        # The dangerous-path check is independent of require_markers.
        root = MagicMock()
        root.uri = "file:///"
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[root])

        with pytest.raises(WorkspaceRootInvalid):
            await resolve_workspace_root(ctx, None, require_markers=False)


# ---------------------------------------------------------------------------
# validate_workspace_root — direct unit tests
# ---------------------------------------------------------------------------


class TestValidateWorkspaceRoot:
    def test_accepts_dir_with_git_marker(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        validate_workspace_root(tmp_path, explicit=False)  # no raise

    def test_accepts_dir_with_dmx_marker(self, tmp_path: Path) -> None:
        (tmp_path / ".dmx").mkdir()
        validate_workspace_root(tmp_path, explicit=False)  # no raise

    def test_rejects_dir_without_markers_when_not_explicit(self, tmp_path: Path) -> None:
        with pytest.raises(WorkspaceRootInvalid):
            validate_workspace_root(tmp_path, explicit=False)

    def test_accepts_dir_without_markers_when_explicit(self, tmp_path: Path) -> None:
        validate_workspace_root(tmp_path, explicit=True)  # no raise

    def test_accepts_dir_without_markers_when_require_markers_false(self, tmp_path: Path) -> None:
        validate_workspace_root(tmp_path, explicit=False, require_markers=False)  # no raise

    def test_still_rejects_dangerous_path_when_require_markers_false(self) -> None:
        with pytest.raises(WorkspaceRootInvalid):
            validate_workspace_root(Path("/"), explicit=False, require_markers=False)

    def test_rejects_filesystem_root_even_when_explicit(self) -> None:
        with pytest.raises(WorkspaceRootInvalid):
            validate_workspace_root(Path("/"), explicit=True)

    def test_rejects_home_directory_even_when_explicit(self) -> None:
        with pytest.raises(WorkspaceRootInvalid):
            validate_workspace_root(Path.home(), explicit=True)

    def test_accepts_subfolder_of_home(self, tmp_path: Path, monkeypatch) -> None:
        # A subfolder of home should not be confused with the bare home
        # directory itself — only the exact home path is rejected.
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        sub = tmp_path / "project"
        sub.mkdir()
        (sub / ".dmx").mkdir()
        validate_workspace_root(sub, explicit=False)  # no raise


# ---------------------------------------------------------------------------
# A tool call pointed at a subfolder of the repo (GH-83)
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _monorepo(tmp_path: Path) -> Path:
    """A git repo with `.dmx/config.md` at the root and a `packages/billing` folder."""
    repo = tmp_path / "mono"
    (repo / "packages" / "billing").mkdir(parents=True)
    (repo / ".dmx").mkdir()
    (repo / ".dmx" / "config.md").write_text("branch_base: main\n", encoding="utf-8")
    (repo / "packages" / "billing" / "README.md").write_text("billing\n", encoding="utf-8")
    _git(repo, "init", "-b", "main", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "checkout", "-q", "-b", "feature-gh-83")
    return repo


def _billing_note(repo: Path) -> str:
    return (
        "Ignored `packages/billing/.dmx/`: it has no `config.md`, and this repo's "
        f"`.dmx/` is at `{repo}`. Runs started from that folder are not picked up; "
        "restart them from here, then delete the folder."
    )


class TestToolCallFromASubfolder:
    @pytest.mark.asyncio
    async def test_run_loop_from_a_package_folder_uses_the_repo_dmx(self, tmp_path: Path) -> None:
        repo = _monorepo(tmp_path)
        billing = repo / "packages" / "billing"

        from fastmcp import Client

        from dmx.server import create_app

        async with Client(create_app()) as client:
            started = await client.call_tool(
                "run_loop", {"name": "dev", "workspace_root": str(billing)}
            )

        assert not (billing / ".dmx").exists(), started.data
        assert list((repo / ".dmx" / "jobs").rglob("dev-*.json")), started.data

    @pytest.mark.asyncio
    async def test_stray_dmx_is_reported_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(workspace, "_REPORTED_SKIPPED_DMX", set())
        repo = _monorepo(tmp_path)
        billing = repo / "packages" / "billing"
        (billing / ".dmx" / "jobs" / "unknown").mkdir(parents=True)
        note = _billing_note(repo)

        from fastmcp import Client

        from dmx.server import create_app

        async with Client(create_app()) as client:
            first = await client.call_tool("list_skills", {"workspace_root": str(billing)})
            second = await client.call_tool("list_skills", {"workspace_root": str(billing)})
            status = await client.call_tool("loop_status", {"workspace_root": str(billing)})

        assert first.data == f"No local or shared skills.\n\n{note}"
        assert second.data == "No local or shared skills."
        assert note not in str(status.data)

    @pytest.mark.asyncio
    async def test_mid_loop_tools_explain_a_run_left_in_a_stray_dmx(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The old behaviour started this run inside the package folder.
        monkeypatch.setattr(workspace, "_REPORTED_SKIPPED_DMX", set())
        repo = _monorepo(tmp_path)
        billing = repo / "packages" / "billing"
        write_initial_state(billing, "dev", "unknown", "task-old", ["implement-next-phase"])
        note = _billing_note(repo)

        from fastmcp import Client

        from dmx.server import create_app

        async with Client(create_app()) as client:
            advance = await client.call_tool(
                "loop_advance",
                {"output": "done", "skill": "implement-next-phase", "workspace_root": str(billing)},
            )
            monkeypatch.setattr(workspace, "_REPORTED_SKIPPED_DMX", set())
            resumed = await client.call_tool("loop_continue", {"workspace_root": str(billing)})

        assert advance.data == (
            f"No active loop run found. Start a loop with `run_loop` first.\n\n{note}"
        )
        assert str(resumed.data).endswith(f"\n\n{note}")

    @pytest.mark.asyncio
    async def test_upgrade_from_a_package_folder_clears_the_rules_reminder(
        self, tmp_path: Path
    ) -> None:
        # Rules live where the IDE opened, so the reminder reads them there,
        # not at the repo root the folder resolved to.
        repo = _monorepo(tmp_path)
        billing = repo / "packages" / "billing"
        old_rules = "<!-- deepmodel:dmx:start 0.4.0 -->\nold\n<!-- deepmodel:dmx:end -->\n"
        for folder in (repo, billing):
            (folder / ".cursor").mkdir()
            (folder / ".cursor" / "AGENTS.md").write_text(old_rules, encoding="utf-8")

        from fastmcp import Client

        from dmx.server import create_app

        async with Client(create_app()) as client:
            before = await client.call_tool("list_skills", {"workspace_root": str(billing)})
            upgrade = await client.call_tool(
                "setup_ide_rules", {"ides": "cursor", "workspace_root": str(billing)}
            )
            target = Path(upgrade.data["workspace_root"])
            for item in upgrade.data["files"]:
                path = target / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item["content"], encoding="utf-8")
            after = await client.call_tool("list_skills", {"workspace_root": str(billing)})

        assert "Run `/dmx/upgrade`" in before.data
        assert target == billing
        assert after.data == "No local or shared skills."


class TestFindDmxRoot:
    def test_subfolder_resolves_to_the_repo_dmx(self, tmp_path: Path) -> None:
        repo = _monorepo(tmp_path)
        assert find_dmx_root(repo / "packages" / "billing") == (repo, ())

    def test_repo_root_resolves_to_itself(self, tmp_path: Path) -> None:
        repo = _monorepo(tmp_path)
        assert find_dmx_root(repo) == (repo, ())

    def test_stray_dmx_without_config_is_skipped(self, tmp_path: Path) -> None:
        repo = _monorepo(tmp_path)
        billing = repo / "packages" / "billing"
        (billing / ".dmx" / "jobs" / "unknown").mkdir(parents=True)
        assert find_dmx_root(billing / "src") == (repo, (billing,))

    def test_nested_project_with_its_own_config_resolves_to_itself(self, tmp_path: Path) -> None:
        repo = _monorepo(tmp_path)
        billing = repo / "packages" / "billing"
        (billing / ".dmx").mkdir()
        (billing / ".dmx" / "config.md").write_text("branch_base: main\n", encoding="utf-8")
        assert find_dmx_root(billing) == (billing, ())

    def test_without_any_config_the_nearest_dmx_wins(self, tmp_path: Path) -> None:
        repo = _monorepo(tmp_path)
        (repo / ".dmx" / "config.md").unlink()
        billing = repo / "packages" / "billing"
        assert find_dmx_root(billing) == (repo, ())
        (billing / ".dmx").mkdir()
        assert find_dmx_root(billing) == (billing, ())

    def test_folder_without_dmx_above_is_unchanged(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        _git(repo, "init", "-q")
        assert find_dmx_root(repo / "src") == (repo / "src", ())

    def test_outside_a_git_repo_nothing_is_walked(self, tmp_path: Path) -> None:
        (tmp_path / ".dmx").mkdir()
        (tmp_path / ".dmx" / "config.md").write_text("branch_base: main\n", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        assert find_dmx_root(sub) == (sub, ())

    def test_walk_stops_at_the_git_repo(self, tmp_path: Path) -> None:
        (tmp_path / ".dmx").mkdir()
        (tmp_path / ".dmx" / "config.md").write_text("branch_base: main\n", encoding="utf-8")
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        _git(repo, "init", "-q")
        assert find_dmx_root(repo / "src") == (repo / "src", ())

    def test_a_worktree_resolves_to_its_own_tree(self, tmp_path: Path) -> None:
        repo = _monorepo(tmp_path)
        tree = repo / ".claude" / "worktrees" / "gh-83"
        _git(repo, "worktree", "add", "-q", str(tree), "-b", "worktree-gh-83")
        assert (tree / ".git").is_file()
        assert find_dmx_root(tree / "packages" / "billing") == (tree, ())
        (tree / ".dmx" / "config.md").unlink()
        assert find_dmx_root(tree / "packages" / "billing") == (tree, ())


class TestResolveFromASubfolder:
    @pytest.mark.asyncio
    async def test_explicit_subfolder_resolves_to_the_repo(self, tmp_path: Path) -> None:
        repo = _monorepo(tmp_path)
        billing = repo / "packages" / "billing"

        result = await resolve_workspace_root(MagicMock(), str(billing))

        assert result == repo
        assert ide_folder() == billing

    @pytest.mark.asyncio
    async def test_mcp_root_at_a_subfolder_resolves_to_the_repo(self, tmp_path: Path) -> None:
        repo = _monorepo(tmp_path)
        root = MagicMock()
        root.uri = f"file://{repo}/packages/billing"
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[root])

        assert await resolve_workspace_root(ctx, None) == repo

    @pytest.mark.asyncio
    async def test_cwd_at_a_subfolder_resolves_to_the_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _monorepo(tmp_path)
        monkeypatch.chdir(repo / "packages" / "billing")
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[])

        assert (await resolve_workspace_root(ctx, None)).resolve() == repo.resolve()

    @pytest.mark.asyncio
    async def test_multi_root_workspace_uses_the_root_with_dmx(self, tmp_path: Path) -> None:
        notes = tmp_path / "notes"
        notes.mkdir()
        _git(notes, "init", "-q")
        repo = _monorepo(tmp_path)
        first, second = MagicMock(), MagicMock()
        first.uri = f"file://{notes}"
        second.uri = f"file://{repo}/packages/billing"
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[first, second])

        assert await resolve_workspace_root(ctx, None) == repo

    @pytest.mark.asyncio
    async def test_multi_root_prefers_any_dmx_over_none(self, tmp_path: Path) -> None:
        notes = tmp_path / "notes"
        notes.mkdir()
        _git(notes, "init", "-q")
        draft = tmp_path / "draft"
        (draft / ".dmx").mkdir(parents=True)
        _git(draft, "init", "-q")
        first, second = MagicMock(), MagicMock()
        first.uri = f"file://{notes}"
        second.uri = f"file://{draft}"
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[first, second])

        assert await resolve_workspace_root(ctx, None) == draft

    @pytest.mark.asyncio
    async def test_explicit_still_wins_over_mcp_roots(self, tmp_path: Path) -> None:
        repo = _monorepo(tmp_path)
        other = tmp_path / "other"
        (other / ".dmx").mkdir(parents=True)
        root = MagicMock()
        root.uri = f"file://{other}"
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[root])

        result = await resolve_workspace_root(ctx, str(repo / "packages" / "billing"))

        assert result == repo
        ctx.list_roots.assert_not_called()

    @pytest.mark.asyncio
    async def test_bootstrap_callers_keep_the_exact_folder(self, tmp_path: Path) -> None:
        repo = _monorepo(tmp_path)
        billing = repo / "packages" / "billing"

        result = await resolve_workspace_root(MagicMock(), str(billing), require_markers=False)

        assert result == billing
        assert ide_folder() == billing
