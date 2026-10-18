"""Shared test fixtures for dmx."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# The sample repos under fixtures/ carry their own tests, which belong to
# them, not to dmx.
collect_ignore_glob = ["fixtures/*"]

REFERENCE_MONOREPO = Path(__file__).parent / "fixtures" / "reference-monorepo"
PAYMENTS_TEAM_SOURCE = Path(__file__).parent / "fixtures" / "payments-team-source"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _commit_all(root: Path, message: str) -> None:
    for args in (
        ("config", "user.email", "test@example.com"),
        ("config", "user.name", "Test"),
        ("add", "-A"),
        ("commit", "-q", "-m", message),
    ):
        _git(root, *args)


@pytest.fixture(autouse=True)
def _isolated_source_cache(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep shared-source git mirrors out of the developer's ``~/.cache/dmx``."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.getbasetemp() / "xdg-cache"))


@pytest.fixture(autouse=True)
def _clear_one_time_notes() -> None:
    """Start each test without a one-time note left by an earlier one.

    Tests that call loop internals directly (``_start_loop``) set these
    notes without returning a tool reply that clears them. The server runs
    each tool call in its own context, so they never leak there.
    """
    from dmx.loop_tools import (
        _LEGACY_MOVE_NOTE,
        _MISPLACED_FILES_NOTE,
        _TICKET_FILES_MOVE_NOTE,
    )

    _LEGACY_MOVE_NOTE.set(None)
    _MISPLACED_FILES_NOTE.set(None)
    _TICKET_FILES_MOVE_NOTE.set(None)


@pytest.fixture()
def tmp_skills_dir(tmp_path: Path) -> Path:
    """Return a temp directory pre-populated with valid and invalid skill files."""
    skills = tmp_path / "skills"
    skills.mkdir()

    # Valid skill with arguments
    (skills / "dmx-commit.md").write_text(
        "---\n"
        "name: dmx-commit\n"
        "title: Commit Changes\n"
        "description: Stage and commit with conventional format.\n"
        "arguments:\n"
        "  - name: message\n"
        "    description: Override generated summary.\n"
        "    required: false\n"
        "---\n\n"
        "Run `git add .` then commit with message: {{message}}\n",
        encoding="utf-8",
    )

    # Valid skill without arguments
    (skills / "dmx-status.md").write_text(
        "---\n"
        "name: dmx-status\n"
        "title: Development Status\n"
        "description: Show open tickets and PRs.\n"
        "---\n\n"
        "Show all in-progress tickets and open PRs.\n",
        encoding="utf-8",
    )

    # Valid skill in a sub-directory (recursive scan test)
    sub = skills / "workflow"
    sub.mkdir()
    (sub / "dmx-plan.md").write_text(
        "---\n"
        "name: dmx-plan\n"
        "title: Plan\n"
        "description: Generate phased task list.\n"
        "---\n\n"
        "Generate tasks.md for the active ticket.\n",
        encoding="utf-8",
    )

    return skills


@pytest.fixture()
def tmp_rules_dir(tmp_path: Path) -> Path:
    """Return a temp directory pre-populated with a valid rule file."""
    rules = tmp_path / "rules"
    rules.mkdir()

    (rules / "system-prompt.md").write_text(
        "---\n"
        "name: system-prompt\n"
        "title: AI SDLC — Engineering Persona\n"
        "description: Always-apply engineering persona and memory bank instructions.\n"
        "alwaysApply: true\n"
        "---\n\n"
        "# Persona\n\nI am a staff-level pair programmer.\n",
        encoding="utf-8",
    )

    return rules


@pytest.fixture(scope="session")
def payments_team_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The payments component's shared source as a git repo, built once per session.

    One path for the whole session, so every reference monorepo locks the same
    source and the shared-source git mirror is fetched once.
    """
    root = tmp_path_factory.mktemp("sources") / "payments-team"
    shutil.copytree(PAYMENTS_TEAM_SOURCE, root)
    _git(root, "init", "-q", "-b", "main")
    _commit_all(root, "payments team source")
    return root


@pytest.fixture()
def reference_monorepo(tmp_path: Path, payments_team_source: Path) -> Path:
    """The reference monorepo (#81) in a fresh git repo, committed on ``main``.

    The payments component's own shared source is declared and locked in its
    folder, as ``/dmx/update-shared-sources payments`` would leave it.
    """
    from dmx.sync_runner import update_shared_sources

    root = tmp_path / "mono"
    shutil.copytree(REFERENCE_MONOREPO, root)
    (root / ".dmx" / "components" / "payments" / "shared-sources.yaml").write_text(
        f"shared_sources:\n  - name: payments-team\n"
        f"    source: git::{payments_team_source}?ref=main\n",
        encoding="utf-8",
    )
    _git(root, "init", "-q", "-b", "main")
    report = update_shared_sources(root, component="payments")
    assert not report.failures, report.failures
    _commit_all(root, "reference monorepo")
    return root
