"""Shared sources: org-wide loops/skills/validators locked by ``/dmx/update-shared-sources``.

See GH-27 for the full design. This module is the shared-sources equivalent
of the small per-field readers in ``loop_tools.py`` (``_read_branch_base`` et
al.): plain-filesystem parsing, no agent context involved, safe to call from
the deterministic MCP tool layer.

Config lives in its own file, ``.dmx/shared-sources.yaml`` — deliberately not
folded into ``.dmx/config.md``, which carries ``alwaysApply: true`` and is
injected into every prompt's context. ``shared_sources`` has exactly two
consumers (the resolver functions in ``loop_tools.py``/``validator_runner.py``,
and the ``/dmx/update-shared-sources`` skill), neither of which needs it preloaded
into every prompt.

Since GH-85 a repo commits ``.dmx/shared-sources.lock`` — each source's
commit and content hash — and the content is fetched into the git-ignored
``.dmx/.cache/`` (see :mod:`dmx.source_cache`). :func:`source_root` resolves
through the lock, fetching on first use. A source the lock does not cover
yet, in a repo still on 0.5, resolves to its committed ``.dmx/vendor/{name}/``
copy as before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "LockEntry",
    "SharedSource",
    "SharedSourceError",
    "lock_path",
    "parse_source_address",
    "read_lock",
    "read_shared_sources",
    "source_root",
    "vendor_dir",
    "vendor_lock_path",
    "write_lock",
]


class SharedSourceError(Exception):
    """Raised when ``.dmx/shared-sources.yaml`` or a source address is malformed."""


_GIT_SCHEME_PREFIX = "git::"

# `name` becomes a literal path segment in `.dmx/vendor/{name}/`, which
# `sync_source` unconditionally `rmtree`s before repopulating — restricting
# it to a plain slug (no `/`, no `..`, no leading `-`) rules out path
# traversal outside `.dmx/vendor/` from a typo'd or malicious config entry.
_NAME_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_-]*$")


@dataclass(frozen=True)
class SharedSource:
    """One declared entry from ``shared_sources`` in ``.dmx/shared-sources.yaml``."""

    name: str
    source: str  # raw address string, as declared
    url: str
    subdir: str | None
    ref: str


def parse_source_address(source: str) -> tuple[str, str | None, str]:
    """Parse a ``git::<url>//<subdir>?ref=<ref>`` source address.

    Deliberately not a single regex: the URL itself already contains ``//``
    (its own scheme separator, e.g. ``https://``), so the *subdir* ``//``
    marker can only be found unambiguously by first skipping past the
    scheme's own ``://`` — matching how Terraform's own module-source
    parser resolves the same ambiguity.

    Returns:
        ``(url, subdir_or_None, ref)``.

    Raises:
        SharedSourceError: If *source* doesn't match the expected shape, or
            ``ref`` is missing — every source must pin something, even if
            it's a floating branch name. There is no default-branch fallback.
    """
    stripped = source.strip()
    if not stripped.startswith(_GIT_SCHEME_PREFIX):
        raise SharedSourceError(
            f"Invalid shared source address: {source!r}. Expected "
            "`git::<url>[//<subdir>]?ref=<tag|branch|sha>`."
        )
    address = stripped[len(_GIT_SCHEME_PREFIX) :]

    if "?ref=" in address:
        address, _, ref = address.rpartition("?ref=")
    else:
        ref = ""
    if not ref:
        raise SharedSourceError(
            f"Shared source address missing `?ref=...`: {source!r}. Every "
            "source must pin a tag, branch, or commit SHA."
        )

    scheme_sep = address.find("://")
    search_start = scheme_sep + 3 if scheme_sep != -1 else 0
    subdir_marker = address.find("//", search_start)
    if subdir_marker == -1:
        url, subdir = address, None
    else:
        url = address[:subdir_marker]
        subdir = address[subdir_marker + 2 :] or None

    if not url:
        raise SharedSourceError(
            f"Invalid shared source address: {source!r}. Expected "
            "`git::<url>[//<subdir>]?ref=<tag|branch|sha>`."
        )
    if subdir is not None:
        _validate_subdir(subdir, source)
    return url, subdir, ref


def _validate_subdir(subdir: str, source: str) -> None:
    """Reject a ``subdir`` that could escape the cloned tree it's joined against.

    Two concrete risks, both from plain ``Path`` joining (no normalization
    happens automatically): a leading ``/`` makes ``Path`` treat it as
    *absolute*, silently discarding the left-hand operand entirely (e.g.
    ``clone_dir / "/etc"`` resolves to ``/etc``, not ``clone_dir/etc``); and
    a ``..`` segment walks back out of it. Either way, code further down
    (``_check_source_shape``, and every resolver via ``source_root``) would
    then search — and, for validators, execute — a location the vendoring
    step never intended to touch.
    """
    if subdir.startswith("/") or ".." in subdir.split("/"):
        raise SharedSourceError(
            f"Invalid `subdir` in shared source address: {source!r}. `subdir` must be a "
            "relative path within the source repo — it cannot start with `/` or contain "
            "a `..` segment."
        )


def config_dir(workspace_root: Path, component: str | None = None) -> Path:
    """Where ``shared-sources.yaml`` and its lock live: ``.dmx/``, or a component's folder."""
    base = workspace_root / ".dmx"
    return base / "components" / component if component else base


def read_shared_sources(
    workspace_root: Path, *, component: str | None = None
) -> list[SharedSource]:
    """Read and parse ``.dmx/shared-sources.yaml``, or a component's (GH-90).

    Returns an empty list if the file doesn't exist — shared sources are
    entirely optional; a repo with none behaves exactly as it did before
    this feature existed.

    Raises:
        SharedSourceError: If the file exists but is malformed (bad YAML,
            wrong shape, a duplicate name, or an entry with an invalid
            source address).
    """
    path = config_dir(workspace_root, component) / "shared-sources.yaml"
    if not path.exists():
        return []

    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SharedSourceError(f"Could not parse {path}: {exc}") from exc

    if raw is None:
        return []
    if not isinstance(raw, dict) or "shared_sources" not in raw:
        raise SharedSourceError(f"{path} must be a mapping with a top-level `shared_sources` list.")

    entries = raw["shared_sources"]
    if not isinstance(entries, list):
        raise SharedSourceError(f"`shared_sources` in {path} must be a list.")

    sources: list[SharedSource] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or "name" not in entry or "source" not in entry:
            raise SharedSourceError(f"{path}: entry #{i + 1} must have `name` and `source` fields.")
        name = str(entry["name"])
        if not _NAME_RE.match(name):
            raise SharedSourceError(
                f"{path}: invalid shared source name `{name}` — names must be a plain slug "
                "(letters, digits, `_`, `-`, not starting with `-`) since it becomes a literal "
                "path segment under `.dmx/vendor/`."
            )
        if name in seen:
            raise SharedSourceError(
                f"{path}: duplicate shared source name `{name}` — names must be unique."
            )
        seen.add(name)
        url, subdir, ref = parse_source_address(str(entry["source"]))
        sources.append(
            SharedSource(name=name, source=str(entry["source"]), url=url, subdir=subdir, ref=ref)
        )
    return sources


@dataclass(frozen=True)
class LockEntry:
    """One source's entry in ``.dmx/shared-sources.lock``."""

    name: str
    source: str  # the declared address it was locked from
    commit: str
    hash: str  # "sha256:<hex>" of the tree at that commit


_LOCK_HEADER = (
    "# Written by /dmx/update-shared-sources. Commit this file; do not edit it by hand.\n"
    "# dmx fetches each source at its commit into .dmx/.cache/ (git-ignored).\n"
)


def lock_path(workspace_root: Path, *, component: str | None = None) -> Path:
    """``.dmx/shared-sources.lock``, or ``.dmx/components/<id>/shared-sources.lock``."""
    return config_dir(workspace_root, component) / "shared-sources.lock"


def read_lock(workspace_root: Path, *, component: str | None = None) -> dict[str, LockEntry]:
    """Entries of the lock by source name; empty when absent.

    Raises:
        SharedSourceError: If the file exists but is malformed.
    """
    path = lock_path(workspace_root, component=component)
    if not path.exists():
        return {}
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SharedSourceError(f"Could not parse {path}: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SharedSourceError(f"{path} must be a mapping with a top-level `shared_sources`.")
    entries = raw.get("shared_sources")
    if entries is None:
        return {}
    if not isinstance(entries, dict):
        raise SharedSourceError(f"`shared_sources` in {path} must be a mapping of names.")
    locked: dict[str, LockEntry] = {}
    for name, entry in entries.items():
        if not isinstance(entry, dict) or not {"source", "commit", "hash"} <= entry.keys():
            raise SharedSourceError(
                f"{path}: entry `{name}` must have `source`, `commit` and `hash`. "
                "Run `/dmx/update-shared-sources --all` to write it again."
            )
        locked[str(name)] = LockEntry(
            name=str(name),
            source=str(entry["source"]),
            commit=str(entry["commit"]),
            hash=str(entry["hash"]),
        )
    return locked


def write_lock(
    workspace_root: Path, entries: list[LockEntry], *, component: str | None = None
) -> None:
    """Write the lock with *entries*, in the order given."""
    body = {
        "shared_sources": {
            entry.name: {"source": entry.source, "commit": entry.commit, "hash": entry.hash}
            for entry in entries
        }
    }
    text = _LOCK_HEADER + yaml.safe_dump(body, sort_keys=False, default_flow_style=False)
    path = lock_path(workspace_root, component=component)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def vendor_dir(workspace_root: Path, name: str) -> Path:
    """The local vendored path for a declared shared source, ``.dmx/vendor/{name}/``."""
    return workspace_root / ".dmx" / "vendor" / name


def vendor_lock_path(workspace_root: Path, name: str) -> Path:
    """The per-source lock file path, ``.dmx/vendor/{name}/.lock.json``."""
    return vendor_dir(workspace_root, name) / ".lock.json"


def source_root(
    workspace_root: Path, source: SharedSource, *, component: str | None = None
) -> Path:
    """The resolved search root for a shared source: its tree + its ``subdir``.

    When ``.dmx/shared-sources.lock`` has an entry for *source*, that is the
    cached tree at the locked commit, fetched on first use (see
    :func:`dmx.source_cache.ensure_fetched`). The lock is the only source of
    truth: if ``shared-sources.yaml`` changed the address since, the locked
    address and commit are still used until ``/dmx/update-shared-sources``
    runs. Without an entry it is the 0.5 vendored copy,
    ``.dmx/vendor/{name}/``, which may not exist. ``subdir`` (from
    ``//<subdir>`` in the address) narrows where within that tree
    loops/skills/validators are actually searched for, mirroring how the
    ``git::...//<subdir>`` address works in Terraform.

    A component's source (GH-90) has no vendored fallback: without a lock
    entry it raises. Every tree is cached under the repo's ``.dmx/.cache/``,
    so sources locked at the same commit share one copy.

    Raises:
        SharedSourceError: The lock is malformed, a component's source is not
            locked, or a locked source cannot be fetched or does not match
            its hash.
    """
    entry = read_lock(workspace_root, component=component).get(source.name)
    if entry is None and component is not None:
        raise SharedSourceError(
            f"`{component}/{source.name}` is not in "
            f"`.dmx/components/{component}/shared-sources.lock`. "
            f"Run `/dmx/update-shared-sources {component}`."
        )
    if entry is None:
        root = vendor_dir(workspace_root, source.name)
        return (root / source.subdir) if source.subdir else root
    # source_cache imports this module, so the import stays here.
    from dmx.source_cache import ensure_fetched

    locked = source
    if entry.source != source.source:
        url, subdir, ref = parse_source_address(entry.source)
        locked = SharedSource(
            name=source.name, source=entry.source, url=url, subdir=subdir, ref=ref
        )
    root = ensure_fetched(workspace_root, locked, entry)
    return (root / locked.subdir) if locked.subdir else root
