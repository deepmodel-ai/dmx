"""Shared sources: org-wide loops/skills/validators vendored via ``/dmx/sync``.

See GH-27 for the full design. This module is the shared-sources equivalent
of the small per-field readers in ``loop_tools.py`` (``_read_branch_base`` et
al.): plain-filesystem parsing, no agent context involved, safe to call from
the deterministic MCP tool layer.

Config lives in its own file, ``.dmx/shared-sources.yaml`` — deliberately not
folded into ``.dmx/config.md``, which carries ``alwaysApply: true`` and is
injected into every prompt's context. ``shared_sources`` has exactly two
consumers (the resolver functions in ``loop_tools.py``/``validator_runner.py``,
and the ``/dmx/sync`` skill at sync time), neither of which needs it preloaded
into every prompt.

This module only *reads* declared sources and resolves paths — it never
shells out to ``git`` itself. Cloning/fetching/locking is the ``/dmx/sync``
skill's job (GH-27 phase 2); this phase only wires the resolver tier so it's
ready to search ``.dmx/vendor/{name}/`` once something populates it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "SharedSource",
    "SharedSourceError",
    "parse_source_address",
    "read_shared_sources",
    "source_root",
    "vendor_dir",
    "vendor_lock_path",
]


class SharedSourceError(Exception):
    """Raised when ``.dmx/shared-sources.yaml`` or a source address is malformed."""


_GIT_SCHEME_PREFIX = "git::"


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
    return url, subdir, ref


def read_shared_sources(workspace_root: Path) -> list[SharedSource]:
    """Read and parse ``.dmx/shared-sources.yaml``.

    Returns an empty list if the file doesn't exist — shared sources are
    entirely optional; a repo with none behaves exactly as it did before
    this feature existed.

    Raises:
        SharedSourceError: If the file exists but is malformed (bad YAML,
            wrong shape, a duplicate name, or an entry with an invalid
            source address).
    """
    path = workspace_root / ".dmx" / "shared-sources.yaml"
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


def vendor_dir(workspace_root: Path, name: str) -> Path:
    """The local vendored path for a declared shared source, ``.dmx/vendor/{name}/``."""
    return workspace_root / ".dmx" / "vendor" / name


def vendor_lock_path(workspace_root: Path, name: str) -> Path:
    """The per-source lock file path, ``.dmx/vendor/{name}/.lock.json``."""
    return vendor_dir(workspace_root, name) / ".lock.json"


def source_root(workspace_root: Path, source: SharedSource) -> Path:
    """The resolved search root for a vendored source: vendor dir + its ``subdir``.

    ``/dmx/sync`` always clones the full source into ``.dmx/vendor/{name}/``;
    ``subdir`` (from ``//<subdir>`` in the source address) narrows where
    within that clone loops/skills/validators are actually searched for,
    mirroring how the ``git::...//<subdir>`` address works in Terraform.
    """
    root = vendor_dir(workspace_root, source.name)
    return (root / source.subdir) if source.subdir else root
