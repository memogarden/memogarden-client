"""Provider registry API wrapper for MemoGarden client.

Thin client for the server-side provider registry (/api/v1/providers).
Providers execute inside the server process; this module only manages
registration and status. See RFC-004 §12.2 for the schema.
"""

import logging
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from mg_client.auth import AuthManager
from mg_client.exceptions import (
    MemoGardenClientError,
    error_from_response,
)


logger = logging.getLogger(__name__)


class RegisterProviderRequest(BaseModel):
    """Payload for POST /api/v1/providers (RFC-004 §12.2).

    Attributes:
        name: Registry slug, used in URL paths
        module: Importable Python module containing the provider
        class_name: Class within module conforming to the Provider Protocol
        interval: Seconds between runs (server heartbeat checks this)
        enabled: Whether the server may execute the provider
        description: Human-readable summary for `list providers`
        config: Provider-specific settings; references only, never secrets
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    module: str = Field(min_length=1)
    class_name: str = Field(alias="class", min_length=1)
    interval: int = Field(default=3600, ge=60)
    enabled: bool = True
    description: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class ProviderAPI:
    """Wrapper for the provider registry API (/api/v1/providers).

    Attributes:
        base_url: API base URL (e.g., "http://localhost:8600")
        auth: AuthManager instance for authentication
        timeout: Request timeout in seconds

    Example:
        >>> api = ProviderAPI(base_url="http://localhost:8600", auth=auth)
        >>> manifest = await api.register(
        ...     RegisterProviderRequest(name="gmail", module="gmail_importer.provider",
        ...                             class_name="GmailProvider"))
    """

    def __init__(
        self,
        base_url: str,
        auth: AuthManager,
        timeout: float = 30.0,
    ):
        """Initialize provider API wrapper.

        Args:
            base_url: API base URL
            auth: AuthManager instance for authentication
            timeout: Request timeout in seconds (default: 30)
        """
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = timeout
        self._endpoint = f"{self.base_url}/api/v1/providers"

    async def register(self, request: RegisterProviderRequest) -> dict[str, Any]:
        """Register a provider with the server.

        Args:
            request: Registration payload

        Returns:
            Stored manifest including server-assigned fields
            (capabilities, registered_at, last_run_at, ...)

        Raises:
            ConflictError: If the provider name is already registered
            ValidationError: If the payload fails server validation
            MemoGardenClientError: On other API errors
        """
        payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
        return await self._request("POST", self._endpoint, payload)

    async def _request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Send a request to the provider registry API.

        Args:
            method: HTTP method
            url: Full endpoint URL
            payload: JSON request body

        Returns:
            Response JSON dict

        Raises:
            MemoGardenClientError: On API errors (mapped from RFC-006 envelope)
        """
        headers = self.auth.get_headers()
        headers["Content-Type"] = "application/json"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(method, url, json=payload, headers=headers)
                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            # Try to parse error response
            try:
                error_data = e.response.json()
                raise error_from_response(error_data) from None
            except ValueError:
                # Non-JSON body (e.g. Flask HTML error page) — keep the message short
                raise MemoGardenClientError(
                    f"HTTP {e.response.status_code} {e.response.reason_phrase}"
                ) from e

        except httpx.RequestError as e:
            raise MemoGardenClientError(f"Request failed: {e}") from e
