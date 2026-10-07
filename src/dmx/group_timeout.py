"""Run a command and, on timeout, stop the processes it started.

POSIX starts the command in its own session and signals that process group:
SIGTERM, then SIGKILL after a short grace period. Windows stops the tree
with ``taskkill /T /F``.

A process that starts its own session or process group, which some Docker
and ``docker compose`` test setups do, is not in this group and keeps running.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys

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
        stdout, stderr = _drain(proc)
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
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
        return
    _signal_group(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        _signal_group(proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=grace_seconds)


def _signal_group(pid: int, sig: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, sig)


def _drain(proc: subprocess.Popen[str]) -> tuple[str, str]:
    try:
        stdout, stderr = proc.communicate(timeout=GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_group(proc.pid, signal.SIGKILL)
        try:
            stdout, stderr = proc.communicate(timeout=1)
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
