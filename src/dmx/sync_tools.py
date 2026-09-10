"""MCP tool: ``sync_shared_sources`` — the deterministic half of ``/dmx/sync``.

Called from within the ``/dmx-sync`` skill; not a user-facing command on its
own (same pattern as ``setup_ide_rules``, called from within ``/dmx-init``).
The skill still owns the parts that need agent judgment or should stay a
plain, reviewable git operation: committing and pushing the result.
"""

from __future__ import annotations

import logging

from fastmcp import (
    Context,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
    FastMCP,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
)

from dmx.exceptions import WorkspaceRootInvalid
from dmx.shared_sources import SharedSourceError, read_shared_sources
from dmx.sync_runner import (
    SyncError,
    detect_collisions,
    require_non_base_branch_error,
    sync_source,
)
from dmx.workspace import resolve_workspace_root

__all__ = ["register_sync_tools"]

logger = logging.getLogger(__name__)


def register_sync_tools(app: FastMCP) -> None:
    """Register ``sync_shared_sources`` on *app*."""

    @app.tool
    async def sync_shared_sources(ctx: Context, workspace_root: str | None = None) -> str:
        """Clone/fetch every declared shared source and vendor it into ``.dmx/vendor/``.

        Reads ``.dmx/shared-sources.yaml``; for each declared source, clones it
        at its pinned ``ref``, copies its tree into ``.dmx/vendor/{name}/``
        (replacing any previous contents), and writes a per-source
        ``.lock.json`` recording the resolved commit SHA. Does **not** commit
        or push — the calling skill does that once this returns.

        After vendoring, checks for same-name collisions across the app
        repo's own ``loops``/``skills``/``validators`` and every declared
        source (in declared-order precedence) and surfaces them as warnings
        — the resolver would silently pick the winner per that same
        precedence, so this is the one point where an unintended shadowing
        can be caught and flagged before it's ever hit at loop-run time.

        Blocked from running on the repo's configured ``branch_base`` — every
        other write path in dmx requires a reviewed PR rather than a direct
        commit there, and this tool produces one.

        Args:
            workspace_root: Repo root path override. Auto-detected if omitted.

        Returns:
            A per-source summary (success with resolved SHA, or a specific
            failure reason), any collision warnings, and next-step
            instructions for the agent.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return f"Could not resolve a valid workspace root: {exc}"

        guard_error = require_non_base_branch_error(root)
        if guard_error:
            return guard_error

        try:
            sources = read_shared_sources(root)
        except SharedSourceError as exc:
            return f"Error reading .dmx/shared-sources.yaml: {exc}"

        if not sources:
            return "No shared sources declared in `.dmx/shared-sources.yaml` — nothing to sync."

        lines: list[str] = []
        any_success = False
        for source in sources:
            try:
                result = sync_source(source, root)
            except SyncError as exc:
                logger.warning("sync failed for shared source '%s': %s", source.name, exc)
                lines.append(f"❌ `{source.name}`: {exc}")
                continue
            any_success = True
            lines.append(f"✅ `{source.name}` synced at `{result.resolved_sha[:12]}`.")

        body = "\n".join(lines)

        collisions = detect_collisions(sources, root)
        collisions_block = ""
        if collisions:
            collision_lines = "\n".join(f"⚠️ {c}" for c in collisions)
            collisions_block = f"\n\n**Collisions detected:**\n\n{collision_lines}"

        if any_success:
            return (
                f"**Shared sources synced:**\n\n{body}{collisions_block}\n\n"
                "Next: stage and commit the vendored files, then push and open a PR — "
                "e.g.:\n```\ngit add .dmx/vendor/ .dmx/shared-sources.yaml\n"
                'git commit -m "chore: sync shared sources"\ngit push\n```'
            )
        return f"**Shared source sync failed — nothing was vendored:**\n\n{body}{collisions_block}"
