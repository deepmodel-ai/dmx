"""``/dmx/upgrade``'s migration from the 0.5 ``.dmx/`` layout (GH-84).

0.5 kept each ticket's ``spec.md``, ``tasks.md`` and ``activeContext.md`` at
the ``.dmx/`` root, so the root copies on the integration branch are the last
merged ticket's leftovers, and a feature branch's are its own. The migration:

- moves root files that belong to the current branch's ticket into its job
  folder;
- deletes any other root ticket files. They are an earlier ticket's, already
  in git history. Moving them into that ticket's folder would look like a
  rename to git, and a branch still on the 0.5 layout that later merges the
  integration branch would have its own edits applied to the renamed files
  in the other ticket's folder, with no conflict reported. A deletion makes
  that merge a visible modify/delete conflict instead;
- lists custom code that still names the old paths, so the developer can fix
  it before anything moves;
- adds the ``.gitattributes`` line that collapses loop state in PR diffs.

The deterministic half of ``/dmx/upgrade``'s migration step, exposed through
one MCP tool (:func:`register_layout_migration_tools`). The skill shows the
plan, asks once, and the developer commits the result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastmcp import (
    Context,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
    FastMCP,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
)

from dmx.exceptions import AmbiguousActiveRun, WorkspaceRootInvalid
from dmx.loop_memory import read_open_items
from dmx.loop_state import (
    ACTIVE_CONTEXT_FILE,
    SPEC_FILE,
    TICKET_FILES,
    current_branch,
    read_spec_identity,
    resolve_job_id,
    root_ticket_file,
    root_ticket_files_belong_to,
    ticket_file,
    usable_ticket,
)
from dmx.loop_tools import (  # noqa: PLC2701 — same package, no public API split
    _read_branch_base,
    _read_config_value,
)
from dmx.workspace import resolve_workspace_root

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "GITATTRIBUTES_LINE",
    "LayoutPlan",
    "apply_layout_migration",
    "plan_layout_migration",
    "register_layout_migration_tools",
]

GITATTRIBUTES_LINE = ".dmx/jobs/**/*.json linguist-generated=true"

# Where custom code that could name the old paths lives: the app repo's own
# validators, loops and skills, and 0.5's vendored shared sources.
_SCAN_DIRS = ("validators", ".dmx/loops", ".dmx/skills", ".dmx/vendor")
_SCAN_SUFFIXES = frozenset({".py", ".md", ".yaml", ".yml", ".sh", ".txt", ".json", ".toml"})
_SCAN_MAX_BYTES = 1_000_000

# `.dmx/spec.md` as written in prose or a path string, and
# `".dmx" / "spec.md"` as a pathlib join in Python.
_OLD_PATH_RE = re.compile(
    r"\.dmx/(?:spec\.md|tasks\.md|activeContext\.md|vendor/)"
    r"|\.dmx[\"']\s*\)?\s*/\s*[\"'](?:spec|tasks|activeContext)\.md"
)


@dataclass(frozen=True)
class LayoutPlan:
    """What the migration does, or why it can't run here.

    Paths are relative to the repo root, with ``/`` separators.
    """

    blocked: str | None
    moves: tuple[tuple[str, str], ...] = ()
    deletes: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    open_items: str = ""
    adds_gitattributes: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(self.moves or self.deletes or self.adds_gitattributes)

    def as_dict(self, *, applied: bool) -> dict[str, Any]:
        return {
            "blocked": self.blocked,
            "applied": applied,
            "has_changes": self.has_changes,
            "moves": [{"from": source, "to": target} for source, target in self.moves],
            "deletes": list(self.deletes),
            "kept": list(self.kept),
            "references": list(self.references),
            "open_items": self.open_items,
            "adds_gitattributes": self.adds_gitattributes,
            "gitattributes_line": GITATTRIBUTES_LINE,
        }


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _blocked(root: Path) -> str | None:
    branch = current_branch(root)
    if branch is None:
        return (
            "HEAD is detached. Check out a branch for the upgrade "
            "(e.g. `git checkout -b chore/dmx-0.6-upgrade`), then run `/dmx/upgrade` again."
        )
    protected = {_read_branch_base(root), _read_config_value(root, "production_branch")}
    if branch in protected:
        return (
            f"`{branch}` is a protected branch; the migration is committed through a PR. "
            "Create a branch first (e.g. `git checkout -b chore/dmx-0.6-upgrade`), "
            "then run `/dmx/upgrade` again."
        )
    return None


def _owner_job(root: Path) -> str | None:
    """The current ticket's job id when the root ticket files are its own."""
    branch = current_branch(root)
    if branch is None:
        return None
    if root_ticket_file(root, SPEC_FILE).is_file():
        ticket, _spec_branch = read_spec_identity(root)
        job_id = usable_ticket(ticket) or branch
    else:
        try:
            job_id = resolve_job_id(root)
        except AmbiguousActiveRun:
            return None
    return job_id if root_ticket_files_belong_to(root, job_id) else None


def _scan_old_paths(root: Path) -> tuple[str, ...]:
    found: list[str] = []
    for rel_dir in _SCAN_DIRS:
        base = root / rel_dir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in _SCAN_SUFFIXES:
                continue
            try:
                if path.stat().st_size > _SCAN_MAX_BYTES:
                    continue
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(lines, start=1):
                if _OLD_PATH_RE.search(line):
                    found.append(f"{_rel(root, path)}:{number}: {line.strip()[:160]}")
    return tuple(found)


def _needs_gitattributes_line(root: Path) -> bool:
    path = root / ".gitattributes"
    if not path.is_file():
        return True
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return GITATTRIBUTES_LINE not in (line.strip() for line in lines)


def plan_layout_migration(root: Path) -> LayoutPlan:
    """Work out the migration for *root* without changing anything."""
    blocked = _blocked(root)
    references = _scan_old_paths(root)
    if blocked is not None:
        return LayoutPlan(blocked=blocked, references=references)

    owner = _owner_job(root)
    moves: list[tuple[str, str]] = []
    deletes: list[str] = []
    kept: list[str] = []
    open_items = ""
    for name in TICKET_FILES:
        source = root_ticket_file(root, name)
        if not source.is_file():
            continue
        if owner is None:
            deletes.append(_rel(root, source))
            if name == ACTIVE_CONTEXT_FILE:
                open_items = read_open_items(source)
            continue
        target = ticket_file(root, owner, name)
        if target.exists():
            kept.append(_rel(root, source))
        else:
            moves.append((_rel(root, source), _rel(root, target)))

    return LayoutPlan(
        blocked=None,
        moves=tuple(moves),
        deletes=tuple(deletes),
        kept=tuple(kept),
        references=references,
        open_items=open_items,
        adds_gitattributes=_needs_gitattributes_line(root),
    )


def apply_layout_migration(root: Path) -> LayoutPlan:
    """Plan the migration again and carry it out. Nothing is committed."""
    plan = plan_layout_migration(root)
    if plan.blocked is not None:
        return plan
    for source, target in plan.moves:
        destination = root / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        (root / source).replace(destination)
    for rel in plan.deletes:
        (root / rel).unlink(missing_ok=True)
    if plan.adds_gitattributes:
        path = root / ".gitattributes"
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        path.write_text(f"{existing}{GITATTRIBUTES_LINE}\n", encoding="utf-8")
    return plan


def register_layout_migration_tools(app: FastMCP) -> None:
    """Register ``migrate_dmx_layout`` on *app*."""

    @app.tool
    async def migrate_dmx_layout(
        ctx: Context, dry_run: bool = True, workspace_root: str | None = None
    ) -> dict[str, Any]:
        """Plan, or carry out, the move from the 0.5 ``.dmx/`` layout.

        Called by ``/dmx/upgrade``: first with ``dry_run`` true to show the
        plan, then with ``dry_run`` false after the developer agrees. Root
        ticket files that belong to the current branch's ticket move into its
        job folder; other root ticket files are deleted. Lists custom code
        that names the old paths, and adds the ``.gitattributes`` line.
        Refuses on ``branch_base``, ``production_branch`` and a detached
        HEAD. Does not commit.

        Args:
            dry_run: When true (the default), change nothing.
            workspace_root: Repo root path override. Auto-detected if omitted.

        Returns:
            ``blocked``, ``applied``, ``has_changes``, ``moves``, ``deletes``,
            ``kept``, ``references``, ``open_items``, ``adds_gitattributes``
            and ``gitattributes_line``.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return LayoutPlan(blocked=f"Could not resolve a valid workspace root: {exc}").as_dict(
                applied=False
            )
        if dry_run:
            return plan_layout_migration(root).as_dict(applied=False)
        plan = apply_layout_migration(root)
        return plan.as_dict(applied=plan.blocked is None)
