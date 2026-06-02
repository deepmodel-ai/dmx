"""CLI entry point for the dmx MCP server."""

from __future__ import annotations

import importlib.metadata
import logging
import os
import sys
from pathlib import Path

import click

__all__ = ["main"]

_BANNER = """\

  █▀▄  █▄█▄█  ▀▄▀
  █▄▀  █ ▀ █  ▄▀▄

  AI SDLC MCP server · https://deepmodel.ai
"""


def _print_banner(*, version: str, transport: str, port: int | None) -> None:
    click.echo(_BANNER, err=True)
    transport_line = f"  transport  {transport}"
    if port is not None:
        transport_line += f"   port  {port}"
    click.echo(f"  version    {version}", err=True)
    click.echo(transport_line, err=True)
    click.echo("", err=True)


def _build_http_middleware() -> list[object]:
    """Build the ASGI middleware stack for the HTTP transport.

    Reads ``REQUIRE_API_KEY`` and ``MCP_API_KEY`` from the environment.
    If ``REQUIRE_API_KEY=true``:
    - ``MCP_API_KEY`` must be set; exits with an error if it is absent.
    - A :class:`~dmx.http_auth.BearerTokenMiddleware` is prepended to the
      stack so every HTTP request must carry a valid ``Authorization: Bearer``
      header.

    Returns:
        A list of :class:`starlette.middleware.Middleware` instances (may be
        empty when auth is disabled).
    """
    require_auth = os.environ.get("REQUIRE_API_KEY", "false").strip().lower() == "true"
    if not require_auth:
        return []

    api_key = os.environ.get("MCP_API_KEY", "").strip()
    if not api_key:
        click.echo(
            "error: REQUIRE_API_KEY=true but MCP_API_KEY is not set",
            err=True,
        )
        sys.exit(1)

    from starlette.middleware import Middleware as ASGIMiddleware  # noqa: PLC0415

    from dmx.http_auth import BearerTokenMiddleware  # noqa: PLC0415

    return [ASGIMiddleware(BearerTokenMiddleware, api_key=api_key)]


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def main(*, verbose: bool) -> None:
    """dmx — AI SDLC MCP server."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


@main.command()
@click.option("--http", is_flag=True, help="Use HTTP+SSE transport instead of stdio.")
@click.option("--port", default=8080, show_default=True, help="HTTP port (--http only).")
@click.option(
    "--skills-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Override the bundled skills directory.",
)
@click.option(
    "--rules-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Override the bundled rules directory.",
)
@click.option(
    "--watch",
    is_flag=True,
    help="Hot-reload on file change (requires watchfiles extra).",
)
def serve(
    *,
    http: bool,
    port: int,
    skills_dir: Path | None,
    rules_dir: Path | None,
    watch: bool,
) -> None:
    """Start the dmx MCP server.

    Default transport is stdio — the IDE spawns the process directly.
    Use --http for a team server, Docker, or Cloud Run deployment.
    """
    import asyncio

    from dmx.server import create_app, resolve_dirs, watch_catalog

    app = create_app(skills_dir=skills_dir, rules_dir=rules_dir)

    http_middleware = _build_http_middleware()

    ver = importlib.metadata.version("deepmodel-dmx")
    _print_banner(
        version=ver,
        transport="http+sse" if http else "stdio",
        port=int(os.environ.get("PORT", port)) if http else None,
    )

    if watch:
        resolved_skills, resolved_rules = resolve_dirs(skills_dir, rules_dir)

        effective_port = int(os.environ.get("PORT", port))

        async def _serve_with_watch() -> None:
            if http:
                server_coro = app.run_async(
                    transport="sse",
                    host="0.0.0.0",  # noqa: S104
                    port=effective_port,
                    middleware=http_middleware,
                    show_banner=False,
                )
            else:
                server_coro = app.run_async(transport="stdio", show_banner=False)
            await asyncio.gather(
                server_coro,
                watch_catalog(app, resolved_skills, resolved_rules),
            )

        asyncio.run(_serve_with_watch())
    elif http:
        effective_port = int(os.environ.get("PORT", port))
        app.run(  # noqa: S104
            transport="sse",
            host="0.0.0.0",
            port=effective_port,
            middleware=http_middleware,
            show_banner=False,
        )
    else:
        app.run(transport="stdio", show_banner=False)


@main.command(name="list-skills")
@click.option(
    "--skills-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Override the bundled skills directory.",
)
def list_skills(*, skills_dir: Path | None) -> None:
    """Print all loaded skills with name, title, and argument count."""
    from dmx.catalog import load_skills
    from dmx.server import resolve_dirs

    directory, _ = resolve_dirs(skills_dir, None)
    skills = load_skills(directory)

    if not skills:
        click.echo("No skills found.")
        return

    name_width = max(len(s.name) for s in skills) + 2
    title_width = max(len(s.title) for s in skills) + 2

    click.echo(f"{'NAME':<{name_width}} {'TITLE':<{title_width}} ARGS")
    click.echo("-" * (name_width + title_width + 6))
    for skill in sorted(skills, key=lambda s: s.name):
        click.echo(
            f"{skill.name:<{name_width}} {skill.title:<{title_width}} {len(skill.arguments)}"
        )


@main.command()
def version() -> None:
    """Print version and exit."""
    ver = importlib.metadata.version("deepmodel-dmx")
    click.echo(f"dmx {ver}")
