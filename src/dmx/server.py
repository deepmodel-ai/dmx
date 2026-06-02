"""FastMCP application factory for dmx."""

from __future__ import annotations

import importlib.resources as pkg
import inspect
import logging
import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from dmx.catalog import SkillDefinition, load_rules, load_skills, substitute_args
from dmx.tools import register_tools

__all__ = ["create_app", "resolve_dirs", "watch_catalog"]

logger = logging.getLogger(__name__)

SERVER_INSTRUCTIONS = (
    "Run /dmx-init to set up a new project. "
    "This writes the always-apply rules and initializes the .dmx/ memory bank. "
    "All /dmx-* skills are available as slash commands."
)


def _bundled_skills_dir() -> Path:
    """Return the path to the bundled skills directory."""
    return Path(str(pkg.files("dmx") / "skills"))


def _bundled_rules_dir() -> Path:
    """Return the path to the bundled rules directory."""
    return Path(str(pkg.files("dmx") / "rules"))


def resolve_dirs(
    skills_dir: Path | None,
    rules_dir: Path | None,
) -> tuple[Path, Path]:
    """Resolve the effective skills and rules directories.

    Resolution order (highest precedence first):

    1. Explicit *skills_dir* / *rules_dir* arguments.
    2. ``DMX_SKILLS_DIR`` / ``DMX_RULES_DIR`` environment variables.
    3. Bundled package data.

    This helper is intentionally separated from ``create_app()`` so the CLI
    watch path, ``list-skills``, and any other callers apply identical
    resolution logic without duplicating env-var handling.

    Args:
        skills_dir: Explicit override from a CLI flag or API call, or ``None``.
        rules_dir: Explicit override from a CLI flag or API call, or ``None``.

    Returns:
        A ``(resolved_skills_dir, resolved_rules_dir)`` tuple of absolute
        :class:`Path` objects.
    """
    if skills_dir is None and (s := os.environ.get("DMX_SKILLS_DIR")):
        skills_dir = Path(s)
    if rules_dir is None and (r := os.environ.get("DMX_RULES_DIR")):
        rules_dir = Path(r)
    return (skills_dir or _bundled_skills_dir(), rules_dir or _bundled_rules_dir())


def create_app(
    skills_dir: Path | None = None,
    rules_dir: Path | None = None,
) -> FastMCP:
    """Create and return a configured FastMCP application.

    Internal extension API — used by the Deepmodel commercial layer only.
    Not a public plugin API. Not documented externally. Stability is
    maintained for internal use; no compatibility guarantees to third parties.

    Directory resolution is delegated to :func:`resolve_dirs`; see its
    docstring for the full precedence order (explicit arg → env var → bundled).

    Args:
        skills_dir: Explicit skills directory override, or ``None`` to apply
            ``DMX_SKILLS_DIR`` / bundled fallback.
        rules_dir: Explicit rules directory override, or ``None`` to apply
            ``DMX_RULES_DIR`` / bundled fallback.

    Returns:
        A :class:`FastMCP` application with all skills registered as MCP
        prompts and both tools registered.
    """
    resolved_skills_dir, resolved_rules_dir = resolve_dirs(skills_dir, rules_dir)

    app = FastMCP("dmx", instructions=SERVER_INSTRUCTIONS)

    skills = load_skills(resolved_skills_dir)
    rules = load_rules(resolved_rules_dir)

    logger.info(
        "loaded %d skill(s) from %s", len(skills), resolved_skills_dir
    )
    logger.info(
        "loaded %d rule(s) from %s", len(rules), resolved_rules_dir
    )

    for skill in skills:
        _register_skill(app, skill)

    register_tools(app, rules)
    return app


async def watch_catalog(
    app: FastMCP,
    skills_dir: Path,
    rules_dir: Path,
) -> None:
    """Watch *skills_dir* and *rules_dir* for changes and hot-reload the catalog.

    Internal extension API — used by the Deepmodel commercial CLI only.
    Requires the ``watch`` optional dependency (``watchfiles``).

    Args:
        app: The running :class:`FastMCP` application to update.
        skills_dir: Directory to watch for skill file changes.
        rules_dir: Directory to watch for rule file changes.

    Raises:
        ImportError: If ``watchfiles`` is not installed.
    """
    try:
        from watchfiles import awatch
    except ImportError as exc:
        raise ImportError(
            "Hot-reload requires the 'watch' extra: "
            "pip install 'deepmodel-dmx[watch]'"
        ) from exc

    logger.info("watching %s and %s for changes", skills_dir, rules_dir)

    async for _ in awatch(skills_dir, rules_dir):
        new_skills = load_skills(skills_dir)
        new_rules = load_rules(rules_dir)

        if not new_skills:
            logger.warning(
                "reload would result in zero skills — skipping to preserve "
                "last valid catalog"
            )
            continue

        logger.info(
            "catalog changed: reloading %d skill(s), %d rule(s)",
            len(new_skills),
            len(new_rules),
        )

        # Re-register skills; tools are re-registered with updated rules.
        # NOTE: FastMCP accumulates registrations — duplicates are possible on
        # repeated reloads. A proper deregister/replace API is not yet exposed
        # by fastmcp; tracked for resolution in Phase 3 / commercial hot-reload.
        for skill in new_skills:
            _register_skill(app, skill)

        register_tools(app, new_rules)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _register_skill(app: FastMCP, skill: SkillDefinition) -> None:
    """Register *skill* as an MCP prompt on *app*.

    MCP prompts are the protocol mechanism that surfaces skills as slash
    commands in IDEs (e.g. ``/dmx-init``).

    FastMCP rejects handlers with ``**kwargs`` at registration time (it uses
    ``inspect.signature`` to build the MCP argument schema).  We work around
    this by building a handler that truly has ``**kwargs`` at runtime but
    exposes an explicit ``inspect.Signature`` via ``__signature__``.
    ``inspect.signature()`` respects ``__signature__``, so FastMCP sees the
    explicit parameter list while the function still collects kwargs at call
    time.

    Argument names with hyphens (valid in MCP/YAML but not in Python
    identifiers) are mapped to underscores in the Python signature; the
    template placeholders must use the same underscore form.

    Args:
        app: The FastMCP application to register the prompt on.
        skill: The skill to register.
    """
    description = skill.description or skill.title

    if skill.arguments:
        handler = _build_skill_handler(skill)
        app.prompt(name=skill.name, description=description)(handler)
    else:
        async def _handler_no_args() -> str:
            return skill.body

        _handler_no_args.__name__ = skill.name
        app.prompt(name=skill.name, description=description)(_handler_no_args)


def _build_skill_handler(skill: SkillDefinition) -> Any:
    """Return an async callable with an explicit signature for a parameterized skill.

    FastMCP's ``FunctionPrompt.from_function`` reads both ``inspect.signature``
    (for the ``**kwargs`` check and parameter names) and ``__annotations__``
    directly (via ``without_injected_parameters`` → ``getattr(fn, '__annotations__')``)
    to build the pydantic TypeAdapter.  Both must be set consistently.

    Argument names are validated as Python identifiers at parse time
    (``ARG_NAME_RE`` in ``catalog.py``), so no sanitization is needed here.

    Args:
        skill: The skill whose arguments should become function parameters.

    Returns:
        An async callable whose ``__signature__`` and ``__annotations__`` match
        ``skill.arguments``.
    """
    body = skill.body
    arg_names = [arg.name for arg in skill.arguments]

    async def _handler(**kwargs: str | None) -> str:
        return substitute_args(body, {n: kwargs.get(n) for n in arg_names})

    params = [
        inspect.Parameter(
            name=arg.name,
            kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=inspect.Parameter.empty if arg.required else None,
            annotation=str | None,
        )
        for arg in skill.arguments
    ]
    # Both __signature__ and __annotations__ must agree: FastMCP reads
    # __annotations__ directly (not via get_type_hints) when building the
    # pydantic TypeAdapter.
    annotations: dict[str, Any] = {arg.name: str | None for arg in skill.arguments}
    annotations["return"] = str
    _handler.__signature__ = inspect.Signature(params, return_annotation=str)  # type: ignore[attr-defined]
    _handler.__annotations__ = annotations
    _handler.__name__ = skill.name
    return _handler
