"""Which tests to run for a branch in a repo with components (GH-89).

:func:`plan_tests` decides; the bundled ``run_tests`` validator runs the plan,
and ``dmx check`` (#91) prints it. A component runs when the branch changed
one of its files, and so does every component that depends on it through
``depends_on``, directly or through others. A file outside every component
is a repo-wide change: it runs ``test_command`` from ``config.md``, or every
component's tests when none is set, because a root ``go.mod`` or lock file
can break anything. A branch with no changes is treated the same way, so a
loop never passes on zero tests.

A component's command is its ``test`` entry, run through ``sh -c`` from
``cwd`` (the repo root when absent), else the default for what its folder
holds (:func:`dmx.workspaces.default_test_command`). A component with
neither is unconfigured, which fails the tests rather than skipping them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dmx.components import ComponentSet, load_components
from dmx.ticket_components import changed_files
from dmx.workspaces import TestCommand, default_test_command

if TYPE_CHECKING:
    from pathlib import Path

    from dmx.components import Component

__all__ = ["REPO_WIDE", "TestPlan", "TestRun", "dependents", "plan_tests"]

REPO_WIDE = "repo-wide"
REASON_CHANGED = "changed files"
REASON_REPO_WIDE = "repo-wide change"

# The whole line: a command has spaces, unlike the other config values.
_TEST_COMMAND_RE = re.compile(r"^test_command[ \t]*:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class TestRun:
    """One command, and the components it tests."""

    components: tuple[str, ...]
    argv: tuple[str, ...]
    cwd: str
    command: str

    @property
    def label(self) -> str:
        where = f" in {self.cwd}" if self.cwd != "." else ""
        return f"{', '.join(self.components)} — {self.command}{where}"


@dataclass(frozen=True)
class TestPlan:
    runs: tuple[TestRun, ...] = ()
    unconfigured: tuple[str, ...] = ()
    reasons: dict[str, str] = field(default_factory=dict)
    repo_wide_files: tuple[str, ...] = ()


def dependents(components: ComponentSet, ids: set[str]) -> dict[str, str]:
    """Every component that depends on one in *ids*, directly or not, with the one it follows."""
    found: dict[str, str] = {}
    frontier = sorted(ids)
    while frontier:
        nxt: list[str] = []
        for component in components.components:
            if component.id in ids or component.id in found:
                continue
            via = next((dep for dep in component.depends_on if dep in frontier), None)
            if via is not None:
                found[component.id] = via
                nxt.append(component.id)
        frontier = nxt
    return found


def _shell(cmd: str, cwd: str | None) -> tuple[tuple[str, ...], str, str]:
    return ("sh", "-c", cmd), (cwd or ".").strip("/") or ".", cmd


def _command(root: Path, component: Component) -> tuple[tuple[str, ...], str, str] | None:
    if component.test is not None:
        return _shell(component.test["cmd"], component.test.get("cwd"))
    found: TestCommand | None = default_test_command(root, component)
    if found is None:
        return None
    return found.argv, found.cwd, " ".join(found.argv)


def _test_command(root: Path) -> str | None:
    """``test_command`` from ``.dmx/config.md``, quotes removed, or None."""
    try:
        text = (root / ".dmx" / "config.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = _TEST_COMMAND_RE.search(text)
    if not match:
        return None
    value = match.group(1)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.strip() or None


def plan_tests(root: Path) -> TestPlan:
    """The test runs for the branch checked out in *root*."""
    components = load_components(root)
    files = changed_files(root)
    owners, repo_wide = components.components_for(files)

    reasons: dict[str, str] = {owner.id: REASON_CHANGED for owner in owners}
    repo_wide_command = None
    if repo_wide or not files:
        repo_wide_command = _test_command(root)
        if repo_wide_command is None:
            for component in components.components:
                reasons.setdefault(component.id, REASON_REPO_WIDE)
    for component_id, via in dependents(components, set(reasons)).items():
        reasons[component_id] = f"depends on {via}"

    grouped: dict[tuple[tuple[str, ...], str], tuple[list[str], str]] = {}
    unconfigured: list[str] = []
    for component_id in sorted(reasons):
        found = components.get(component_id)
        if found is None:
            continue
        command = _command(root, found)
        if command is None:
            unconfigured.append(component_id)
            continue
        argv, cwd, shown = command
        grouped.setdefault((argv, cwd), ([], shown))[0].append(component_id)
    runs = [
        TestRun(components=tuple(ids), argv=argv, cwd=cwd, command=shown)
        for (argv, cwd), (ids, shown) in grouped.items()
    ]
    if repo_wide_command is not None:
        argv, cwd, shown = _shell(repo_wide_command, None)
        runs.append(TestRun(components=(REPO_WIDE,), argv=argv, cwd=cwd, command=shown))
    return TestPlan(
        runs=tuple(runs),
        unconfigured=tuple(unconfigured),
        reasons=reasons,
        repo_wide_files=repo_wide,
    )
