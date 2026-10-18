"""Where a new ticket goes: a Jira project or a GitHub repo, per component (GH-92).

``config.md`` may allow more than one destination::

    jira_projects: [LEDG, PAY, BILL]                    # project_key stays the default
    github_issue_repos: [acme/mono, acme/ledger-issues]  # owner/repo stays the default

and a component may set its own default in ``component.yaml``::

    ticket: {jira_project: PAY}
    ticket: {github_repo: acme/ledger-issues, github_labels: [area/ledger]}

:func:`choose_destination` picks what the developer named, else the
ticket's components' common default, else the repo default, and asks first
whenever dmx made the choice and there was one to make. With one allowed
destination and no component defaults, nothing changes from before.

An issue in a repo other than the default is written ``acme/ledger-issues#7``
in prose and ``ledger-issues-7`` in branch names, job ids and the spec's
``ticket`` field, so no ``/`` or ``#`` reaches a path. The default repo keeps
``#7`` and ``gh-7``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml
from fastmcp import (
    Context,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
    FastMCP,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
)

from dmx.components import load_components
from dmx.exceptions import WorkspaceRootInvalid
from dmx.workspace import resolve_workspace_root

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "Destination",
    "Destinations",
    "allowed_destinations",
    "choose_destination",
    "component_ticket_problems",
    "full_ref",
    "parse_branch_ref",
    "read_config_list",
    "register_ticket_routing_tools",
    "short_ref",
]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _config_text(root: Path) -> str:
    try:
        return (root / ".dmx" / "config.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _config_value(root: Path, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}[ \t]*:[ \t]*([^\s#]+)", _config_text(root), re.MULTILINE)
    if not match or match.group(1) == "{REQUIRED}":
        return None
    return match.group(1)


def read_config_list(root: Path, key: str) -> list[str] | None:
    """A ``key: [a, b]`` line in ``.dmx/config.md`` as a list, or None.

    The whole value is read as YAML, so flow lists and quotes work; a single
    value becomes a one-item list.
    """
    match = re.search(
        rf"^{re.escape(key)}[ \t]*:[ \t]*(.+?)[ \t]*$", _config_text(root), re.MULTILINE
    )
    if not match:
        return None
    try:
        value = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    if isinstance(value, str | int):
        value = [value]
    if not isinstance(value, list) or not value:
        return None
    items = [str(item).strip() for item in value if str(item).strip()]
    return items or None


@dataclass(frozen=True)
class Destinations:
    """The ticketing system, its default destination, and every allowed one."""

    kind: str  # jira, github-issues or none
    default: str | None
    allowed: list[str]

    def find(self, name: str) -> str | None:
        """*name* as it is spelt in ``allowed``, matched without case, or None."""
        return next((a for a in self.allowed if a.lower() == name.strip().lower()), None)


def allowed_destinations(root: Path) -> Destinations:
    kind = _config_value(root, "ticketing") or "none"
    if kind == "jira":
        default = _config_value(root, "project_key")
        listed = read_config_list(root, "jira_projects") or []
    elif kind == "github-issues":
        owner, repo = _config_value(root, "owner"), _config_value(root, "repo")
        default = f"{owner}/{repo}" if owner and repo else None
        listed = read_config_list(root, "github_issue_repos") or []
    else:
        return Destinations(kind="none", default=None, allowed=[])
    allowed = ([default] if default else []) + [
        item for item in listed if not default or item.lower() != default.lower()
    ]
    return Destinations(kind=kind, default=default, allowed=allowed)


@dataclass(frozen=True)
class Destination:
    kind: str
    target: str | None
    reason: str  # named, component, repo or none
    labels: list[str] = field(default_factory=list)
    confirm: bool = False
    question: str | None = None
    options: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    blocked: str | None = None


def _component_defaults(
    root: Path, allowed: Destinations, component_ids: list[str]
) -> tuple[dict[tuple[str, tuple[str, ...]], list[str]], list[str]]:
    """``{(target, labels): [component ids]}`` and notes about ignored values."""
    components = load_components(root)
    defaults: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    notes: list[str] = []
    for component_id in component_ids:
        component = components.get(component_id)
        ticket = component.ticket if component else None
        if not ticket:
            continue
        if allowed.kind == "jira":
            raw = ticket.get("jira_project")
            if raw is None:
                continue
            target = allowed.find(str(raw))
            if target is None:
                notes.append(
                    f"{component_id}'s ticket.jira_project `{raw}` is not in jira_projects; "
                    "ignored."
                )
                continue
            defaults.setdefault((target, ()), []).append(component_id)
        elif allowed.kind == "github-issues":
            raw_repo = ticket.get("github_repo")
            labels = ticket.get("github_labels") or []
            labels = tuple(str(label) for label in labels) if isinstance(labels, list) else ()
            if raw_repo is None and not labels:
                continue
            target = allowed.find(str(raw_repo)) if raw_repo is not None else allowed.default
            if target is None:
                notes.append(
                    f"{component_id}'s ticket.github_repo `{raw_repo}` is not in "
                    "github_issue_repos; ignored."
                )
                continue
            defaults.setdefault((target, labels), []).append(component_id)
    return defaults, notes


def _describe(kind: str, target: str, labels: list[str]) -> str:
    if kind == "jira":
        return target
    labelled = f" labelled {', '.join(labels)}" if labels else ""
    return f"{target}{labelled}"


def _ask(kind: str, target: str, labels: list[str], why: str) -> str:
    if kind == "jira":
        return f"Create the ticket in {target} ({why})?"
    return f"Open the issue in {_describe(kind, target, labels)} ({why})?"


def choose_destination(
    root: Path, requested: str | None = None, components: list[str] | None = None
) -> Destination:
    """Where a new ticket goes, and whether to ask first."""
    allowed = allowed_destinations(root)
    if allowed.kind == "none":
        return Destination(kind="none", target=None, reason="none")
    defaults, notes = _component_defaults(root, allowed, components or [])

    if requested:
        target = allowed.find(requested)
        if target is None:
            what = "Jira project" if allowed.kind == "jira" else "issue repo"
            key = "jira_projects" if allowed.kind == "jira" else "github_issue_repos"
            return Destination(
                kind=allowed.kind,
                target=None,
                reason="named",
                notes=notes,
                blocked=(
                    f"`{requested}` is not an allowed {what}. Allowed: "
                    f"{', '.join(allowed.allowed)} ({key} in .dmx/config.md)."
                ),
            )
        named_labels = next((list(lb) for (t, lb) in defaults if t == target), [])
        return Destination(
            kind=allowed.kind, target=target, reason="named", labels=named_labels, notes=notes
        )

    if len(defaults) == 1:
        [((only, only_labels), owners)] = defaults.items()
        return Destination(
            kind=allowed.kind,
            target=only,
            reason="component",
            labels=list(only_labels),
            confirm=True,
            question=_ask(
                allowed.kind, only, list(only_labels), f"default for {', '.join(owners)}"
            ),
            notes=notes,
        )
    if len(defaults) > 1:
        choices = [(t, list(lb), owners) for (t, lb), owners in defaults.items()]
        options: list[dict[str, Any]] = [
            {"target": t, "labels": lb, "components": owners} for t, lb, owners in choices
        ]
        listed = ", ".join(
            f"{_describe(allowed.kind, t, lb)} ({', '.join(owners)})" for t, lb, owners in choices
        )
        return Destination(
            kind=allowed.kind,
            target=None,
            reason="component",
            confirm=True,
            question=f"This ticket's components have different defaults: {listed}. Which one?",
            options=options,
            notes=notes,
        )

    target = allowed.default
    others = [a for a in allowed.allowed if a != target]
    if target is None:
        return Destination(kind=allowed.kind, target=None, reason="repo", notes=notes)
    question = None
    if others:
        what = "the ticket" if allowed.kind == "jira" else "the issue"
        verb = "Create" if allowed.kind == "jira" else "Open"
        question = (
            f"{verb} {what} in {target} (the repo default)? Other options: {', '.join(others)}."
        )
    return Destination(
        kind=allowed.kind,
        target=target,
        reason="repo",
        confirm=bool(others),
        question=question,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# GitHub references
# ---------------------------------------------------------------------------


def _prefix(repo: str) -> str:
    return _SLUG_RE.sub("-", repo.rsplit("/", 1)[-1].lower()).strip("-")


def _is_default(root: Path, repo: str) -> bool:
    default = allowed_destinations(root).default
    return default is None or repo.lower() == default.lower()


def short_ref(root: Path, repo: str, number: int | str) -> str:
    """``gh-7`` for the default repo, ``ledger-issues-7`` for another: safe in paths."""
    return f"gh-{number}" if _is_default(root, repo) else f"{_prefix(repo)}-{number}"


def full_ref(root: Path, repo: str, number: int | str) -> str:
    """``#7`` for the default repo, ``acme/ledger-issues#7`` for another."""
    return f"#{number}" if _is_default(root, repo) else f"{repo}#{number}"


def branch_prefixes(root: Path) -> dict[str, str]:
    """Each short-reference prefix and the repo it stands for."""
    allowed = allowed_destinations(root)
    if allowed.kind != "github-issues":
        return {}
    prefixes = {"gh": allowed.default} if allowed.default else {}
    for repo in allowed.allowed:
        if repo != allowed.default:
            prefixes[_prefix(repo)] = repo
    return prefixes


def parse_branch_ref(root: Path, branch: str) -> tuple[str, int] | None:
    """The ``(repo, number)`` a branch name refers to, or None."""
    for prefix, repo in sorted(branch_prefixes(root).items(), key=lambda kv: -len(kv[0])):
        match = re.search(rf"(?:^|-){re.escape(prefix)}-(\d+)(?:-|$)", branch)
        if match:
            return repo, int(match.group(1))
    return None


def component_ticket_problems(root: Path) -> list[str]:
    """Component ``ticket`` defaults outside the allowed destinations, for ``dmx check``."""
    allowed = allowed_destinations(root)
    if allowed.kind == "none":
        return []
    ids = list(load_components(root).ids)
    return _component_defaults(root, allowed, ids)[1]


def register_ticket_routing_tools(app: FastMCP) -> None:
    """Register ``ticket_destination`` on *app*."""

    @app.tool
    async def ticket_destination(
        ctx: Context,
        requested: str | None = None,
        components: list[str] | None = None,
        workspace_root: str | None = None,
    ) -> dict[str, Any]:
        """Where a new ticket goes: a Jira project, or a GitHub repo and labels.

        Called by ``create-ticket``, ``derive-ticket``, ``create-branch`` and
        ``/dmx/hotfix`` before creating a ticket. Picks what the developer
        named, else the default of the ticket's components, else the repo
        default. When ``confirm`` is true, ask ``question`` and wait before
        creating anything. ``options`` lists each choice when components
        disagree. ``short_ref`` and ``full_ref`` are the reference forms
        for the chosen GitHub repo, with ``{number}`` to fill in.

        Args:
            requested: A project key or ``owner/repo`` the developer named.
            components: Component ids. The current ticket's when omitted.
            workspace_root: Repo root path override. Auto-detected if omitted.

        Returns:
            ``kind``, ``target``, ``reason``, ``labels``, ``confirm``,
            ``question``, ``options``, ``notes``, ``blocked``, ``short_ref``,
            ``full_ref`` and ``branch_prefixes``.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return {"blocked": f"Could not resolve a valid workspace root: {exc}"}
        if components is None:
            # Imported here: ticket_components imports loop_state, which is heavy.
            from dmx.ticket_components import current_job, ticket_components  # noqa: PLC0415
            from dmx.workspace import ide_folder  # noqa: PLC0415

            found = ticket_components(root, current_job(root), folder=ide_folder())
            components = [c.id for c in found.components]
        chosen = choose_destination(root, requested, components)
        repo = chosen.target if chosen.kind == "github-issues" else None
        return {
            "kind": chosen.kind,
            "target": chosen.target,
            "reason": chosen.reason,
            "labels": chosen.labels,
            "confirm": chosen.confirm,
            "question": chosen.question,
            "options": chosen.options,
            "notes": chosen.notes,
            "blocked": chosen.blocked,
            "short_ref": short_ref(root, repo, "{number}") if repo else None,
            "full_ref": full_ref(root, repo, "{number}") if repo else None,
            "branch_prefixes": branch_prefixes(root),
        }
