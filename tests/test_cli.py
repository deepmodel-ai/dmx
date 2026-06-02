"""Tests for dmx.cli, dmx.http_auth, and server env var resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from dmx.cli import _build_http_middleware, main
from dmx.server import resolve_dirs

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# resolve_dirs — env var resolution helper
# ---------------------------------------------------------------------------


class TestResolveDirs:
    def test_explicit_args_take_precedence_over_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        skills = tmp_path / "skills"
        rules = tmp_path / "rules"
        skills.mkdir()
        rules.mkdir()
        monkeypatch.setenv("DMX_SKILLS_DIR", str(tmp_path / "env-skills"))
        monkeypatch.setenv("DMX_RULES_DIR", str(tmp_path / "env-rules"))

        s, r = resolve_dirs(skills, rules)
        assert s == skills
        assert r == rules

    def test_env_vars_applied_when_args_are_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        skills = tmp_path / "skills"
        rules = tmp_path / "rules"
        skills.mkdir()
        rules.mkdir()
        monkeypatch.setenv("DMX_SKILLS_DIR", str(skills))
        monkeypatch.setenv("DMX_RULES_DIR", str(rules))

        s, r = resolve_dirs(None, None)
        assert s == skills
        assert r == rules

    def test_bundled_fallback_when_no_args_or_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("DMX_SKILLS_DIR", raising=False)
        monkeypatch.delenv("DMX_RULES_DIR", raising=False)

        s, r = resolve_dirs(None, None)
        assert s.name == "skills"
        assert r.name == "rules"
        assert s.exists()
        assert r.exists()

    def test_partial_env_override(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only DMX_SKILLS_DIR set — rules falls back to bundled."""
        skills = tmp_path / "skills"
        skills.mkdir()
        monkeypatch.setenv("DMX_SKILLS_DIR", str(skills))
        monkeypatch.delenv("DMX_RULES_DIR", raising=False)

        s, r = resolve_dirs(None, None)
        assert s == skills
        assert r.name == "rules"  # bundled

    def test_explicit_arg_overrides_env_independently(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Explicit skills_dir wins over env; rules still uses env var."""
        skills = tmp_path / "skills"
        rules = tmp_path / "rules"
        skills.mkdir()
        rules.mkdir()
        monkeypatch.setenv("DMX_SKILLS_DIR", str(tmp_path / "env-skills"))
        monkeypatch.setenv("DMX_RULES_DIR", str(rules))

        s, r = resolve_dirs(skills, None)
        assert s == skills  # explicit wins
        assert r == rules  # env var used


# ---------------------------------------------------------------------------
# create_app — DMX_SKILLS_DIR / DMX_RULES_DIR env var integration
# ---------------------------------------------------------------------------


class TestCreateAppEnvVars:
    def test_dmx_skills_dir_env_loads_from_empty_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """create_app() uses DMX_SKILLS_DIR and loads 0 skills from empty dir."""
        import asyncio

        from dmx.server import create_app

        empty = tmp_path / "skills"
        empty.mkdir()
        monkeypatch.setenv("DMX_SKILLS_DIR", str(empty))
        monkeypatch.delenv("DMX_RULES_DIR", raising=False)

        app = create_app()
        prompts = asyncio.run(app.list_prompts())
        assert prompts == []

    def test_explicit_arg_overrides_dmx_skills_dir_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Explicit skills_dir argument wins over DMX_SKILLS_DIR env var."""
        import asyncio

        from dmx.server import _bundled_skills_dir, create_app

        empty_env = tmp_path / "env-skills"
        empty_env.mkdir()
        monkeypatch.setenv("DMX_SKILLS_DIR", str(empty_env))
        monkeypatch.delenv("DMX_RULES_DIR", raising=False)

        # Pass bundled dir explicitly — env var (empty_env) must be ignored.
        app = create_app(skills_dir=_bundled_skills_dir())
        prompts = asyncio.run(app.list_prompts())
        assert len(prompts) == 23


# ---------------------------------------------------------------------------
# _build_http_middleware — bearer auth env logic
# ---------------------------------------------------------------------------


class TestBuildHttpMiddleware:
    def test_no_auth_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REQUIRE_API_KEY", raising=False)
        monkeypatch.delenv("MCP_API_KEY", raising=False)

        assert _build_http_middleware() == []

    def test_require_api_key_false_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REQUIRE_API_KEY", "false")
        monkeypatch.setenv("MCP_API_KEY", "secret")

        assert _build_http_middleware() == []

    def test_require_api_key_true_returns_middleware(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REQUIRE_API_KEY", "true")
        monkeypatch.setenv("MCP_API_KEY", "mysecret")

        result = _build_http_middleware()
        assert len(result) == 1

    def test_require_api_key_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REQUIRE_API_KEY", "TRUE")
        monkeypatch.setenv("MCP_API_KEY", "key")

        result = _build_http_middleware()
        assert len(result) == 1

    def test_require_api_key_true_missing_mcp_key_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REQUIRE_API_KEY", "true")
        monkeypatch.delenv("MCP_API_KEY", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            _build_http_middleware()
        assert exc_info.value.code == 1

    def test_require_api_key_true_empty_mcp_key_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REQUIRE_API_KEY", "true")
        monkeypatch.setenv("MCP_API_KEY", "   ")  # whitespace only

        with pytest.raises(SystemExit) as exc_info:
            _build_http_middleware()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# BearerTokenMiddleware — Starlette ASGI unit tests
# ---------------------------------------------------------------------------


def _make_auth_app(api_key: str) -> object:
    """Build a minimal Starlette app wrapped with BearerTokenMiddleware."""
    from starlette.applications import Starlette  # noqa: PLC0415
    from starlette.middleware import Middleware  # noqa: PLC0415
    from starlette.responses import PlainTextResponse  # noqa: PLC0415
    from starlette.routing import Route  # noqa: PLC0415

    from dmx.http_auth import BearerTokenMiddleware  # noqa: PLC0415

    async def handler(request: object) -> PlainTextResponse:  # type: ignore[misc]
        return PlainTextResponse("ok")

    return Starlette(
        routes=[Route("/", handler)],  # type: ignore[arg-type]
        middleware=[Middleware(BearerTokenMiddleware, api_key=api_key)],
    )


class TestBearerTokenMiddleware:
    def test_valid_token_passes(self) -> None:
        from starlette.testclient import TestClient  # noqa: PLC0415

        client = TestClient(_make_auth_app("secret123"), raise_server_exceptions=True)
        resp = client.get("/", headers={"Authorization": "Bearer secret123"})
        assert resp.status_code == 200

    def test_missing_token_rejected(self) -> None:
        from starlette.testclient import TestClient  # noqa: PLC0415

        client = TestClient(_make_auth_app("secret"), raise_server_exceptions=True)
        resp = client.get("/")
        assert resp.status_code == 401

    def test_wrong_token_rejected(self) -> None:
        from starlette.testclient import TestClient  # noqa: PLC0415

        client = TestClient(_make_auth_app("correct"), raise_server_exceptions=True)
        resp = client.get("/", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_invalid_auth_scheme_rejected(self) -> None:
        from starlette.testclient import TestClient  # noqa: PLC0415

        client = TestClient(_make_auth_app("secret"), raise_server_exceptions=True)
        resp = client.get("/", headers={"Authorization": "Basic secret"})
        assert resp.status_code == 401

    def test_401_response_is_json(self) -> None:
        from starlette.testclient import TestClient  # noqa: PLC0415

        client = TestClient(_make_auth_app("secret"), raise_server_exceptions=True)
        resp = client.get("/")
        assert resp.headers["content-type"].startswith("application/json")
        data = resp.json()
        assert "error" in data


# ---------------------------------------------------------------------------
# dmx list-skills CLI — Click test runner
# ---------------------------------------------------------------------------


class TestListSkillsCli:
    def test_lists_bundled_skills(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DMX_SKILLS_DIR", raising=False)
        runner = CliRunner()
        result = runner.invoke(main, ["list-skills"])
        assert result.exit_code == 0
        assert "NAME" in result.output
        assert "TITLE" in result.output
        assert "ARGS" in result.output
        assert "init" in result.output

    def test_custom_skills_dir_empty_reports_none(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["list-skills", "--skills-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "No skills found." in result.output

    def test_dmx_skills_dir_env_var_respected(
        self,
        tmp_path: Path,
    ) -> None:
        """DMX_SKILLS_DIR env var is picked up by list-skills."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["list-skills"],
            env={"DMX_SKILLS_DIR": str(tmp_path)},
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "No skills found." in result.output

    def test_explicit_arg_overrides_env(
        self,
        tmp_path: Path,
    ) -> None:
        """--skills-dir flag takes precedence over DMX_SKILLS_DIR env."""
        other = tmp_path / "other"
        other.mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["list-skills", "--skills-dir", str(other)],
            env={"DMX_SKILLS_DIR": str(tmp_path)},
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "No skills found." in result.output

    def test_version_command(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["version"])
        assert result.exit_code == 0
        assert "dmx" in result.output
