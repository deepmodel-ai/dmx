"""Shared workspace root resolution for MCP tools.

Resolution order (highest precedence first):

1. Explicit ``workspace_root`` argument, supplied by the calling agent.
   Must be an absolute path (``~`` is expanded) — a relative value is
   rejected rather than silently resolved against the server's cwd.
2. An MCP root reported by the client (``ctx.list_roots()``). In a
   multi-root workspace, the first whose walk-up finds a ``.dmx/`` wins;
   otherwise the first root.
3. The dmx server process's current working directory, as a last resort.

The chosen folder may be a subfolder of the project, for example one package
of a monorepo. Unless the caller is bootstrapping (``require_markers=False``),
dmx walks up from it to the repo's ``.dmx/`` — see :func:`find_dmx_root`
(GH-83).

Every resolved path is validated before being returned — see
:func:`validate_workspace_root`. Resolution is re-run on every tool call;
nothing is cached, so a rejected or stale value can never linger across
calls. This trades a couple of cheap ``stat()`` calls and an MCP round-trip
per tool call for the guarantee that we never silently write into the wrong
directory.
"""

from __future__ import annotations

import contextvars
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from dmx.exceptions import WorkspaceRootInvalid

if TYPE_CHECKING:
    from fastmcp import Context

__all__ = [
    "find_dmx_root",
    "ide_folder",
    "resolve_workspace_root",
    "validate_workspace_root",
    "with_skipped_dmx_note",
]

logger = logging.getLogger(__name__)

_PROJECT_MARKERS = (".git", ".dmx")

# The folder this tool call pointed at, before walking up to the repo's
# `.dmx/`. Kept so a later step can tell which part of the repo it was.
_IDE_FOLDER: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "dmx_ide_folder", default=None
)
# `.dmx/` folders without a config that this call's resolution skipped.
_SKIPPED_DMX: contextvars.ContextVar[tuple[Path, ...]] = contextvars.ContextVar(
    "dmx_skipped_dmx", default=()
)
# Skipped folders already reported, so each is reported once per server.
_REPORTED_SKIPPED_DMX: set[Path] = set()


def _parse_root_uri(root: object) -> Path:
    """Parse a single MCP ``Root`` entry (or its string form) into a Path."""
    uri = getattr(root, "uri", None) or str(root)
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        # url2pathname turns `/C:/Users/x` into `C:\Users\x` on Windows and
        # decodes `%20`; a host other than localhost is a UNC share.
        path = parsed.path
        if parsed.netloc and parsed.netloc != "localhost":
            path = f"//{parsed.netloc}{path}"
        return Path(url2pathname(path))
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


def _git_top(start: Path) -> Path | None:
    """The nearest folder at or above *start* that contains ``.git``, if any.

    ``.git`` is a directory in a normal checkout and a file in a worktree or
    submodule, so either counts. Each worktree is its own repository here.
    """
    for folder in (start, *start.parents):
        if (folder / ".git").exists():
            return folder
    return None


def _has_dmx_config(folder: Path) -> bool:
    return (folder / ".dmx" / "config.md").is_file()


def find_dmx_root(start: Path) -> tuple[Path, tuple[Path, ...]]:
    """Walk up from *start* to the repo's ``.dmx/``, within the same git repo.

    Returns the folder to use and the config-less ``.dmx/`` folders skipped
    below it. The nearest ``.dmx/`` with a ``config.md`` wins, so a stray
    ``.dmx/`` left in a package folder is skipped and a nested dmx project
    with its own config still resolves to itself. With no ``config.md``
    anywhere, the nearest ``.dmx/`` wins and nothing is skipped. Outside a git
    repo, or with no ``.dmx/`` at all, *start* is returned unchanged. The
    filesystem root and the home directory are never used.
    """
    top = _git_top(start)
    if top is None:
        return start, ()
    candidates: list[Path] = []
    for folder in (start, *start.parents):
        if (folder / ".dmx").is_dir() and not _is_dangerous(folder):
            candidates.append(folder)
        if folder == top:
            break
    for index, folder in enumerate(candidates):
        if _has_dmx_config(folder):
            return folder, tuple(candidates[:index])
    if candidates:
        return candidates[0], ()
    return start, ()


def _pick_mcp_folder(folders: list[Path]) -> Path:
    """Choose among the client's MCP roots in a multi-root workspace.

    The first root whose walk-up finds a ``.dmx/`` with a ``config.md`` wins,
    then the first that finds any ``.dmx/``, then the first root, as before.
    """
    found = [(folder, find_dmx_root(folder)[0]) for folder in folders]
    for folder, root in found:
        if _has_dmx_config(root):
            return folder
    for folder, root in found:
        if (root / ".dmx").is_dir():
            return folder
    return folders[0]


def ide_folder() -> Path | None:
    """The folder the current tool call pointed at, before walking up.

    ``None`` before :func:`resolve_workspace_root` has run in this call.
    """
    return _IDE_FOLDER.get()


def with_skipped_dmx_note(root: Path, message: str) -> str:
    """Append one line per skipped ``.dmx/`` not yet reported, then clear them."""
    skipped = _SKIPPED_DMX.get()
    _SKIPPED_DMX.set(())
    lines: list[str] = []
    for folder in skipped:
        if folder in _REPORTED_SKIPPED_DMX:
            continue
        _REPORTED_SKIPPED_DMX.add(folder)
        shown = (folder.relative_to(root) if folder.is_relative_to(root) else folder).as_posix()
        lines.append(
            f"Ignored `{shown}/.dmx/`: it has no `config.md`, and this repo's `.dmx/` "
            f"is at `{root}`. Runs started from that folder are not picked up; "
            "restart them from here, then delete the folder."
        )
    if not lines:
        return message
    return message + "\n\n" + "\n".join(lines)


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
        A validated, absolute :class:`Path`: the folder that holds the repo's
        ``.dmx/`` (see :func:`find_dmx_root`), or the chosen folder itself
        when bootstrapping or when no ``.dmx/`` is found above it.

    Raises:
        WorkspaceRootInvalid: If *explicit* is set but not an absolute path,
            or if the resolved path fails validation.
    """
    _IDE_FOLDER.set(None)
    _SKIPPED_DMX.set(())

    if explicit:
        explicit_root = Path(explicit).expanduser()
        if not explicit_root.is_absolute():
            raise WorkspaceRootInvalid(
                explicit,
                "workspace_root must be an absolute path. "
                "Call again with the project's full absolute path.",
            )
        root = _walk_up(explicit_root, require_markers=require_markers)
        validate_workspace_root(root, explicit=True)
        return root

    folders: list[Path] = []
    try:
        roots = await ctx.list_roots()
        if roots:
            folders = [_parse_root_uri(entry) for entry in roots]
    except Exception:  # noqa: BLE001
        pass

    if folders:
        folder = _pick_mcp_folder(folders) if require_markers else folders[0]
    else:
        folder = Path(os.getcwd())
        logger.warning(
            "workspace_root not provided and MCP roots unavailable; falling back to cwd: %s",
            folder,
        )

    detected = _walk_up(folder, require_markers=require_markers)
    validate_workspace_root(detected, explicit=False, require_markers=require_markers)
    return detected


def _walk_up(folder: Path, *, require_markers: bool) -> Path:
    """Record *folder* for :func:`ide_folder` and return the root to use.

    Bootstrapping callers (``require_markers=False``) keep *folder* as it is.
    """
    _IDE_FOLDER.set(folder)
    if not require_markers:
        return folder
    root, skipped = find_dmx_root(folder)
    if skipped:
        _SKIPPED_DMX.set(skipped)
        for stray in skipped:
            logger.warning("ignored %s/.dmx: no config.md; using %s/.dmx", stray, root)
    return root
