"""Tests for bundled validator implementations.

Each validator's ``run()`` function is tested directly for speed. A couple
of end-to-end subprocess tests confirm the stdin/stdout contract wiring
(via ``validator_runner.run_validator``) actually works against the
bundled scripts on disk.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dmx.validator_runner import run_validator
from dmx.validators import (
    check_plan_complete,
    check_pr_ready,
    check_spec_complete,
    run_tests,
    spec_adherence,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# check_spec_complete
# ---------------------------------------------------------------------------


class TestCheckSpecComplete:
    def _write_spec(self, tmp_path: Path, content: str) -> None:
        dmx = tmp_path / ".dmx"
        dmx.mkdir(parents=True, exist_ok=True)
        (dmx / "spec.md").write_text(content, encoding="utf-8")

    def test_missing_spec_fails(self, tmp_path: Path) -> None:
        result = check_spec_complete.run(tmp_path)
        assert result["pass"] is False
        assert any(c["name"] == "spec_exists" and not c["pass"] for c in result["checks"])

    def test_complete_spec_passes(self, tmp_path: Path) -> None:
        self._write_spec(
            tmp_path,
            "Q: What?\nA: Rate limiting.\n\n"
            "## Technical Approach\nUse a token bucket per API key, stored in Redis.\n\n"
            "## Scope\n- Add rate limiter middleware\n- Add config for limits\n",
        )
        result = check_spec_complete.run(tmp_path)
        assert result["pass"] is True

    def test_tbd_answers_fail_qa_check(self, tmp_path: Path) -> None:
        self._write_spec(
            tmp_path,
            "Q: What?\nA: TBD\n\n"
            "## Technical Approach\nUse a token bucket per API key, stored in Redis.\n\n"
            "## Scope\n- Add rate limiter middleware\n",
        )
        result = check_spec_complete.run(tmp_path)
        qa_check = next(c for c in result["checks"] if c["name"] == "qa_answered")
        assert qa_check["pass"] is False


# ---------------------------------------------------------------------------
# check_plan_complete
# ---------------------------------------------------------------------------


class TestCheckPlanComplete:
    def _write_tasks(self, tmp_path: Path, content: str) -> None:
        dmx = tmp_path / ".dmx"
        dmx.mkdir(parents=True, exist_ok=True)
        (dmx / "tasks.md").write_text(content, encoding="utf-8")

    def test_missing_tasks_fails(self, tmp_path: Path) -> None:
        result = check_plan_complete.run(tmp_path)
        assert result["pass"] is False
        assert any(c["name"] == "tasks_file_exists" and not c["pass"] for c in result["checks"])

    def test_complete_plan_passes(self, tmp_path: Path) -> None:
        self._write_tasks(
            tmp_path,
            "# Tasks\n\n## Phase 1: Data Model\n"
            "- [ ] Add RateLimitEntry model to app/models/rate_limit.py\n"
            "- [ ] Add migration for rate_limit_entries table\n",
        )
        result = check_plan_complete.run(tmp_path)
        assert result["pass"] is True

    def test_no_phases_fails(self, tmp_path: Path) -> None:
        self._write_tasks(tmp_path, "# Tasks\n\n- [ ] Do the thing\n")
        result = check_plan_complete.run(tmp_path)
        phases_check = next(c for c in result["checks"] if c["name"] == "phases_defined")
        assert phases_check["pass"] is False

    def test_vague_task_fails_description_check(self, tmp_path: Path) -> None:
        self._write_tasks(tmp_path, "## Phase 1: X\n- [ ] x\n")
        result = check_plan_complete.run(tmp_path)
        desc_check = next(c for c in result["checks"] if c["name"] == "tasks_have_descriptions")
        assert desc_check["pass"] is False


# ---------------------------------------------------------------------------
# spec_adherence
# ---------------------------------------------------------------------------


class TestSpecAdherence:
    def _write_spec(self, tmp_path: Path, content: str) -> None:
        dmx = tmp_path / ".dmx"
        dmx.mkdir(parents=True, exist_ok=True)
        (dmx / "spec.md").write_text(content, encoding="utf-8")

    def test_no_scope_section_fails(self, tmp_path: Path) -> None:
        result = spec_adherence.run(tmp_path, {})
        scope_check = next(c for c in result["checks"] if c["name"] == "scope_matches_spec")
        assert scope_check["pass"] is False

    def test_scope_covered_by_skill_output_passes(self, tmp_path: Path) -> None:
        self._write_spec(tmp_path, "## Scope\n- Add rate limiter middleware\n")
        skill_outputs = {
            "implement-next-phase": "Added rate limiter middleware to app/middleware.py",
        }
        result = spec_adherence.run(tmp_path, skill_outputs)
        scope_check = next(c for c in result["checks"] if c["name"] == "scope_matches_spec")
        assert scope_check["pass"] is True

    def test_edge_case_keyword_detected(self, tmp_path: Path) -> None:
        self._write_spec(tmp_path, "## Scope\n- Add rate limiter middleware\n")
        skill_outputs = {
            "s": "Added rate limiter middleware and handled the edge case of a missing header"
        }
        result = spec_adherence.run(tmp_path, skill_outputs)
        edge_check = next(c for c in result["checks"] if c["name"] == "edge_cases_addressed")
        assert edge_check["pass"] is True

    def test_regression_language_fails_no_regressions_check(self, tmp_path: Path) -> None:
        self._write_spec(tmp_path, "## Scope\n- Add rate limiter middleware\n")
        skill_outputs = {
            "s": "Added rate limiter middleware; one existing test is now failing (regression)"
        }
        result = spec_adherence.run(tmp_path, skill_outputs)
        regression_check = next(c for c in result["checks"] if c["name"] == "no_regressions")
        assert regression_check["pass"] is False


# ---------------------------------------------------------------------------
# run_tests
# ---------------------------------------------------------------------------


class TestRunTests:
    def test_no_recognized_command_fails(self, tmp_path: Path) -> None:
        result = run_tests.run(tmp_path)
        assert result["pass"] is False
        assert result["checks"] == [{"name": "tests_pass", "pass": False}]

    def test_makefile_target_passing(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text(
            'test:\n\tpython3 -c "print(\'ok\')"\n', encoding="utf-8"
        )
        result = run_tests.run(tmp_path)
        assert result["pass"] is True
        assert result["checks"][0]["name"] == "tests_pass"
        assert result["checks"][0]["pass"] is True

    def test_makefile_target_failing(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text(
            'test:\n\tpython3 -c "import sys; sys.exit(1)"\n', encoding="utf-8"
        )
        result = run_tests.run(tmp_path)
        assert result["pass"] is False
        assert result["checks"][0]["pass"] is False

    def test_detects_pyproject_without_uv_lock(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        cmd = run_tests._detect_test_command(tmp_path)
        assert cmd == ["pytest", "-q"]

    def test_detects_pyproject_with_uv_lock(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        (tmp_path / "uv.lock").write_text("", encoding="utf-8")
        cmd = run_tests._detect_test_command(tmp_path)
        assert cmd == ["uv", "run", "pytest", "-q"]

    def test_detects_package_json_test_script(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}', encoding="utf-8")
        cmd = run_tests._detect_test_command(tmp_path)
        assert cmd == ["npm", "test", "--silent"]

    def test_no_markers_detects_nothing(self, tmp_path: Path) -> None:
        assert run_tests._detect_test_command(tmp_path) is None


# ---------------------------------------------------------------------------
# check_pr_ready
# ---------------------------------------------------------------------------


class TestCheckPrReady:
    def test_no_ticket_ref_passes_ticket_check(self, tmp_path: Path) -> None:
        result = check_pr_ready.run(tmp_path, {"ticket_ref": None})
        ticket_check = next(c for c in result["checks"] if c["name"] == "ticket_transitioned")
        assert ticket_check["pass"] is True

    def test_ticket_ref_reports_manual_confirmation(self, tmp_path: Path) -> None:
        result = check_pr_ready.run(tmp_path, {"ticket_ref": "gh-123"})
        ticket_check = next(c for c in result["checks"] if c["name"] == "ticket_transitioned")
        assert ticket_check["pass"] is True
        assert "gh-123" in ticket_check["message"]

    def test_no_memory_bank_files_fails_memory_check(self, tmp_path: Path) -> None:
        result = check_pr_ready.run(tmp_path, {"ticket_ref": None})
        memory_check = next(c for c in result["checks"] if c["name"] == "memory_updated")
        assert memory_check["pass"] is False

    def test_active_context_present_passes_memory_check(self, tmp_path: Path) -> None:
        dmx = tmp_path / ".dmx"
        dmx.mkdir(parents=True, exist_ok=True)
        (dmx / "activeContext.md").write_text("# Active Context\n", encoding="utf-8")
        result = check_pr_ready.run(tmp_path, {"ticket_ref": None})
        memory_check = next(c for c in result["checks"] if c["name"] == "memory_updated")
        assert memory_check["pass"] is True


# ---------------------------------------------------------------------------
# End-to-end subprocess contract (bundled scripts on disk)
# ---------------------------------------------------------------------------


class TestBundledValidatorSubprocessContract:
    def test_check_plan_complete_via_subprocess(self, tmp_path: Path) -> None:
        dmx = tmp_path / ".dmx"
        dmx.mkdir(parents=True, exist_ok=True)
        (dmx / "tasks.md").write_text(
            "## Phase 1: X\n- [ ] Add RateLimitEntry model\n", encoding="utf-8"
        )
        result = run_validator("check_plan_complete", tmp_path, {}, "goal", {"job_id": "J"})
        assert result["pass"] is True
        assert result["tool"] == "check_plan_complete"

    def test_check_pr_ready_via_subprocess(self, tmp_path: Path) -> None:
        result = run_validator(
            "check_pr_ready", tmp_path, {}, "goal", {"job_id": "J", "ticket_ref": None}
        )
        assert result["pass"] is False
        names = {c["name"] for c in result["checks"]}
        assert names == {"pr_exists", "ticket_transitioned", "memory_updated"}
