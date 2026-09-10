"""``/dmx/sync`` mechanics: clone/fetch a declared shared source and vendor it.

See GH-27 (phase 2). This module holds the deterministic, git-mechanical
half of ``/dmx/sync`` — cloning a source at its pinned ``ref``, resolving
the commit it points to, copying its tree into ``.dmx/vendor/{name}/``, and
writing the per-source lock file. No LLM judgment is needed for any of this
(same reasoning that keeps validators and loop orchestration out of the
agent's hands), so it's a plain, directly-testable Python module exposed to
the ``/dmx/sync`` skill through one deterministic MCP tool
(:func:`register_sync_tools`) rather than a sequence of raw shell commands
the agent runs itself.

What this module deliberately does **not** do: commit or push the vendored
files. That stays the agent's job, run from the skill, matching the
existing convention that dmx's deterministic tools write files and skills
commit them (e.g. ``draft-release-note`` writes ``.dmx/releases/{v}.md`` in
one step, then commits it in the next).

Vendored content is a plain copy of the source's working tree at the
resolved ref — not a nested git repository, not a submodule. The clone used
to fetch it is scratch space in a temp directory and is discarded once the
copy is made, so ``.dmx/vendor/{name}/`` never contains a ``.git`` of its
own; every file under it is tracked by the *app repo's* own git history,
consistent with "vendor-and-commit."
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dmx.loop_state import current_branch
from dmx.loop_tools import _read_branch_base  # noqa: PLC2701 — same package, no public API split
from dmx.shared_sources import SharedSource, vendor_dir, vendor_lock_path

__all__ = ["SyncError", "SyncResult", "require_non_base_branch_error", "sync_source"]

# Network-bound (unlike the local-only git calls elsewhere in dmx), so a more
# generous budget than e.g. _COMMIT_DMX_STATE_TIMEOUT_SECONDS in loop_tools.py —
# but still bounded, so a hung clone (bad credentials prompting interactively,
# an unreachable host) can't hang the whole MCP tool call indefinitely.
_SYNC_TIMEOUT_SECONDS = 120

# The category directories a shared source is expected to contain at least
# one of. See "Directory structure a shared source must follow" in GH-27.
_EXPECTED_CATEGORIES = ("loops", "skills", "validators")


def require_non_base_branch_error(root: Path) -> str | None:
    """Return an error message if ``/dmx/sync`` is being run from ``branch_base``.

    ``/dmx/sync`` produces a commit (the vendored files + lock), and every
    other write path in dmx already enforces that commits go through a
    reviewed PR rather than landing directly on ``branch_base`` — the
    release loop, ``close-ticket``, ``check_pr_ready``'s dirty-tree check.
    This is the same guard applied in the opposite direction from the
    ``spec`` loop's ``require_branch: base`` (which requires *base*; this
    requires *not-base*).

    Returns ``None`` (does not block) if ``branch_base`` or the current
    branch can't be determined — that's a different, unrelated problem the
    subsequent git commands will surface on their own; this guard only
    exists to catch the one specific case where identity resolution
    succeeds and the answer is "you're on the wrong branch."
    """
    branch_base = _read_branch_base(root)
    if branch_base is None:
        return None
    branch = current_branch(root)
    if branch is None or branch != branch_base:
        return None
    return (
        f"Cannot run `/dmx/sync` from `{branch_base}` — it commits vendored shared-source "
        "files directly, and every other write path in dmx requires that go through a "
        f"reviewed PR rather than landing on `{branch_base}` directly. Create a branch first "
        "(e.g. `git checkout -b chore/sync-shared-sources`), then run `/dmx/sync` again."
    )


class SyncError(Exception):
    """Raised when cloning, checking out, or vendoring a shared source fails.

    Always constructed with a message that names which of the three
    distinguishable failure causes applies (auth, missing ref, wrong-shaped
    source) — see GH-27's ``/dmx/sync`` step 6 — never a generic
    passthrough of a raw git error.
    """


@dataclass(frozen=True)
class SyncResult:
    """The outcome of successfully vendoring one declared shared source."""

    name: str
    source: str
    resolved_sha: str
    warning: str | None  # e.g. an unexpected-but-not-fatal shape observation


def _run_git(args: list[str], cwd: Path, *, error_context: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_SYNC_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SyncError(f"{error_context}: timed out after {_SYNC_TIMEOUT_SECONDS}s") from exc


def _check_source_shape(source_dir: Path, name: str) -> None:
    """Raise if *source_dir* has none of the expected category directories.

    A source with zero of ``loops/``, ``skills/``, ``validators/`` is almost
    certainly the wrong repo, the wrong ``subdir``, or a source that was
    never meant to be a dmx shared source — one of the three failure causes
    ``/dmx/sync`` must be able to tell apart (GH-27 step 6), not something to
    silently vendor and let resolve to "not found" later with no context.
    """
    if not any((source_dir / category).is_dir() for category in _EXPECTED_CATEGORIES):
        raise SyncError(
            f"Shared source '{name}' doesn't look like a dmx shared source: none of "
            f"{', '.join(_EXPECTED_CATEGORIES)} exist under {source_dir}. Check the "
            "`source` URL and `//<subdir>` in `.dmx/shared-sources.yaml`."
        )


def sync_source(source: SharedSource, workspace_root: Path) -> SyncResult:
    """Clone *source* at its pinned ``ref`` and vendor it into ``.dmx/vendor/{name}/``.

    Clones into a scratch temp directory, checks out ``ref``, resolves the
    commit it points to, copies the (sub)tree — excluding ``.git`` — into
    ``.dmx/vendor/{name}/`` (replacing any previous contents), and writes
    ``.dmx/vendor/{name}/.lock.json``.

    Raises:
        SyncError: If cloning fails (likely auth or a bad URL), the ref
            doesn't exist, the declared ``subdir`` doesn't exist, or the
            resolved directory doesn't look like a dmx shared source.
    """
    with tempfile.TemporaryDirectory(prefix="dmx-sync-") as tmp:
        clone_dir = Path(tmp) / "clone"

        clone = _run_git(
            ["clone", "--quiet", "--no-single-branch", source.url, str(clone_dir)],
            cwd=Path(tmp),
            error_context=f"Cloning shared source '{source.name}'",
        )
        if clone.returncode != 0:
            raise SyncError(
                f"Could not clone shared source '{source.name}' from {source.url}: "
                f"{clone.stderr.strip() or clone.stdout.strip()}. Confirm the URL is correct "
                "and that this machine's git credentials (SSH key / PAT / `gh auth`) can "
                "access it, then retry."
            )

        checkout = _run_git(
            ["checkout", "--quiet", source.ref],
            cwd=clone_dir,
            error_context=f"Checking out ref for shared source '{source.name}'",
        )
        if checkout.returncode != 0:
            raise SyncError(
                f"Ref '{source.ref}' not found in shared source '{source.name}' ({source.url}): "
                f"{checkout.stderr.strip() or checkout.stdout.strip()}. It may have been "
                "deleted, renamed, or force-pushed since `.dmx/shared-sources.yaml` was written — "
                "confirm the ref still exists upstream and update the config if needed."
            )

        rev_parse = _run_git(
            ["rev-parse", "HEAD"],
            cwd=clone_dir,
            error_context=f"Resolving commit SHA for shared source '{source.name}'",
        )
        if rev_parse.returncode != 0:
            raise SyncError(
                f"Could not resolve a commit SHA for shared source '{source.name}' after "
                f"checkout: {rev_parse.stderr.strip()}"
            )
        resolved_sha = rev_parse.stdout.strip()

        checked_out_root = (clone_dir / source.subdir) if source.subdir else clone_dir
        if not checked_out_root.is_dir():
            raise SyncError(
                f"Subdir '{source.subdir}' declared for shared source '{source.name}' does not "
                f"exist at ref '{source.ref}' ({source.url})."
            )
        _check_source_shape(checked_out_root, source.name)

        dest = vendor_dir(workspace_root, source.name)
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        for item in checked_out_root.iterdir():
            if item.name == ".git":
                continue
            target = dest / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)

        lock_path = vendor_lock_path(workspace_root, source.name)
        lock_path.write_text(
            json.dumps(
                {"name": source.name, "source": source.source, "resolved_sha": resolved_sha},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        return SyncResult(
            name=source.name, source=source.source, resolved_sha=resolved_sha, warning=None
        )
