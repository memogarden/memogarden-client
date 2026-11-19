"""`memogarden` command-line interface.

Thin authenticated client of the MemoGarden API — the CLI has the same
relationship to the server as every other client. Grammar is verb-first,
mirroring the RFC-005 semantic verbs: `memogarden <verb> <noun> ...`.

Supported (RFC-015 §5 — operator surface):

    Session (Tier 0 — the JWT unblocker; governed verbs require it):
        memogarden login|logout|whoami|change-password

    Identity & authority (Tier 1, JWT-gated):
        memogarden register agent|key|scope|grant
        memogarden list agents|keys|scopes|grants
        memogarden edit key|account
        memogarden remove key|scope|grant

    Provider operations (Tier 2, API-key OK):
        memogarden register provider|account    (RFC-004 §12.2, RFC-012 §6.2)
        memogarden list providers|accounts|runs
        memogarden show provider|account|run
        memogarden verify|remove|run account
        memogarden run provider|providers
        memogarden import opml

    Review & audit flows (Tier 3):
        memogarden enter SCOPE / leave SCOPE
        memogarden list triage|actions
        memogarden relabel fact
        memogarden query / track

Connection settings: --url / --api-key flags or MEMOGARDEN_URL /
MEMOGARDEN_API_KEY (agent transport, X-API-Key); operator JWT from the
session file written by `login` or MEMOGARDEN_JWT (Bearer).

Exit codes: 0 success, 1 error, 2 usage error (argparse).
"""

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from mg_client.accounts import AccountAPI, RegisterAccountRequest, UpdateAccountRequest
from mg_client.auth import AuthManager
from mg_client.exceptions import MemoGardenClientError
from mg_client.identity import OperatorAPI
from mg_client.opml import OPMLParseError, parse_opml
from mg_client.operators import OperatorReadsAPI
from mg_client.providers import ProviderAPI, RegisterProviderRequest
from mg_client.semantic import SemanticAPI
from mg_client.session import clear_session, load_session, resolve_jwt, save_session


# Top-level request fields accepted by POST /api/v1/providers (RFC-004 §12.2)
_ALLOWED_FIELDS = {"name", "module", "class", "interval", "enabled", "description", "config"}
_REQUIRED_FIELDS = ("name", "module", "class")

# Default platform per provider (RFC-012 §6.2); --platform overrides.
_PROVIDER_PLATFORMS = {"gmail": "google"}

# Builtin scope aliases (RFC-011 §4.1) — mirrors the server resolver.
# "Personal" (renamed from "Private", 2026-11-19) — clean break, no legacy alias.
_BUILTIN_SCOPE_ALIASES = {"triage": "Triage / Untrusted", "personal": "Personal"}

# JWT-gated command names (exact command key) — for the fail-fast message.
_SESSION_HINT = (
    "this verb requires an operator session — run `memogarden login` "
    "(or set MEMOGARDEN_JWT)"
)


class CliError(Exception):
    """User-facing CLI error (printed to stderr, exit code 1)."""


# ============================================================================
# Argument parser
# ============================================================================


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
        help="API base URL (default: MEMOGARDEN_URL, then the login session)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key, mg_sk_... (default: MEMOGARDEN_API_KEY environment variable)",
    )
    parser.add_argument(
        "--jwt",
        default=None,
        help="Operator JWT (default: MEMOGARDEN_JWT, then the login session)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # ------------------------------------------------------------------
    # Tier 0: session (bare verbs — the auth family)
    # ------------------------------------------------------------------

    login = sub.add_parser(
        "login",
        help="Authenticate as the operator and store the JWT session (RFC-015 §5.1)",
    )
    login.add_argument("--username", help="Operator username (prompted when omitted)")

    sub.add_parser("logout", help="Clear the stored operator session")
    sub.add_parser(
        "whoami",
        help="Show the presenting identity (GET /auth/me; JWT or API key)",
    )
    change_pw = sub.add_parser(
        "change-password",
        help="Rotate the operator password (JWT-gated; prompts when flags omitted)",
    )
    change_pw.add_argument("--current", help="Current password (prompted when omitted)")
    change_pw.add_argument("--new", help="New password (prompted when omitted)")

    # ------------------------------------------------------------------
    # register: shared RFC-005 mint verb across nouns
    # ------------------------------------------------------------------

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

    register_agent = register_sub.add_parser(
        "agent", help="Mint an Agent entity (POST /agents; JWT-gated, RFC-010)"
    )
    register_agent.add_argument("--label", required=True, help="Agent label, e.g. readly")

    register_key = register_sub.add_parser(
        "key", help="Mint an API key (POST /api-keys/; JWT-gated). Shows the raw key once."
    )
    register_key.add_argument("--label", required=True, help="Key name, e.g. zcode")
    register_key.add_argument(
        "--agent", help="Bind to this agent at creation (label or core_ uuid)"
    )

    register_scope = register_sub.add_parser(
        "scope", help="Mint a Scope entity (POST /mg create; JWT-gated, RFC-011 D1)"
    )
    register_scope.add_argument("--name", required=True, help="Scope label, e.g. Finances")
    register_scope.add_argument("--description", help="What the scope means")

    register_grant = register_sub.add_parser(
        "grant", help="Issue a scope grant (POST /grants; JWT-gated, RFC-011 §6)"
    )
    principal = register_grant.add_mutually_exclusive_group(required=True)
    principal.add_argument("--key", help="Key id (uuid)")
    principal.add_argument("--agent", help="Agent id (uuid; grants all bound keys)")
    register_grant.add_argument(
        "--scope", action="append", required=True,
        help="Scope reference (label, builtin alias, or uuid). Repeatable",
    )
    register_grant.add_argument("--expires", help="ISO 8601 expiry (optional)")

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    list_ = sub.add_parser("list", help="List a resource")
    list_sub = list_.add_subparsers(dest="resource", required=True)

    list_accounts = list_sub.add_parser("accounts", help="List a provider's accounts")
    list_accounts.add_argument("--provider", required=True, help="Registry slug")

    list_sub.add_parser("providers", help="List registered providers")
    list_sub.add_parser("agents", help="List minted agents (JWT-gated)")
    list_sub.add_parser("keys", help="List API keys (JWT-gated)")
    list_sub.add_parser("scopes", help="List scopes with item counts (JWT-gated)")

    list_grants = list_sub.add_parser("grants", help="List grants for a key or agent")
    grants_principal = list_grants.add_mutually_exclusive_group(required=True)
    grants_principal.add_argument("--key", help="Key id (uuid)")
    grants_principal.add_argument("--agent", help="Agent id (uuid)")

    list_runs = list_sub.add_parser("runs", help="List provider run history (RFC-015 §3.2)")
    list_runs.add_argument("--provider", required=True, help="Registry slug")
    list_runs.add_argument("--account", help="Filter to account id ('' = provider-level)")
    list_runs.add_argument("--status", help="running | success | failed")
    list_runs.add_argument("--limit", type=int, default=50, help="Row cap (default 50)")

    list_triage = list_sub.add_parser(
        "triage", help="List facts/entities labeled into a scope (JWT-gated)"
    )
    list_triage.add_argument(
        "--scope", default="triage", help="Scope ref (default: builtin triage)"
    )
    list_triage.add_argument("--account", help="Filter by metadata account stamp")
    list_triage.add_argument("--limit", type=int, default=200)

    list_actions = list_sub.add_parser(
        "actions", help="Query Action audit facts (RFC-005 full-op audit)"
    )
    list_actions.add_argument("--actor", help="Filter by actor uuid/username")
    list_actions.add_argument(
        "--actor-type", dest="actor_type",
        help="operator | agent | api_key",
    )
    list_actions.add_argument("--op", dest="operation", help="Filter by operation name")
    list_actions.add_argument("--limit", type=int, default=50)

    # ------------------------------------------------------------------
    # show / verify / remove / run / edit
    # ------------------------------------------------------------------

    def _add_account_resource(verb_sub, help_text):
        """Register the `account` resource under a verb (shared target flags)."""
        account_parser = verb_sub.add_parser("account", help=help_text)
        account_parser.add_argument("--provider", required=True, help="Registry slug")
        target = account_parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--id", help="Account id (uuid)")
        target.add_argument("--label", help="Resolve by operator label")
        target.add_argument("--subject", help="Resolve by upstream identity (email)")

    show = sub.add_parser("show", help="Show one resource")
    show_sub = show.add_subparsers(dest="resource", required=True)
    _add_account_resource(show_sub, "Show one account manifest")
    show_provider = show_sub.add_parser("provider", help="Show one provider manifest")
    show_provider.add_argument("--name", required=True, help="Registry slug")
    show_run = show_sub.add_parser("run", help="Show one provider run (RFC-015 §3.2)")
    show_run.add_argument("--id", required=True, help="Run id (uuid)")

    verify = sub.add_parser(
        "verify", help="Probe an account's credential health (re-activates on success)"
    )
    verify_sub = verify.add_subparsers(dest="resource", required=True)
    _add_account_resource(verify_sub, "verify")

    run_cmd = sub.add_parser("run", help="Trigger runs")
    run_sub = run_cmd.add_subparsers(dest="resource", required=True)
    _add_account_resource(run_sub, "Trigger a run for one account")
    run_provider = run_sub.add_parser(
        "provider", help="Trigger runs for one provider, even if not due"
    )
    run_provider.add_argument("--name", required=True, help="Registry slug")
    run_sub.add_parser(
        "providers", help="Run all due work (the heartbeat entry point, manual)"
    )

    edit = sub.add_parser("edit", help="Edit a resource (PATCH semantics)")
    edit_sub = edit.add_subparsers(dest="resource", required=True)

    edit_key = edit_sub.add_parser("key", help="Bind/unbind a key (PATCH /api-keys/{id})")
    edit_key.add_argument("--id", required=True, help="Key id (uuid)")
    bind_group = edit_key.add_mutually_exclusive_group(required=True)
    bind_group.add_argument("--bind", help="Agent label or core_ uuid to bind")
    bind_group.add_argument("--unbind", action="store_true", help="Unbind from its agent")

    edit_account = edit_sub.add_parser(
        "account", help="Update account label/status/config/interval (RFC-015 §5.3)"
    )
    edit_account.add_argument("--provider", required=True, help="Registry slug")
    acc_target = edit_account.add_mutually_exclusive_group(required=True)
    acc_target.add_argument("--id", help="Account id (uuid)")
    acc_target.add_argument("--label", help="Resolve by operator label")
    acc_target.add_argument("--subject", help="Resolve by upstream identity (email)")
    edit_account.add_argument("--new-label", dest="new_label", help="Set operator nickname")
    edit_account.add_argument(
        "--status", help="active | needs_reauth | revoked | disabled"
    )
    edit_account.add_argument("--interval", type=int, help="Seconds between runs (>= 60)")
    edit_account.add_argument(
        "--config", action="append", metavar="KEY=VALUE",
        help="Config entry (replaces the stored map when given). Repeatable",
    )

    remove_parser = sub.add_parser("remove", help="Remove a resource")
    remove_sub = remove_parser.add_subparsers(dest="resource", required=True)
    _add_account_resource(remove_sub, "Delete an account binding (HACM credential is untouched)")
    rm_key = remove_sub.add_parser("key", help="Revoke an API key (JWT-gated)")
    rm_key.add_argument("--id", required=True, help="Key id (uuid)")
    rm_scope = remove_sub.add_parser("scope", help="Forget a Scope entity (JWT-gated)")
    rm_scope.add_argument("--name", required=True, help="Scope label, alias, or uuid")
    rm_grant = remove_sub.add_parser("grant", help="Revoke a grant (JWT-gated)")
    rm_grant.add_argument("--id", required=True, help="Grant id (from list grants)")

    # ------------------------------------------------------------------
    # Tier 3: review & audit flows
    # ------------------------------------------------------------------

    enter_cmd = sub.add_parser("enter", help="Enter a scope (grant-gated, RFC-011 SG-4)")
    enter_cmd.add_argument("scope", help="Scope label, builtin alias, or uuid")

    leave_cmd = sub.add_parser("leave", help="Leave a scope (exclusive activation)")
    leave_cmd.add_argument("scope", help="Scope label, builtin alias, or uuid")

    relabel = sub.add_parser(
        "relabel", help="Change a fact's scope labels (sugar over /mg amend; D2-gated)"
    )
    relabel_sub = relabel.add_subparsers(dest="resource", required=True)
    relabel_fact = relabel_sub.add_parser("fact", help="Relabel one fact")
    relabel_fact.add_argument("--id", required=True, help="Fact uuid (soil_...)")
    relabel_fact.add_argument(
        "--add", action="append", default=[], help="Scope to add (repeatable)"
    )
    relabel_fact.add_argument(
        "--remove", action="append", default=[], help="Scope to remove (repeatable)"
    )

    query = sub.add_parser(
        "query", help="Generic /mg query passthrough (escape hatch)"
    )
    query.add_argument("--type", dest="type_name", required=True, help="Fact/entity type")
    query.add_argument(
        "--target-type", dest="target_type",
        choices=("fact", "entity", "relation"), default="fact",
    )
    query.add_argument(
        "--filter", action="append", metavar="KEY=VALUE",
        help="Equality filter (VALUE parsed as JSON when possible). Repeatable",
    )
    query.add_argument("--limit", type=int, default=50)

    track = sub.add_parser("track", help="Track an entity's provenance chain")
    track.add_argument("uuid", help="Entity uuid (core_...)")
    track.add_argument("--depth", type=int, help="Hop limit (default: unlimited)")

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


# ============================================================================
# Payload builders (unchanged contracts)
# ============================================================================


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


# ============================================================================
# Connection resolution
# ============================================================================


def _resolve_connection(args: argparse.Namespace) -> tuple[str, str]:
    """Resolve API URL and API key from flags or environment (agent transport)."""
    url = args.url or os.environ.get("MEMOGARDEN_URL") or (load_session() or {}).get("url")
    api_key = args.api_key or os.environ.get("MEMOGARDEN_API_KEY")
    if not url:
        raise CliError("no API URL: use --url or set MEMOGARDEN_URL")
    if not api_key:
        raise CliError("no API key: use --api-key or set MEMOGARDEN_API_KEY")
    return url, api_key


def _resolve_principal(args: argparse.Namespace) -> tuple[str, AuthManager]:
    """API key when present, else the operator JWT session.

    Tier-2 endpoints (providers, accounts, runs reads) accept any
    authenticated principal — the CLI prefers the agent transport and
    falls back to the login session so an operator never needs an agent
    key for provider ops.
    """
    if args.api_key or os.environ.get("MEMOGARDEN_API_KEY"):
        url, api_key = _resolve_connection(args)
        return url, AuthManager(api_key=api_key)
    return _resolve_jwt_auth(args)


def _resolve_jwt_auth(args: argparse.Namespace) -> tuple[str, AuthManager]:
    """Resolve URL + JWT AuthManager (flags > MEMOGARDEN_JWT > session file).

    Governed verbs fail fast here rather than upgrading silently — the
    operator session is issued by `login`, never assumed (RFC-010).
    """
    jwt, session_url = resolve_jwt(getattr(args, "jwt", None))
    url = (args.url or os.environ.get("MEMOGARDEN_URL")
           or session_url or (load_session() or {}).get("url"))
    if not url:
        raise CliError("no API URL: use --url or set MEMOGARDEN_URL")
    if not jwt:
        raise CliError(_SESSION_HINT)
    return url, AuthManager(jwt=jwt)


def _resolve_flexible_auth(args: argparse.Namespace) -> tuple[str, AuthManager]:
    """JWT when a session exists, API key otherwise (for /mg data verbs).

    enter/leave/query/track/relabel/list actions are usable by both the
    operator (JWT) and bound agents (API key) — same verbs, different
    principals.
    """
    jwt, session_url = resolve_jwt(getattr(args, "jwt", None))
    if jwt:
        url = args.url or os.environ.get("MEMOGARDEN_URL") or session_url
        if not url:
            raise CliError("no API URL: use --url or set MEMOGARDEN_URL")
        return url, AuthManager(jwt=jwt)
    url, api_key = _resolve_connection(args)
    return url, AuthManager(api_key=api_key)


async def _resolve_agent_id(api: OperatorAPI, reference: str) -> str:
    """Resolve an agent label to its entity uuid; pass uuids through."""
    if _is_uuid_ref(reference):
        return reference
    for agent in await api.list_agents():
        if (agent.get("label") or "") == reference:
            return agent["agent_id"]
    raise CliError(f"no agent with label '{reference}' — see `memogarden list agents`")


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _is_uuid_ref(ref: str) -> bool:
    """True for bare or core_-prefixed uuids (pass-through, no lookup)."""
    return _UUID_RE.match(ref) is not None or (
        ref.startswith("core_") and _UUID_RE.match(ref.removeprefix("core_")) is not None
    )


async def _resolve_scope_refs(reads: OperatorReadsAPI, refs: list[str]) -> list[str]:
    """Resolve scope references (label/alias/uuid) to plain uuids client-side.

    Uuid-shaped refs pass through locally — the scopes overview is
    JWT-only, so an agent key on enter/leave/relabel must not need it.
    Label/alias refs use the overview; without an operator session the
    error says exactly that instead of a server 401.
    """
    local = [ref for ref in refs if _is_uuid_ref(ref)]
    named = [ref for ref in refs if not _is_uuid_ref(ref)]
    resolved: list[str] = [ref.removeprefix("core_") for ref in local]

    if named:
        overview = await reads.scopes_overview()
        scopes = overview.get("scopes", [])
        for ref in named:
            label = _BUILTIN_SCOPE_ALIASES.get(ref, ref)
            match = None
            for scope in scopes:
                scope_label = scope.get("label") or (scope.get("data") or {}).get("label")
                scope_uuid = scope.get("uuid", "")
                if scope_label == label or ref in (scope_uuid, scope_uuid.removeprefix("core_")):
                    match = scope_uuid
                    break
            if match is None:
                raise CliError(
                    f"unknown scope '{ref}' — see `memogarden list scopes` "
                    "(labels need an operator session; pass the scope uuid "
                    "or run `memogarden login`)"
                )
            resolved.append(match.removeprefix("core_"))
    return resolved


async def _grant_principal_id(
    api: OperatorAPI, args: argparse.Namespace
) -> tuple[str, str]:
    """Resolve the grant principal to (field, uuid); labels resolve for agents."""
    if args.key:
        return "key_id", args.key
    return "agent_id", await _resolve_agent_id(api, args.agent)


# ============================================================================
# Async command implementations
# ============================================================================


def _feed_key(xml_url: str) -> str:
    """First 8 hex of SHA-256 of the feed URL — the tag the provider's
    User-Agent carries so a publisher can correspond about one feed
    (schemas/subscription.yaml; the provider recomputes it independently,
    this copy just stamps the fact)."""
    return hashlib.sha256(xml_url.encode("utf-8")).hexdigest()[:8]


async def _import_opml(
    url: str, auth: "AuthManager | str", path: str, dry_run: bool
) -> dict[str, Any]:
    """Import OPML subscriptions as Subscription facts (idempotent on
    xml_url: existing subscriptions are skipped, so re-import is safe)."""
    text = sys.stdin.read() if path == "-" else _read_file(path)
    try:
        feeds = parse_opml(text)
    except OPMLParseError as e:
        raise CliError(f"cannot parse OPML: {e}") from e

    existing: set[Any] = set()
    api = SemanticAPI(url, _coerce_auth(auth))
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


async def _relabel_fact(
    url: str, auth: AuthManager, fact_id: str, add_refs: list[str], remove_refs: list[str]
) -> dict[str, Any]:
    """Relabel one fact: fetch, recompute the label set, amend (RFC-015 §5.4).

    Amend overlays metadata wholesale, so the FULL desired scope list is
    sent — D2's before/after gate applies unchanged server-side.
    """
    if not add_refs and not remove_refs:
        raise CliError("nothing to do: pass --add and/or --remove")
    semantic = SemanticAPI(url, auth)
    reads = OperatorReadsAPI(url, auth)

    fact = await semantic.get(fact_id)
    current = ((fact.get("metadata") or {}).get("scopes")) or []
    additions = await _resolve_scope_refs(reads, add_refs)
    removals = await _resolve_scope_refs(reads, remove_refs)
    new_labels = sorted((set(current) | set(additions)) - set(removals))
    if new_labels == sorted(current):
        return {
            "fact": fact_id,
            "scopes": [f"core_{u}" for u in new_labels],
            "changed": False,
            "note": "labels unchanged after resolution",
        }

    result = await semantic.amend(
        target=fact_id,
        data=fact.get("data") or {},
        metadata={"scopes": new_labels},
    )
    return {
        "fact": fact_id,
        "scopes": [f"core_{u}" for u in new_labels],
        "changed": True,
        "amended": result,
    }


# ============================================================================
# Command handlers (sync; each resolves credentials and dispatches async)
# ============================================================================


def _cmd_login(args: argparse.Namespace) -> int:
    url = args.url or os.environ.get("MEMOGARDEN_URL") or (load_session() or {}).get("url")
    if not url:
        raise CliError("no API URL: use --url or set MEMOGARDEN_URL")
    username = args.username or input("username: ")
    password = getpass.getpass("password: ")
    result = asyncio.run(_login(url, username, password))
    path = save_session(url, result["access_token"], result.get("user"))
    print(json.dumps({
        "logged_in": result.get("user", {}).get("username", username),
        "url": url,
        "session": str(path),
        "note": "0600 file; governed verbs use it automatically",
    }, indent=2))
    return 0


async def _login(url: str, username: str, password: str) -> dict[str, Any]:
    # POST /auth/login is unauthenticated — OperatorAPI.login sends no
    # auth header regardless of the wrapper's AuthManager.
    api = OperatorAPI(url, AuthManager(api_key="mg_sk_unused"))
    return await api.login(username, password)


def _cmd_logout(args: argparse.Namespace) -> int:
    removed = clear_session()
    print(json.dumps({"logged_out": bool(removed)}))
    return 0


def _cmd_whoami(args: argparse.Namespace) -> int:
    jwt, session_url = resolve_jwt(getattr(args, "jwt", None))
    if jwt:
        url = args.url or os.environ.get("MEMOGARDEN_URL") or session_url
        auth = AuthManager(jwt=jwt)
    else:
        url, api_key = _resolve_connection(args)
        auth = AuthManager(api_key=api_key)
    result = asyncio.run(OperatorAPI(url, auth).me())
    print(json.dumps(result, indent=2))
    return 0


def _cmd_change_password(args: argparse.Namespace) -> int:
    url, auth = _resolve_jwt_auth(args)
    current = args.current or getpass.getpass("current password: ")
    new = args.new or getpass.getpass("new password: ")
    if not args.new:
        confirm = getpass.getpass("confirm new password: ")
        if confirm != new:
            raise CliError("passwords do not match")
    result = asyncio.run(OperatorAPI(url, auth).change_password(current, new))
    print(json.dumps(result, indent=2))
    return 0


def _cmd_register(args: argparse.Namespace) -> int:
    resource = args.resource

    if resource == "provider":
        payload = provider_payload(args)
        url, auth = _resolve_principal(args)
        try:
            result = asyncio.run(_register_provider(url, auth, payload))
        except ValidationError as e:
            raise CliError(f"invalid registration payload: {e}") from e
        print(json.dumps(result, indent=2))
        return 0

    if resource == "account":
        payload = account_payload(args)
        url, auth = _resolve_principal(args)
        try:
            result = asyncio.run(
                _register_account(url, auth, args.provider, payload)
            )
        except ValidationError as e:
            raise CliError(f"invalid account payload: {e}") from e
        print(json.dumps(result, indent=2))
        return 0

    if resource == "agent":
        url, auth = _resolve_jwt_auth(args)
        api = OperatorAPI(url, auth)
        result = asyncio.run(api.mint_agent(args.label))
        print(json.dumps(result, indent=2))
        return 0

    if resource == "key":
        url, auth = _resolve_jwt_auth(args)
        api = OperatorAPI(url, auth)
        agent_id = None
        if args.agent:
            agent_id = asyncio.run(_resolve_agent_id(api, args.agent))
        result = asyncio.run(api.mint_key(args.label, agent_id=agent_id))
        print(json.dumps(result, indent=2))
        raw = None
        if isinstance(result, dict):
            raw = result.get("key") or result.get("api_key") or result.get("raw_key")
        if raw:
            print(
                "\nSave this key now — it is shown only once "
                "(custody: operator-held env file, RFC-001 §6).",
                file=sys.stderr,
            )
        return 0

    if resource == "scope":
        url, auth = _resolve_jwt_auth(args)
        semantic = SemanticAPI(url, auth)
        data: dict[str, Any] = {"label": args.name}
        if args.description:
            data["description"] = args.description
        result = asyncio.run(semantic.create_entity("Scope", data=data))
        print(json.dumps(result, indent=2))
        return 0

    if resource == "grant":
        url, auth = _resolve_jwt_auth(args)
        api = OperatorAPI(url, auth)
        reads = OperatorReadsAPI(url, auth)
        scope_uuids = asyncio.run(_resolve_scope_refs(reads, args.scope))
        field, principal_id = asyncio.run(_grant_principal_id(api, args))
        created: list[dict[str, Any]] = []
        for scope_uuid in scope_uuids:
            kwargs = {"scope_uuid": scope_uuid, field: principal_id}
            if args.expires:
                kwargs["expires_at"] = args.expires
            created.extend(asyncio.run(api.issue_grant(**kwargs)).get("grants", []))
        print(json.dumps({"grants": created, "count": len(created)}, indent=2))
        return 0

    raise CliError(f"unsupported register resource: {resource}")  # pragma: no cover


def _cmd_list(args: argparse.Namespace) -> int:
    resource = args.resource

    if resource == "accounts":
        url, auth = _resolve_principal(args)
        accounts = asyncio.run(_list_accounts(url, auth, args.provider))
        print(json.dumps({"accounts": accounts, "count": len(accounts)}, indent=2))
        return 0

    if resource == "providers":
        url, auth = _resolve_principal(args)
        providers = asyncio.run(ProviderAPI(url, auth).list())
        print(json.dumps({"providers": providers, "count": len(providers)}, indent=2))
        return 0

    if resource == "agents":
        url, auth = _resolve_jwt_auth(args)
        agents = asyncio.run(OperatorAPI(url, auth).list_agents())
        print(json.dumps({"agents": agents, "count": len(agents)}, indent=2))
        return 0

    if resource == "keys":
        url, auth = _resolve_jwt_auth(args)
        keys = asyncio.run(OperatorAPI(url, auth).list_keys())
        print(json.dumps({"keys": keys, "count": len(keys)}, indent=2))
        return 0

    if resource == "scopes":
        url, auth = _resolve_jwt_auth(args)
        overview = asyncio.run(OperatorReadsAPI(url, auth).scopes_overview())
        print(json.dumps({
            "enforcement": overview.get("enforcement"),
            "scopes": overview.get("scopes", []),
        }, indent=2))
        return 0

    if resource == "grants":
        url, auth = _resolve_jwt_auth(args)
        api = OperatorAPI(url, auth)
        kwargs = {"key_id": args.key} if args.key else {"agent_id": args.agent}
        grants = asyncio.run(api.list_grants(**kwargs))
        print(json.dumps({"grants": grants, "count": len(grants)}, indent=2))
        return 0

    if resource == "runs":
        url, auth = _resolve_principal(args)
        api = ProviderAPI(url, auth)
        runs = asyncio.run(api.list_runs(
            args.provider,
            account_id=args.account,
            status=args.status,
            limit=args.limit,
        ))
        print(json.dumps({"runs": runs, "count": len(runs)}, indent=2))
        return 0

    if resource == "triage":
        url, auth = _resolve_jwt_auth(args)
        reads = OperatorReadsAPI(url, auth)
        result = asyncio.run(reads.triage(
            scope=args.scope, account=args.account, limit=args.limit,
        ))
        print(json.dumps(result, indent=2))
        return 0

    if resource == "actions":
        url, auth = _resolve_flexible_auth(args)
        semantic = SemanticAPI(url, auth)
        filters: dict[str, Any] = {}
        if args.actor:
            filters["actor"] = args.actor
        if args.actor_type:
            filters["actor_type"] = args.actor_type
        if args.operation:
            filters["operation"] = args.operation
        result = asyncio.run(semantic.query(
            type="Action", filters=filters or None,
            target_type="fact", count=args.limit,
        ))
        print(json.dumps(result, indent=2))
        return 0

    raise CliError(f"unsupported list resource: {resource}")  # pragma: no cover


def _cmd_show(args: argparse.Namespace) -> int:
    resource = args.resource

    if resource == "provider":
        url, auth = _resolve_principal(args)
        result = asyncio.run(ProviderAPI(url, auth).get(args.name))
        print(json.dumps(result, indent=2))
        return 0

    if resource == "run":
        url, auth = _resolve_principal(args)
        result = asyncio.run(ProviderAPI(url, auth).get_run(args.id))
        print(json.dumps(result, indent=2))
        return 0

    if resource == "account":
        url, api_key = _resolve_connection(args)
        result = asyncio.run(
            _account_action(url, api_key, args.provider, args, "show")
        )
        print(json.dumps(result, indent=2))
        return 0

    raise CliError(f"unsupported show resource: {resource}")  # pragma: no cover


def _cmd_verify(args: argparse.Namespace) -> int:
    url, auth = _resolve_principal(args)
    result = asyncio.run(_account_action(url, auth, args.provider, args, "verify"))
    print(json.dumps(result, indent=2))
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    resource = args.resource

    if resource == "account":
        url, auth = _resolve_principal(args)
        result = asyncio.run(_account_action(url, auth, args.provider, args, "remove"))
        print(json.dumps(result, indent=2))
        return 0

    if resource == "key":
        url, auth = _resolve_jwt_auth(args)
        api = OperatorAPI(url, auth)
        asyncio.run(api.revoke_key(args.id))
        print(json.dumps({"revoked": args.id}))
        return 0

    if resource == "scope":
        url, auth = _resolve_jwt_auth(args)
        semantic = SemanticAPI(url, auth)
        reads = OperatorReadsAPI(url, auth)
        scope_uuid = asyncio.run(_resolve_scope_refs(reads, [args.name]))[0]
        result = asyncio.run(semantic.forget(scope_uuid))
        print(json.dumps(result, indent=2))
        return 0

    if resource == "grant":
        url, auth = _resolve_jwt_auth(args)
        result = asyncio.run(OperatorAPI(url, auth).revoke_grant(args.id))
        print(json.dumps(result, indent=2))
        return 0

    raise CliError(f"unsupported remove resource: {resource}")  # pragma: no cover


def _cmd_run(args: argparse.Namespace) -> int:
    resource = args.resource

    if resource == "account":
        url, auth = _resolve_principal(args)
        result = asyncio.run(_account_action(url, auth, args.provider, args, "run"))
        print(json.dumps(result, indent=2))
        return 0

    if resource == "provider":
        url, auth = _resolve_principal(args)
        api = ProviderAPI(url, auth)
        result = asyncio.run(api.run(getattr(args, "name", "")))
        print(json.dumps(result, indent=2))
        return 0

    if resource == "providers":
        url, auth = _resolve_principal(args)
        api = ProviderAPI(url, auth)
        result = asyncio.run(api.run_due())
        print(json.dumps(result, indent=2))
        return 0

    raise CliError(f"unsupported run resource: {resource}")  # pragma: no cover


def _cmd_edit(args: argparse.Namespace) -> int:
    resource = args.resource

    if resource == "key":
        url, auth = _resolve_jwt_auth(args)
        api = OperatorAPI(url, auth)
        if args.unbind:
            result = asyncio.run(api.unbind_key(args.id))
        else:
            agent_id = asyncio.run(_resolve_agent_id(api, args.bind))
            result = asyncio.run(api.bind_key(args.id, agent_id))
        print(json.dumps(result, indent=2))
        return 0

    if resource == "account":
        url, auth = _resolve_principal(args)
        api = AccountAPI(url, auth)
        account_id = asyncio.run(_resolve_account_target(api, args.provider, args))
        fields: dict[str, Any] = {}
        if args.new_label:
            fields["label"] = args.new_label
        if args.status:
            fields["status"] = args.status
        if args.interval is not None:
            fields["interval"] = args.interval
        config = _kv_pairs(args.config)
        if config is not None:
            fields["config"] = config
        if not fields:
            raise CliError(
                "nothing to update: pass --new-label / --status / --interval / --config"
            )
        result = asyncio.run(
            api.update(args.provider, account_id, UpdateAccountRequest(**fields))
        )
        print(json.dumps(result, indent=2))
        return 0

    raise CliError(f"unsupported edit resource: {resource}")  # pragma: no cover


def _kv_pairs(items: list[str] | None) -> dict[str, Any] | None:
    """Parse repeated KEY=VALUE flags; None when no entries given."""
    if not items:
        return None
    config: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise CliError(f"expected KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        config[key] = _config_value(value)
    return config


def _cmd_enter(args: argparse.Namespace) -> int:
    url, auth = _resolve_flexible_auth(args)
    semantic = SemanticAPI(url, auth)
    reads = OperatorReadsAPI(url, auth)
    scope_uuid = asyncio.run(_resolve_scope_refs(reads, [args.scope]))[0]
    result = asyncio.run(semantic.enter(scope_uuid))
    print(json.dumps(result, indent=2))
    return 0


def _cmd_leave(args: argparse.Namespace) -> int:
    url, auth = _resolve_flexible_auth(args)
    semantic = SemanticAPI(url, auth)
    reads = OperatorReadsAPI(url, auth)
    scope_uuid = asyncio.run(_resolve_scope_refs(reads, [args.scope]))[0]
    result = asyncio.run(semantic.leave(scope_uuid))
    print(json.dumps(result, indent=2))
    return 0


def _cmd_relabel(args: argparse.Namespace) -> int:
    url, auth = _resolve_flexible_auth(args)
    result = asyncio.run(
        _relabel_fact(url, auth, args.id, args.add, args.remove)
    )
    print(json.dumps(result, indent=2))
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    url, auth = _resolve_flexible_auth(args)
    semantic = SemanticAPI(url, auth)
    filters = _kv_pairs(args.filter)
    result = asyncio.run(semantic.query(
        type=args.type_name,
        filters=filters,
        target_type=args.target_type,
        count=args.limit,
    ))
    print(json.dumps(result, indent=2))
    return 0


def _cmd_track(args: argparse.Namespace) -> int:
    url, auth = _resolve_flexible_auth(args)
    semantic = SemanticAPI(url, auth)
    result = asyncio.run(semantic.track(args.uuid, depth=args.depth))
    print(json.dumps(result, indent=2))
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    url, auth = _resolve_principal(args)
    result = asyncio.run(_import_opml(url, auth, args.file, args.dry_run))
    print(json.dumps(result, indent=2))
    return 0


def _coerce_auth(auth: "AuthManager | str") -> AuthManager:
    """Accept a raw mg_sk_ key (historical helper signature) or AuthManager."""
    return auth if isinstance(auth, AuthManager) else AuthManager(api_key=auth)


async def _register_provider(url: str, auth: "AuthManager | str", payload: dict[str, Any]) -> dict[str, Any]:
    """Send the registration request to the server."""
    api = ProviderAPI(url, _coerce_auth(auth))
    return await api.register(RegisterProviderRequest(**payload))


async def _register_account(url: str, auth: "AuthManager | str", provider: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Send the account registration request to the server (RFC-012)."""
    api = AccountAPI(url, _coerce_auth(auth))
    return await api.register(provider, RegisterAccountRequest(**payload))


async def _list_accounts(url: str, auth: "AuthManager | str", provider: str) -> "list[dict[str, Any]]":
    api = AccountAPI(url, _coerce_auth(auth))
    return await api.list(provider)


async def _account_action(
    url: str, auth: "AuthManager | str", provider: str, args: argparse.Namespace, action: str
) -> dict[str, Any] | None:
    """Resolve the account target and perform show/verify/remove/run."""
    api = AccountAPI(url, _coerce_auth(auth))
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


# ============================================================================
# Dispatch
# ============================================================================

_HANDLERS = {
    "login": _cmd_login,
    "logout": _cmd_logout,
    "whoami": _cmd_whoami,
    "change-password": _cmd_change_password,
    "register": _cmd_register,
    "list": _cmd_list,
    "show": _cmd_show,
    "verify": _cmd_verify,
    "remove": _cmd_remove,
    "run": _cmd_run,
    "edit": _cmd_edit,
    "enter": _cmd_enter,
    "leave": _cmd_leave,
    "relabel": _cmd_relabel,
    "query": _cmd_query,
    "track": _cmd_track,
    "import": _cmd_import,
}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list (defaults to sys.argv[1:])

    Returns:
        Process exit code: 0 success, 1 error (2 = argparse usage errors)
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = _HANDLERS.get(args.command)
    if handler is None:  # pragma: no cover - argparse restricts commands
        parser.error(f"unsupported command: {args.command}")
        return 2

    try:
        return handler(args)
    except CliError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except MemoGardenClientError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ValidationError as e:
        print(f"error: invalid input: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
