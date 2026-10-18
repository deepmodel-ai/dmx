"""GH-90: a component's own shared sources, locked in its folder."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client

from dmx.component_config import component_dirs
from dmx.server import create_app
from dmx.shared_sources import lock_path, read_lock, read_shared_sources
from dmx.source_cache import cached_tree
from dmx.sync_runner import fetch_shared_sources, update_shared_sources

if TYPE_CHECKING:
    from pathlib import Path

_BRANCH = "feature-gh-7"
_SKILL = "---\ndescription: {text}\n---\n{text}\n"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _source_repo(root: Path, files: dict[str, str]) -> str:
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    return _git(root, "rev-parse", "HEAD")


def _declare(folder: Path, sources: dict[str, str]) -> None:
    lines = "".join(f"  - name: {name}\n    source: {addr}\n" for name, addr in sources.items())
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "shared-sources.yaml").write_text(f"shared_sources:\n{lines}", encoding="utf-8")


def _ticket(root: Path, components: str) -> None:
    _git(root, "checkout", "-q", "-b", _BRANCH)
    spec = root / ".dmx" / "jobs" / "GH-7" / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        f"---\nticket: GH-7\nbranch: {_BRANCH}\ncomponents: {components}\n---\n# Spec\n",
        encoding="utf-8",
    )


@pytest.fixture
def team_source(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "ledger-team"
    head = _source_repo(
        repo,
        {
            "skills/settle.md": _SKILL.format(text="Settle the day."),
            "validators/balanced.py": (
                "import json\nprint(json.dumps({'pass': True, 'checks': []}))\n"
            ),
            "loops/close-day.yaml": "name: close-day\nskills:\n  - ledger/settle\n",
        },
    )
    return repo, head


def _ledger_sources(root: Path, repo: Path) -> Path:
    folder = root / ".dmx" / "components" / "ledger"
    _declare(folder, {"ledger-team": f"git::{repo}?ref=main"})
    return folder


class TestLock:
    def test_the_component_lock_is_its_own(
        self, reference_monorepo: Path, team_source: tuple[Path, str]
    ) -> None:
        repo, head = team_source
        folder = _ledger_sources(reference_monorepo, repo)

        report = update_shared_sources(reference_monorepo, component="ledger")

        assert [change.name for change in report.changes] == ["ledger-team"]
        assert lock_path(reference_monorepo, component="ledger") == folder / "shared-sources.lock"
        assert read_lock(reference_monorepo, component="ledger")["ledger-team"].commit == head
        assert not (reference_monorepo / ".dmx" / "shared-sources.lock").exists()
        assert read_shared_sources(reference_monorepo) == []

    def test_two_components_on_one_commit_share_one_tree(
        self, reference_monorepo: Path, team_source: tuple[Path, str]
    ) -> None:
        repo, head = team_source
        _ledger_sources(reference_monorepo, repo)
        _declare(
            reference_monorepo / ".dmx" / "components" / "payments",
            {"shared": f"git::{repo}?ref=main"},
        )

        update_shared_sources(reference_monorepo, component="ledger")
        update_shared_sources(reference_monorepo, component="payments")

        tree = cached_tree(reference_monorepo, str(repo), head)
        trees = list((reference_monorepo / ".dmx" / ".cache").glob(tree.name.split("@")[0] + "@*"))
        assert trees == [tree]

    def test_fetch_covers_every_lock(
        self, reference_monorepo: Path, team_source: tuple[Path, str]
    ) -> None:
        repo, head = team_source
        _ledger_sources(reference_monorepo, repo)
        update_shared_sources(reference_monorepo, component="ledger")
        _declare(
            reference_monorepo / ".dmx" / "components" / "payments",
            {"shared": f"git::{repo}?ref=main"},
        )

        lines, ok = fetch_shared_sources(reference_monorepo)

        assert ok is False
        assert lines == [
            f"✓ `ledger/ledger-team` at `{head[:12]}`",
            "✗ `payments/shared`: not in `.dmx/components/payments/shared-sources.lock` for "
            "its current address. Run `/dmx/update-shared-sources payments`.",
        ]


class TestResolution:
    def test_component_dirs_include_its_sources(
        self, reference_monorepo: Path, team_source: tuple[Path, str]
    ) -> None:
        repo, _head = team_source
        _ledger_sources(reference_monorepo, repo)
        update_shared_sources(reference_monorepo, component="ledger")

        dirs = component_dirs(reference_monorepo, "ledger", "skills")

        assert dirs[0] == reference_monorepo / ".dmx/components/ledger/skills"
        assert (dirs[1] / "settle.md").is_file()

    def test_an_unlocked_source_is_skipped(
        self, reference_monorepo: Path, team_source: tuple[Path, str]
    ) -> None:
        repo, _head = team_source
        _ledger_sources(reference_monorepo, repo)

        assert component_dirs(reference_monorepo, "ledger", "skills") == [
            reference_monorepo / ".dmx/components/ledger/skills"
        ]

    @pytest.mark.asyncio
    async def test_skills_validators_and_loops_from_the_source(
        self, reference_monorepo: Path, team_source: tuple[Path, str]
    ) -> None:
        repo, _head = team_source
        _ledger_sources(reference_monorepo, repo)
        update_shared_sources(reference_monorepo, component="ledger")
        (reference_monorepo / ".dmx/components/ledger/component.yaml").write_text(
            "description: Ledger\npaths: ['services/ledger/**']\n"
            "validators:\n  validate: [balanced]\n",
            encoding="utf-8",
        )
        _ticket(reference_monorepo, "[ledger]")

        async with Client(create_app()) as client:
            body = await _call(
                client, "get_skill_definition", reference_monorepo, name="ledger/settle"
            )
            started = await _call(client, "run_loop", reference_monorepo, name="ledger/close-day")
            context = await _call(client, "get_context", reference_monorepo)

        assert body.strip() == "Settle the day."
        assert "Next skill: `ledger/settle` (Loop: ledger/close-day" in started
        [validator] = [v for v in context["validators"] if v["name"] == "ledger/balanced"]
        assert validator["path"].endswith("validators/balanced.py")
        assert ".dmx/.cache/" in validator["path"]

    def test_the_components_own_files_win(
        self, reference_monorepo: Path, team_source: tuple[Path, str]
    ) -> None:
        repo, _head = team_source
        _ledger_sources(reference_monorepo, repo)
        update_shared_sources(reference_monorepo, component="ledger")
        own = reference_monorepo / ".dmx/components/ledger/skills/settle.md"
        own.parent.mkdir(parents=True)
        own.write_text(_SKILL.format(text="Our own settle."), encoding="utf-8")

        from dmx.loop_tools import _resolve_skill  # noqa: PLC0415, PLC2701

        resolved = _resolve_skill("ledger/settle", reference_monorepo)

        assert resolved is not None
        assert "Our own settle." in resolved.raw


async def _call(client: Client, tool: str, root: Path, **kwargs: Any) -> Any:
    result = await client.call_tool(tool, {"workspace_root": str(root), **kwargs})
    return result.data


class TestTool:
    @pytest.mark.asyncio
    async def test_update_one_components_sources(
        self, reference_monorepo: Path, team_source: tuple[Path, str]
    ) -> None:
        repo, head = team_source
        _ledger_sources(reference_monorepo, repo)
        _git(reference_monorepo, "checkout", "-q", "-b", "chore-ledger-sources")

        async with Client(create_app()) as client:
            reply = await _call(
                client, "update_shared_sources", reference_monorepo, component="ledger"
            )
            unknown = await _call(
                client, "update_shared_sources", reference_monorepo, component="nope"
            )

        assert f"✅ `ledger-team` locked at `{head[:12]}`." in reply
        assert "**Validators (code to review)**: added `balanced`" in reply
        assert "**Loops (code to review)**: added `close-day`" in reply
        assert "git add .dmx/components/ledger/shared-sources.lock" in reply
        assert unknown == "`nope` is not a component (no `.dmx/components/nope/component.yaml`)."


def test_the_skill_takes_a_component() -> None:
    from pathlib import Path  # noqa: PLC0415

    text = (
        Path(__file__).parent.parent / "src/dmx/skills/utility/dmx-update-shared-sources.md"
    ).read_text(encoding="utf-8")

    assert "  - name: component\n" in text
    assert "If `{{component}}` was provided, pass it as `component`." in text
    assert "git add {folder}/shared-sources.lock {folder}/shared-sources.yaml" in text
