"""A timed-out command must not leave the processes it started running."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from typing import TYPE_CHECKING

import pytest

from dmx.group_timeout import run_with_group_timeout
from dmx.validator_runner import ValidatorRunError, run_validator
from dmx.validators import run_tests

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="CI covers macOS and Linux; Windows uses taskkill",
)


def _not_running(pid: int) -> bool:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "state="],
            capture_output=True,
            text=True,
            check=False,
        )
        state = result.stdout.strip()
        if state == "" or state.startswith("Z"):
            return True
        time.sleep(0.05)
    return False


def _kill(pid: int) -> None:
    subprocess.run(["kill", "-9", str(pid)], capture_output=True, check=False)


def test_timeout_stops_a_grandchild(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    script = textwrap.dedent(
        f"""
        import subprocess, time
        from pathlib import Path
        child = subprocess.Popen(["sleep", "120"])
        Path({str(pidfile)!r}).write_text(str(child.pid))
        time.sleep(120)
        """
    )
    child_pid = 0
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_with_group_timeout([sys.executable, "-c", script], timeout=2, grace_seconds=2)
        child_pid = int(pidfile.read_text())
        assert _not_running(child_pid)
    finally:
        if child_pid:
            _kill(child_pid)


def test_sigterm_is_sent_before_sigkill(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    script = textwrap.dedent(
        f"""
        import signal, time
        from pathlib import Path
        marker = Path({str(marker)!r})
        marker.write_text("started")
        def on_term(signum, frame):
            marker.write_text("term")
        signal.signal(signal.SIGTERM, on_term)
        time.sleep(120)
        """
    )
    with pytest.raises(subprocess.TimeoutExpired):
        run_with_group_timeout([sys.executable, "-c", script], timeout=1, grace_seconds=2)
    assert marker.read_text() == "term"


def test_run_tests_timeout_stops_the_child(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    script = tmp_path / "hold.py"
    script.write_text(
        "import subprocess, time\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen(['sleep', '120'])\n"
        f"Path({str(pidfile)!r}).write_text(str(child.pid))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    executable = str(sys.executable)
    (tmp_path / "Makefile").write_text(
        f'test:\n\t"{executable}" "{script}"\n',
        encoding="utf-8",
    )
    child_pid = 0
    try:
        result = run_tests.run(tmp_path, timeout_seconds=6, loop_name="dev")
        assert result["pass"] is False
        assert "timed out after 5s" in result["message"]
        child_pid = int(pidfile.read_text())
        assert _not_running(child_pid)
    finally:
        if not child_pid and pidfile.exists():
            child_pid = int(pidfile.read_text() or "0")
        if child_pid:
            _kill(child_pid)


def test_runner_timeout_stops_the_validators_child(tmp_path: Path) -> None:
    validator = tmp_path / "validators" / "slow.py"
    validator.parent.mkdir()
    validator.write_text(
        textwrap.dedent(
            """
            import json, subprocess, sys, time
            from pathlib import Path
            contract = json.loads(sys.stdin.read() or "{}")
            root = Path(contract["loop_context"]["workspace_root"])
            child = subprocess.Popen(["sleep", "120"])
            (root / "child.pid").write_text(str(child.pid))
            time.sleep(120)
            """
        ),
        encoding="utf-8",
    )
    pidfile = tmp_path / "child.pid"
    child_pid = 0
    try:
        with pytest.raises(ValidatorRunError, match="exceeded `timeout_seconds` \\(4\\)"):
            run_validator(
                "slow",
                tmp_path,
                {},
                "goal",
                {"loop_name": "validate"},
                timeout_seconds=4,
            )
        child_pid = int(pidfile.read_text())
        assert _not_running(child_pid)
    finally:
        if not child_pid and pidfile.exists():
            child_pid = int(pidfile.read_text() or "0")
        if child_pid:
            _kill(child_pid)
