"""MCP tool implementations: detect_invoking_ide and setup_ide_rules."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from fastmcp import (
    Context,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
    FastMCP,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
)

from dmx._workflow_version import WORKFLOW_VERSION
from dmx.catalog import (
    RuleDefinition,  # noqa: TCH001 — needed at runtime for FastMCP annotation resolution
)
from dmx.exceptions import EmitterError, WorkspaceRootInvalid
from dmx.ide.detect import resolve_ide_targets
from dmx.ide.emitters import emit_ide_rule_files
from dmx.ide.rules_refresh import (
    RULE_WRITE_INSTRUCTIONS,
    SUMMARY_PATHS,
    UnclosedDmxBlock,
    ides_with_dmx_rules,
    merge_dmx_block,
    previous_version_label,
)
from dmx.workspace import resolve_workspace_root

if TYPE_CHECKING:
    from pathlib import Path

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
        include_existing: bool = False,
    ) -> dict[str, object]:
        """Return rule files formatted for the target IDE(s).

        Does **not** write to disk — the agent writes the returned ``files``
        as complete files. Summary files are already merged. Called from
        ``/dmx/init`` and ``/dmx/upgrade``.

        Args:
            ctx: FastMCP request context.
            ides: IDE target(s). If omitted, auto-detected from the request.
            workspace_root: Absolute path to the project root. If omitted,
                resolved from MCP roots, then ``cwd``.
            overwrite: Accepted for compatibility. Returned files are complete.
            include_existing: When ``True``, also emit for every IDE that
                already has dmx rule files. ``/dmx/upgrade`` sets this.
                When those files are already current, ``files`` is empty.

        Returns:
            A dict with keys:
            - ``resolved_ides``: list of canonical IDE identifiers used.
            - ``ides_source``: how the IDEs were resolved.
            - ``workspace_root``: the resolved workspace root path.
            - ``workflow_version``: this package's workflow version.
            - ``already_current``: every returned file already matches disk.
            - ``previous_version``: oldest version found, ``"an earlier version"``
              when no file has a version, or ``None`` when no dmx rule file
              was found.
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
        # require_markers=False — this is the pre-`.dmx`/`.git` bootstrap
        # call (`/dmx-init` may run before the project is even a git repo).
        try:
            root = await resolve_workspace_root(ctx, workspace_root, require_markers=False)
        except WorkspaceRootInvalid as exc:
            return {
                "resolved_ides": [],
                "ides_source": ides_source,
                "workspace_root": None,
                "files": [],
                "notes": f"Could not resolve a valid workspace root: {exc}",
            }

        if include_existing:
            resolved_ides = tuple(dict.fromkeys((*resolved_ides, *ides_with_dmx_rules(root))))

        if not resolved_ides:
            return {
                "resolved_ides": [],
                "ides_source": ides_source,
                "workspace_root": str(root),
                "files": [],
                "already_current": False,
                "previous_version": None,
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
                "already_current": False,
                "previous_version": None,
                "notes": (
                    f"Rule file generation failed for IDE(s) {list(resolved_ides)}: {exc}. "
                    "Check server logs for details. "
                    "You can retry with a different `ides` value or report this as a bug."
                ),
            }

        skipped: list[str] = []
        unclosed: list[str] = []
        files: list[dict[str, str]] = []
        for rule_file in rule_files:
            content = rule_file.content
            disk = root / rule_file.path
            if rule_file.path in SUMMARY_PATHS and disk.is_file():
                try:
                    content = merge_dmx_block(disk.read_text(encoding="utf-8"), content)
                except OSError:
                    skipped.append(rule_file.path)
                    continue
                except UnclosedDmxBlock:
                    unclosed.append(rule_file.path)
                    continue
            files.append({"path": rule_file.path, "content": content, "ide": rule_file.ide})

        previous = previous_version_label(root, resolved_ides)
        already = (
            bool(files)
            and not skipped
            and not unclosed
            and all(_file_matches(root / item["path"], item["content"]) for item in files)
        )
        notes = RULE_WRITE_INSTRUCTIONS
        if skipped:
            notes += " Could not read and did not replace: " + ", ".join(skipped) + "."
        if unclosed:
            listed = ", ".join(unclosed)
            notes += (
                f" Could not find the end of the dmx block in {listed}; fix it by hand and re-run."
            )
        if already and include_existing:
            notes = (
                f"dmx rules are already current ({WORKFLOW_VERSION}). Do not write files. " + notes
            )
            files = []

        return {
            "resolved_ides": list(resolved_ides),
            "ides_source": ides_source,
            "workspace_root": str(root),
            "workflow_version": WORKFLOW_VERSION,
            "already_current": already,
            "previous_version": previous,
            "files": files,
            "notes": notes,
        }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _file_matches(path: Path, content: str) -> bool:
    """True when *path* already contains *content*."""
    try:
        return path.is_file() and path.read_text(encoding="utf-8") == content
    except OSError:
        return False


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
