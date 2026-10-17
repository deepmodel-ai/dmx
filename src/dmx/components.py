"""Components: one folder per service or package under ``.dmx/components/`` (GH-86).

Each ``.dmx/components/<id>/component.yaml`` declares one part of a
monorepo::

    description: Payment processing service      # required
    paths: ["services/payments/**"]              # required
    depends_on: [ledger]                         # optional
    test: {cmd: make test, cwd: services/payments}   # optional (#89)
    validators: {validate: [no_float_money]}     # optional (#90)
    ticket: {jira_project: PAY}                  # optional (#92)

``paths`` decide which component a file belongs to. They are
gitignore-style globs relative to the repo root: ``**`` spans folders,
``*`` and ``?`` stay within one, and a pattern that matches a folder
matches everything below it. When patterns nest, the most specific match
wins (see :func:`_specificity`). A file no component matches is repo-wide.

A broken ``component.yaml`` is skipped with its reason, so one typo never
stops a loop. ``dmx check`` (#91) reports the problems this module lets
through, such as ``depends_on`` naming an unknown id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

__all__ = [
    "COMPONENTS_DIR",
    "COMPONENT_FILE",
    "Component",
    "ComponentSet",
    "SkippedComponent",
    "has_components",
    "load_components",
    "path_matches",
]

COMPONENTS_DIR = ".dmx/components"
COMPONENT_FILE = "component.yaml"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_WILDCARDS = frozenset("*?")


@dataclass(frozen=True)
class Component:
    """One ``component.yaml``, checked."""

    id: str
    description: str
    paths: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    test: dict[str, str] | None = None
    validators: dict[str, Any] | None = None
    ticket: dict[str, Any] | None = None

    @property
    def folder(self) -> str:
        """The component's folder, relative to the repo root, with a trailing ``/``."""
        return f"{COMPONENTS_DIR}/{self.id}/"


@dataclass(frozen=True)
class SkippedComponent:
    """A ``component.yaml`` that could not be used, and why."""

    path: str
    reason: str


@dataclass(frozen=True)
class ComponentSet:
    """Every usable component in a repo, sorted by id, and the skipped files."""

    components: tuple[Component, ...] = ()
    skipped: tuple[SkippedComponent, ...] = ()
    _by_id: dict[str, Component] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._by_id.update({component.id: component for component in self.components})

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(component.id for component in self.components)

    def get(self, component_id: str) -> Component | None:
        return self._by_id.get(component_id)

    def component_for(self, path: str) -> Component | None:
        """The component that owns *path* (relative to the repo root), or None."""
        best: tuple[tuple[int, int], Component] | None = None
        for component in self.components:
            for pattern in component.paths:
                if not path_matches(pattern, path):
                    continue
                rank = _specificity(pattern)
                # Components are in id order, so on a tie the first id stays.
                if best is None or rank > best[0]:
                    best = (rank, component)
        return best[1] if best else None

    def components_for(self, paths: Iterable[str]) -> tuple[tuple[Component, ...], tuple[str, ...]]:
        """The components that own *paths*, in id order, and the repo-wide paths."""
        owners: dict[str, Component] = {}
        repo_wide: list[str] = []
        for path in paths:
            owner = self.component_for(path)
            if owner is not None:
                owners[owner.id] = owner
            elif path not in repo_wide:
                repo_wide.append(path)
        return tuple(owners[key] for key in sorted(owners)), tuple(repo_wide)


def has_components(root: Path) -> bool:
    """True when the repo has a ``.dmx/components/`` folder."""
    return (root / COMPONENTS_DIR).is_dir()


def load_components(root: Path) -> ComponentSet:
    """Read every ``.dmx/components/<id>/component.yaml`` under *root*."""
    base = root / COMPONENTS_DIR
    if not base.is_dir():
        return ComponentSet()
    components: list[Component] = []
    skipped: list[SkippedComponent] = []
    for folder in sorted(base.iterdir()):
        path = folder / COMPONENT_FILE
        if not folder.is_dir() or not path.is_file():
            continue
        rel = f"{COMPONENTS_DIR}/{folder.name}/{COMPONENT_FILE}"
        try:
            components.append(_parse(folder.name, path.read_text(encoding="utf-8")))
        except _Invalid as exc:
            skipped.append(SkippedComponent(path=rel, reason=str(exc)))
        except (OSError, UnicodeDecodeError) as exc:
            skipped.append(SkippedComponent(path=rel, reason=f"can't be read: {exc}"))
    return ComponentSet(components=tuple(components), skipped=tuple(skipped))


class _Invalid(ValueError):
    pass


def _parse(component_id: str, text: str) -> Component:
    if not _ID_RE.match(component_id):
        raise _Invalid(
            f"`{component_id}` is not a valid component id "
            "(use lowercase letters, digits, `-` and `_`)"
        )
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _Invalid(f"not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise _Invalid("not a mapping of keys to values")

    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise _Invalid("`description` is missing")
    return Component(
        id=component_id,
        description=description.strip(),
        paths=_paths(data),
        depends_on=_string_list(data, "depends_on", "a list of component ids"),
        test=_test(data),
        validators=_mapping(data, "validators", "a mapping of loop names to validators"),
        ticket=_mapping(data, "ticket", "a mapping"),
    )


def _paths(data: dict[str, Any]) -> tuple[str, ...]:
    if "paths" not in data:
        raise _Invalid("`paths` is missing")
    paths = _string_list(data, "paths", "a list of path patterns")
    if not paths:
        raise _Invalid("`paths` is empty")
    for pattern in paths:
        if ".." in pattern.split("/"):
            raise _Invalid(f"`paths` entry {pattern!r} has a `..` segment")
    return paths


def _string_list(data: dict[str, Any], key: str, shape: str) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise _Invalid(f"`{key}` must be {shape}")
    return tuple(v.strip() for v in value)


def _mapping(data: dict[str, Any], key: str, shape: str) -> dict[str, Any] | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _Invalid(f"`{key}` must be {shape}")
    return value


def _test(data: dict[str, Any]) -> dict[str, str] | None:
    value = _mapping(data, "test", "a mapping with `cmd` and an optional `cwd`")
    if value is None:
        return None
    cmd = value.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        raise _Invalid("`test.cmd` is missing")
    test = {"cmd": cmd.strip()}
    cwd = value.get("cwd")
    if cwd is not None:
        if not isinstance(cwd, str) or ".." in cwd.split("/"):
            raise _Invalid("`test.cwd` must be a folder in the repo")
        test["cwd"] = cwd.strip()
    return test


# ---------------------------------------------------------------------------
# Path patterns
# ---------------------------------------------------------------------------


def _normalize(pattern: str) -> str:
    return pattern.strip().strip("/")


@cache
def _pattern_re(pattern: str) -> re.Pattern[str]:
    pattern = _normalize(pattern)
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out.append("(?:/.*)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def path_matches(pattern: str, path: str) -> bool:
    """True when *pattern* matches *path* or one of the folders above it."""
    regex = _pattern_re(pattern)
    parts = path.strip("/").split("/")
    return any(regex.fullmatch("/".join(parts[:end])) for end in range(len(parts), 0, -1))


def _specificity(pattern: str) -> tuple[int, int]:
    """Literal leading folders, then length: higher is more specific."""
    normalized = _normalize(pattern)
    literal = 0
    for segment in normalized.split("/"):
        if _WILDCARDS & set(segment):
            break
        literal += 1
    return literal, len(normalized)
