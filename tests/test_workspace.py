"""Tests for dmx.workspace — resolve_workspace_root and validate_workspace_root."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from dmx.exceptions import WorkspaceRootInvalid
from dmx.workspace import resolve_workspace_root, validate_workspace_root

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
        root.uri = f"file://{Path.home()}"
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
