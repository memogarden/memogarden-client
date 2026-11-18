"""Operator session store (RFC-015 §5.1).

The CLI's Tier-0 session path: `memogarden login` exchanges the operator
password for a JWT and stores it in an operator-owned file — custody
follows issuance, mirroring the per-agent env files in
~/.config/memogarden/agents.d/. MEMOGARDEN_JWT overrides the file for
scripts; --url / MEMOGARDEN_URL always win over the stored URL.

The governed surface is JWT-only by design (RFC-010 INV-1, RFC-011),
so verbs that declare "jwt" fail fast with a pointer to `memogarden
login` rather than upgrading silently — no lazy minting.
"""

import json
import os
import stat
from pathlib import Path
from typing import Any

SESSION_FILENAME = "operator-session.json"
JWT_ENV_VAR = "MEMOGARDEN_JWT"


def session_path() -> Path:
    """Resolve the session file path (HOME-respectable for tests)."""
    config_root = os.environ.get(
        "MEMOGARDEN_CONFIG_DIR",
        str(Path.home() / ".config" / "memogarden"),
    )
    return Path(config_root) / SESSION_FILENAME


def save_session(url: str, access_token: str, user: dict[str, Any] | None = None) -> Path:
    """Persist an operator session (0600 file).

    Args:
        url: API base URL the token is valid for
        access_token: JWT access token
        user: Optional user record from the login response

    Returns:
        The session file path
    """
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": url.rstrip("/"),
        "access_token": access_token,
        "user": user or {},
    }
    # Create with 0600 from the first byte — write-then-chmod leaves a
    # window where the JWT is world-readable on the default umask.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(payload, indent=2))
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 (belt for pre-existing files)
    return path


def load_session() -> dict[str, Any] | None:
    """Load the stored session, or None when absent/corrupt."""
    path = session_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("access_token"):
        return None
    return data


def clear_session() -> bool:
    """Remove the stored session. Returns True if a session was removed."""
    path = session_path()
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def resolve_jwt(explicit: str | None = None) -> tuple[str, str | None]:
    """Resolve an operator JWT: explicit flag > env > session file.

    Returns:
        (jwt, session_url_or_None) — session_url is the URL stored with
        the token (a fallback for --url/MEMOGARDEN_URL, never a
        replacement).

    Raises:
        SystemExit-free CliError-text: returns ("", None) when unresolvable;
        the caller converts that into a 'requires memogarden login' error.
    """
    if explicit:
        return explicit, None
    env = os.environ.get(JWT_ENV_VAR)
    if env:
        return env, None
    session = load_session()
    if session:
        return session["access_token"], session.get("url")
    return "", None
