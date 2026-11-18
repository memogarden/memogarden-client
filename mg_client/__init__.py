"""MemoGarden Python Client SDK.

This package provides a Python SDK for interacting with the MemoGarden API.

Main classes:
    - MemoGardenClient: Async HTTP client with authentication
    - SyncMemoGardenClient: Synchronous wrapper for non-async contexts
    - SemanticAPI: Wrapper for /mg endpoint operations
    - OperatorAPI: identity, keys, grants, password (JWT-governed surface)
    - OperatorReadsAPI: triage/scopes/pending-review console reads
    - AuthManager: API key (X-API-Key) and operator JWT (Bearer) handling

Example:
    >>> from mg_client import MemoGardenClient
    >>> client = MemoGardenClient(
    ...     base_url="http://localhost:5000",
    ...     api_key="mg_sk_agent_..."
    ... )
    >>> scope = await client.semantic.create_scope(label="My Project")
"""

from mg_client.client import MemoGardenClient, SyncMemoGardenClient
from mg_client.identity import OperatorAPI
from mg_client.operators import OperatorReadsAPI
from mg_client.semantic import SemanticAPI
from mg_client.auth import AuthManager
from mg_client.sse import SSESubscription, connect_sse, SSEEvent
from mg_client.exceptions import (
    MemoGardenClientError,
    AuthenticationError,
    ResourceNotFoundError,
    ValidationError,
    ConflictError,
    NetworkError,
    RateLimitError,
    InternalServerError,
)

__version__ = "0.2.0"
__all__ = [
    "MemoGardenClient",
    "SyncMemoGardenClient",
    "SemanticAPI",
    "OperatorAPI",
    "OperatorReadsAPI",
    "AuthManager",
    "SSESubscription",
    "connect_sse",
    "SSEEvent",
    "MemoGardenClientError",
    "AuthenticationError",
    "ResourceNotFoundError",
    "ValidationError",
    "ConflictError",
    "NetworkError",
    "RateLimitError",
    "InternalServerError",
]
