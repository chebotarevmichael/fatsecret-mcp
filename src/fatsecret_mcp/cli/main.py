"""fatsecret-mcp: an MCP server for a Household's FatSecret Food Diaries, and the Operator's commands.

Without a command it serves over stdio, as Claude Code starts it.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence

import anyio

from fatsecret_mcp.catalog.sync import DEFAULT_BUDGET
from fatsecret_mcp.cli.serve import serve
from fatsecret_mcp.config.loader import USERS_VAR, HouseholdConfigError, load_consumer_key
from fatsecret_mcp.fatsecret.errors import FatSecretError


def main(argv: Sequence[str] | None = None) -> None:
    command_line = sys.argv[1:] if argv is None else list(argv)
    arguments = _parser().parse_args(command_line or ["serve"])
    environ = os.environ

    try:
        if arguments.command == "add-user":
            anyio.run(_add_user, environ)
        elif arguments.command == "check":
            if not anyio.run(_check, environ):
                sys.exit(1)
        elif arguments.command == "sync-catalog":
            anyio.run(_sync_catalog, environ, arguments)
        else:
            anyio.run(_serve, environ, arguments)
    except (HouseholdConfigError, FatSecretError) as error:
        sys.exit(f"fatsecret-mcp: {error}")
    except (EOFError, KeyboardInterrupt):
        sys.exit("fatsecret-mcp: cancelled")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fatsecret-mcp", description=__doc__)
    commands = parser.add_subparsers(dest="command", metavar="command")

    commands.add_parser("add-user", help="onboard a User: FatSecret sign-in with a PIN, settings, password")
    commands.add_parser("check", help="check the configuration and every User's FatSecret connection")

    sync_command = commands.add_parser(
        "sync-catalog", help="fill a User's catalog of logged Foods from their Food Diary"
    )

    sync_command.add_argument("--user", help="the User's id; optional when there's only one")
    sync_command.add_argument("--full", action="store_true", help="read the whole Food Diary again")
    sync_command.add_argument("--budget", type=_positive_int, default=DEFAULT_BUDGET, help="most requests")
    sync_command.add_argument(
        "--classify", action="store_true", help="check each new Food: regional if FatSecret can't return it"
    )

    serve_command = commands.add_parser("serve", help="run the server (the default, over stdio)")

    serve_command.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8000)

    return parser


async def _add_user(environ: Mapping[str, str]) -> None:
    from fatsecret_mcp.cli.add_user import add_user
    from fatsecret_mcp.cli.console import Terminal
    from fatsecret_mcp.fatsecret.client import connected_api

    consumer = load_consumer_key(environ)

    async with connected_api(consumer) as fatsecret:
        await add_user(Terminal(), fatsecret, existing_users=environ.get(USERS_VAR))


async def _check(environ: Mapping[str, str]) -> bool:
    from fatsecret_mcp.cli.check import check
    from fatsecret_mcp.cli.console import Terminal
    from fatsecret_mcp.fatsecret.client import connected_api

    consumer = load_consumer_key(environ)

    async with connected_api(consumer) as fatsecret:
        return await check(Terminal(), environ, fatsecret)


async def _sync_catalog(environ: Mapping[str, str], arguments: argparse.Namespace) -> None:
    from fatsecret_mcp.cli.console import Terminal
    from fatsecret_mcp.cli.sync_catalog import sync_catalog
    from fatsecret_mcp.fatsecret.client import connected_api

    consumer = load_consumer_key(environ)

    async with connected_api(consumer) as fatsecret:
        await sync_catalog(
            Terminal(),
            environ,
            fatsecret,
            user_id=arguments.user,
            full=arguments.full,
            budget=arguments.budget,
            classify=arguments.classify,
        )


def _positive_int(value: str) -> int:
    number = int(value)

    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive whole number")

    return number


async def _serve(environ: Mapping[str, str], arguments: argparse.Namespace) -> None:
    await serve(
        environ,
        transport=arguments.transport,
        host=arguments.host,
        port=arguments.port,
    )
