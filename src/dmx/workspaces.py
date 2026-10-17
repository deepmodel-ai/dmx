"""Find components in a repo's workspace files, for ``/dmx/init`` (GH-86).

One entry in :data:`WORKSPACES` per kind of workspace. Each says which root
file declares the members, how to read the member folders from it, which
manifest a member folder has, and how to read a name and description from
that manifest. #89's default test commands key off the same entries, so
adding a language is one entry.

The root folder itself is never a component: it holds the repo-wide files.
A member a component already covers is not offered again, and an existing
``component.yaml`` is never overwritten.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml
from fastmcp import (
    Context,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
    FastMCP,  # noqa: TCH002 — needed at runtime for FastMCP annotation resolution
)

from dmx.components import COMPONENT_FILE, COMPONENTS_DIR, load_components, path_matches
from dmx.exceptions import WorkspaceRootInvalid
from dmx.workspace import resolve_workspace_root

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = [
    "WORKSPACES",
    "AppliedComponents",
    "DetectedComponent",
    "DetectedComponents",
    "Workspace",
    "apply_detected_components",
    "codeowners_lines",
    "detect_components",
    "register_workspace_tools",
]

# Never members, wherever a glob reaches.
_SKIP_DIRS = frozenset({"node_modules", ".git", ".dmx", "target", ".venv", "dist", "build"})
_ID_CHARS = re.compile(r"[^a-z0-9_-]+")
_GO_MAJOR = re.compile(r"^v\d+$")
_CODEOWNERS_WIDTH = 32


@dataclass(frozen=True)
class Workspace:
    """One kind of workspace."""

    name: str
    language: str
    marker: str
    manifest: str
    members: Callable[[Path], tuple[list[str], list[str]] | None]
    identity: Callable[[Path], tuple[str | None, str | None]]
    applies: Callable[[Path], bool] = lambda _root: True


@dataclass(frozen=True)
class DetectedComponent:
    """A member folder offered as a component."""

    id: str
    path: str
    description: str
    language: str
    workspace: str

    @property
    def paths(self) -> tuple[str, ...]:
        return (f"{self.path}/**",)

    def component_yaml(self) -> str:
        paths = ", ".join(json.dumps(pattern) for pattern in self.paths)
        return f"description: {_yaml_scalar(self.description)}\npaths: [{paths}]\n"


@dataclass(frozen=True)
class DetectedComponents:
    """The workspaces found and the components they offer."""

    workspaces: tuple[str, ...] = ()
    components: tuple[DetectedComponent, ...] = ()

    @property
    def ask(self) -> bool:
        """Offer components only when there is more than one to split the repo into."""
        return len(self.components) >= 2


@dataclass(frozen=True)
class AppliedComponents:
    written: tuple[str, ...]
    kept: tuple[str, ...]
    unknown_ids: tuple[str, ...]
    codeowners: tuple[str, ...]


# ---------------------------------------------------------------------------
# Reading workspace files. Each reader returns (include, exclude) globs, or
# None when the file is missing, unreadable or declares no members.
# ---------------------------------------------------------------------------


def _read_toml(path: Path) -> dict[str, Any] | None:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _pnpm_members(root: Path) -> tuple[list[str], list[str]] | None:
    try:
        data = yaml.safe_load((root / "pnpm-workspace.yaml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    patterns = _strings(data.get("packages")) if isinstance(data, dict) else []
    include = [p for p in patterns if not p.startswith("!")]
    exclude = [p[1:] for p in patterns if p.startswith("!")]
    return (include, exclude) if include else None


def _package_json_members(root: Path) -> tuple[list[str], list[str]] | None:
    data = _read_json(root / "package.json")
    if data is None:
        return None
    workspaces = data.get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    patterns = _strings(workspaces)
    include = [p for p in patterns if not p.startswith("!")]
    exclude = [p[1:] for p in patterns if p.startswith("!")]
    return (include, exclude) if include else None


def _go_work_members(root: Path) -> tuple[list[str], list[str]] | None:
    try:
        text = (root / "go.work").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    uses: list[str] = []
    in_block = False
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if in_block:
            if line == ")":
                in_block = False
            elif line:
                uses.append(line.strip('"'))
        elif line == "use (":
            in_block = True
        elif line.startswith("use "):
            uses.append(line[4:].strip().strip('"'))
    return (uses, []) if uses else None


def _uv_members(root: Path) -> tuple[list[str], list[str]] | None:
    data = _read_toml(root / "pyproject.toml")
    workspace = (data or {}).get("tool", {}).get("uv", {}).get("workspace")
    if not isinstance(workspace, dict):
        return None
    include = _strings(workspace.get("members"))
    return (include, _strings(workspace.get("exclude"))) if include else None


def _cargo_members(root: Path) -> tuple[list[str], list[str]] | None:
    workspace = (_read_toml(root / "Cargo.toml") or {}).get("workspace")
    if not isinstance(workspace, dict):
        return None
    include = _strings(workspace.get("members"))
    return (include, _strings(workspace.get("exclude"))) if include else None


# ---------------------------------------------------------------------------
# Reading member manifests: (name, description).
# ---------------------------------------------------------------------------


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _node_identity(folder: Path) -> tuple[str | None, str | None]:
    data = _read_json(folder / "package.json") or {}
    return _text(data.get("name")), _text(data.get("description"))


def _go_identity(folder: Path) -> tuple[str | None, str | None]:
    try:
        text = (folder / "go.mod").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, None
    match = re.search(r"^module\s+(\S+)", text, re.MULTILINE)
    if not match:
        return None, None
    module = match.group(1).strip('"')
    parts = [part for part in module.split("/") if part]
    if len(parts) > 1 and _GO_MAJOR.match(parts[-1]):
        parts.pop()
    return (parts[-1] if parts else None), f"Go module {module}"


def _python_identity(folder: Path) -> tuple[str | None, str | None]:
    project = (_read_toml(folder / "pyproject.toml") or {}).get("project") or {}
    return _text(project.get("name")), _text(project.get("description"))


def _rust_identity(folder: Path) -> tuple[str | None, str | None]:
    package = (_read_toml(folder / "Cargo.toml") or {}).get("package") or {}
    return _text(package.get("name")), _text(package.get("description"))


WORKSPACES: tuple[Workspace, ...] = (
    Workspace("go.work", "go", "go.work", "go.mod", _go_work_members, _go_identity),
    Workspace("pnpm", "node", "pnpm-workspace.yaml", "package.json", _pnpm_members, _node_identity),
    Workspace(
        "yarn",
        "node",
        "package.json",
        "package.json",
        _package_json_members,
        _node_identity,
        applies=lambda root: (
            (root / "yarn.lock").is_file() and not (root / "pnpm-workspace.yaml").is_file()
        ),
    ),
    Workspace(
        "npm",
        "node",
        "package.json",
        "package.json",
        _package_json_members,
        _node_identity,
        applies=lambda root: (
            not (root / "yarn.lock").is_file() and not (root / "pnpm-workspace.yaml").is_file()
        ),
    ),
    Workspace("uv", "python", "pyproject.toml", "pyproject.toml", _uv_members, _python_identity),
    Workspace("cargo", "rust", "Cargo.toml", "Cargo.toml", _cargo_members, _rust_identity),
)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _expand(root: Path, include: list[str], exclude: list[str], manifest: str) -> list[str]:
    found: list[str] = []
    for pattern in include:
        pattern = pattern.strip().removeprefix("./").strip("/")
        if pattern in ("", ".") or ".." in pattern.split("/"):
            continue
        for folder in sorted(root.glob(pattern)):
            if not folder.is_dir() or not (folder / manifest).is_file():
                continue
            rel = folder.relative_to(root).as_posix()
            if _SKIP_DIRS & set(rel.split("/")):
                continue
            if any(path_matches(p.removeprefix("./"), rel) for p in exclude):
                continue
            if rel not in found:
                found.append(rel)
    return found


def _slug(text: str) -> str:
    return _ID_CHARS.sub("-", text.lower()).strip("-_")


def _base_id(name: str | None, path: str) -> str:
    if name:
        candidate = _slug(name.rsplit("/", 1)[-1])
        if candidate:
            return candidate
    return _slug(path.rsplit("/", 1)[-1]) or _slug(path)


def detect_components(root: Path) -> DetectedComponents:
    """The components *root*'s workspace files offer, sorted by id."""
    existing = load_components(root)
    workspaces: list[str] = []
    members: list[tuple[str, str | None, str | None, Workspace]] = []
    seen_paths: set[str] = set()
    for workspace in WORKSPACES:
        if not (root / workspace.marker).is_file() or not workspace.applies(root):
            continue
        declared = workspace.members(root)
        if declared is None:
            continue
        workspaces.append(workspace.name)
        for path in _expand(root, *declared, workspace.manifest):
            if path in seen_paths or existing.component_for(path) is not None:
                continue
            seen_paths.add(path)
            name, description = workspace.identity(root / path)
            members.append((path, name, description, workspace))

    # Every folder name under .dmx/components/ is taken, including one whose
    # component.yaml is broken and so did not load.
    components_dir = root / COMPONENTS_DIR
    taken = set(existing.ids) | (
        {folder.name for folder in components_dir.iterdir() if folder.is_dir()}
        if components_dir.is_dir()
        else set()
    )
    base_ids = [_base_id(name, path) for path, name, _d, _w in members]
    clashing = {i for i in base_ids if base_ids.count(i) > 1 or i in taken}
    components: list[DetectedComponent] = []
    used = set(taken)
    for (path, _name, description, workspace), base in zip(members, base_ids, strict=True):
        component_id = _slug(path.replace("/", "-")) if base in clashing else base
        unique, n = component_id, 2
        while unique in used:
            unique, n = f"{component_id}-{n}", n + 1
        used.add(unique)
        components.append(
            DetectedComponent(
                id=unique,
                path=path,
                description=description or f"{workspace.name} workspace member at {path}",
                language=workspace.language,
                workspace=workspace.name,
            )
        )
    components.sort(key=lambda component: component.id)
    return DetectedComponents(workspaces=tuple(workspaces), components=tuple(components))


def codeowners_lines(component_ids: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Suggested ``CODEOWNERS`` lines: platform owns ``.dmx/``, each team its folder."""
    rows = [("/.dmx/", "@<platform-team>")]
    rows += [(f"/{COMPONENTS_DIR}/{cid}/", f"@<{cid}-team>") for cid in component_ids]
    lines = [f"{path:<{_CODEOWNERS_WIDTH}}{owner}" for path, owner in rows]
    # Job folders have no owner, so a ticket's own files never need platform approval.
    lines.append("/.dmx/jobs/")
    return tuple(lines)


def apply_detected_components(root: Path, ids: list[str] | None = None) -> AppliedComponents:
    """Write ``component.yaml`` for the detected components named in *ids* (all by default).

    An existing ``component.yaml`` is left as it is.
    """
    detected = {component.id: component for component in detect_components(root).components}
    wanted = list(detected) if ids is None else list(dict.fromkeys(ids))
    written: list[str] = []
    kept: list[str] = []
    unknown: list[str] = []
    for component_id in sorted(wanted):
        component = detected.get(component_id)
        if component is None:
            unknown.append(component_id)
            continue
        path = root / COMPONENTS_DIR / component_id / COMPONENT_FILE
        rel = path.relative_to(root).as_posix()
        if path.exists():
            kept.append(rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(component.component_yaml(), encoding="utf-8")
        written.append(rel)
    written_ids = [rel.split("/")[2] for rel in written]
    return AppliedComponents(
        written=tuple(written),
        kept=tuple(kept),
        unknown_ids=tuple(unknown),
        codeowners=codeowners_lines(written_ids),
    )


def _yaml_scalar(text: str) -> str:
    """*text* as a plain YAML scalar when that reads back the same, else quoted."""
    try:
        if yaml.safe_load(f"v: {text}") == {"v": text}:
            return text
    except yaml.YAMLError:
        pass
    return json.dumps(text)


def register_workspace_tools(app: FastMCP) -> None:
    """Register ``detect_components`` on *app*."""

    @app.tool(name="detect_components")
    async def detect_components_tool(
        ctx: Context,
        apply: bool = False,
        ids: list[str] | None = None,
        workspace_root: str | None = None,
    ) -> dict[str, Any]:
        """Find components in the repo's workspace files, then write the chosen ones.

        Called by ``/dmx/init``. Reads pnpm (``pnpm-workspace.yaml``), npm
        and yarn (``package.json`` ``workspaces``), ``go.work``, ``uv``
        (``[tool.uv.workspace]``) and Cargo (``[workspace]``) workspaces.
        First call it without ``apply`` to get the plan; ``ask`` is false
        when there are fewer than two components to offer, and then nothing
        should be asked. Then call it with ``apply`` true and the ``ids`` the
        developer kept to write ``.dmx/components/<id>/component.yaml``.
        Existing component files are never overwritten.

        Args:
            apply: When true, write the component files.
            ids: The components to write. All detected ones when omitted.
            workspace_root: Repo root path override. Auto-detected if omitted.

        Returns:
            ``workspaces``, ``components`` (each with ``id``, ``path``,
            ``paths``, ``description``, ``language``, ``workspace`` and
            ``component_yaml``), ``ask`` and ``codeowners``; after ``apply``,
            also ``written``, ``kept`` and ``unknown_ids``.
        """
        try:
            root = await resolve_workspace_root(ctx, workspace_root)
        except WorkspaceRootInvalid as exc:
            return {"error": f"Could not resolve a valid workspace root: {exc}"}
        detected = detect_components(root)
        result: dict[str, Any] = {
            "workspaces": list(detected.workspaces),
            "components": [
                {
                    "id": c.id,
                    "path": c.path,
                    "paths": list(c.paths),
                    "description": c.description,
                    "language": c.language,
                    "workspace": c.workspace,
                    "component_yaml": c.component_yaml(),
                }
                for c in detected.components
            ],
            "ask": detected.ask,
            "codeowners": list(codeowners_lines([c.id for c in detected.components])),
        }
        if apply:
            applied = apply_detected_components(root, ids)
            result.update(
                written=list(applied.written),
                kept=list(applied.kept),
                unknown_ids=list(applied.unknown_ids),
                codeowners=list(applied.codeowners),
            )
        return result
