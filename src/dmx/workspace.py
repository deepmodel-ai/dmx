"""Shared workspace root resolution for MCP tools.

Resolution order (highest precedence first):

1. Explicit ``workspace_root`` argument, supplied by the calling agent.
   Must be an absolute path (``~`` is expanded) — a relative value is
   rejected rather than silently resolved against the server's cwd.
2. The first MCP root reported by the client (``ctx.list_roots()``).
3. The dmx server process's current working directory, as a last resort.

Every resolved path is validated before being returned — see
:func:`validate_workspace_root`. Resolution is re-run on every tool call;
nothing is cached, so a rejected or stale value can never linger across
calls. This trades a couple of cheap ``stat()`` calls and an MCP round-trip
per tool call for the guarantee that we never silently write into the wrong
directory.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from dmx.exceptions import WorkspaceRootInvalid

if TYPE_CHECKING:
    from fastmcp import Context

__all__ = ["resolve_workspace_root", "validate_workspace_root"]

logger = logging.getLogger(__name__)

_PROJECT_MARKERS = (".git", ".dmx")


def _parse_root_uri(root: object) -> Path:
    """Parse a single MCP ``Root`` entry (or its string form) into a Path."""
    uri = getattr(root, "uri", None) or str(root)
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    return Path(unquote(uri))


def _is_dangerous(path: Path) -> bool:
    """Return True if *path* is a filesystem root or the bare home directory.

    These are never a legitimate dmx project root — writing there is almost
    always the result of a broken ``workspace_root`` resolution (e.g. an MCP
    client that didn't report roots, combined with a server process spawned
    with an unrelated cwd).
    """
    resolved = path.resolve()
    if resolved == Path(resolved.anchor):
        return True
    try:
        home = Path.home().resolve()
    except RuntimeError:  # pragma: no cover — no home dir resolvable
        return False
    return resolved == home


def validate_workspace_root(root: Path, *, explicit: bool, require_markers: bool = True) -> None:
    """Validate a resolved workspace root, raising if it looks wrong.

    Args:
        root: The resolved candidate path.
        explicit: Whether *root* came from an explicit ``workspace_root``
            argument (as opposed to auto-detection via MCP roots or cwd).
            Explicit values skip the project-marker check — the agent may be
            pointing at a brand-new project that hasn't run ``git init`` or
            ``/dmx-init`` yet.
        require_markers: If ``False``, auto-detected roots also skip the
            project-marker check. Used by callers that legitimately run
            before ``.dmx``/``.git`` exist (e.g. ``setup_ide_rules``, called
            from ``/dmx-init`` on a project that may not be a git repo yet).

    Raises:
        WorkspaceRootInvalid: If *root* is a filesystem/home root, or (for
            auto-detected roots, when markers are required) has no ``.git``
            or ``.dmx`` marker.
    """
    if _is_dangerous(root):
        raise WorkspaceRootInvalid(
            str(root),
            "this looks like a filesystem root or home directory, not a project. "
            "Call again with `workspace_root` set explicitly to the correct absolute path.",
        )

    if explicit or not require_markers:
        return

    if not any((root / marker).exists() for marker in _PROJECT_MARKERS):
        raise WorkspaceRootInvalid(
            str(root),
            "no `.git` or `.dmx` found here — this doesn't look like a project root. "
            "If your project lives in a subfolder, re-run this command from inside it, "
            "or call again with `workspace_root` set explicitly to its absolute path.",
        )


async def resolve_workspace_root(
    ctx: Context, explicit: str | None, *, require_markers: bool = True
) -> Path:
    """Resolve and validate the workspace root for the current tool call.

    Args:
        ctx: FastMCP request context.
        explicit: Explicit workspace root path, or ``None`` to auto-detect.
            Must be absolute (``~`` is expanded first) — a relative path
            would just reintroduce the same cwd ambiguity this module
            exists to avoid.
        require_markers: Passed through to :func:`validate_workspace_root` —
            set ``False`` for callers that may legitimately run before
            ``.dmx``/``.git`` exist.

    Returns:
        A validated, absolute :class:`Path`.

    Raises:
        WorkspaceRootInvalid: If *explicit* is set but not an absolute path,
            or if the resolved path fails validation.
    """
    if explicit:
        explicit_root = Path(explicit).expanduser()
        if not explicit_root.is_absolute():
            raise WorkspaceRootInvalid(
                explicit,
                "workspace_root must be an absolute path. "
                "Call again with the project's full absolute path.",
            )
        validate_workspace_root(explicit_root, explicit=True)
        return explicit_root

    detected: Path | None = None
    try:
        roots = await ctx.list_roots()
        if roots:
            detected = _parse_root_uri(roots[0])
    except Exception:  # noqa: BLE001
        pass

    if detected is None:
        detected = Path(os.getcwd())
        logger.warning(
            "workspace_root not provided and MCP roots unavailable; falling back to cwd: %s",
            detected,
        )

    validate_workspace_root(detected, explicit=False, require_markers=require_markers)
    return detected
