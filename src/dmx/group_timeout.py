"""Run a command and, on timeout, stop the processes it started.

POSIX starts the command in its own session. On timeout the group gets
SIGTERM. If any process in the group is still there after a short grace
period, the group gets SIGKILL. Windows stops the tree with ``taskkill /T /F``.

A process that starts its own session or process group, which some Docker
and ``docker compose`` test setups do, is not in this group and keeps running.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time

GRACE_SECONDS = 5.0


def run_with_group_timeout(
    cmd: list[str],
    *,
    timeout: float,
    input: str | None = None,
    cwd: str | None = None,
    grace_seconds: float = GRACE_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run *cmd* and return its output.

    Raises:
        subprocess.TimeoutExpired: The limit fired. The process group has
            already been stopped.
        FileNotFoundError: *cmd* is not on PATH.
    """
    proc = _popen(cmd, input=input, cwd=cwd)
    try:
        stdout, stderr = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_group(proc, grace_seconds)
        stdout, stderr = _drain(proc, grace_seconds)
        raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr) from None
    return subprocess.CompletedProcess(cmd, _returncode(proc), stdout or "", stderr or "")


def _popen(
    cmd: list[str],
    *,
    input: str | None,
    cwd: str | None,
) -> subprocess.Popen[str]:
    stdin: int | None = subprocess.PIPE if input is not None else subprocess.DEVNULL
    if sys.platform == "win32":
        return subprocess.Popen(
            cmd,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    return subprocess.Popen(
        cmd,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        start_new_session=True,
    )


def _stop_group(proc: subprocess.Popen[str], grace_seconds: float) -> None:
    if sys.platform == "win32":
        _taskkill(proc.pid)
        return
    # Wait for the group, not only the leader. make, npm and sh exit on
    # SIGTERM at once, which used to skip SIGKILL and leave a child that
    # ignores SIGTERM running.
    # Reap a leader that has already exited. On macOS a group that contains
    # only that zombie raises PermissionError for SIGTERM and SIGKILL.
    proc.poll()
    _signal_group(proc.pid, signal.SIGTERM)
    if _group_empties(proc, grace_seconds):
        return
    _signal_group(proc.pid, signal.SIGKILL)
    _group_empties(proc, grace_seconds)


def _group_empties(proc: subprocess.Popen[str], grace_seconds: float) -> bool:
    deadline = time.monotonic() + grace_seconds
    while True:
        proc.poll()
        if not _group_alive(proc.pid):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.1, remaining))


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS can return EPERM for signal 0 once the group leader has
        # exited, including when the group is already empty.
        return _ps_has_pgid(pgid)
    return True


def _ps_has_pgid(pgid: int) -> bool:
    result = subprocess.run(
        ["ps", "-ax", "-o", "pgid="],
        capture_output=True,
        text=True,
        check=False,
    )
    target = str(pgid)
    return any(line.strip() == target for line in result.stdout.splitlines())


def _taskkill(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/T", "/F", "/PID", str(pid)],
        capture_output=True,
        check=False,
    )


def _signal_group(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


def _force_stop(proc: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        _taskkill(proc.pid)
        return
    _signal_group(proc.pid, signal.SIGKILL)


def _drain(proc: subprocess.Popen[str], grace_seconds: float) -> tuple[str, str]:
    # The last wait is at most 1 second. With the default grace, cleanup is
    # 2 * GRACE_SECONDS + GRACE_SECONDS + 1, which stays under run_tests' margin.
    try:
        stdout, stderr = proc.communicate(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        _force_stop(proc)
        try:
            stdout, stderr = proc.communicate(timeout=min(1.0, grace_seconds))
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
            for stream in (proc.stdout, proc.stderr, proc.stdin):
                if stream is not None:
                    stream.close()
    return stdout or "", stderr or ""


def _returncode(proc: subprocess.Popen[str]) -> int:
    if proc.returncode is None:
        return 1
    return proc.returncode
