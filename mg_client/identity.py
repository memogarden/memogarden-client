"""Operator identity & authority API wrapper (RFC-015 §5.2, Tier 1).

Every endpoint here is JWT-only on the server — "identity is issued,
never assumed" (RFC-010 INV-1), grants are operator-issued (RFC-011 §6),
and scope minting is `scope_mint_denied` to API keys (RFC-011 D1). The
wrapper therefore speaks Bearer; an API-key AuthManager works for
`whoami` (GET /auth/me accepts both) and nothing else.
"""

from typing import Any

from pydantic import BaseModel, Field

from mg_client.auth import AuthManager
from mg_client.http import request_json


class AgentCreateRequest(BaseModel):
    """Payload for POST /agents (RFC-010 §4)."""

    label: str = Field(min_length=1, max_length=100)


class APIKeyCreateRequest(BaseModel):
    """Payload for POST /api-keys/ (RFC-010 §6)."""

    name: str = Field(min_length=1, max_length=100)
    agent_id: str | None = Field(default=None, description="Bind at creation")


class GrantCreateRequest(BaseModel):
    """Payload for POST /grants (RFC-011 §6): exactly one principal."""

    key_id: str | None = None
    agent_id: str | None = None
    scope_uuid: str = Field(min_length=1)
    expires_at: str | None = None


class OperatorAPI:
    """Operator-governed API wrapper: identity, keys, grants, password."""

    def __init__(self, base_url: str, auth: AuthManager, timeout: float = 30.0):
        """Initialize the operator API wrapper.

        Args:
            base_url: API base URL
            auth: AuthManager — pass AuthManager(jwt=...) for the governed
                verbs; AuthManager(api_key=...) only reaches whoami
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = timeout

    # ---- session / account (RFC-015 §5.1) ----

    async def login(self, username: str, password: str) -> dict[str, Any]:
        """Exchange credentials for a JWT (POST /auth/login). No auth.

        Returns:
            {"access_token", "token_type", "user": {...}}
        """
        return await request_json(
            self.base_url, None, "POST", "/auth/login",
            {"username": username, "password": password}, self.timeout,
        )

    async def change_password(self, current_password: str, new_password: str) -> dict[str, Any]:
        """Rotate the operator's password (POST /auth/change-password). JWT."""
        return await request_json(
            self.base_url, self.auth, "POST", "/auth/change-password",
            {"current_password": current_password, "new_password": new_password},
            self.timeout,
        )

    async def me(self) -> dict[str, Any]:
        """Introspect the presenting identity (GET /auth/me). Any transport."""
        return await request_json(
            self.base_url, self.auth, "GET", "/auth/me", None, self.timeout,
        )

    # ---- agents (RFC-010) ----

    async def mint_agent(self, label: str) -> dict[str, Any]:
        """Mint an Agent entity (POST /agents). JWT."""
        payload = AgentCreateRequest(label=label).model_dump(mode="json")
        return await request_json(
            self.base_url, self.auth, "POST", "/agents", payload, self.timeout,
        )

    async def list_agents(self) -> list[dict[str, Any]]:
        """List operator-minted agents (GET /agents). JWT."""
        result = await request_json(
            self.base_url, self.auth, "GET", "/agents", None, self.timeout,
        )
        return result if isinstance(result, list) else result.get("agents", [])

    # ---- API keys (RFC-010) ----

    async def mint_key(self, name: str, agent_id: str | None = None) -> dict[str, Any]:
        """Mint an API key, optionally bound at creation (POST /api-keys/). JWT.

        The response is the ONLY time the raw key material is visible.
        """
        payload = APIKeyCreateRequest(name=name, agent_id=agent_id).model_dump(
            mode="json", exclude_none=True
        )
        return await request_json(
            self.base_url, self.auth, "POST", "/api-keys/", payload, self.timeout,
        )

    async def list_keys(self) -> list[dict[str, Any]]:
        """List API keys (GET /api-keys/). JWT."""
        result = await request_json(
            self.base_url, self.auth, "GET", "/api-keys/", None, self.timeout,
        )
        if isinstance(result, list):
            return result
        return result.get("api_keys", result.get("keys", []))

    async def bind_key(self, key_id: str, agent_id: str) -> dict[str, Any]:
        """Bind (or re-bind) a key to an agent (PATCH /api-keys/{id}). JWT."""
        return await self._patch_key(key_id, agent_id)

    async def unbind_key(self, key_id: str) -> dict[str, Any]:
        """Unbind a key from its agent (PATCH /api-keys/{id}, agent_id null). JWT."""
        return await self._patch_key(key_id, None, explicit_unbind=True)

    async def _patch_key(
        self, key_id: str, agent_id: str | None, explicit_unbind: bool = False
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if explicit_unbind:
            payload["agent_id"] = None  # present-and-null = unbind
        else:
            payload["agent_id"] = agent_id
        return await request_json(
            self.base_url, self.auth, "PATCH",
            f"/api-keys/{key_id}", payload, self.timeout,
        )

    async def revoke_key(self, key_id: str) -> None:
        """Revoke an API key (DELETE /api-keys/{id}). JWT."""
        await request_json(
            self.base_url, self.auth, "DELETE",
            f"/api-keys/{key_id}", None, self.timeout,
        )

    # ---- grants (RFC-011) ----

    async def issue_grant(
        self,
        scope_uuid: str,
        key_id: str | None = None,
        agent_id: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        """Issue a scope grant to a key or agent (POST /grants). JWT.

        Agent grants apply to all keys bound to that agent; future
        bindings inherit at bind time.
        """
        payload = GrantCreateRequest(
            key_id=key_id, agent_id=agent_id, scope_uuid=scope_uuid,
            expires_at=expires_at,
        ).model_dump(mode="json", exclude_none=True)
        return await request_json(
            self.base_url, self.auth, "POST", "/grants", payload, self.timeout,
        )

    async def list_grants(
        self, key_id: str | None = None, agent_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List grants for a key or agent (GET /grants). JWT."""
        query = f"?{'key_id=' + key_id if key_id else 'agent_id=' + (agent_id or '')}"
        result = await request_json(
            self.base_url, self.auth, "GET", f"/grants{query}", None, self.timeout,
        )
        return result if isinstance(result, list) else result.get("grants", [])

    async def revoke_grant(self, grant_id: str) -> dict[str, Any]:
        """Revoke a grant by id (DELETE /grants/{id}). JWT."""
        return await request_json(
            self.base_url, self.auth, "DELETE",
            f"/grants/{grant_id}", None, self.timeout,
        )
