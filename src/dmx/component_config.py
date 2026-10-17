"""Component skills, validators and loops, named ``<id>/<name>`` (GH-90).

A component keeps its own ``skills/``, ``validators/`` and ``loops/`` in
``.dmx/components/<id>/``. Its skills and loops are named
``<id>/<name>``, such as ``ledger/reconcile``, and only a ticket that
includes the component may use them. A plain name never reaches a component
folder, so plain names, and the standard loops, resolve exactly as before.

``validators`` in ``component.yaml`` adds validators to named loops::

    validators:
      validate:
        - no_float_money             # every check it reports is required
        - tool: migration_safety     # the loop-YAML shape
          checks:
            - name: reversible
              required: true

They run after the loop's own validators for a ticket that includes the
component, and only add checks. Their check names carry the component
(``ledger/reversible``), so they never collide with repo-wide checks and the
pause message says whose check failed.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from dmx.components import COMPONENTS_DIR, is_valid_id, load_components
from dmx.loop_schema import LoopConfig, ValidatorCheck, ValidatorConfig
from dmx.ticket_components import ticket_components
from dmx.workspace import ide_folder

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "component_dirs",
    "component_validators",
    "expand_reported_checks",
    "parse_validator_entry",
    "scope_refusal",
    "split_component_name",
    "ticket_component_ids",
]

logger = logging.getLogger(__name__)

_PART_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_-]*$")


def split_component_name(name: str) -> tuple[str, str] | None:
    """``("ledger", "reconcile")`` for ``ledger/reconcile``; None for any other shape."""
    component_id, sep, item = name.partition("/")
    if not sep or not is_valid_id(component_id) or not _PART_RE.match(item):
        return None
    return component_id, item


def component_dirs(root: Path, component_id: str, kind: str) -> list[Path]:
    """Where a component's ``skills``, ``validators`` or ``loops`` live, in search order."""
    return [root / COMPONENTS_DIR / component_id / kind]


def ticket_component_ids(root: Path, job_id: str | None) -> list[str]:
    """The ids of the ticket's known components, sorted."""
    found = ticket_components(root, job_id, folder=ide_folder())
    return [component.id for component in found.components]


def scope_refusal(root: Path, job_id: str | None, name: str) -> str | None:
    """Why the current ticket can't use component item *name*, or None when it can."""
    split = split_component_name(name)
    if split is None:
        return None
    component_id = split[0]
    ids = ticket_component_ids(root, job_id)
    if component_id in ids:
        return None
    covered = ", ".join(ids) if ids else "none"
    return (
        f"`{name}` belongs to the `{component_id}` component, and this ticket's components "
        f"are {covered}. Add `{component_id}` to the `components` list in the ticket's "
        "spec.md to use it."
    )


def parse_validator_entry(component_id: str, index: int, raw: object) -> ValidatorConfig:
    if isinstance(raw, str) and _PART_RE.match(raw.strip()):
        return ValidatorConfig(
            tool=f"{component_id}/{raw.strip()}", owner=component_id, require_reported=True
        )
    if isinstance(raw, dict) and isinstance(raw.get("tool"), str):
        try:
            parsed = ValidatorConfig.model_validate(raw)
        except ValidationError as exc:
            logger.warning("component %s: bad validator entry %r: %s", component_id, raw, exc)
        else:
            if _PART_RE.match(parsed.tool):
                return parsed.model_copy(
                    update={
                        "tool": f"{component_id}/{parsed.tool}",
                        "owner": component_id,
                        "require_reported": not parsed.checks,
                        "checks": [
                            ValidatorCheck(name=f"{component_id}/{c.name}", required=c.required)
                            for c in parsed.checks
                        ],
                    }
                )
    # A broken entry still runs, and fails: a check must never vanish silently.
    return ValidatorConfig(
        tool=f"{component_id}/invalid-validator-entry-{index + 1}",
        owner=component_id,
        require_reported=True,
    )


def component_validators(root: Path, job_id: str | None, loop_name: str) -> list[ValidatorConfig]:
    """The validators the ticket's components add to *loop_name*, in component order."""
    components = load_components(root)
    added: list[ValidatorConfig] = []
    for component_id in ticket_component_ids(root, job_id):
        component = components.get(component_id)
        entries = (component.validators or {}).get(loop_name) if component else None
        if not isinstance(entries, list):
            continue
        added.extend(parse_validator_entry(component_id, i, raw) for i, raw in enumerate(entries))
    return added


def expand_reported_checks(config: LoopConfig, results: list[dict[str, Any]]) -> LoopConfig:
    """*config* with every check a ``require_reported`` validator reported made required."""
    by_tool = {result.get("tool"): result for result in results}
    validators: list[ValidatorConfig] = []
    for validator in config.validators:
        if validator.require_reported:
            checks = by_tool.get(validator.tool, {}).get("checks") or []
            names = [c["name"] for c in checks if isinstance(c, dict) and "name" in c]
            validator = validator.model_copy(
                update={
                    "checks": [ValidatorCheck(name=n, required=True) for n in names]
                    or [ValidatorCheck(name=validator.tool, required=True)]
                }
            )
        validators.append(validator)
    return config.model_copy(update={"validators": validators})


def component_skill_catalog(
    root: Path, component_ids: list[str]
) -> list[tuple[str, str, str, Path | None]]:
    """``(name, description, source, file)`` for each component skill, components in order."""
    # Imported here: loop_tools imports this module.
    from dmx.loop_tools import (  # noqa: PLC0415, PLC2701
        _find_skill_in_dir,
        _frontmatter_description,
        _skill_file,
    )
    from dmx.sync_runner import _skill_names  # noqa: PLC0415, PLC2701

    found: list[tuple[str, str, str, Path | None]] = []
    for component_id in component_ids:
        seen: set[str] = set()
        for skills_dir in component_dirs(root, component_id, "skills"):
            for name in _skill_names(skills_dir):
                if name in seen or not _PART_RE.match(name):
                    continue
                resolved = _find_skill_in_dir(
                    skills_dir, [name, f"dmx-{name}"], root, recursive=False
                )
                if resolved is None:
                    continue
                seen.add(name)
                found.append(
                    (
                        f"{component_id}/{name}",
                        _frontmatter_description(resolved.raw),
                        component_id,
                        _skill_file(skills_dir, name),
                    )
                )
    return found
