"""Tests for bundled validator implementations.

Each validator's ``run()`` function is tested directly for speed. A couple
of end-to-end subprocess tests confirm the stdin/stdout contract wiring
(via ``validator_runner.run_validator``) actually works against the
bundled scripts on disk.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
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
    import pytest

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

    def test_dmx_create_ticket_numbered_answer_template_passes(self, tmp_path: Path) -> None:
        """The actual template dmx-create-ticket.md Step 8 writes — numbered
        questions with an indented 'Answer:' line, not 'Q:'/'A:'."""
        self._write_spec(
            tmp_path,
            "## Technical Approach\nUse a token bucket per API key, stored in Redis.\n\n"
            "## Scope\n- Add rate limiter middleware\n\n"
            "## Questions\n"
            "1. Should limits be per-key or per-owner?\n"
            "   Answer: Per-owner, matching how tiers are assigned.\n\n"
            "2. What happens on a burst at the boundary?\n"
            "   Answer: Reject with 429 and a Retry-After header.\n",
        )
        result = check_spec_complete.run(tmp_path)
        qa_check = next(c for c in result["checks"] if c["name"] == "qa_answered")
        assert qa_check["pass"] is True

    def test_unfilled_answer_template_fails_qa_check(self, tmp_path: Path) -> None:
        """A bare 'Answer:' with nothing after it — the un-filled-in
        template state — must still count as unanswered."""
        self._write_spec(
            tmp_path,
            "## Technical Approach\nUse a token bucket per API key, stored in Redis.\n\n"
            "## Scope\n- Add rate limiter middleware\n\n"
            "## Questions\n1. Should limits be per-key or per-owner?\n   Answer:\n",
        )
        result = check_spec_complete.run(tmp_path)
        qa_check = next(c for c in result["checks"] if c["name"] == "qa_answered")
        assert qa_check["pass"] is False

    def test_qa_answered_matches_dmx_create_ticket_live_template(self) -> None:
        """Drift guard, not a fixed-format test.

        Extracts the real ``## Questions`` block straight from
        dmx-create-ticket.md at test time, fills in its placeholders, and
        runs it through the actual ``_qa_answered`` check. If a future edit
        to that skill's template changes its answer format, this test
        fails immediately — the same class of bug this file's other tests
        were written to catch after it shipped silently once already.
        """
        skill_path = (
            Path(__file__).parent.parent
            / "src"
            / "dmx"
            / "skills"
            / "workflow"
            / "1-triage"
            / "dmx-create-ticket.md"
        )
        skill_content = skill_path.read_text(encoding="utf-8")

        match = re.search(
            r"^## Questions\n(.*?)(?=^```)",
            skill_content,
            re.MULTILINE | re.DOTALL,
        )
        assert match, "dmx-create-ticket.md '## Questions' template not found — did it move?"

        template_block = match.group(1)
        assert "Answer:" in template_block, (
            "dmx-create-ticket.md no longer scaffolds an 'Answer:' line — "
            "update this test and _qa_answered together"
        )

        filled_block = template_block.replace("{Question}", "Sample clarifying question?")
        filled_block = re.sub(
            r"Answer:\s*$", "Answer: Sample substantive answer.", filled_block, flags=re.MULTILINE
        )

        passed, message = check_spec_complete._qa_answered(filled_block)
        assert passed is True, message


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
    """Grades ``.dmx/jobs/{job_id}/validation-report.json``, not skill_outputs prose."""

    def _write_report(self, tmp_path: Path, job_id: str, report: dict[str, object]) -> None:
        job_dir = tmp_path / ".dmx" / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "validation-report.json").write_text(json.dumps(report), encoding="utf-8")

    def test_missing_report_fails_every_check(self, tmp_path: Path) -> None:
        result = spec_adherence.run(tmp_path, "main")
        assert result["pass"] is False
        assert {c["name"] for c in result["checks"]} == {
            "scope_matches_spec",
            "edge_cases_addressed",
            "no_regressions",
        }
        assert all(not c["pass"] for c in result["checks"])

    def test_malformed_json_fails_every_check(self, tmp_path: Path) -> None:
        job_dir = tmp_path / ".dmx" / "jobs" / "main"
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "validation-report.json").write_text("not json", encoding="utf-8")
        result = spec_adherence.run(tmp_path, "main")
        assert result["pass"] is False

    def test_scope_items_covered_passes(self, tmp_path: Path) -> None:
        self._write_report(
            tmp_path,
            "main",
            {
                "scope_items": [
                    {
                        "item": "Add rate limiter middleware",
                        "verdict": "covered",
                        "evidence": "app/middleware.py:12",
                    }
                ],
                "scope_creep": [],
                "regressions": [],
                "edge_cases": [],
            },
        )
        result = spec_adherence.run(tmp_path, "main")
        scope_check = next(c for c in result["checks"] if c["name"] == "scope_matches_spec")
        assert scope_check["pass"] is True

    def test_missing_scope_item_fails(self, tmp_path: Path) -> None:
        self._write_report(
            tmp_path,
            "main",
            {
                "scope_items": [
                    {"item": "Add rate limiter middleware", "verdict": "missing", "evidence": ""}
                ],
                "scope_creep": [],
                "regressions": [],
                "edge_cases": [],
            },
        )
        result = spec_adherence.run(tmp_path, "main")
        scope_check = next(c for c in result["checks"] if c["name"] == "scope_matches_spec")
        assert scope_check["pass"] is False

    def test_partial_scope_item_passes_but_is_noted(self, tmp_path: Path) -> None:
        self._write_report(
            tmp_path,
            "main",
            {
                "scope_items": [
                    {
                        "item": "Add rate limiter middleware",
                        "verdict": "partial",
                        "evidence": "app/middleware.py:12",
                    }
                ],
                "scope_creep": [],
                "regressions": [],
                "edge_cases": [],
            },
        )
        result = spec_adherence.run(tmp_path, "main")
        scope_check = next(c for c in result["checks"] if c["name"] == "scope_matches_spec")
        assert scope_check["pass"] is True
        assert "Add rate limiter middleware" in scope_check["message"]

    def test_scope_creep_fails(self, tmp_path: Path) -> None:
        self._write_report(
            tmp_path,
            "main",
            {
                "scope_items": [
                    {
                        "item": "Add rate limiter middleware",
                        "verdict": "covered",
                        "evidence": "app/middleware.py:12",
                    }
                ],
                "scope_creep": [
                    {"description": "Refactored unrelated auth module", "evidence": "app/auth.py:1"}
                ],
                "regressions": [],
                "edge_cases": [],
            },
        )
        result = spec_adherence.run(tmp_path, "main")
        scope_check = next(c for c in result["checks"] if c["name"] == "scope_matches_spec")
        assert scope_check["pass"] is False

    def test_regression_found_fails_no_regressions_check(self, tmp_path: Path) -> None:
        self._write_report(
            tmp_path,
            "main",
            {
                "scope_items": [],
                "scope_creep": [],
                "regressions": [
                    {
                        "description": "Removed input validation used elsewhere",
                        "evidence": "app/api.py:40",
                    }
                ],
                "edge_cases": [],
            },
        )
        result = spec_adherence.run(tmp_path, "main")
        regression_check = next(c for c in result["checks"] if c["name"] == "no_regressions")
        assert regression_check["pass"] is False

    def test_description_mentioning_regression_word_does_not_fail(self, tmp_path: Path) -> None:
        """The whole point of the fix: prose mentioning 'regression' isn't graded at all."""
        self._write_report(
            tmp_path,
            "main",
            {
                "scope_items": [
                    {
                        "item": "Fix login regression",
                        "verdict": "covered",
                        "evidence": "app/login.py:5",
                    }
                ],
                "scope_creep": [],
                "regressions": [],
                "edge_cases": [],
            },
        )
        result = spec_adherence.run(tmp_path, "main")
        regression_check = next(c for c in result["checks"] if c["name"] == "no_regressions")
        assert regression_check["pass"] is True

    def test_unaddressed_edge_case_fails(self, tmp_path: Path) -> None:
        self._write_report(
            tmp_path,
            "main",
            {
                "scope_items": [],
                "scope_creep": [],
                "regressions": [],
                "edge_cases": [{"description": "Empty input", "addressed": False, "evidence": ""}],
            },
        )
        result = spec_adherence.run(tmp_path, "main")
        edge_check = next(c for c in result["checks"] if c["name"] == "edge_cases_addressed")
        assert edge_check["pass"] is False

    def test_stale_report_fails_with_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(spec_adherence, "_current_head", lambda _root: "f" * 40)

        self._write_report(
            tmp_path,
            "main",
            {
                "commit": "0" * 40,
                "scope_items": [],
                "scope_creep": [],
                "regressions": [],
                "edge_cases": [],
            },
        )
        result = spec_adherence.run(tmp_path, "main")
        assert result["pass"] is False
        assert "stale" in result["message"].lower()


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
            "test:\n\tpython3 -c \"print('ok')\"\n", encoding="utf-8"
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

    def test_uncommitted_dmx_changes_fail_memory_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GH-15: a skill that edits .dmx/ without committing (e.g.
        update-memory chained after create-pr already opened the PR) must
        not be reported as "memory updated" — the edits aren't actually in
        the PR until they're committed."""
        dmx = tmp_path / ".dmx"
        dmx.mkdir(parents=True, exist_ok=True)
        (dmx / "activeContext.md").write_text("# Active Context\n", encoding="utf-8")
        monkeypatch.setattr(check_pr_ready, "_dirty_dmx_files", lambda _root: ["activeContext.md"])

        result = check_pr_ready.run(tmp_path, {"ticket_ref": None})

        memory_check = next(c for c in result["checks"] if c["name"] == "memory_updated")
        assert memory_check["pass"] is False
        assert "uncommitted" in memory_check["message"].lower()
        assert "activeContext.md" in memory_check["message"]

    def test_clean_tree_does_not_trigger_dirty_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dmx = tmp_path / ".dmx"
        dmx.mkdir(parents=True, exist_ok=True)
        (dmx / "activeContext.md").write_text("# Active Context\n", encoding="utf-8")
        monkeypatch.setattr(check_pr_ready, "_dirty_dmx_files", lambda _root: [])

        result = check_pr_ready.run(tmp_path, {"ticket_ref": None})

        memory_check = next(c for c in result["checks"] if c["name"] == "memory_updated")
        assert memory_check["pass"] is True

    def test_dirty_dmx_files_returns_empty_outside_a_git_repo(self, tmp_path: Path) -> None:
        """The real (non-monkeypatched) implementation must not raise or
        misreport when run outside a git repo — same fail-open behavior as
        the pre-existing HEAD~1 diff check."""
        assert check_pr_ready._dirty_dmx_files(tmp_path) == []

    def _run_git(self, tmp_path: Path, *args: str) -> None:
        import subprocess

        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    def test_dirty_dmx_files_detects_real_uncommitted_changes(self, tmp_path: Path) -> None:
        """End-to-end against a real git repo — an uncommitted .dmx/ edit is
        detected regardless of whether it's staged."""
        self._run_git(tmp_path, "init", "-q")
        self._run_git(tmp_path, "config", "user.email", "test@example.com")
        self._run_git(tmp_path, "config", "user.name", "Test")
        dmx = tmp_path / ".dmx"
        dmx.mkdir()
        (dmx / "activeContext.md").write_text("initial\n", encoding="utf-8")
        self._run_git(tmp_path, "add", ".")
        self._run_git(tmp_path, "commit", "-q", "-m", "initial commit")

        assert check_pr_ready._dirty_dmx_files(tmp_path) == []

        # Unstaged edit.
        (dmx / "activeContext.md").write_text("edited without committing\n", encoding="utf-8")
        assert check_pr_ready._dirty_dmx_files(tmp_path) == [".dmx/activeContext.md"]

        # Staged edit.
        self._run_git(tmp_path, "add", ".")
        assert check_pr_ready._dirty_dmx_files(tmp_path) == [".dmx/activeContext.md"]

        result = check_pr_ready.run(tmp_path, {"ticket_ref": None})
        memory_check = next(c for c in result["checks"] if c["name"] == "memory_updated")
        assert memory_check["pass"] is False


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
