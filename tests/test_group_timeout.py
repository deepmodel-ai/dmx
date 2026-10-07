"""A timed-out command must not leave the processes it started running."""

from __future__ import annotations

import shlex
import subprocess
import sys
import textwrap
import time
from typing import TYPE_CHECKING

import pytest

from dmx.group_timeout import GRACE_SECONDS, run_with_group_timeout
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


def test_cleanup_fits_in_the_run_tests_margin() -> None:
    # Two grace waits in _stop_group, one in _drain, then at most 1 second.
    cleanup = 2 * GRACE_SECONDS + GRACE_SECONDS + 1
    assert cleanup < run_tests.TIMEOUT_MARGIN_SECONDS


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
            run_with_group_timeout([sys.executable, "-c", script], timeout=1, grace_seconds=0.5)
        child_pid = int(pidfile.read_text())
        assert _not_running(child_pid)
    finally:
        if child_pid:
            _kill(child_pid)


def test_child_that_ignores_sigterm_is_killed(tmp_path: Path) -> None:
    pidfile = tmp_path / "sleep.pid"
    # $$ in a ( ) subshell is the outer shell. The inner sh -c is a new
    # process, so $$ is the child that ignores TERM and HUP across exec.
    inner = f"echo $$ > {shlex.quote(str(pidfile))}; exec sleep 42"
    script = f"(trap '' TERM HUP; exec sh -c {shlex.quote(inner)}) >/dev/null 2>&1 & sleep 60"
    child_pid = 0
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_with_group_timeout(["sh", "-c", script], timeout=1, grace_seconds=0.5)
        child_pid = int(pidfile.read_text())
        assert _not_running(child_pid)
    finally:
        if not child_pid and pidfile.exists():
            child_pid = int(pidfile.read_text() or "0")
        if child_pid:
            _kill(child_pid)


def test_detached_child_holding_output_raises_timeout(tmp_path: Path) -> None:
    """A setsid child that keeps the pipe open must not raise PermissionError."""
    pidfile = tmp_path / "detached.pid"
    code = (
        "import os, time, pathlib; "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); "
        "os.setsid(); time.sleep(30)"
    )
    script = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)} & exit 0"
    child_pid = 0
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_with_group_timeout(["sh", "-c", script], timeout=1, grace_seconds=0.5)
        if pidfile.exists():
            child_pid = int(pidfile.read_text())
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
        run_with_group_timeout([sys.executable, "-c", script], timeout=1, grace_seconds=0.5)
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
        result = run_tests.run(tmp_path, timeout_seconds=2, loop_name="dev")
        assert result["pass"] is False
        assert "timed out after 1s" in result["message"]
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
        with pytest.raises(ValidatorRunError, match="exceeded `timeout_seconds` \\(1\\)"):
            run_validator(
                "slow",
                tmp_path,
                {},
                "goal",
                {"loop_name": "validate"},
                timeout_seconds=1,
            )
        child_pid = int(pidfile.read_text())
        assert _not_running(child_pid)
    finally:
        if not child_pid and pidfile.exists():
            child_pid = int(pidfile.read_text() or "0")
        if child_pid:
            _kill(child_pid)
