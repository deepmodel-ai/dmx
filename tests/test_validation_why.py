"""A failed check's message is part of the pause reply."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from dmx.loop_schema import LoopConfig
from dmx.loop_state import read_state, write_initial_state
from dmx.loop_tools import _finish_loop, loop_status_message

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_BRANCH = "feature-why"


def _validator(root: Path, name: str, body: str) -> None:
    path = root / "validators" / f"{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _loop(tool: str, *, required: bool = True, timeout_seconds: int | None = None) -> LoopConfig:
    check_name = "tests_pass" if tool == "run_tests" else "check_a"
    validator: dict[str, object] = {
        "tool": tool,
        "checks": [{"name": check_name, "required": required}],
    }
    if timeout_seconds is not None:
        validator["timeout_seconds"] = timeout_seconds
    payload: dict[str, object] = {
        "name": "validate",
        "skills": ["validate"],
        "failure_handling": "pause",
        "on_optional_failure": "warn",
        "validators": [validator],
    }
    return LoopConfig.model_validate(payload)


def _ready(root: Path, config: LoopConfig, job_id: str = "J") -> None:
    (root / ".dmx").mkdir(exist_ok=True)
    write_initial_state(root, config.name, job_id, "task-1", config.skills)


def _why(message: str) -> str:
    why = message.split("Why:\n", 1)[1]
    marker = "\n\nBefore calling"
    if marker in why:
        why = why.split(marker, 1)[0]
    assert len("Why:\n" + why) <= 2000
    return why


def _bind_branch(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    (root / ".dmx").mkdir(exist_ok=True)
    (root / ".dmx" / "spec.md").write_text(
        f"---\nticket: none\nbranch: {_BRANCH}\n---\n# Spec\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("dmx.loop_state.current_branch", lambda _root: _BRANCH)
    monkeypatch.setattr("dmx.loop_tools.current_branch", lambda _root: _BRANCH)


class TestValidationWhy:
    def test_failing_tests_name_the_test_and_status_matches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "failing_test.py").write_text(
            "def test_widget_rejects_empty_name():\n    assert False\n",
            encoding="utf-8",
        )
        (tmp_path / "Makefile").write_text(
            "test:\n\tpython -m pytest -q failing_test.py\n",
            encoding="utf-8",
        )
        config = _loop("run_tests")
        _bind_branch(monkeypatch, tmp_path)
        _ready(tmp_path, config, job_id=_BRANCH)

        message = _finish_loop(tmp_path, _BRANCH, "validate", "task-1", config, {})

        _why(message)
        assert "paused (validation failed)" in message
        assert "test_widget_rejects_empty_name" in message
        assert "Why:" in message
        assert loop_status_message(tmp_path) == message
        assert read_state(tmp_path, _BRANCH, "validate", "task-1")["finish_message"] == message

    def test_timeout_reason_reaches_the_pause_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dmx.validators import run_tests

        (tmp_path / "Makefile").write_text("test:\n\ttrue\n", encoding="utf-8")

        def _expire(*_args: object, **_kwargs: object) -> None:
            raise subprocess.TimeoutExpired(["make", "test"], 30)

        monkeypatch.setattr(run_tests, "run_with_group_timeout", _expire)
        timed_out = run_tests.run(tmp_path, timeout_seconds=60, loop_name="validate")
        # The runner executes validators in a subprocess, so replay the real
        # timeout result instead of waiting out the inner limit.
        _validator(
            tmp_path,
            "run_tests",
            f"import json, sys\nprint(json.dumps({timed_out!r}))\nsys.exit(1)\n",
        )
        config = _loop("run_tests", timeout_seconds=60)
        _ready(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "validate", "task-1", config, {})

        _why(message)
        assert "timed out after" in timed_out["message"]
        assert "timed out after" in message
        assert "timeout_seconds" in message

    def test_validator_crash_is_in_the_pause_message(self, tmp_path: Path) -> None:
        _validator(tmp_path, "crash", "import sys\nsys.exit(2)\n")
        config = _loop("crash")
        _ready(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "validate", "task-1", config, {})

        _why(message)
        assert "Validator error" in message
        assert "Why:" in message

    def test_long_output_keeps_the_header_and_the_last_line(self, tmp_path: Path) -> None:
        body_lines = [f"output line {i:02d} " + ("x" * 120) for i in range(19)]
        last = "FAILED failing_test.py::test_widget_rejects_empty_name"
        body_lines.append(last)
        body = "\n".join(body_lines)
        assert len(body_lines) == 20
        assert 2400 <= len(body) <= 2800
        detail = "Tests failed — ran `pytest -q` (exit 1)"
        _validator(
            tmp_path,
            "run_tests",
            "import json, sys\n"
            f"message = {body!r}\n"
            "print(json.dumps({"
            f'"pass": False, "message": {detail!r}, '
            '"checks": [{"name": "tests_pass", "pass": False, "message": message}]'
            "}))\n"
            "sys.exit(1)\n",
        )
        config = _loop("run_tests")
        _ready(tmp_path, config)

        why = _why(_finish_loop(tmp_path, "J", "validate", "task-1", config, {}))

        assert "`run_tests`" in why
        assert detail in why
        assert "`tests_pass`" in why
        assert last in why
        assert "…" in why
        assert body_lines[0] not in why
        kept = why.split("…\n", 1)[1].splitlines()[0]
        assert kept in body_lines

    def test_unreported_check_says_it_was_not_reported(self, tmp_path: Path) -> None:
        _validator(
            tmp_path,
            "run_tests",
            "import json, sys\n"
            "print(json.dumps({"
            '"pass": True, "message": "Tests passed", '
            '"checks": [{"name": "tests_pass", "pass": True}]'
            "}))\n"
            "sys.exit(0)\n",
        )
        config = LoopConfig.model_validate(
            {
                "name": "validate",
                "skills": ["validate"],
                "failure_handling": "pause",
                "on_optional_failure": "warn",
                "validators": [
                    {
                        "tool": "run_tests",
                        "checks": [
                            {"name": "tests_pass", "required": True},
                            {"name": "coverage_threshold", "required": False},
                        ],
                    }
                ],
            }
        )
        _ready(tmp_path, config)

        why = _why(_finish_loop(tmp_path, "J", "validate", "task-1", config, {}))

        assert "`coverage_threshold`: not reported by `run_tests`" in why
        assert "declared in the loop config" in why
        assert "`run_tests`" in why

    def test_two_checks_share_one_validator_message(self, tmp_path: Path) -> None:
        detail = "Spec adherence failed: report is stale (commit abc != HEAD def)"
        _validator(
            tmp_path,
            "spec_adherence",
            "import json, sys\n"
            "print(json.dumps({"
            f'"pass": False, "message": {detail!r}, '
            '"checks": ['
            '{"name": "scope_matches_spec", "pass": False, "message": "scope drifted"}, '
            '{"name": "no_regressions", "pass": False, "message": "a test regressed"}'
            "]}))\n"
            "sys.exit(1)\n",
        )
        config = LoopConfig.model_validate(
            {
                "name": "validate",
                "skills": ["validate"],
                "failure_handling": "pause",
                "validators": [
                    {
                        "tool": "spec_adherence",
                        "checks": [
                            {"name": "scope_matches_spec", "required": True},
                            {"name": "no_regressions", "required": True},
                        ],
                    }
                ],
            }
        )
        _ready(tmp_path, config)

        why = _why(_finish_loop(tmp_path, "J", "validate", "task-1", config, {}))

        assert why.count(detail) == 1
        assert "`scope_matches_spec`" in why
        assert "`no_regressions`" in why
        assert why.index("`no_regressions`") < why.index("`scope_matches_spec`")

    def test_crash_names_every_declared_check_once(self, tmp_path: Path) -> None:
        _validator(tmp_path, "crash", "import sys\nsys.exit(2)\n")
        config = LoopConfig.model_validate(
            {
                "name": "validate",
                "skills": ["validate"],
                "failure_handling": "pause",
                "validators": [
                    {
                        "tool": "crash",
                        "checks": [
                            {"name": "check_a", "required": True},
                            {"name": "check_b", "required": True},
                            {"name": "check_c", "required": True},
                        ],
                    }
                ],
            }
        )
        _ready(tmp_path, config)

        why = _why(_finish_loop(tmp_path, "J", "validate", "task-1", config, {}))

        assert why.count("Validator error") == 1
        assert "`check_a`" in why
        assert "`check_b`" in why
        assert "`check_c`" in why

    def test_optional_warning_includes_the_check_message(self, tmp_path: Path) -> None:
        _validator(
            tmp_path,
            "optional",
            "import json, sys\n"
            "print(json.dumps({"
            '"pass": False, "message": "optional validator failed", '
            '"checks": [{"name": "check_a", "pass": False, "message": "edge case missed"}]'
            "}))\n"
            "sys.exit(1)\n",
        )
        config = _loop("optional", required=False)
        _ready(tmp_path, config)

        message = _finish_loop(tmp_path, "J", "validate", "task-1", config, {})

        _why(message)
        assert "warning" in message
        assert "Why:" in message
        assert "edge case missed" in message
        assert "optional validator failed" in message

    def test_long_validator_traceback_stays_bounded(self, tmp_path: Path) -> None:
        stack = "\n".join(
            f'  File "custom_check.py", line {number}, in run' for number in range(1, 149)
        )
        stderr = f"Traceback (most recent call last):\n{stack}\nKeyError: 'workspace_root'\n"
        assert len(stderr.splitlines()) == 150
        _validator(
            tmp_path,
            "custom_check",
            f"import sys\nsys.stderr.write({stderr!r})\nsys.exit(2)\n",
        )
        config = LoopConfig.model_validate(
            {
                "name": "validate",
                "skills": ["validate"],
                "failure_handling": "pause",
                "validators": [
                    {"tool": "custom_check", "checks": [{"name": "check_a", "required": True}]}
                ],
            }
        )
        _ready(tmp_path, config)

        why = _why(_finish_loop(tmp_path, "J", "validate", "task-1", config, {}))

        assert "KeyError: 'workspace_root'" in why
        assert "…" in why
        assert "Validator error: Validator 'custom_check' exited with unexpected code 2:" in why

    def test_one_long_line_keeps_its_end(self, tmp_path: Path) -> None:
        line = ("x" * (3000 - len("FINAL"))) + "FINAL"
        assert len(line) == 3000
        _validator(
            tmp_path,
            "run_tests",
            "import json, sys\n"
            f"message = {line!r}\n"
            "print(json.dumps({"
            '"pass": False, "message": "Tests failed", '
            '"checks": [{"name": "tests_pass", "pass": False, "message": message}]'
            "}))\n"
            "sys.exit(1)\n",
        )
        config = _loop("run_tests")
        _ready(tmp_path, config)

        why = _why(_finish_loop(tmp_path, "J", "validate", "task-1", config, {}))

        assert "FINAL" in why
        assert "`tests_pass`" in why
        assert "…" in why
