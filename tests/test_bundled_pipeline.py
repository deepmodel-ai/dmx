"""Bundled loops against the bundled validators.

The stubbed pipeline in ``test_loop_integration`` reports every declared
check, so it cannot catch a loop that asks for a check its validator never
emits. These tests use the real scripts.
"""

from __future__ import annotations

import ast
import json
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest
from fastmcp import Client

from dmx.loop_schema import load_loops_dir
from dmx.server import create_app
from tests.test_loop_integration import _call, _write_config

_BRANCH = "bug-gh-74-example"
_TICKET = "GH-74"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "initial")


def _write_passing_project(root: Path) -> None:
    (root / "test_ok.py").write_text("def test_ok() -> None:\n    assert True\n", encoding="utf-8")
    (root / "Makefile").write_text("test:\n\tpython -m pytest -q test_ok.py\n", encoding="utf-8")


def _write_spec(root: Path) -> None:
    (root / ".dmx" / "spec.md").write_text(
        f"---\nticket: {_TICKET}\nbranch: {_BRANCH}\n---\n"
        "Q: What?\nA: A widget.\n\n"
        "## Technical Approach\nAdd one function and a test that calls it.\n\n"
        "## Scope\n- Add the widget\n",
        encoding="utf-8",
    )


def _write_tasks(root: Path) -> None:
    (root / ".dmx" / "tasks.md").write_text(
        "## Phase 1: Widget\n- [x] Implement the widget\n",
        encoding="utf-8",
    )


def _write_validation_report(root: Path) -> None:
    head = _git(root, "rev-parse", "HEAD")
    report = {
        "commit": head,
        "scope_items": [{"item": "widget", "verdict": "covered", "evidence": "test_ok.py"}],
        "scope_creep": [],
        "regressions": [],
        "edge_cases": [],
    }
    path = root / ".dmx" / "jobs" / _TICKET / "validation-report.json"
    path.write_text(json.dumps(report), encoding="utf-8")


def _tuple_heads(node: ast.AST) -> set[str]:
    if not isinstance(node, ast.List):
        return set()
    found: set[str] = set()
    for item in node.elts:
        if (
            isinstance(item, ast.Tuple)
            and item.elts
            and isinstance(item.elts[0], ast.Constant)
            and isinstance(item.elts[0].value, str)
        ):
            found.add(item.elts[0].value)
    return found


def _string_list(node: ast.AST) -> set[str]:
    if not isinstance(node, ast.List):
        return set()
    return {
        item.value
        for item in node.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    }


def _reported_check_names(script: Path) -> set[str]:
    """Check names the validator source builds, ignoring its docstring."""
    tree = ast.parse(script.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if "checks_raw" in targets:
                found.update(_tuple_heads(node.value))
            if "check_names" in targets:
                found.update(_string_list(node.value))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=False):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "name"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    found.add(value.value)
    return found


class TestBundledCheckNames:
    def test_declared_checks_are_ones_the_validator_reports(self) -> None:
        package = files("dmx")
        loops_dir = Path(str(package / "loops"))
        validators_dir = Path(str(package / "validators"))
        for config in load_loops_dir(loops_dir).values():
            for validator in config.validators:
                script = validators_dir / f"{validator.tool}.py"
                assert script.is_file()
                reported = _reported_check_names(script)
                declared = {check.name for check in validator.checks}
                missing = declared - reported
                assert not missing, (
                    f"{config.name}/{validator.tool} never reports {sorted(missing)}"
                )


class TestBundledPipeline:
    @pytest.mark.asyncio
    async def test_spec_plan_dev_validate_chain(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        _write_config(tmp_path)
        _write_passing_project(tmp_path)
        app = create_app()

        async with Client(app) as client:

            async def call(tool: str, **kwargs: str) -> str:
                return await _call(client, tool, tmp_path, **kwargs)

            await call("run_loop", name="spec")
            _git(tmp_path, "checkout", "-b", _BRANCH)
            _write_spec(tmp_path)
            await call("loop_advance", output="created ticket", skill="create-ticket")
            spec_done = await call("loop_continue")
            assert "chaining automatically to **plan**" in spec_done.lower()
            assert "outcome: `success`" in spec_done

            _write_tasks(tmp_path)
            await call("loop_advance", output="tasks.md written", skill="plan")
            plan_done = await call("loop_continue")
            assert "chaining automatically to **dev**" in plan_done.lower()
            assert "outcome: `success`" in plan_done

            await call("loop_advance", output="implemented", skill="implement-next-phase")
            await call("loop_continue")
            await call("loop_advance", output="committed", skill="commit")
            dev_done = await call("loop_continue")
            assert "chaining automatically to **validate**" in dev_done.lower()
            assert "outcome: `success`" in dev_done

            await call("loop_advance", output="reviewed", skill="validate")
            _write_validation_report(tmp_path)
            validate_done = await call("loop_continue")
            assert "chaining automatically to **release**" in validate_done.lower()
            assert "outcome: `success`" in validate_done
