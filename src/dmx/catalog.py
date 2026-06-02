"""Skill and rule catalog: load, parse, and template substitution."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path  # noqa: TCH003 — used at runtime in rglob/stat calls
from typing import Any, cast

import frontmatter

from dmx.exceptions import RuleLoadError, SkillLoadError

__all__ = [
    "ARG_NAME_RE",
    "SLUG_RE",
    "SkillArgument",
    "SkillDefinition",
    "RuleDefinition",
    "load_skills",
    "load_rules",
    "substitute_args",
]

logger = logging.getLogger(__name__)

SLUG_RE = re.compile(r"^[a-z0-9_-]+$")
# Argument names must be valid Python identifiers (lowercase, underscores only).
# Hyphens are intentionally excluded: argument names are template placeholders
# and Python parameter names — hyphens are not valid in either context.
ARG_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_SKILL_BYTES = 256 * 1024  # 256 KiB


@dataclass(frozen=True)
class SkillArgument:
    """A single argument accepted by a skill.

    Attributes:
        name: Argument name used in ``{{name}}`` template placeholders.
        description: Human-readable description shown in IDE command palette.
        required: Whether the argument must be provided at invocation time.
    """

    name: str
    description: str = ""
    required: bool = False


@dataclass(frozen=True)
class SkillDefinition:
    """A fully parsed skill loaded from a Markdown file.

    Attributes:
        name: Slug from frontmatter ``name`` field; falls back to filename stem.
        title: Display name shown in IDE command palette.
        description: Short description shown in IDE command palette.
        arguments: Tuple of accepted arguments.
        body: Raw Markdown body with ``{{arg}}`` placeholders.
    """

    name: str
    title: str
    description: str
    arguments: tuple[SkillArgument, ...] = field(default_factory=tuple)
    body: str = ""


@dataclass(frozen=True)
class RuleDefinition:
    """A fully parsed rule loaded from a Markdown file.

    Attributes:
        name: Slug from frontmatter ``name`` field; falls back to filename stem.
        title: Display name.
        description: Short description shown in IDE rule picker.
        always_apply: Whether the rule is always injected into context.
        globs: Optional file-pattern filter (Cursor ``.mdc`` / Claude paths).
        ides: Target IDEs; empty tuple means all targets.
        body: Raw Markdown rule body.
    """

    name: str
    title: str
    description: str = ""
    always_apply: bool = True
    globs: str | None = None
    ides: tuple[str, ...] = field(default_factory=tuple)
    body: str = ""


def load_skills(directory: Path) -> tuple[SkillDefinition, ...]:
    """Load all skill files from *directory* recursively.

    Files that cannot be parsed are skipped with a warning; the rest are
    returned. Files larger than ``MAX_SKILL_BYTES`` are also skipped.

    Args:
        directory: Root directory to scan with ``rglob("*.md")``.

    Returns:
        Tuple of parsed :class:`SkillDefinition` objects, sorted by name.
    """
    skills: list[SkillDefinition] = []
    for path in directory.rglob("*.md"):
        try:
            skill = _parse_skill(path)
        except SkillLoadError as exc:
            logger.warning("skipping skill — %s", exc)
            continue
        if skill is not None:
            skills.append(skill)
    return tuple(sorted(skills, key=lambda s: s.name))


def load_rules(directory: Path) -> tuple[RuleDefinition, ...]:
    """Load all rule files from *directory* recursively.

    Files that cannot be parsed are skipped with a warning.

    Args:
        directory: Root directory to scan with ``rglob("*.md")``.

    Returns:
        Tuple of parsed :class:`RuleDefinition` objects, sorted by name.
    """
    rules: list[RuleDefinition] = []
    for path in directory.rglob("*.md"):
        try:
            rule = _parse_rule(path)
        except RuleLoadError as exc:
            logger.warning("skipping rule — %s", exc)
            continue
        if rule is not None:
            rules.append(rule)
    return tuple(sorted(rules, key=lambda r: r.name))


def substitute_args(template: str, args: dict[str, str | None]) -> str:
    """Substitute ``{{name}}`` placeholders in *template* with *args* values.

    Unknown placeholders and those whose value is ``None`` resolve to an
    empty string.

    Args:
        template: Markdown template containing ``{{name}}`` placeholders.
        args: Mapping of argument name to value.

    Returns:
        Template with all placeholders resolved.
    """

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        value = args.get(key)
        return value if value is not None else ""

    return re.sub(r"\{\{([^}]+)\}\}", _replace, template)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _valid_arg_name(skill_name: str, arg_name: object) -> bool:
    """Return True if *arg_name* is a valid skill argument name.

    Argument names must be lowercase Python identifiers (letters, digits,
    underscores; must start with a letter). Hyphens are rejected: they are
    not valid Python identifiers and cannot be used as template placeholders
    or function parameter names without conversion gymnastics.
    """
    if not isinstance(arg_name, str) or not ARG_NAME_RE.match(arg_name):
        logger.warning(
            "skill %r: argument name %r is invalid (must match %s) — skipping argument",
            skill_name,
            arg_name,
            ARG_NAME_RE.pattern,
        )
        return False
    return True


def _parse_skill(path: Path) -> SkillDefinition | None:
    """Parse a single skill file; return None and log a warning on failure."""
    try:
        if path.stat().st_size > MAX_SKILL_BYTES:
            logger.warning("skill file exceeds size limit, skipping: %s", path)
            return None

        post = frontmatter.load(str(path))
        name = str(post.metadata.get("name", path.stem))

        if not SLUG_RE.match(name):
            logger.warning("invalid skill name %r, skipping: %s", name, path)
            return None

        raw_args_value = post.metadata.get("arguments", [])
        if not isinstance(raw_args_value, list):
            logger.warning(
                "skill %r: 'arguments' must be a list, got %s — ignoring arguments",
                name,
                type(raw_args_value).__name__,
            )
            raw_args_value = []
        raw_args: list[dict[str, Any]] = cast("list[dict[str, Any]]", raw_args_value)
        arguments = tuple(
            SkillArgument(
                name=str(arg["name"]),
                description=str(arg.get("description", "")),
                required=bool(arg.get("required", False)),
            )
            for arg in raw_args
            if isinstance(arg, dict) and "name" in arg and _valid_arg_name(name, arg["name"])
        )

        return SkillDefinition(
            name=name,
            title=str(post.metadata.get("title", name)),
            description=str(post.metadata.get("description", "")),
            arguments=arguments,
            body=post.content,
        )
    except Exception as exc:  # noqa: BLE001
        raise SkillLoadError(f"failed to load skill at {path}: {exc}", path=str(path)) from exc


def _parse_rule(path: Path) -> RuleDefinition | None:
    """Parse a single rule file; return None and log a warning on failure."""
    try:
        post = frontmatter.load(str(path))
        name = str(post.metadata.get("name", path.stem))

        if not SLUG_RE.match(name):
            logger.warning("invalid rule name %r, skipping: %s", name, path)
            return None

        raw_ides_value = post.metadata.get("ides", [])
        # Normalize: YAML scalar string → list; e.g. `ides: cursor` → ["cursor"]
        if isinstance(raw_ides_value, str):
            raw_ides_value = [raw_ides_value]
        raw_ides: list[str] = cast("list[str]", raw_ides_value or [])
        ides = tuple(raw_ides)

        globs_raw = post.metadata.get("globs")
        globs: str | None = str(globs_raw) if globs_raw is not None else None

        return RuleDefinition(
            name=name,
            title=str(post.metadata.get("title", name)),
            description=str(post.metadata.get("description", "")),
            always_apply=bool(post.metadata.get("alwaysApply", True)),
            globs=globs,
            ides=ides,
            body=post.content,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuleLoadError(f"failed to load rule at {path}: {exc}", path=str(path)) from exc
