"""``dmx check``: validate the setup and show what a branch will load and run (GH-91).

Two sections, like ``terraform validate`` and ``terraform plan``:

- **Setup** checks the component config and everything around it. Every
  problem names the command or edit that fixes it. Errors (``✗``) make
  ``dmx check`` exit 1; warnings (``!``) and notes (``i``) don't.
- **This branch** shows the components the branch touches, the memory it
  loads, the validators each loop runs, and the test commands, from the
  same functions the loops call, so the plan can't drift from what runs.

Nothing here writes a file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp import (
    Context,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
    FastMCP,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
)

from dmx.component_config import component_validators
from dmx.component_tests import KNOWN_FAILURES_KEY, _test_command, plan_tests  # noqa: PLC2701
from dmx.components import (
    COMPONENT_FILE,
    COMPONENTS_DIR,
    has_components,
    load_components,
    path_matches,
)
from dmx.context_tools import build_context
from dmx.exceptions import WorkspaceRootInvalid
from dmx.learnings import REPO_WIDE as REPO_WIDE_OWNER
from dmx.learnings import waiting_counts, waiting_learnings
from dmx.loop_state import current_branch
from dmx.shared_sources import SharedSourceError, read_lock, read_shared_sources
from dmx.ticket_components import current_job, ticket_components
from dmx.ticket_routing import read_config_list
from dmx.workspace import resolve_workspace_root
from dmx.workspaces import _SKIP_DIRS, default_test_command, detect_components  # noqa: PLC2701

if TYPE_CHECKING:
    from dmx.components import ComponentSet

__all__ = ["Finding", "branch_plan", "check_setup", "has_errors", "register_check_tools", "render"]

_MARKS = {"ok": "✓", "error": "✗", "warn": "!", "info": "i"}
_CODEOWNERS_FILES = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
_STANDARD_LOOPS = ("spec", "plan", "dev", "validate", "release")


@dataclass(frozen=True)
class Finding:
    level: str  # ok, error, warn or info
    text: str
    fix: str | None = None

    def line(self) -> str:
        fix = f" ({self.fix})" if self.fix else ""
        return f"{_MARKS[self.level]} {self.text}{fix}"


def _component_file(component_id: str) -> str:
    return f"{COMPONENTS_DIR}/{component_id}/{COMPONENT_FILE}"


def _git_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.splitlines() if result.returncode == 0 else []


# ---------------------------------------------------------------------------
# Component checks
# ---------------------------------------------------------------------------


def _dependencies(components: ComponentSet) -> list[Finding]:
    findings = [
        Finding(
            "error",
            f'{component.id}: depends_on "{dep}" is not a component',
            f"fix depends_on in {_component_file(component.id)}",
        )
        for component in components.components
        for dep in component.depends_on
        if components.get(dep) is None
    ]
    seen_cycles: set[frozenset[str]] = set()
    for start in components.components:
        stack: list[tuple[str, list[str]]] = [(start.id, [start.id])]
        while stack:
            current, path = stack.pop()
            node = components.get(current)
            for dep in node.depends_on if node else ():
                if dep == start.id:
                    cycle = frozenset(path)
                    if cycle not in seen_cycles:
                        seen_cycles.add(cycle)
                        shown = " → ".join([*path, start.id])
                        findings.append(
                            Finding(
                                "error",
                                f"depends_on cycle: {shown}",
                                "remove one depends_on in the cycle",
                            )
                        )
                elif dep not in path and components.get(dep) is not None:
                    stack.append((dep, [*path, dep]))
    return findings


def _paths(root: Path, components: ComponentSet) -> list[Finding]:
    findings: list[Finding] = []
    owners: dict[str, list[str]] = {}
    for component in components.components:
        for pattern in component.paths:
            owners.setdefault(pattern.strip().strip("/"), []).append(component.id)
    for pattern, ids in owners.items():
        if len(ids) > 1:
            findings.append(
                Finding(
                    "error",
                    f'{" and ".join(ids)} both own "{pattern}"',
                    "give each component its own paths",
                )
            )
    files = _git_files(root)
    for component in components.components:
        if not any(path_matches(p, f) for p in component.paths for f in files):
            findings.append(
                Finding(
                    "error",
                    f"{component.id}: paths match no files (moved?)",
                    f"update paths in {_component_file(component.id)}",
                )
            )
    return findings


def _tests(root: Path, components: ComponentSet) -> list[Finding]:
    findings: list[Finding] = []
    for component in components.components:
        known = component.known_failures
        if known:
            # Listed so the accepted failures stay visible (GH-118).
            count = f"{len(known)} known failure{'' if len(known) == 1 else 's'}"
            findings.append(
                Finding("info", f"{component.id}: {count} in its tests ({', '.join(known)})")
            )
        if component.test is not None:
            continue
        command = default_test_command(root, component)
        if command is None:
            findings.append(
                Finding(
                    "error",
                    f"{component.id}: no test command (no default for its language, "
                    "or no tests in its folder or paths)",
                    f"set `test: {{cmd: ..., cwd: ...}}` in {_component_file(component.id)}",
                )
            )
        elif shutil.which(command.argv[0]) is None:
            findings.append(
                Finding(
                    "warn",
                    f"{component.id}: `{command.argv[0]}` not found on PATH",
                    f"install {command.argv[0]}",
                )
            )
    if _test_command(root) is None:
        findings.append(
            Finding(
                "warn",
                "no test_command in .dmx/config.md: changes outside every component run "
                "every component's tests",
                "set test_command in .dmx/config.md",
            )
        )
    repo_known = read_config_list(root, KNOWN_FAILURES_KEY) or []
    if repo_known:
        count = f"{len(repo_known)} known failure{'' if len(repo_known) == 1 else 's'}"
        findings.append(Finding("info", f"test_command: {count} ({', '.join(repo_known)})"))
    return findings


def _uncovered_members(root: Path) -> list[Finding]:
    return [
        Finding("warn", f"{member.path} has no component", f"run /dmx/add-component {member.path}")
        for member in detect_components(root).components
    ]


def _codeowners(root: Path, components: ComponentSet) -> list[Finding]:
    path = next((root / rel for rel in _CODEOWNERS_FILES if (root / rel).is_file()), None)
    if path is None:
        return [
            Finding(
                "warn",
                "no CODEOWNERS file",
                "add .github/CODEOWNERS with a line per component; /dmx/init suggests them",
            )
        ]
    patterns = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            patterns.append(line.split()[0])
    rel = path.relative_to(root).as_posix()
    return [
        Finding(
            "warn",
            f"{component.id}: no CODEOWNERS line",
            f'add "/{COMPONENTS_DIR}/{component.id}/  @<team>" to {rel}',
        )
        for component in components.components
        if not any(path_matches(p, _component_file(component.id)) for p in patterns)
    ]


def _learning_tags(root: Path, components: ComponentSet) -> list[Finding]:
    unknown: dict[str, set[str]] = {}
    for item in waiting_learnings(root, None, exclude_job=None):
        if item.owner != REPO_WIDE_OWNER and components.get(item.owner) is None:
            unknown.setdefault(item.owner, set()).add(item.job)
    findings = [
        Finding(
            "error",
            f"learnings tagged [{owner}] in {', '.join(sorted(jobs))}, "
            f"but {owner} is not a component",
            "re-tag them, or mark them folded with mark_learnings_folded",
        )
        for owner, jobs in sorted(unknown.items())
    ]
    for owner, count in waiting_counts(root, exclude_job=current_job(root)).items():
        if owner in unknown:
            continue
        noun = "learning" if count == 1 else "learnings"
        findings.append(
            Finding("info", f"{owner}: {count} {noun} waiting", f"run /dmx/update-memory {owner}")
        )
    return findings


# ---------------------------------------------------------------------------
# Repo-wide checks
# ---------------------------------------------------------------------------


def _locks(root: Path, components: ComponentSet | None) -> list[Finding]:
    scopes: list[str | None] = [None]
    if components is not None:
        scopes += [c.id for c in components.components]
    findings: list[Finding] = []
    for scope in scopes:
        label = scope or "repo-wide"
        command = (
            f"run /dmx/update-shared-sources {scope}"
            if scope
            else ("run /dmx/update-shared-sources")
        )
        try:
            sources = read_shared_sources(root, component=scope)
            lock = read_lock(root, component=scope)
        except SharedSourceError as exc:
            findings.append(Finding("error", f"{label}: {exc}", command))
            continue
        declared = {source.name for source in sources}
        for source in sources:
            entry = lock.get(source.name)
            if entry is None:
                findings.append(
                    Finding(
                        "error", f'{label}: shared source "{source.name}" is not locked', command
                    )
                )
            elif entry.source != source.source:
                findings.append(
                    Finding(
                        "error",
                        f'{label}: shared source "{source.name}" is locked from a '
                        "different address",
                        command,
                    )
                )
        for name in sorted(set(lock) - declared):
            findings.append(
                Finding("error", f'{label}: "{name}" is locked but no longer declared', command)
            )
    return findings


def _stray_dmx(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for folder, dirs, _files in os.walk(root):
        here = Path(folder)
        if here == root:
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            continue
        if ".dmx" in dirs and not (here / ".dmx" / "config.md").is_file():
            rel = (here / ".dmx").relative_to(root).as_posix()
            findings.append(
                Finding(
                    "warn",
                    f"{rel}/ has no config.md",
                    "restart any runs from the repo root, then delete it",
                )
            )
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
    return findings


def _old_paths(root: Path) -> list[Finding]:
    # Imported here: layout_migration imports loop_tools, which is heavy.
    from dmx.layout_migration import _scan_old_paths  # noqa: PLC0415, PLC2701

    findings: list[Finding] = []
    for hit in _scan_old_paths(root):
        where, _sep, line = hit.partition(": ")
        findings.append(
            Finding(
                "warn",
                f"{where} names an old path: {line}",
                "point it at loop_context's spec_path, then run /dmx/upgrade",
            )
        )
    return findings


_GH_TIMEOUT_SECONDS = 15


def _github_access(root: Path) -> list[Finding]:
    """Whether the GitHub access ``/dmx/init`` recorded works (GH-120)."""
    access = (read_config_list(root, "github_access") or [""])[0]
    if access == "mcp":
        return [Finding("ok", "GitHub access: the GitHub MCP server (user-github)")]
    if access != "gh":
        return []
    gh = shutil.which("gh")
    if gh is None:
        return [
            Finding(
                "error",
                "GitHub access is the gh CLI, but `gh` isn't on PATH",
                "install the GitHub CLI, or set github_access: mcp",
            )
        ]
    try:
        status = subprocess.run(
            [gh, "auth", "status"],  # the resolved path, so a gh.cmd runs too
            capture_output=True,
            text=True,
            check=False,
            timeout=_GH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        status = None
    if status is None or status.returncode != 0:
        return [
            Finding(
                "error", "GitHub access is the gh CLI, but it isn't signed in", "run gh auth login"
            )
        ]
    return [Finding("ok", "GitHub access: the gh CLI")]


def check_setup(root: Path) -> list[Finding]:
    """Every Setup finding for the repo at *root*, problems after the summary line."""
    if not has_components(root):
        findings = [Finding("ok", "no components (.dmx/components/ not set up)")]
        return (
            findings
            + _github_access(root)
            + _locks(root, None)
            + _stray_dmx(root)
            + _old_paths(root)
        )
    components = load_components(root)
    count = len(components.components)
    findings = [Finding("ok", f"{count} component{'' if count == 1 else 's'}")]
    findings += [
        Finding("error", f"{skipped.path}: {skipped.reason}", f"fix {skipped.path}")
        for skipped in components.skipped
    ]
    findings += _github_access(root)
    findings += _dependencies(components)
    findings += _paths(root, components)
    findings += _tests(root, components)
    findings += _uncovered_members(root)
    findings += _codeowners(root, components)
    findings += _locks(root, components)
    findings += _stray_dmx(root)
    findings += _old_paths(root)
    findings += _learning_tags(root, components)
    findings += _ticket_defaults(root)
    return findings


def _ticket_defaults(root: Path) -> list[Finding]:
    # Imported here: ticket_routing imports this package's modules.
    from dmx.ticket_routing import component_ticket_problems  # noqa: PLC0415

    return [
        Finding(
            "warn",
            problem.removesuffix("; ignored."),
            "fix ticket in its component.yaml, or allow it in .dmx/config.md",
        )
        for problem in component_ticket_problems(root)
    ]


def has_errors(findings: list[Finding]) -> bool:
    return any(finding.level == "error" for finding in findings)


# ---------------------------------------------------------------------------
# This branch
# ---------------------------------------------------------------------------


def _loop_validators(root: Path, job_id: str | None) -> dict[str, list[str]]:
    # Imported here: loop_tools imports this package's modules.
    from dmx.loop_tools import _resolve_loop  # noqa: PLC0415, PLC2701

    validators: dict[str, list[str]] = {}
    for loop_name in _STANDARD_LOOPS:
        try:
            config = _resolve_loop(loop_name, root)
        except (FileNotFoundError, SharedSourceError, ValueError):
            continue
        names = [v.tool for v in config.validators]
        if has_components(root):
            names += [v.tool for v in component_validators(root, job_id, loop_name)]
        if names:
            validators[loop_name] = names
    return validators


def branch_plan(root: Path) -> dict[str, Any]:
    """What the next loop on this branch loads, checks and runs."""
    job_id = current_job(root)
    context = build_context(root)
    plan: dict[str, Any] = {
        "branch": current_branch(root),
        "loads": context["memory_files"],
        "validators": _loop_validators(root, job_id),
    }
    if not has_components(root):
        # Imported here: the validator module runs as a script too.
        from dmx.validators.run_tests import _detect_test_command  # noqa: PLC0415, PLC2701

        command = _detect_test_command(root)
        plan.update(
            components=[],
            repo_wide_files=[],
            runs=[" ".join(command)] if command else [],
            unconfigured=[],
        )
        return plan
    found = ticket_components(root, job_id)
    tests = plan_tests(root)
    components = [(entry.id, entry.reason) for entry in found.entries]
    listed = {component_id for component_id, _reason in components}
    for component_id, reason in tests.reasons.items():
        if component_id not in listed:
            components.append((component_id, reason))
    plan.update(
        components=components,
        repo_wide_files=list(found.repo_wide_files),
        runs=[run.label for run in tests.runs],
        unconfigured=list(tests.unconfigured),
    )
    return plan


def _block(label: str, values: list[str]) -> list[str]:
    if not values:
        return [f"  {label:<11} (none)"]
    head, *rest = values
    return [f"  {label:<11} {head}", *(f"  {'':<11} {value}" for value in rest)]


def render(findings: list[Finding], plan: dict[str, Any]) -> str:
    """The report ``dmx check`` prints."""
    lines = ["Setup", *(f"  {finding.line()}" for finding in findings), ""]
    lines.append(f"This branch ({plan['branch'] or 'detached HEAD'})")
    if plan["components"]:
        shown = ", ".join(f"{cid} ({reason})" for cid, reason in plan["components"])
        lines.append(f"  Components: {shown}")
    if plan["repo_wide_files"]:
        lines.append(f"  Repo-wide files: {', '.join(plan['repo_wide_files'])}")
    lines += _block("Loads:", plan["loads"])
    lines += _block(
        "Validators:", [f"{loop}: {', '.join(names)}" for loop, names in plan["validators"].items()]
    )
    runs = list(plan["runs"]) + [f"{cid} — not configured" for cid in plan["unconfigured"]]
    lines += _block("Runs:", runs)
    return "\n".join(lines)


def register_check_tools(app: FastMCP) -> None:
    """Register ``check_setup`` on *app*."""

    @app.tool(name="check_setup")
    async def check_setup_tool(ctx: Context, workspace_root: str | None = None) -> str:
        """Check the dmx setup and show what this branch will load, check and run.

        Called by ``/dmx/check``. Never changes a file. Each problem names the
        command or edit that fixes it.

        Args:
            workspace_root: Repo root path override. Auto-detected if omitted.

        Returns:
            The report, as ``dmx check`` prints it.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return f"Could not resolve a valid workspace root: {exc}"
        return render(check_setup(root), branch_plan(root))
