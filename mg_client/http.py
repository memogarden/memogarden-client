"""Shared HTTP transport for the operator API wrappers (RFC-015 §5).

The provider/account wrappers each carry their own `_request`; the
operator modules (identity.py, operators.py) share this one so the new
verb surface doesn't multiply the pattern further. Existing modules are
left untouched deliberately — this lands with the RFC-015 epic only.
"""

import logging
from typing import Any

import httpx

from mg_client.auth import AuthManager
from mg_client.exceptions import MemoGardenClientError, error_from_response

logger = logging.getLogger(__name__)


async def request_json(
    base_url: str,
    auth: AuthManager | None,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any] | list[Any]:
    """Send one authenticated request and return the response JSON.

    Args:
        base_url: API base URL
        auth: AuthManager (X-API-Key or Bearer per transport); None for
            unauthenticated endpoints (login)
        method: HTTP verb
        path: Endpoint path beginning with "/" (appended to base_url)
        payload: JSON body, or None for body-less requests
        timeout: Request timeout in seconds

    Raises:
        MemoGardenClientError: On API errors (mapped from RFC-006 envelope)
    """
    url = base_url.rstrip("/") + path
    headers: dict[str, str] = {}
    if auth is not None:
        headers.update(auth.get_headers())
    if payload is not None:
        headers["Content-Type"] = "application/json"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, json=payload, headers=headers)
            response.raise_for_status()
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
