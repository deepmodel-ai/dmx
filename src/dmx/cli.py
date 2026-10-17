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


@main.command(name="fetch-shared-sources")
@click.option(
    "--workspace-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="A folder in the repo (default: the current directory). Its .dmx/ is found from there.",
)
def fetch_shared_sources(*, workspace_root: Path | None) -> None:
    """Fetch every locked shared source into .dmx/.cache/ without changing a lock.

    Covers the repo-wide lock and each component's.

    For CI and fresh clones. Needs read access to each source repo.
    """
    from dmx.shared_sources import SharedSourceError
    from dmx.sync_runner import fetch_shared_sources as fetch
    from dmx.workspace import find_dmx_root

    root, _skipped = find_dmx_root((workspace_root or Path.cwd()).resolve())
    try:
        lines, ok = fetch(root)
    except SharedSourceError as exc:
        raise click.ClickException(str(exc)) from exc
    if not lines:
        click.echo("No shared sources declared in .dmx/shared-sources.yaml.")
        return
    for line in lines:
        click.echo(line)
    if not ok:
        raise SystemExit(1)


def _parse_days(_ctx: click.Context, _param: click.Parameter, value: str) -> int:
    days = value.strip().removesuffix("d")
    if not days.isdigit():
        raise click.BadParameter(f"{value!r} is not a number of days, like 30d or 7.")
    return int(days)


@main.command(name="prune-jobs")
@click.option(
    "--older-than",
    "older_than_days",
    default="30d",
    show_default=True,
    callback=_parse_days,
    help="Remove finished jobs whose newest run finished longer ago than this, e.g. 7d.",
)
@click.option("--dry-run", is_flag=True, help="Show what would be removed and change nothing.")
@click.option(
    "--workspace-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="A folder in the repo (default: the current directory). Its .dmx/ is found from there.",
)
def prune_jobs(*, older_than_days: int, dry_run: bool, workspace_root: Path | None) -> None:
    """Remove finished jobs from .dmx/jobs/ in one commit.

    A job is finished when all its loop runs are, and it is on branch_base.
    The current ticket, jobs with open learnings or decisions, and jobs with
    uncommitted changes are kept. The release loop runs this before create-pr.
    """
    from datetime import timedelta

    from dmx.job_pruning import PruneError, plan_prune
    from dmx.job_pruning import prune_jobs as prune
    from dmx.workspace import find_dmx_root

    root, _skipped = find_dmx_root((workspace_root or Path.cwd()).resolve())
    older_than = timedelta(days=older_than_days)
    try:
        plan = plan_prune(root, older_than) if dry_run else prune(root, older_than)
    except PruneError as exc:
        raise click.ClickException(str(exc)) from exc
    if plan.blocked is not None:
        click.echo(plan.blocked, err=True)
        raise SystemExit(1)

    count = len(plan.removes)
    noun = "job" if count == 1 else "jobs"
    if not plan.removes:
        click.echo("No finished jobs to remove.")
    elif dry_run:
        click.echo(f"Would remove {count} finished {noun}: {', '.join(plan.removes)}")
    else:
        click.echo(f"Removed {count} finished {noun} in {plan.commit}: {', '.join(plan.removes)}")
    if plan.kept:
        click.echo("Kept:")
        for job, reason in plan.kept:
            click.echo(f"  {job}: {reason}")


@main.command()
def version() -> None:
    """Print version and exit."""
    ver = importlib.metadata.version("deepmodel-dmx")
    click.echo(f"dmx {ver}")
