"""`memogarden` command-line interface.

Thin authenticated client of the MemoGarden API — the CLI has the same
relationship to the server as every other client. Grammar is verb-first,
mirroring the RFC-005 semantic verbs: `memogarden <verb> <noun> ...`.

Currently supported: `memogarden register provider` (RFC-004 §12.2).

Connection settings come from --url/--api-key flags or the
MEMOGARDEN_URL / MEMOGARDEN_API_KEY environment variables.

Exit codes: 0 success, 1 error, 2 usage error (argparse).
"""

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from pydantic import ValidationError

from mg_client.auth import AuthManager
from mg_client.exceptions import MemoGardenClientError
from mg_client.providers import ProviderAPI, RegisterProviderRequest


# Top-level request fields accepted by POST /api/v1/providers (RFC-004 §12.2)
_ALLOWED_FIELDS = {"name", "module", "class", "interval", "enabled", "description", "config"}
_REQUIRED_FIELDS = ("name", "module", "class")


class CliError(Exception):
    """User-facing CLI error (printed to stderr, exit code 1)."""


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the memogarden CLI."""
    parser = argparse.ArgumentParser(
        prog="memogarden",
        description=(
            "MemoGarden CLI — thin authenticated client of the MemoGarden API. "
            "Verb-first grammar mirroring the RFC-005 semantic verbs."
        ),
    )
    parser.add_argument(
        "--url",
        default=None,
        help="API base URL (default: MEMOGARDEN_URL environment variable)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key, mg_sk_... (default: MEMOGARDEN_API_KEY environment variable)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register", help="Register a resource with the server")
    register_sub = register.add_subparsers(dest="resource", required=True)

    provider = register_sub.add_parser(
        "provider",
        help="Register a data provider (POST /api/v1/providers)",
    )
    provider.add_argument(
        "--file",
        help=(
            "JSON payload file ('-' for stdin). Takes precedence over field "
            "flags; field flags are rejected when --file is given"
        ),
    )
    fields = provider.add_argument_group("field flags (alternative to --file)")
    fields.add_argument("--name", help="Registry slug, e.g. gmail")
    fields.add_argument("--module", help="Importable module, e.g. gmail_importer.provider")
    fields.add_argument("--class", dest="class_name", help="Provider Protocol class, e.g. GmailProvider")
    fields.add_argument("--interval", type=int, help="Seconds between runs (>= 60, default 3600)")
    fields.add_argument("--description", help="Human-readable description")
    fields.add_argument("--disabled", action="store_true", help="Register in disabled state")
    fields.add_argument(
        "--config",
        action="append",
        metavar="KEY=VALUE",
        help="Provider config entry; VALUE parsed as JSON when possible, else string. Repeatable",
    )
    return parser


def _config_value(raw: str) -> Any:
    """Parse a KEY=VALUE payload as JSON when possible, else keep as string."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def provider_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Build the registration payload from CLI arguments.

    Args:
        args: Parsed arguments for `register provider`

    Returns:
        Payload dict containing only explicitly supplied fields

    Raises:
        CliError: On missing required fields, unknown keys, or malformed input
    """
    if args.file:
        field_flags_used = any(
            getattr(args, name) is not None
            for name in ("name", "module", "class_name", "interval", "description", "config")
        ) or args.disabled
        if field_flags_used:
            raise CliError("--file cannot be combined with field flags")
        text = sys.stdin.read() if args.file == "-" else _read_file(args.file)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise CliError(f"invalid JSON in {args.file or 'stdin'}: {e}") from e
        if not isinstance(data, dict):
            raise CliError("payload must be a JSON object")
        unknown = set(data) - _ALLOWED_FIELDS
        if unknown:
            raise CliError(
                f"unknown payload fields: {', '.join(sorted(unknown))} "
                f"(allowed: {', '.join(sorted(_ALLOWED_FIELDS))})"
            )
        return data

    missing = [f for f in _REQUIRED_FIELDS if not getattr(args, f if f != "class" else "class_name")]
    if missing:
        raise CliError(
            "missing required fields: "
            + ", ".join(f"--{f}" for f in missing)
            + " (or use --file)"
        )

    config: dict[str, Any] = {}
    for item in args.config or []:
        if "=" not in item:
            raise CliError(f"--config expects KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        config[key] = _config_value(value)

    payload: dict[str, Any] = {
        "name": args.name,
        "module": args.module,
        "class": args.class_name,
    }
    if args.interval is not None:
        payload["interval"] = args.interval
    if args.disabled:
        payload["enabled"] = False
    if args.description is not None:
        payload["description"] = args.description
    if config:
        payload["config"] = config
    return payload


def _read_file(path: str) -> str:
    """Read a payload file, raising CliError on failure."""
    try:
        with open(path) as f:
            return f.read()
    except OSError as e:
        raise CliError(f"cannot read {path}: {e}") from e


def _resolve_connection(args: argparse.Namespace) -> tuple[str, str]:
    """Resolve API URL and API key from flags or environment."""
    url = args.url or os.environ.get("MEMOGARDEN_URL")
    api_key = args.api_key or os.environ.get("MEMOGARDEN_API_KEY")
    if not url:
        raise CliError("no API URL: use --url or set MEMOGARDEN_URL")
    if not api_key:
        raise CliError("no API key: use --api-key or set MEMOGARDEN_API_KEY")
    return url, api_key


async def _register_provider(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Send the registration request to the server."""
    api = ProviderAPI(url, AuthManager(api_key=api_key))
    return await api.register(RegisterProviderRequest(**payload))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list (defaults to sys.argv[1:])

    Returns:
        Process exit code: 0 success, 1 error (2 = argparse usage errors)
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "register" and args.resource == "provider":
            payload = provider_payload(args)
            url, api_key = _resolve_connection(args)
            try:
                result = asyncio.run(_register_provider(url, api_key, payload))
            except ValidationError as e:
                raise CliError(f"invalid registration payload: {e}") from e
            print(json.dumps(result, indent=2))
            return 0
        parser.error(f"unsupported command: {args.command} {getattr(args, 'resource', '')}")
        return 2  # pragma: no cover - parser.error exits

    except CliError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except MemoGardenClientError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
