"""`memogarden` command-line interface.

Thin authenticated client of the MemoGarden API — the CLI has the same
relationship to the server as every other client. Grammar is verb-first,
mirroring the RFC-005 semantic verbs: `memogarden <verb> <noun> ...`.

Supported:
    memogarden register provider   (RFC-004 §12.2)
    memogarden register account    (RFC-012 §6.2)
    memogarden list accounts
    memogarden show|verify|remove|run account
    memogarden import opml         (RSS/Atom subscriptions from OPML)

Connection settings come from --url/--api-key flags or the
MEMOGARDEN_URL / MEMOGARDEN_API_KEY environment variables.

Exit codes: 0 success, 1 error, 2 usage error (argparse).
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from mg_client.accounts import AccountAPI, RegisterAccountRequest
from mg_client.auth import AuthManager
from mg_client.exceptions import MemoGardenClientError
from mg_client.opml import OPMLParseError, parse_opml
from mg_client.providers import ProviderAPI, RegisterProviderRequest
from mg_client.semantic import SemanticAPI


# Top-level request fields accepted by POST /api/v1/providers (RFC-004 §12.2)
_ALLOWED_FIELDS = {"name", "module", "class", "interval", "enabled", "description", "config"}
_REQUIRED_FIELDS = ("name", "module", "class")

# Default platform per provider (RFC-012 §6.2); --platform overrides.
_PROVIDER_PLATFORMS = {"gmail": "google"}


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

    # ------------------------------------------------------------------
    # RFC-012 §6.2: provider accounts
    # ------------------------------------------------------------------

    register_account = register_sub.add_parser(
        "account",
        help=(
            "Bind an upstream account to a provider (POST .../accounts). "
            "The OAuth consent itself happens in the provider's own CLI "
            "(python -m gmail_importer.ingest auth-login --register) — this "
            "records/repairs the registry row and never sees tokens"
        ),
    )
    ra_fields = register_account.add_argument_group("field flags")
    ra_fields.add_argument("--provider", required=True, help="Registry slug, e.g. gmail")
    ra_fields.add_argument("--account", required=True, help="Upstream identity (email/username)")
    ra_fields.add_argument("--label", help="Operator nickname, e.g. work")
    ra_fields.add_argument(
        "--platform",
        help=f"Identity platform (default: {_PROVIDER_PLATFORMS})",
    )
    ra_fields.add_argument(
        "--credential-ref",
        help="HACM credential id (default: <platform>:<account>)",
    )
    ra_fields.add_argument(
        "--config",
        action="append",
        metavar="KEY=VALUE",
        help="Per-account config entry; repeatable",
    )

    list_ = sub.add_parser("list", help="List a resource")
    list_sub = list_.add_subparsers(dest="resource", required=True)
    list_accounts = list_sub.add_parser(
        "accounts", help="List a provider's accounts"
    )
    list_accounts.add_argument("--provider", required=True, help="Registry slug")

    for verb, help_text in (
        ("show", "Show one account manifest"),
        ("verify", "Probe an account's credential health (re-activates on success)"),
        ("remove", "Delete an account binding (HACM credential is untouched)"),
        ("run", "Trigger a run for one account"),
    ):
        verb_parser = sub.add_parser(verb, help=help_text)
        verb_sub = verb_parser.add_subparsers(dest="resource", required=True)
        account_parser = verb_sub.add_parser("account", help=help_text)
        account_parser.add_argument("--provider", required=True, help="Registry slug")
        target = account_parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--id", help="Account id (uuid)")
        target.add_argument("--label", help="Resolve by operator label")
        target.add_argument("--subject", help="Resolve by upstream identity (email)")

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    import_ = sub.add_parser("import", help="Import a resource into memogarden")
    import_sub = import_.add_subparsers(dest="resource", required=True)
    opml = import_sub.add_parser(
        "opml",
        help=(
            "Import feed subscriptions from an OPML file as Subscription "
            "facts (idempotent on xml_url — re-import skips what exists)"
        ),
    )
    opml.add_argument("--file", required=True, help="OPML file path ('-' for stdin)")
    opml.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report what would be imported without creating facts",
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


def account_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Build the account registration payload from CLI arguments."""
    platform = args.platform or _PROVIDER_PLATFORMS.get(args.provider)
    if not platform:
        raise CliError(
            f"no default platform for provider '{args.provider}' — pass --platform"
        )
    payload: dict[str, Any] = {
        "platform": platform,
        "subject": args.account,
    }
    if args.label is not None:
        payload["label"] = args.label
    if args.credential_ref is not None:
        payload["credential_ref"] = args.credential_ref
    config: dict[str, Any] = {}
    for item in args.config or []:
        if "=" not in item:
            raise CliError(f"--config expects KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        config[key] = _config_value(value)
    if config:
        payload["config"] = config
    return payload


async def _resolve_account_target(
    api: AccountAPI, provider: str, args: argparse.Namespace
) -> str:
    """Resolve --id directly, or --label/--subject through a listing."""
    if args.id:
        return args.id
    field = "label" if args.label else "subject"
    wanted = args.label or args.subject
    accounts = await api.list(provider)
    matches = [a for a in accounts if (a.get(field) or "").lower() == wanted.lower()]
    if not matches:
        raise CliError(
            f"no account with {field} '{wanted}' under provider '{provider}'"
        )
    if len(matches) > 1:
        ids = ", ".join(a["id"] for a in matches)
        raise CliError(
            f"{field} '{wanted}' is ambiguous ({ids}) — pass --id explicitly"
        )
    return matches[0]["id"]


def _resolve_connection(args: argparse.Namespace) -> tuple[str, str]:
    """Resolve API URL and API key from flags or environment."""
    url = args.url or os.environ.get("MEMOGARDEN_URL")
    api_key = args.api_key or os.environ.get("MEMOGARDEN_API_KEY")
    if not url:
        raise CliError("no API URL: use --url or set MEMOGARDEN_URL")
    if not api_key:
        raise CliError("no API key: use --api-key or set MEMOGARDEN_API_KEY")
    return url, api_key


def _feed_key(xml_url: str) -> str:
    """First 8 hex of SHA-256 of the feed URL — the tag the provider's
    User-Agent carries so a publisher can correspond about one feed
    (schemas/subscription.yaml; the provider recomputes it independently,
    this copy just stamps the fact)."""
    return hashlib.sha256(xml_url.encode("utf-8")).hexdigest()[:8]


async def _import_opml(
    url: str, api_key: str, path: str, dry_run: bool
) -> dict[str, Any]:
    """Import OPML subscriptions as Subscription facts (idempotent on
    xml_url: existing subscriptions are skipped, so re-import is safe)."""
    text = sys.stdin.read() if path == "-" else _read_file(path)
    try:
        feeds = parse_opml(text)
    except OPMLParseError as e:
        raise CliError(f"cannot parse OPML: {e}") from e

    existing: set[Any] = set()
    api = SemanticAPI(url, AuthManager(api_key=api_key))
    if not dry_run:
        # idempotency probe: page through existing Subscription facts
        start = 0
        while True:
            page = await api.query(
                type="Subscription", target_type="fact", count=100, start_index=start
            )
            results = page.get("results", [])
            existing |= {
                (row.get("data") or {}).get("xml_url") for row in results
            }
            start += len(results)
            if not results or start >= int(page.get("total") or 0):
                break

    now = datetime.now(UTC).isoformat()
    added: list[str] = []
    skipped: list[str] = []
    for feed in feeds:
        if feed.xml_url in existing:
            skipped.append(feed.xml_url)
            continue
        if not dry_run:
            data: dict[str, Any] = {"xml_url": feed.xml_url}
            if feed.title:
                data["title"] = feed.title
            if feed.html_url:
                data["html_url"] = feed.html_url
            if feed.categories:
                data["categories"] = feed.categories
            await api.add(
                type="Subscription",
                data=data,
                metadata={
                    "source": "opml",
                    "imported_at": now,
                    "feed_key": _feed_key(feed.xml_url),
                },
                canonical_at=now,
            )
        added.append(feed.xml_url)

    summary: dict[str, Any] = {
        "file": path,
        "found": len(feeds),
        "dry_run": dry_run,
    }
    if dry_run:
        summary["would_add"] = len(added)
        summary["already_present"] = len(skipped)
    else:
        summary["added"] = len(added)
        summary["skipped"] = len(skipped)
    summary["feeds"] = added if dry_run or added else []
    if skipped:
        summary["skipped_feeds"] = skipped
    return summary


async def _register_provider(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Send the registration request to the server."""
    api = ProviderAPI(url, AuthManager(api_key=api_key))
    return await api.register(RegisterProviderRequest(**payload))


async def _register_account(url: str, api_key: str, provider: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Send the account registration request to the server (RFC-012)."""
    api = AccountAPI(url, AuthManager(api_key=api_key))
    return await api.register(provider, RegisterAccountRequest(**payload))


async def _list_accounts(url: str, api_key: str, provider: str) -> list[dict[str, Any]]:
    api = AccountAPI(url, AuthManager(api_key=api_key))
    return await api.list(provider)


async def _account_action(
    url: str, api_key: str, provider: str, args: argparse.Namespace, action: str
) -> dict[str, Any] | None:
    """Resolve the account target and perform show/verify/remove/run."""
    api = AccountAPI(url, AuthManager(api_key=api_key))
    account_id = await _resolve_account_target(api, provider, args)
    if action == "show":
        return await api.get(provider, account_id)
    if action == "verify":
        return await api.verify(provider, account_id)
    if action == "run":
        return await api.run(provider, account_id)
    if action == "remove":
        await api.remove(provider, account_id)
        return {"removed": account_id, "provider": provider}
    raise CliError(f"unsupported action: {action}")  # pragma: no cover


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

        if args.command == "register" and args.resource == "account":
            payload = account_payload(args)
            url, api_key = _resolve_connection(args)
            try:
                result = asyncio.run(
                    _register_account(url, api_key, args.provider, payload)
                )
            except ValidationError as e:
                raise CliError(f"invalid account payload: {e}") from e
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "list" and args.resource == "accounts":
            url, api_key = _resolve_connection(args)
            accounts = asyncio.run(_list_accounts(url, api_key, args.provider))
            print(json.dumps({"accounts": accounts, "count": len(accounts)}, indent=2))
            return 0

        if (
            args.command in ("show", "verify", "remove", "run")
            and args.resource == "account"
        ):
            url, api_key = _resolve_connection(args)
            result = asyncio.run(
                _account_action(url, api_key, args.provider, args, args.command)
            )
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "import" and args.resource == "opml":
            url, api_key = _resolve_connection(args)
            result = asyncio.run(
                _import_opml(url, api_key, args.file, args.dry_run)
            )
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
