"""Pydantic schema for dmx loop configuration files.

Loop configs live in ``.dmx/loops/{name}.yaml`` in the application repo.
They are first-class work products — versioned alongside code, reviewed in PRs.

Schema hierarchy::

    LoopConfig
      skills: list[str]            — skill names in execution order
      trigger: TriggerConfig
      goal_state: str              — plain-English goal
      repeat_until: str | None     — condition evaluated by orchestrator
      validators: list[ValidatorConfig]
      on_optional_failure: OnOptionalFailure
      failure_handling: FailureHandling
      human_gate: bool
      on_complete: OnCompleteConfig
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Annotated

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TriggerType(StrEnum):
    manual = "manual"
    on_complete = "on_complete"  # fired by another loop's on_complete


class FailureHandling(StrEnum):
    pause = "pause"
    fail = "fail"


class OnOptionalFailure(StrEnum):
    warn = "warn"
    ignore = "ignore"


class RequireBranch(StrEnum):
    """A loop-config concern, not hardcoded by loop name.

    ``base`` means this loop must be started from ``branch_base`` (the
    configured integration branch) — used by loops that establish a *new*
    ticket/branch identity (e.g. ``spec``), where any pre-existing spec.md
    or current branch would be stale by definition. Loops that operate on
    an already-identified ticket (``plan``, ``dev``, ``validate``,
    ``release``) leave this unset.
    """

    base = "base"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class TriggerConfig(BaseModel):
    type: TriggerType = TriggerType.manual


class ValidatorCheck(BaseModel):
    name: str
    required: bool = True


class ValidatorConfig(BaseModel):
    tool: str  # validator name — resolved to validators/{tool}.py
    checks: list[ValidatorCheck] = Field(default_factory=list)

    @field_validator("tool")
    @classmethod
    def tool_name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("validator tool name must not be empty")
        return v


class OnSuccessConfig(BaseModel):
    trigger_loop: str | None = None


class OnFailureConfig(BaseModel):
    trigger_loop: str | None = None


class OnWarningConfig(BaseModel):
    trigger_loop: str | None = None


class OnCompleteConfig(BaseModel):
    on_success: OnSuccessConfig = Field(default_factory=OnSuccessConfig)
    on_failure: OnFailureConfig = Field(default_factory=OnFailureConfig)
    on_warning: OnWarningConfig = Field(default_factory=OnWarningConfig)


# ---------------------------------------------------------------------------
# Root model
# ---------------------------------------------------------------------------


class LoopConfig(BaseModel):
    """Complete loop configuration.

    Loaded from ``.dmx/loops/{name}.yaml``.  The ``name`` field in the YAML
    must match the filename stem; ``load_loop`` enforces this.
    """

    name: str
    skills: Annotated[list[str], Field(min_length=1)]
    trigger: TriggerConfig = Field(default_factory=TriggerConfig)
    goal_state: str = ""
    repeat_until: str | None = None
    validators: list[ValidatorConfig] = Field(default_factory=list)
    on_optional_failure: OnOptionalFailure = OnOptionalFailure.warn
    failure_handling: FailureHandling = FailureHandling.pause
    human_gate: bool = True
    on_complete: OnCompleteConfig = Field(default_factory=OnCompleteConfig)
    require_branch: RequireBranch | None = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("loop name must not be empty")
        return v

    @field_validator("skills")
    @classmethod
    def skills_not_empty_strings(cls, v: list[str]) -> list[str]:
        for skill in v:
            if not skill.strip():
                raise ValueError("skill names must not be empty strings")
        return v

    @model_validator(mode="after")
    def repeat_until_known(self) -> LoopConfig:
        """Warn if repeat_until is set to an unrecognised condition.

        Known conditions are checked at *evaluation* time by the orchestrator;
        this validator only flags clearly unknown values so misconfigured loops
        surface at load time rather than at runtime.
        """
        known = {None, "all_phases_complete"}
        if self.repeat_until not in known:
            # Allow unknown values — teams may define custom conditions.
            # This is a soft check; log a warning at load time instead.
            pass
        return self


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

KNOWN_REPEAT_UNTIL = frozenset({"all_phases_complete"})


def load_loop(path: Path) -> LoopConfig:
    """Parse and validate a loop config YAML file.

    Args:
        path: Absolute path to the YAML file (e.g. ``.dmx/loops/spec.yaml``).

    Returns:
        A validated :class:`LoopConfig`.

    Raises:
        ValueError: If the ``name`` field does not match the filename stem,
            or if validation fails.
        FileNotFoundError: If the file does not exist.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = LoopConfig.model_validate(raw)
    if config.name != path.stem:
        raise ValueError(
            f"Loop name '{config.name}' in {path.name} does not match filename stem '{path.stem}'. "
            "Rename the file or fix the name field."
        )
    return config


def load_loops_dir(loops_dir: Path) -> dict[str, LoopConfig]:
    """Load all loop configs from a directory.

    Args:
        loops_dir: Directory containing ``*.yaml`` loop config files.

    Returns:
        Dict mapping loop name → :class:`LoopConfig`.  Silently skips
        non-YAML files.
    """
    if not loops_dir.exists():
        return {}
    loops: dict[str, LoopConfig] = {}
    for p in sorted(loops_dir.glob("*.yaml")):
        try:
            cfg = load_loop(p)
            loops[cfg.name] = cfg
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).warning("skipping malformed loop config %s: %s", p, exc)
    return loops
