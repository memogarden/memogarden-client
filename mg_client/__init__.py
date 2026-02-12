"""MemoGarden Python Client SDK.

This package provides a Python SDK for interacting with the MemoGarden API.

Main classes:
    - MemoGardenClient: Async HTTP client with authentication
    - SyncMemoGardenClient: Synchronous wrapper for non-async contexts
    - SemanticAPI: Wrapper for /mg endpoint operations
    - AuthManager: JWT/API key handling

Example:
    >>> from mg_client import MemoGardenClient
    >>> client = MemoGardenClient(
    ...     base_url="http://localhost:5000",
    ...     api_key="mg_sk_agent_..."
    ... )
    >>> scope = await client.semantic.create_scope(label="My Project")
"""

from mg_client.client import MemoGardenClient, SyncMemoGardenClient
from mg_client.semantic import SemanticAPI
from mg_client.auth import AuthManager
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

__version__ = "0.1.0"
__all__ = [
    "MemoGardenClient",
    "SyncMemoGardenClient",
    "SemanticAPI",
    "AuthManager",
    "MemoGardenClientError",
    "AuthenticationError",
    "ResourceNotFoundError",
    "ValidationError",
    "ConflictError",
    "NetworkError",
    "RateLimitError",
    "InternalServerError",
]
