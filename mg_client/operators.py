"""Operator console reads wrapper (RFC-015 §5.4, Tier 3).

JWT-only server-side: the triage queue, the scopes/grants/policies
overview, and the occurrence review queue. These back the review-loop
CLI verbs (`list triage`, `list scopes`, `enter`/`leave`, `relabel`).
"""

from typing import Any

from mg_client.auth import AuthManager
from mg_client.http import request_json


class OperatorReadsAPI:
    """Operator-only read wrappers backing the review/audit verbs."""

    def __init__(self, base_url: str, auth: AuthManager, timeout: float = 30.0):
        """Initialize the operator reads wrapper.

        Args:
            base_url: API base URL
            auth: AuthManager — JWT required (endpoints are JWT-only)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = timeout

    async def triage(
        self,
        scope: str | None = None,
        account: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        """List facts/entities labeled into a scope (GET /api/v1/triage). JWT.

        Defaults to the builtin Triage scope; `scope` accepts uuid,
        alias, or label (RFC-015 §3.3).
        """
        params: dict[str, str] = {}
        if scope:
            params["scope"] = scope
        if account:
            params["account"] = account
        if limit is not None:
            params["limit"] = str(limit)
        if offset is not None:
            params["offset"] = str(offset)
        query = "&".join(f"{k}={v}" for k, v in params.items())
        path = "/api/v1/triage" + (f"?{query}" if query else "")
        return await request_json(
            self.base_url, self.auth, "GET", path, None, self.timeout,
        )

    async def scopes_overview(self) -> dict[str, Any]:
        """Authority overview: scopes, counts, grants, policies (JWT)."""
        return await request_json(
            self.base_url, self.auth, "GET", "/api/v1/scopes/overview",
            None, self.timeout,
        )

    async def pending_review(
        self, window_days: int | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        """Events awaiting occurrence review (GET /api/v1/events/pending-review). JWT."""
        params: dict[str, str] = {}
        if window_days is not None:
            params["window_days"] = str(window_days)
        if limit is not None:
            params["limit"] = str(limit)
        query = "&".join(f"{k}={v}" for k, v in params.items())
        path = "/api/v1/events/pending-review" + (f"?{query}" if query else "")
        return await request_json(
            self.base_url, self.auth, "GET", path, None, self.timeout,
        )
