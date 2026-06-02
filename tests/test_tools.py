"""Tests for dmx.tools — detect_invoking_ide, setup_ide_rules, and helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Helpers: _extract_client_name
# ---------------------------------------------------------------------------


class TestExtractClientName:
    """Unit tests for the private _extract_client_name helper."""

    def _make_ctx(self, name: str | None) -> object:
        client_info = MagicMock()
        client_info.name = name
        params = MagicMock()
        params.clientInfo = client_info
        session = MagicMock()
        session.client_params = params
        ctx = MagicMock()
        ctx.session = session
        return ctx

    def test_returns_name_when_present(self) -> None:
        from dmx.tools import _extract_client_name  # noqa: PLC0415

        ctx = self._make_ctx("Cursor/1.0")
        assert _extract_client_name(ctx) == "Cursor/1.0"  # type: ignore[arg-type]

    def test_returns_none_when_name_is_none(self) -> None:
        from dmx.tools import _extract_client_name  # noqa: PLC0415

        ctx = self._make_ctx(None)
        assert _extract_client_name(ctx) is None  # type: ignore[arg-type]

    def test_returns_none_when_name_is_empty_string(self) -> None:
        from dmx.tools import _extract_client_name  # noqa: PLC0415

        ctx = self._make_ctx("")
        assert _extract_client_name(ctx) is None  # type: ignore[arg-type]

    def test_returns_none_on_missing_session(self) -> None:
        from dmx.tools import _extract_client_name  # noqa: PLC0415

        ctx = MagicMock()
        ctx.session = None
        assert _extract_client_name(ctx) is None  # type: ignore[arg-type]

    def test_returns_none_on_attribute_error(self) -> None:
        from dmx.tools import _extract_client_name  # noqa: PLC0415

        ctx = MagicMock()
        ctx.session = MagicMock(spec=[])  # no attributes at all
        assert _extract_client_name(ctx) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers: _extract_ide_header
# ---------------------------------------------------------------------------


class TestExtractIdeHeader:
    """Unit tests for the private _extract_ide_header helper."""

    def _make_ctx(self, header_value: str | None) -> object:
        headers = MagicMock()
        headers.get = MagicMock(return_value=header_value)
        meta = MagicMock()
        meta.headers = headers
        ctx = MagicMock()
        ctx.request_context = meta
        return ctx

    def test_returns_header_value_when_present(self) -> None:
        from dmx.tools import _extract_ide_header  # noqa: PLC0415

        ctx = self._make_ctx("claude")
        assert _extract_ide_header(ctx) == "claude"  # type: ignore[arg-type]

    def test_returns_none_when_no_request_context(self) -> None:
        from dmx.tools import _extract_ide_header  # noqa: PLC0415

        ctx = MagicMock()
        ctx.request_context = None
        assert _extract_ide_header(ctx) is None  # type: ignore[arg-type]

    def test_returns_none_when_no_headers_attr(self) -> None:
        from dmx.tools import _extract_ide_header  # noqa: PLC0415

        meta = MagicMock(spec=[])  # no 'headers' attribute
        ctx = MagicMock()
        ctx.request_context = meta
        assert _extract_ide_header(ctx) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers: _resolve_workspace_root
# ---------------------------------------------------------------------------


class TestResolveWorkspaceRoot:
    """Unit tests for the private _resolve_workspace_root helper."""

    @pytest.mark.asyncio
    async def test_returns_explicit_path(self) -> None:
        from dmx.tools import _resolve_workspace_root  # noqa: PLC0415

        ctx = MagicMock()
        result = await _resolve_workspace_root(ctx, "/home/user/project")  # type: ignore[arg-type]
        assert result == Path("/home/user/project")

    @pytest.mark.asyncio
    async def test_uses_mcp_root_file_uri(self) -> None:
        from dmx.tools import _resolve_workspace_root  # noqa: PLC0415

        root = MagicMock()
        root.uri = "file:///home/user/myproject"
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[root])

        result = await _resolve_workspace_root(ctx, None)  # type: ignore[arg-type]
        assert result == Path("/home/user/myproject")

    @pytest.mark.asyncio
    async def test_decodes_percent_encoded_uri(self) -> None:
        from dmx.tools import _resolve_workspace_root  # noqa: PLC0415

        root = MagicMock()
        root.uri = "file:///home/user/my%20project"
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[root])

        result = await _resolve_workspace_root(ctx, None)  # type: ignore[arg-type]
        assert result == Path("/home/user/my project")

    @pytest.mark.asyncio
    async def test_falls_back_to_cwd_when_no_roots(self, tmp_path: Path) -> None:
        from dmx.tools import _resolve_workspace_root  # noqa: PLC0415

        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[])

        result = await _resolve_workspace_root(ctx, None)  # type: ignore[arg-type]
        assert result == Path(os.getcwd())

    @pytest.mark.asyncio
    async def test_falls_back_to_cwd_when_list_roots_raises(self) -> None:
        from dmx.tools import _resolve_workspace_root  # noqa: PLC0415

        ctx = MagicMock()
        ctx.list_roots = AsyncMock(side_effect=RuntimeError("not supported"))

        result = await _resolve_workspace_root(ctx, None)  # type: ignore[arg-type]
        assert result == Path(os.getcwd())

    @pytest.mark.asyncio
    async def test_explicit_takes_precedence_over_roots(self) -> None:
        from dmx.tools import _resolve_workspace_root  # noqa: PLC0415

        root = MagicMock()
        root.uri = "file:///from/roots"
        ctx = MagicMock()
        ctx.list_roots = AsyncMock(return_value=[root])

        result = await _resolve_workspace_root(ctx, "/explicit/path")  # type: ignore[arg-type]
        assert result == Path("/explicit/path")
        ctx.list_roots.assert_not_called()


# ---------------------------------------------------------------------------
# detect_invoking_ide — via FastMCP in-process Client
# ---------------------------------------------------------------------------


class TestDetectInvokingIde:
    """Integration tests for the detect_invoking_ide MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_unknown_with_no_client_info(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool("detect_invoking_ide", {})
        assert result.data["source"] == "unknown"
        assert result.data["ides"] == []
        assert "hint" in result.data

    @pytest.mark.asyncio
    async def test_detects_cursor_from_client_info(self) -> None:
        import mcp.types  # noqa: PLC0415

        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        info = mcp.types.Implementation(name="Cursor/1.0", version="1.0")
        async with Client(app, client_info=info) as client:
            result = await client.call_tool("detect_invoking_ide", {})
        assert result.data["source"] == "client_info"
        assert "cursor" in result.data["ides"]

    @pytest.mark.asyncio
    async def test_detects_claude_from_client_info(self) -> None:
        import mcp.types  # noqa: PLC0415

        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        info = mcp.types.Implementation(name="claude-code/1.0", version="1.0")
        async with Client(app, client_info=info) as client:
            result = await client.call_tool("detect_invoking_ide", {})
        assert result.data["source"] == "client_info"
        assert "claude" in result.data["ides"]

    @pytest.mark.asyncio
    async def test_env_var_overrides_client_info(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import mcp.types  # noqa: PLC0415

        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        monkeypatch.setenv("DMX_IDE", "antigravity")
        app = create_app()
        info = mcp.types.Implementation(name="Cursor/1.0", version="1.0")
        async with Client(app, client_info=info) as client:
            result = await client.call_tool("detect_invoking_ide", {})
        assert result.data["source"] == "env"
        assert "antigravity" in result.data["ides"]

    @pytest.mark.asyncio
    async def test_hint_present_on_success(self) -> None:
        import mcp.types  # noqa: PLC0415

        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        info = mcp.types.Implementation(name="Cursor", version="1.0")
        async with Client(app, client_info=info) as client:
            result = await client.call_tool("detect_invoking_ide", {})
        assert isinstance(result.data["hint"], str)
        assert len(result.data["hint"]) > 0

    @pytest.mark.asyncio
    async def test_hint_present_on_unknown(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool("detect_invoking_ide", {})
        assert "hint" in result.data
        assert "DMX_IDE" in result.data["hint"]


# ---------------------------------------------------------------------------
# setup_ide_rules — via FastMCP in-process Client
# ---------------------------------------------------------------------------


class TestSetupIdeRules:
    """Integration tests for the setup_ide_rules MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_empty_files_when_no_ide(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool("setup_ide_rules", {})
        assert result.data["files"] == []
        assert result.data["resolved_ides"] == []

    @pytest.mark.asyncio
    async def test_cursor_returns_mdc_and_agents_md(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": "cursor", "workspace_root": "/tmp/ws"},
            )
        paths = {f["path"] for f in result.data["files"]}
        assert any(p.startswith(".cursor/rules/") and p.endswith(".mdc") for p in paths)
        assert ".cursor/AGENTS.md" in paths

    @pytest.mark.asyncio
    async def test_claude_returns_claude_md_and_rules(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": "claude", "workspace_root": "/tmp/ws"},
            )
        paths = {f["path"] for f in result.data["files"]}
        assert any(p.startswith(".claude/rules/") for p in paths)
        assert "CLAUDE.md" in paths

    @pytest.mark.asyncio
    async def test_copilot_returns_only_instructions_file(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": "copilot", "workspace_root": "/tmp/ws"},
            )
        paths = {f["path"] for f in result.data["files"]}
        assert paths == {".github/copilot-instructions.md"}

    @pytest.mark.asyncio
    async def test_antigravity_returns_only_agents_rules(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": "antigravity", "workspace_root": "/tmp/ws"},
            )
        paths = {f["path"] for f in result.data["files"]}
        assert all(p.startswith(".agents/rules/") for p in paths)
        assert not any("AGENTS.md" == p for p in paths)

    @pytest.mark.asyncio
    async def test_agents_returns_only_agents_md(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": "agents", "workspace_root": "/tmp/ws"},
            )
        paths = {f["path"] for f in result.data["files"]}
        assert paths == {"AGENTS.md"}

    @pytest.mark.asyncio
    async def test_string_ides_arg_normalised(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": "cursor", "workspace_root": "/tmp/ws"},
            )
        assert result.data["resolved_ides"] == ["cursor"]

    @pytest.mark.asyncio
    async def test_list_ides_arg_normalised(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": ["cursor"], "workspace_root": "/tmp/ws"},
            )
        assert result.data["resolved_ides"] == ["cursor"]

    @pytest.mark.asyncio
    async def test_explicit_workspace_root_in_response(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": "cursor", "workspace_root": "/home/dev/project"},
            )
        assert result.data["workspace_root"] == "/home/dev/project"

    @pytest.mark.asyncio
    async def test_files_have_path_content_ide_keys(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": "cursor", "workspace_root": "/tmp/ws"},
            )
        for f in result.data["files"]:
            assert "path" in f
            assert "content" in f
            assert "ide" in f

    @pytest.mark.asyncio
    async def test_notes_cover_marker_instructions(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": "cursor", "workspace_root": "/tmp/ws"},
            )
        notes = result.data["notes"]
        assert "deepmodel:dmx:start" in notes
        assert "deepmodel:dmx:end" in notes

    @pytest.mark.asyncio
    async def test_response_includes_workflow_version(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx._workflow_version import WORKFLOW_VERSION  # noqa: PLC0415
        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": "cursor", "workspace_root": "/tmp/ws"},
            )
        assert result.data["workflow_version"] == WORKFLOW_VERSION

    @pytest.mark.asyncio
    async def test_marker_start_contains_workflow_version(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx._workflow_version import WORKFLOW_VERSION  # noqa: PLC0415
        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": "cursor", "workspace_root": "/tmp/ws"},
            )
        summary = next(f for f in result.data["files"] if f["path"] == ".cursor/AGENTS.md")
        assert WORKFLOW_VERSION in summary["content"]

    @pytest.mark.asyncio
    async def test_ide_detected_from_client_info(self) -> None:
        import mcp.types  # noqa: PLC0415

        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        info = mcp.types.Implementation(name="Cursor/1.0", version="1.0")
        async with Client(app, client_info=info) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"workspace_root": "/tmp/ws"},
            )
        assert result.data["resolved_ides"] == ["cursor"]
        assert result.data["ides_source"] == "client_info"

    @pytest.mark.asyncio
    async def test_ides_source_is_explicit_when_arg_given(self) -> None:
        from fastmcp import Client  # noqa: PLC0415

        from dmx.server import create_app  # noqa: PLC0415

        app = create_app()
        async with Client(app) as client:
            result = await client.call_tool(
                "setup_ide_rules",
                {"ides": "cursor", "workspace_root": "/tmp/ws"},
            )
        assert result.data["ides_source"] == "explicit"
