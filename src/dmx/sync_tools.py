"""MCP tool: ``update_shared_sources`` — the deterministic half of ``/dmx/update-shared-sources``.

Called from within the ``/dmx/update-shared-sources`` skill (and the
``/dmx/sync`` alias kept for 0.6); not a user-facing command on its own
(same pattern as ``setup_ide_rules``, called from within ``/dmx/init``). The
skill still owns the parts that should stay a plain, reviewable git
operation: committing the lock and pushing.
"""

from __future__ import annotations

import logging

from fastmcp import (
    Context,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
    FastMCP,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
)

from dmx.components import is_valid_id
from dmx.exceptions import WorkspaceRootInvalid
from dmx.shared_sources import SharedSourceError, config_dir, read_shared_sources
from dmx.sync_runner import (
    detect_collisions,
    require_non_base_branch_error,
    summary_markdown,
    update_shared_sources,
)
from dmx.workspace import resolve_workspace_root

__all__ = ["register_sync_tools"]

logger = logging.getLogger(__name__)


def register_sync_tools(app: FastMCP) -> None:
    """Register ``update_shared_sources`` on *app*."""

    @app.tool(name="update_shared_sources")
    async def update_shared_sources_tool(
        ctx: Context,
        names: list[str] | None = None,
        update_all: bool = False,
        component: str | None = None,
        workspace_root: str | None = None,
    ) -> str:
        """Lock the declared shared sources in ``.dmx/shared-sources.lock``.

        Looks up the commit each ``ref`` points to for sources that are new
        or whose address changed in ``.dmx/shared-sources.yaml``, plus any
        listed in *names*, or every source with *update_all*. Fetches those
        trees into the git-ignored ``.dmx/.cache/``, checks their shape,
        hashes them, and writes the lock. Every other entry keeps its
        commit. Does **not** commit or push — the calling skill does that.

        After locking, checks for same-name collisions across the app repo's
        own ``loops``/``skills``/``validators`` and every declared source (in
        declared-order precedence) and surfaces them as warnings.

        Blocked from running on the repo's configured ``branch_base`` — the
        lock goes through a reviewed PR.

        With *component*, updates that component's own sources instead:
        ``.dmx/components/<id>/shared-sources.yaml`` and its lock (GH-90).
        The cache is shared, so the same source at the same commit is
        fetched once for every component.

        Args:
            names: Sources to look up again even if their entry is current.
            update_all: Look up every declared source again.
            component: A component id, to update its sources instead.
            workspace_root: Repo root path override. Auto-detected if omitted.

        Returns:
            Per-source results, a summary to paste into the PR, any collision
            warnings, and next-step instructions for the agent.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return f"Could not resolve a valid workspace root: {exc}"

        if component is not None and (
            not is_valid_id(component)
            or not (root / ".dmx" / "components" / component / "component.yaml").is_file()
        ):
            return (
                f"`{component}` is not a component "
                f"(no `.dmx/components/{component}/component.yaml`)."
            )
        guard_error = require_non_base_branch_error(root)
        if guard_error:
            return guard_error

        folder = config_dir(root, component).relative_to(root).as_posix()
        try:
            sources = read_shared_sources(root, component=component)
            if not sources:
                return (
                    f"No shared sources declared in `{folder}/shared-sources.yaml` — "
                    "nothing to update."
                )
            report = update_shared_sources(
                root, names=names, update_all=update_all, component=component
            )
        except SharedSourceError as exc:
            return f"Error reading shared sources: {exc}"

        lines: list[str] = []
        for change in report.changes:
            lines.append(f"✅ `{change.name}` locked at `{change.current.commit[:12]}`.")
        if report.unchanged:
            listed = ", ".join(f"`{name}`" for name in report.unchanged)
            lines.append(f"Unchanged: {listed}.")
        for failure in report.failures:
            logger.warning("update failed for shared source %s", failure)
            lines.append(f"❌ {failure}")
        if report.unknown:
            listed = ", ".join(f"`{name}`" for name in report.unknown)
            lines.append(f"Not declared in `.dmx/shared-sources.yaml`: {listed}.")
        body = "\n".join(lines)

        # A component's items are named `<id>/<name>`, so they can't collide.
        collisions = detect_collisions(sources, root) if component is None else []
        collisions_block = ""
        if collisions:
            collision_lines = "\n".join(f"⚠️ {c}" for c in collisions)
            collisions_block = f"\n\n**Collisions detected:**\n\n{collision_lines}"

        if not report.wrote_lock:
            if report.failures:
                return f"**Shared source update failed:**\n\n{body}{collisions_block}"
            return f"**Shared sources:** nothing changed.\n\n{body}{collisions_block}"
        summary = summary_markdown(report)
        return (
            f"**Shared sources updated:**\n\n{body}{collisions_block}\n\n"
            f"**Summary for the PR description:**\n\n{summary}\n\n"
            "Next: commit the lock, push, and open a PR with that summary — e.g.:\n"
            f"```\ngit add {folder}/shared-sources.lock {folder}/shared-sources.yaml\n"
            'git commit -m "chore: update shared sources"\ngit push\n```'
        )
