"""``/dmx/update-shared-sources`` mechanics: lock declared shared sources.

See GH-27 for shared sources and GH-85 for locking. This module holds the
deterministic, git-mechanical half of ``/dmx/update-shared-sources``:
looking up the commit each source's ``ref`` points to, fetching that tree
into the git-ignored cache (:mod:`dmx.source_cache`), checking its shape,
hashing it, and writing ``.dmx/shared-sources.lock``. No LLM judgment is
needed for any of this, so it's a plain, directly-testable module exposed to
the skill through one MCP tool (:func:`dmx.sync_tools.register_sync_tools`).

Only sources that are new or whose declared address changed are looked up
again by default, so a floating ``?ref=main`` moves only when asked
(``update_all`` or naming the source). The result includes a summary for the
PR: old and new commit, a compare link, and the skills, loops and validators
added, removed or changed, with loops and validators called out as code to
review.

What this module deliberately does **not** do: commit or push the lock.
That stays the agent's job, run from the skill, matching the existing
convention that dmx's deterministic tools write files and skills commit
them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from dmx.loop_state import current_branch
from dmx.loop_tools import _read_branch_base  # noqa: PLC2701 — same package, no public API split
from dmx.shared_sources import (
    LockEntry,
    SharedSource,
    SharedSourceError,
    read_lock,
    read_shared_sources,
    source_root,
    write_lock,
)
from dmx.source_cache import (
    SharedSourceFetchError,
    ensure_fetched,
    extract_commit,
    resolve_commit,
    tree_hash,
)

__all__ = [
    "SourceChange",
    "SyncError",
    "UpdateReport",
    "detect_collisions",
    "fetch_shared_sources",
    "lock_source",
    "require_non_base_branch_error",
    "update_shared_sources",
]

# The category directories a shared source is expected to contain at least
# one of. See "Directory structure a shared source must follow" in GH-27.
_EXPECTED_CATEGORIES = ("loops", "skills", "validators")

# Where the app repo itself keeps each category, and the flat-file glob used
# to enumerate filenames within it — mirrors the resolvers in loop_tools.py/
# validator_runner.py. Deliberately excludes the bundled fallback: GH-27's
# collision-handling section only widens the check to app-repo-vs-shared, not
# bundled-vs-anything — the bundled tier is dmx's own content, not something
# an app repo or shared source could accidentally shadow in a way that
# warrants a warning.
_APP_CATEGORY_DIRS = {
    "loops": Path(".dmx/loops"),
    "skills": Path(".dmx/skills"),
    "validators": Path("validators"),
}
_CATEGORY_GLOB = {"loops": "*.yaml", "skills": "*.md", "validators": "*.py"}

# Sentinel used as the "owner" name for the app repo's own tier in
# detect_collisions — never a valid shared_sources `name` (parse_source_address
# only ever produces filesystem paths, not this literal token), so it can't
# collide with a real source name.
_APP_REPO_OWNER = "<app repo>"


def require_non_base_branch_error(root: Path) -> str | None:
    """Return an error message if ``/dmx/update-shared-sources`` runs on ``branch_base``.

    ``/dmx/update-shared-sources`` produces a commit (the lock), and every
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
        f"Cannot run `/dmx/update-shared-sources` from `{branch_base}` — it commits "
        "`.dmx/shared-sources.lock`, and every other write path in dmx requires that go "
        f"through a reviewed PR rather than landing on `{branch_base}` directly. Create a "
        "branch first (e.g. `git checkout -b chore/update-shared-sources`), then run "
        "`/dmx/update-shared-sources` again."
    )


class SyncError(Exception):
    """Raised when fetching or locking a shared source fails.

    Always constructed with a message that names which of the three
    distinguishable failure causes applies (auth, missing ref, wrong-shaped
    source) — see GH-27's sync step 6 — never a generic passthrough of a raw
    git error.
    """


def _check_source_shape(source_dir: Path, name: str) -> None:
    """Raise if *source_dir* has none of the expected category directories.

    A source with zero of ``loops/``, ``skills/``, ``validators/`` is almost
    certainly the wrong repo, the wrong ``subdir``, or a source that was
    never meant to be a dmx shared source — one of the three failure causes
    the update must be able to tell apart (GH-27 step 6), not something to
    silently lock and let resolve to "not found" later with no context.
    """
    if not any((source_dir / category).is_dir() for category in _EXPECTED_CATEGORIES):
        raise SyncError(
            f"Shared source '{name}' doesn't look like a dmx shared source: none of "
            f"{', '.join(_EXPECTED_CATEGORIES)} exist under {source_dir}. Check the "
            "`source` URL and `//<subdir>` in `.dmx/shared-sources.yaml`."
        )


def _check_skills_shape(source_dir: Path, name: str) -> None:
    """Raise if any entry under ``skills/`` is neither of the two shapes
    ``_resolve_skill`` recognizes (GH-27 phase 4): a flat ``{name}.md`` file,
    or a ``{name}/SKILL.md`` folder. Anything else — a stray non-``.md``
    file, an empty directory, a directory with no ``SKILL.md`` inside —
    would otherwise vendor silently and then simply never resolve, with no
    diagnostic pointing at which entry was wrong.

    Hidden entries (name starting with ``.``) are skipped entirely rather
    than flagged — ``.gitkeep``, ``.gitignore``, ``.DS_Store``, and similar
    repo-hygiene artifacts are extremely common at the top of any directory
    in a real git repo and aren't a "wrong shape" in any meaningful sense;
    flagging them would fail an otherwise well-formed source on something
    unrelated to whether its skills resolve correctly.

    No-ops if the source has no ``skills/`` directory at all.
    """
    skills_dir = source_dir / "skills"
    if not skills_dir.is_dir():
        return
    bad: list[str] = []
    for entry in sorted(skills_dir.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_file():
            if entry.suffix != ".md":
                bad.append(entry.name)
        elif entry.is_dir():
            if not (entry / "SKILL.md").is_file():
                bad.append(f"{entry.name}/ (missing SKILL.md)")
        else:
            bad.append(entry.name)
    if bad:
        raise SyncError(
            f"Shared source '{name}' has malformed entries under skills/: "
            f"{', '.join(bad)}. Each skill must be either a flat `{{name}}.md` file or a "
            "`{name}/SKILL.md` folder."
        )


def lock_source(source: SharedSource, workspace_root: Path) -> LockEntry:
    """Look up *source*'s ``ref`` now, fetch that tree, and return its lock entry.

    The tree is checked for the shared-source shape and hashed. Nothing is
    written to the lock file here.

    Raises:
        SyncError: The fetch failed (likely auth or a bad URL), the ref
            doesn't exist, the declared ``subdir`` doesn't exist, or the
            tree doesn't look like a dmx shared source.
    """
    try:
        commit = resolve_commit(source)
        # Fresh, so the hash written to the lock is of the real tree, not of a
        # cached copy someone may have edited.
        tree = extract_commit(workspace_root, source, commit, fresh=True)
    except SharedSourceFetchError as exc:
        raise SyncError(str(exc)) from exc
    searched = (tree / source.subdir) if source.subdir else tree
    if not searched.is_dir():
        raise SyncError(
            f"Subdir '{source.subdir}' declared for shared source '{source.name}' does not "
            f"exist at ref '{source.ref}' ({source.url})."
        )
    _check_source_shape(searched, source.name)
    _check_skills_shape(searched, source.name)
    return LockEntry(name=source.name, source=source.source, commit=commit, hash=tree_hash(tree))


@dataclass(frozen=True)
class SourceChange:
    """One source whose locked commit or address changed."""

    name: str
    url: str
    previous: LockEntry | None
    current: LockEntry
    added: dict[str, list[str]] = field(default_factory=dict)
    removed: dict[str, list[str]] = field(default_factory=dict)
    changed: dict[str, list[str]] = field(default_factory=dict)
    note: str | None = None  # e.g. the previous tree could not be fetched


@dataclass(frozen=True)
class UpdateReport:
    """What ``/dmx/update-shared-sources`` did."""

    changes: list[SourceChange]
    unchanged: list[str]
    failures: list[str]
    removed: list[str]
    unknown: list[str]

    @property
    def wrote_lock(self) -> bool:
        return bool(self.changes or self.removed)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    for item in files:
        digest.update(item.relative_to(path).as_posix().encode() if path.is_dir() else b"")
        digest.update(item.read_bytes())
    return digest.hexdigest()


def _category_items(searched: Path, category: str) -> dict[str, str]:
    """Logical name -> content digest for one category of a source tree."""
    directory = searched / category
    if not directory.is_dir():
        return {}
    items: dict[str, str] = {}
    for entry in sorted(directory.iterdir()):
        if category == "skills":
            if entry.is_file() and entry.suffix == ".md":
                items[entry.stem.removeprefix("dmx-")] = _digest(entry)
            elif entry.is_dir() and (entry / "SKILL.md").is_file():
                items[entry.name.removeprefix("dmx-")] = _digest(entry)
        elif entry.is_file() and entry.match(_CATEGORY_GLOB[category]):
            items[entry.stem] = _digest(entry)
    return items


def _diff_trees(old: Path | None, new: Path) -> tuple[dict[str, list[str]], ...]:
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    changed: dict[str, list[str]] = {}
    for category in _EXPECTED_CATEGORIES:
        before = _category_items(old, category) if old is not None else {}
        after = _category_items(new, category)
        if names := sorted(set(after) - set(before)):
            added[category] = names
        if names := sorted(set(before) - set(after)):
            removed[category] = names
        if names := sorted(n for n in set(before) & set(after) if before[n] != after[n]):
            changed[category] = names
    return added, removed, changed


def _searched(tree: Path, source: SharedSource) -> Path:
    return (tree / source.subdir) if source.subdir else tree


def _describe_change(
    workspace_root: Path, source: SharedSource, previous: LockEntry | None, current: LockEntry
) -> SourceChange:
    new_tree = _searched(ensure_fetched(workspace_root, source, current), source)
    old_tree: Path | None = None
    note = None
    if previous is not None:
        try:
            old_tree = _searched(ensure_fetched(workspace_root, source, previous), source)
        except SharedSourceError as exc:
            note = f"Could not fetch the previous commit to compare: {exc}"
    added, removed, changed = _diff_trees(old_tree, new_tree)
    if note is not None:
        added, removed, changed = {}, {}, {}
    return SourceChange(
        name=source.name,
        url=source.url,
        previous=previous,
        current=current,
        added=added,
        removed=removed,
        changed=changed,
        note=note,
    )


def update_shared_sources(
    workspace_root: Path, *, names: list[str] | None = None, update_all: bool = False
) -> UpdateReport:
    """Lock the declared shared sources and write ``.dmx/shared-sources.lock``.

    A source is looked up again when it is new, its declared address
    changed, it is named in *names*, or *update_all* is set. Every other
    entry keeps its locked commit, so a floating ref never moves by
    surprise. Entries for sources no longer declared are dropped. A failed
    source keeps its previous entry. The lock is written only when something
    changed.

    Raises:
        SharedSourceError: ``.dmx/shared-sources.yaml`` or the lock is
            malformed.
    """
    sources = read_shared_sources(workspace_root)
    lock = read_lock(workspace_root)
    wanted = set(names or [])
    declared = {source.name for source in sources}

    entries: list[LockEntry] = []
    changes: list[SourceChange] = []
    unchanged: list[str] = []
    failures: list[str] = []
    for source in sources:
        previous = lock.get(source.name)
        stale = previous is None or previous.source != source.source
        if not (stale or update_all or source.name in wanted):
            assert previous is not None
            entries.append(previous)
            unchanged.append(source.name)
            continue
        try:
            current = lock_source(source, workspace_root)
        except SyncError as exc:
            failures.append(f"`{source.name}`: {exc}")
            if previous is not None:
                entries.append(previous)
            continue
        entries.append(current)
        if previous is not None and (previous.commit, previous.source) == (
            current.commit,
            current.source,
        ):
            unchanged.append(source.name)
            continue
        changes.append(_describe_change(workspace_root, source, previous, current))

    removed = sorted(set(lock) - declared)
    report = UpdateReport(
        changes=changes,
        unchanged=unchanged,
        failures=failures,
        removed=removed,
        unknown=sorted(wanted - declared),
    )
    if report.wrote_lock:
        write_lock(workspace_root, entries)
    return report


_GITHUB_RE = re.compile(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$")


def compare_link(url: str, old: str, new: str) -> str | None:
    """A GitHub compare URL for two commits of *url*, or None for other hosts."""
    match = _GITHUB_RE.search(url)
    if match is None:
        return None
    owner, repo = match.groups()
    return f"https://github.com/{owner}/{repo}/compare/{old}...{new}"


_LABELS = {"skills": "Skills", "loops": "Loops", "validators": "Validators"}


def summary_markdown(report: UpdateReport) -> str:
    """The PR summary: each changed source, with loops and validators flagged."""
    blocks: list[str] = []
    for change in report.changes:
        new = change.current.commit[:12]
        if change.previous is None:
            heading = f"**`{change.name}`**: locked at `{new}`"
        else:
            old = change.previous.commit[:12]
            heading = f"**`{change.name}`**: `{old}` → `{new}`"
            link = compare_link(change.url, change.previous.commit, change.current.commit)
            if link:
                heading += f" ([compare]({link}))"
        lines = [heading]
        if change.note:
            lines.append(f"- {change.note}")
        for category in _EXPECTED_CATEGORIES:
            parts = []
            for verb, found in (
                ("added", change.added),
                ("removed", change.removed),
                ("changed", change.changed),
            ):
                if found.get(category):
                    parts.append(f"{verb} " + ", ".join(f"`{n}`" for n in found[category]))
            if not parts:
                continue
            label = _LABELS[category]
            if category != "skills":
                label = f"**{label} (code to review)**"
            lines.append(f"- {label}: " + "; ".join(parts))
        blocks.append("\n".join(lines))
    if report.removed:
        blocks.append("Dropped from the lock: " + ", ".join(f"`{n}`" for n in report.removed))
    return "\n\n".join(blocks)


def fetch_shared_sources(workspace_root: Path) -> tuple[list[str], bool]:
    """Fetch every locked source into the cache without changing the lock.

    Returns one line per declared source and whether all of them are ready.
    A declared source with no lock entry, or one locked from a different
    address, is reported as needing ``/dmx/update-shared-sources``.
    """
    sources = read_shared_sources(workspace_root)
    lock = read_lock(workspace_root)
    lines: list[str] = []
    ok = True
    for source in sources:
        entry = lock.get(source.name)
        if entry is None or entry.source != source.source:
            ok = False
            lines.append(
                f"✗ `{source.name}`: not in `.dmx/shared-sources.lock` for its current "
                "address. Run `/dmx/update-shared-sources`."
            )
            continue
        try:
            ensure_fetched(workspace_root, source, entry)
        except SharedSourceError as exc:
            ok = False
            lines.append(f"✗ `{source.name}`: {exc}")
            continue
        lines.append(f"✓ `{source.name}` at `{entry.commit[:12]}`")
    return lines, ok


def _filenames(directory: Path, glob: str) -> set[str]:
    if not directory.is_dir():
        return set()
    return {p.name for p in directory.glob(glob)}


def _skill_names(directory: Path) -> set[str]:
    """Logical skill names present in *directory*.

    Unlike ``_filenames`` (a plain glob), this normalizes the two things
    that make "same name" ambiguous for skills specifically, so it matches
    exactly what ``_resolve_skill`` would treat as the same skill:

    - Both supported shapes (GH-27 phase 4) — flat ``{name}.md`` and
      folder-shaped ``{name}/SKILL.md`` — collapse to the same logical
      name, so a flat skill in one source colliding with a folder-shaped
      version of the same name in another is caught, not silently missed
      because one's a file glob match and the other's a directory.
    - The ``dmx-`` prefix alias — ``dmx-{name}.md`` and ``{name}.md`` (or
      their folder-shaped equivalents) also collapse to the same logical
      name, for the same reason.

    Loops and validators have neither ambiguity (no external ecosystem
    folder-shape standard, no prefix aliasing), so they stay on the plain
    ``_filenames`` glob.
    """
    if not directory.is_dir():
        return set()
    names: set[str] = set()
    for entry in directory.iterdir():
        if entry.is_file() and entry.suffix == ".md":
            stem = entry.stem
        elif entry.is_dir() and (entry / "SKILL.md").is_file():
            stem = entry.name
        else:
            continue
        names.add(stem.removeprefix("dmx-"))
    return names


def _names_in(directory: Path, category: str) -> set[str]:
    """Dispatch to :func:`_skill_names` for ``skills``, the plain glob-based
    :func:`_filenames` for every other category."""
    if category == "skills":
        return _skill_names(directory)
    return _filenames(directory, _CATEGORY_GLOB[category])


def _format_others(names: list[str]) -> str:
    quoted = [f"`{n}`" for n in names]
    if len(quoted) == 1:
        return quoted[0]
    return ", ".join(quoted[:-1]) + f" and {quoted[-1]}"


def detect_collisions(sources: list[SharedSource], workspace_root: Path) -> list[str]:
    """Find same-name files across the app repo and already-vendored shared sources.

    Only meaningful *after* :func:`sync_source` has run for each source —
    this reads directly from ``.dmx/vendor/{name}/``, the same on-disk state
    the resolvers in ``loop_tools.py``/``validator_runner.py`` read from, so
    a warning here reflects exactly what those resolvers will actually do.

    Per category (``loops``, ``skills``, ``validators``), builds the
    filename set for the app repo's own directory and each declared source
    (in declared order), then reports every filename that appears in more
    than one of those locations — the "app repo always wins, then
    ``shared_sources`` order, then bundled" precedence means the *first*
    location in that list is always the actual winner, and every other
    location listing the same filename is silently shadowed. The bundled
    fallback is intentionally excluded — see the module-level comment on
    ``_APP_CATEGORY_DIRS``.

    Returns:
        A sorted list of human-readable warning strings (empty if no
        collisions). Never raises — a collision is a warning, not a
        failure; the lock is still written and committed regardless.
    """
    warnings: list[str] = []
    for category, app_rel_dir in _APP_CATEGORY_DIRS.items():
        owners_by_file: dict[str, list[str]] = {}

        for filename in _names_in(workspace_root / app_rel_dir, category):
            owners_by_file.setdefault(filename, []).append(_APP_REPO_OWNER)
        for source in sources:
            source_dir = source_root(workspace_root, source) / category
            for filename in _names_in(source_dir, category):
                owners_by_file.setdefault(filename, []).append(source.name)

        for filename, owners in sorted(owners_by_file.items()):
            if len(owners) < 2:
                continue
            winner, *shadowed = owners
            if winner == _APP_REPO_OWNER:
                warnings.append(
                    f"`{filename}` exists both locally (`{app_rel_dir}/`) and in "
                    f"{_format_others(shadowed)} — the local copy wins and "
                    f"{'it is' if len(shadowed) == 1 else 'they are'} fully shadowed. If "
                    "this local file isn't meant to be a deliberate override, remove it."
                )
            else:
                warnings.append(
                    f"`{filename}` is defined in both `{winner}` and "
                    f"{_format_others(shadowed)} — `{winner}` wins per shared_sources "
                    "order. If unintended, rename one or reorder shared_sources."
                )
    return warnings
