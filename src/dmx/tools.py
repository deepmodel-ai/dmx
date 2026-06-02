"""MCP tool implementations: detect_invoking_ide and setup_ide_rules."""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path  # noqa: TCH003 — used at runtime in Path() calls
from urllib.parse import unquote, urlparse

from fastmcp import (
    Context,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
    FastMCP,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
)

from dmx._workflow_version import WORKFLOW_VERSION
from dmx.catalog import (
    RuleDefinition,  # noqa: TCH001 — needed at runtime for FastMCP annotation resolution
)
from dmx.exceptions import EmitterError
from dmx.ide.detect import resolve_ide_targets
from dmx.ide.emitters import DMX_MARKER_END, DMX_MARKER_START, emit_ide_rule_files

__all__ = ["register_tools"]

logger = logging.getLogger(__name__)


def register_tools(app: FastMCP, rules: tuple[RuleDefinition, ...]) -> None:
    """Register MCP tools on *app*.

    Tools are implementation details called by the ``/dmx-init`` skill.
    They are not intended to be called directly by users.

    Args:
        app: The :class:`FastMCP` application instance.
        rules: Parsed rule definitions available for emission.
    """

    @app.tool
    async def detect_invoking_ide(ctx: Context) -> dict[str, object]:
        """Identify the IDE making the current MCP request.

        Detection order: DMX_IDE/DEEPMODEL_IDE env var → X-Dmx-IDE header →
        MCP clientInfo.name pattern matching → unknown fallback.

        Returns:
            A dict with keys:
            - ``ides``: list of canonical IDE identifiers (may be empty).
            - ``source``: how the IDE was resolved (``"env"``, ``"header"``,
              ``"client_info"``, ``"explicit"``, or ``"unknown"``).
            - ``hint``: human-readable message for the agent.
        """
        client_name = _extract_client_name(ctx)
        ide_from_header = _extract_ide_header(ctx)

        ides, source = resolve_ide_targets(
            explicit_ides=None,
            client_name=client_name,
            ide_from_header=ide_from_header,
        )

        hint = (
            f"Detected IDE: {', '.join(ides)} (via {source})"
            if ides
            else (
                "Could not detect IDE automatically. "
                "Pass the `ides` argument to setup_ide_rules explicitly, "
                "or set the DMX_IDE environment variable."
            )
        )

        return {"ides": list(ides), "source": source, "hint": hint}

    @app.tool
    async def setup_ide_rules(
        ctx: Context,
        ides: str | list[str] | None = None,
        workspace_root: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, object]:
        """Return rule files formatted for the target IDE(s).

        Does **not** write to disk — the agent writes the returned ``files``.
        Called from within the ``/dmx-init`` skill; not a user-facing command.

        Args:
            ctx: FastMCP request context.
            ides: IDE target(s). If omitted, auto-detected from the request.
            workspace_root: Absolute path to the project root. If omitted,
                resolved from MCP roots, then ``cwd``.
            overwrite: If ``True``, existing rule files are replaced.

        Returns:
            A dict with keys:
            - ``resolved_ides``: list of canonical IDE identifiers used.
            - ``ides_source``: how the IDEs were resolved.
            - ``workspace_root``: the resolved workspace root path.
            - ``files``: list of ``{path, content, ide}`` dicts to write.
            - ``notes``: human-readable notes for the agent.
        """
        # Normalise ides argument.
        explicit_list: list[str] | None = None
        if isinstance(ides, str):
            explicit_list = [ides]
        elif isinstance(ides, list):
            explicit_list = ides

        client_name = _extract_client_name(ctx)
        ide_from_header = _extract_ide_header(ctx)

        resolved_ides, ides_source = resolve_ide_targets(
            explicit_ides=explicit_list,
            client_name=client_name,
            ide_from_header=ide_from_header,
        )

        # Resolve workspace root: explicit → MCP roots → cwd (with warning).
        root = await _resolve_workspace_root(ctx, workspace_root)

        if not resolved_ides:
            return {
                "resolved_ides": [],
                "ides_source": ides_source,
                "workspace_root": str(root),
                "files": [],
                "notes": (
                    "No IDE could be determined. "
                    "Pass the `ides` argument explicitly (e.g. ides='cursor')."
                ),
            }

        try:
            rule_files = emit_ide_rule_files(rules, resolved_ides)
        except EmitterError as exc:
            logger.error("emitter failed for %s: %s", resolved_ides, exc)
            return {
                "resolved_ides": list(resolved_ides),
                "ides_source": ides_source,
                "workspace_root": str(root),
                "files": [],
                "notes": (
                    f"Rule file generation failed for IDE(s) {list(resolved_ides)}: {exc}. "
                    "Check server logs for details. "
                    "You can retry with a different `ides` value or report this as a bug."
                ),
            }

        files = [{"path": f.path, "content": f.content, "ide": f.ide} for f in rule_files]

        notes = (
            f"Write each file to <workspace_root>/<path>. "
            f"overwrite={overwrite}. "
            f"For per-rule files (.cursor/rules/*.mdc, .claude/rules/*.md, "
            f".agents/rules/*.md): write directly (create parent dirs as needed). "
            f"For summary/merged files (.cursor/AGENTS.md, CLAUDE.md, AGENTS.md, "
            f".github/copilot-instructions.md): if the file already exists, replace "
            f"the content between '{DMX_MARKER_START}' and '{DMX_MARKER_END}' markers "
            f"with the new block; if the markers are absent, append the block to the "
            f"end of the file. "
            f"After writing, open a new chat for the rules to take effect."
        )

        return {
            "resolved_ides": list(resolved_ides),
            "ides_source": ides_source,
            "workspace_root": str(root),
            "workflow_version": WORKFLOW_VERSION,
            "files": files,
            "notes": notes,
        }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_client_name(ctx: Context) -> str | None:
    """Extract MCP ``clientInfo.name`` from the session handshake.

    Reads ``ctx.session.client_params.clientInfo.name`` — the field populated
    by the IDE during the MCP ``initialize`` handshake.  Falls back to
    ``None`` safely on any attribute access failure.
    """
    with contextlib.suppress(Exception):
        session = ctx.session
        params = getattr(session, "client_params", None)
        if params is not None:
            client_info = getattr(params, "clientInfo", None)
            if client_info is not None:
                name: str | None = getattr(client_info, "name", None)
                return name or None
    return None


def _extract_ide_header(ctx: Context) -> str | None:
    """Extract the ``X-Dmx-IDE`` HTTP header value, if present.

    Only populated on HTTP/SSE transport; returns ``None`` for stdio.
    """
    with contextlib.suppress(Exception):
        meta = getattr(ctx, "request_context", None)
        if meta and hasattr(meta, "headers"):
            return meta.headers.get("x-dmx-ide")  # type: ignore[no-any-return]
    return None


async def _resolve_workspace_root(ctx: Context, explicit: str | None) -> Path:
    """Resolve workspace root with fallback chain.

    1. Explicit ``workspace_root`` argument.
    2. First MCP root from the protocol handshake (``await ctx.list_roots()``).
    3. Current working directory (logged as warning).

    Args:
        ctx: FastMCP request context.
        explicit: Explicit workspace root path, or ``None``.

    Returns:
        Resolved :class:`Path`.
    """
    if explicit:
        return Path(explicit)

    try:
        roots = await ctx.list_roots()
        if roots:
            first_root = roots[0]
            uri = getattr(first_root, "uri", None) or str(first_root)
            # MCP roots URIs are file:// URIs or percent-encoded plain paths.
            parsed = urlparse(uri)
            if parsed.scheme == "file":
                return Path(unquote(parsed.path))
            return Path(unquote(uri))
    except Exception:  # noqa: BLE001
        pass

    cwd = Path(os.getcwd())
    logger.warning(
        "workspace_root not provided and MCP roots unavailable; falling back to cwd: %s",
        cwd,
    )
    return cwd
