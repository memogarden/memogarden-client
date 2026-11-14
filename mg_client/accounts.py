"""Provider account API wrapper for MemoGarden client (RFC-012).

Thin client for provider accounts (/api/v1/providers/{name}/accounts).
Accounts bind a provider to a verified upstream identity; tokens stay in
HACM on the host and never transit the API — this wrapper moves credential
REFERENCES only.
"""

import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

from mg_client.auth import AuthManager
from mg_client.exceptions import (
    MemoGardenClientError,
    error_from_response,
)


logger = logging.getLogger(__name__)


class RegisterAccountRequest(BaseModel):
    """Payload for POST /api/v1/providers/{name}/accounts (RFC-012 §6.1)."""

    platform: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    subject: str = Field(min_length=1, max_length=255)
    label: str | None = Field(default=None, max_length=120)
    credential_ref: str | None = Field(default=None, min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)


class AccountAPI:
    """Wrapper for the provider accounts API (/api/v1/providers/{name}/accounts).

    Example:
        >>> api = AccountAPI(base_url="http://localhost:8600", auth=auth)
        >>> account = await api.register("gmail",
        ...     RegisterAccountRequest(platform="google", subject="you@gmail.com"))
    """

    def __init__(self, base_url: str, auth: AuthManager, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = timeout

    def _endpoint(self, provider: str, suffix: str = "") -> str:
        return f"{self.base_url}/api/v1/providers/{provider}/accounts{suffix}"

    async def register(self, provider: str, request: RegisterAccountRequest) -> dict[str, Any]:
        """Bind an upstream account to a provider (credential reference only)."""
        payload = request.model_dump(mode="json", exclude_none=True)
        return await self._request("POST", self._endpoint(provider), payload)

    async def list(self, provider: str) -> list[dict[str, Any]]:
        """List the provider's account bindings."""
        body = await self._request("GET", self._endpoint(provider), {})
        return body.get("accounts", [])

    async def get(self, provider: str, account_id: str) -> dict[str, Any]:
        """Fetch one account manifest."""
        return await self._request("GET", self._endpoint(provider, f"/{account_id}"), {})

    async def verify(self, provider: str, account_id: str) -> dict[str, Any]:
        """Probe credential health server-side (re-activates on success)."""
        return await self._request(
            "POST", self._endpoint(provider, f"/{account_id}/verify"), {}
        )

    async def run(self, provider: str, account_id: str) -> dict[str, Any]:
        """Trigger a run for one account (202 with the run id)."""
        return await self._request(
            "POST", self._endpoint(provider, f"/{account_id}/run"), {}
        )

    async def remove(self, provider: str, account_id: str) -> None:
        """Delete the account binding (registry row only; HACM is untouched)."""
        await self._request("DELETE", self._endpoint(provider, f"/{account_id}"), {})

    async def _request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any],
    ) -> Any:
        headers = self.auth.get_headers()
        if payload:
            headers["Content-Type"] = "application/json"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method, url, json=payload or None, headers=headers
                )
                if response.status_code == 204:
                    return None
                response.raise_for_status()
                if not response.content:
                    return None
                return response.json()

        except httpx.HTTPStatusError as e:
            try:
                error_data = e.response.json()
                raise error_from_response(error_data) from None
            except ValueError:
                raise MemoGardenClientError(
                    f"HTTP {e.response.status_code} {e.response.reason_phrase}"
                ) from e

        except httpx.RequestError as e:
            raise MemoGardenClientError(f"Request failed: {e}") from e
