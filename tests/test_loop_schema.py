"""Tests for dmx.loop_schema — LoopConfig validation and loader."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from pydantic import ValidationError

from dmx.loop_schema import (
    FailureHandling,
    LoopConfig,
    OnOptionalFailure,
    RequireBranch,
    TriggerType,
    load_loop,
    load_loops_dir,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_loop(tmp_path: Path, name: str, data: dict) -> Path:
    p = tmp_path / f"{name}.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


MINIMAL_LOOP = {
    "name": "spec",
    "skills": ["dmx-create-ticket"],
}

FULL_LOOP = {
    "name": "dev",
    "skills": ["dmx-implement-next-phase", "dmx-commit"],
    "trigger": {"type": "manual"},
    "goal_state": "All phases done",
    "repeat_until": "all_phases_complete",
    "validators": [
        {
            "tool": "run_tests",
            "checks": [
                {"name": "tests_pass", "required": True},
                {"name": "coverage_threshold", "required": False},
            ],
        }
    ],
    "on_optional_failure": "warn",
    "failure_handling": "pause",
    "human_gate": True,
    "on_complete": {
        "on_success": {"trigger_loop": "validate"},
        "on_failure": {"trigger_loop": None},
        "on_warning": {"trigger_loop": None},
    },
}


# ---------------------------------------------------------------------------
# LoopConfig validation
# ---------------------------------------------------------------------------


class TestLoopConfigValidation:
    def test_minimal_config_parses(self) -> None:
        cfg = LoopConfig.model_validate(MINIMAL_LOOP)
        assert cfg.name == "spec"
        assert cfg.skills == ["dmx-create-ticket"]
        assert cfg.human_gate is True  # default
        assert cfg.failure_handling == FailureHandling.pause  # default

    def test_full_config_parses(self) -> None:
        cfg = LoopConfig.model_validate(FULL_LOOP)
        assert cfg.name == "dev"
        assert cfg.repeat_until == "all_phases_complete"
        assert len(cfg.validators) == 1
        assert cfg.validators[0].tool == "run_tests"
        assert cfg.validators[0].checks[0].name == "tests_pass"
        assert cfg.validators[0].checks[0].required is True
        assert cfg.validators[0].checks[1].required is False
        assert cfg.on_complete.on_success.trigger_loop == "validate"
        assert cfg.on_complete.on_failure.trigger_loop is None

    def test_empty_skills_raises(self) -> None:
        with pytest.raises(ValidationError):
            LoopConfig.model_validate({"name": "x", "skills": []})

    def test_missing_skills_raises(self) -> None:
        with pytest.raises(ValidationError):
            LoopConfig.model_validate({"name": "x"})

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValidationError):
            LoopConfig.model_validate({"name": "", "skills": ["s"]})

    def test_empty_skill_name_raises(self) -> None:
        with pytest.raises(ValidationError):
            LoopConfig.model_validate({"name": "x", "skills": [""]})

    def test_human_gate_defaults_true(self) -> None:
        cfg = LoopConfig.model_validate(MINIMAL_LOOP)
        assert cfg.human_gate is True

    def test_human_gate_false(self) -> None:
        cfg = LoopConfig.model_validate({**MINIMAL_LOOP, "human_gate": False})
        assert cfg.human_gate is False

    def test_failure_handling_fail(self) -> None:
        cfg = LoopConfig.model_validate({**MINIMAL_LOOP, "failure_handling": "fail"})
        assert cfg.failure_handling == FailureHandling.fail

    def test_on_optional_failure_ignore(self) -> None:
        cfg = LoopConfig.model_validate({**MINIMAL_LOOP, "on_optional_failure": "ignore"})
        assert cfg.on_optional_failure == OnOptionalFailure.ignore

    def test_trigger_type_on_complete(self) -> None:
        cfg = LoopConfig.model_validate({**MINIMAL_LOOP, "trigger": {"type": "on_complete"}})
        assert cfg.trigger.type == TriggerType.on_complete

    def test_unknown_failure_handling_raises(self) -> None:
        with pytest.raises(ValidationError):
            LoopConfig.model_validate({**MINIMAL_LOOP, "failure_handling": "retry"})

    def test_validator_empty_tool_raises(self) -> None:
        with pytest.raises(ValidationError):
            LoopConfig.model_validate({**MINIMAL_LOOP, "validators": [{"tool": "", "checks": []}]})

    def test_repeat_until_none(self) -> None:
        cfg = LoopConfig.model_validate(MINIMAL_LOOP)
        assert cfg.repeat_until is None

    def test_require_branch_defaults_none(self) -> None:
        cfg = LoopConfig.model_validate(MINIMAL_LOOP)
        assert cfg.require_branch is None

    def test_require_branch_base(self) -> None:
        cfg = LoopConfig.model_validate({**MINIMAL_LOOP, "require_branch": "base"})
        assert cfg.require_branch == RequireBranch.base

    def test_require_branch_unknown_value_raises(self) -> None:
        with pytest.raises(ValidationError):
            LoopConfig.model_validate({**MINIMAL_LOOP, "require_branch": "production"})


# ---------------------------------------------------------------------------
# load_loop
# ---------------------------------------------------------------------------


class TestLoadLoop:
    def test_load_minimal(self, tmp_path: Path) -> None:
        p = _write_loop(tmp_path, "spec", MINIMAL_LOOP)
        cfg = load_loop(p)
        assert cfg.name == "spec"

    def test_load_full(self, tmp_path: Path) -> None:
        p = _write_loop(tmp_path, "dev", FULL_LOOP)
        cfg = load_loop(p)
        assert cfg.repeat_until == "all_phases_complete"

    def test_name_must_match_filename_stem(self, tmp_path: Path) -> None:
        p = tmp_path / "spec.yaml"
        p.write_text(yaml.dump({**MINIMAL_LOOP, "name": "other"}), encoding="utf-8")
        with pytest.raises(ValueError, match="does not match filename stem"):
            load_loop(p)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_loop(tmp_path / "nonexistent.yaml")


# ---------------------------------------------------------------------------
# load_loops_dir
# ---------------------------------------------------------------------------


class TestBundledSpecLoopRequiresBaseBranch:
    """Regression guard for GH-9: the bundled spec loop must declare
    require_branch — it's the loop that establishes a brand new ticket
    identity, so it must never start from a stale/wrong branch context."""

    def test_bundled_spec_yaml_requires_base_branch(self) -> None:
        from pathlib import Path

        spec_path = Path(__file__).parent.parent / "src" / "dmx" / "loops" / "spec.yaml"
        cfg = load_loop(spec_path)
        assert cfg.require_branch == RequireBranch.base


class TestLoadLoopsDir:
    def test_loads_multiple_files(self, tmp_path: Path) -> None:
        _write_loop(tmp_path, "spec", MINIMAL_LOOP)
        _write_loop(tmp_path, "dev", FULL_LOOP)
        loops = load_loops_dir(tmp_path)
        assert "spec" in loops
        assert "dev" in loops

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        result = load_loops_dir(tmp_path / "nodir")
        assert result == {}

    def test_skips_non_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("not yaml", encoding="utf-8")
        _write_loop(tmp_path, "spec", MINIMAL_LOOP)
        loops = load_loops_dir(tmp_path)
        assert list(loops.keys()) == ["spec"]

    def test_skips_malformed_yaml_gracefully(self, tmp_path: Path) -> None:
        (tmp_path / "broken.yaml").write_text("name: broken\n# no skills", encoding="utf-8")
        _write_loop(tmp_path, "spec", MINIMAL_LOOP)
        loops = load_loops_dir(tmp_path)
        assert "spec" in loops
        assert "broken" not in loops
