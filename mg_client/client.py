"""Main MemoGarden client class.

Provides a unified interface for accessing MemoGarden API functionality.
"""

import logging
from typing import Any, Literal

import httpx

from mg_client.auth import AuthManager
from mg_client.semantic import SemanticAPI


logger = logging.getLogger(__name__)


class MemoGardenClient:
    """Main MemoGarden API client.

    Provides a unified interface for accessing MemoGarden functionality.
    Manages HTTP connection pooling, authentication, and API access.

    Attributes:
        base_url: API base URL
        auth: AuthManager instance
        timeout: Request timeout in seconds
        semantic: SemanticAPI wrapper instance

    Example:
        >>> client = MemoGardenClient(
        ...     base_url="http://localhost:5000",
        ...     api_key="mg_sk_agent_..."
        ... )
        >>> scope = await client.semantic.create_scope(label="My Project")
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        max_connections: int = 10,
        max_keepalive_connections: int = 5,
    ):
        """Initialize MemoGarden client.

        Args:
            base_url: API base URL (e.g., "http://localhost:5000")
            api_key: MemoGarden API key (starts with "mg_sk_")
            timeout: Request timeout in seconds (default: 30)
            max_connections: Max HTTP connections in pool
            max_keepalive_connections: Max keepalive connections

        Raises:
            ValueError: If base_url is empty or api_key is invalid
        """
        if not base_url:
            raise ValueError("base_url is required")

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._max_connections = max_connections
        self._max_keepalive_connections = max_keepalive_connections

        # Initialize authentication
        if api_key:
            self.auth = AuthManager(api_key=api_key)
        else:
            # Allow anonymous client for testing (will fail on actual requests)
            logger.warning("Creating client without API key - will fail on authenticated requests")
            self.auth = None

        # Initialize API wrappers
        self.semantic = SemanticAPI(
            base_url=self.base_url,
            auth=self.auth,
            timeout=timeout,
        ) if self.auth else None

        # Connection limits for HTTP client
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )

    async def health_check(self) -> dict[str, Any]:
        """Check if API is accessible.

        Returns:
            Health check response dict

        Raises:
            MemoGardenClientError: If health check fails
        """
        if not self.auth:
            return {"status": "no_auth", "api": "unknown"}

        try:
            # Try a simple query (will return empty if no auth issues)
            result = await self.semantic.query(type="_nonexistent_type_", count=1)
            return {"status": "ok", "api": "accessible"}
        except Exception as e:
            return {"status": "error", "api": str(e)}

    async def close(self):
        """Close client and cleanup resources.

        Called automatically when using async context manager.
        """
        # HTTPX clients are managed per-request in SemanticAPI
        # No persistent connections to close currently
        pass

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    def __repr__(self) -> str:
        auth_str = self.auth.get_auth_string() if self.auth else "no_auth"
        return f"MemoGardenClient(base_url='{self.base_url}', auth={auth_str})"


# Synchronous wrapper for convenience

class SyncMemoGardenClient:
    """Synchronous wrapper for MemoGarden client.

    Provides synchronous methods that run async operations in an event loop.
    Useful for scripts and non-async contexts.

    Attributes:
        _async_client: Underlying async MemoGardenClient

    Example:
        >>> client = SyncMemoGardenClient(
        ...     base_url="http://localhost:5000",
        ...     api_key="mg_sk_agent_..."
        ... )
        >>> scope = client.semantic.create_scope(label="My Project")
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        """Initialize synchronous client.

        Args:
            base_url: API base URL
            api_key: MemoGarden API key
            timeout: Request timeout in seconds
        """
        import asyncio

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self._async_client = MemoGardenClient(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )

    @property
    def semantic(self):
        """Get synchronous semantic API wrapper."""
        return _SyncSemanticAPI(self._async_client.semantic)

    def close(self):
        """Close client and cleanup."""
        self._loop.run_until_complete(self._async_client.close())
        self._loop.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def __repr__(self) -> str:
        return f"SyncMemoGardenClient({self._async_client!r})"


class _SyncSemanticAPI:
    """Synchronous wrapper for SemanticAPI.

    Converts async methods to sync by running in event loop.
    """

    def __init__(self, async_api: SemanticAPI):
        """Initialize sync wrapper.

        Args:
            async_api: Async SemanticAPI instance to wrap
        """
        self._async_api = async_api
        self._loop = None  # asyncio.run() creates new loop per call

    def _run_async(self, coro):
        """Run async coroutine in event loop."""
        import asyncio
        # Try to get running loop
        try:
            loop = asyncio.get_running_loop()
            # If we're in an async context, raise error
            raise RuntimeError(
                "SyncMemoGardenClient cannot be used in async context. "
                "Use MemoGardenClient instead."
            )
        except RuntimeError:
            # No running loop, create new one
            pass

        return asyncio.run(coro)

    def create_entity(self, *args, **kwargs):
        """Create entity (sync wrapper)."""
        return self._run_async(self._async_api.create_entity(*args, **kwargs))

    def create_scope(self, *args, **kwargs):
        """Create scope (sync wrapper)."""
        return self._run_async(self._async_api.create_scope(*args, **kwargs))

    def create_artifact(self, *args, **kwargs):
        """Create artifact (sync wrapper)."""
        return self._run_async(self._async_api.create_artifact(*args, **kwargs))

    def get(self, *args, **kwargs):
        """Get entity (sync wrapper)."""
        return self._run_async(self._async_api.get(*args, **kwargs))

    def edit(self, *args, **kwargs):
        """Edit entity (sync wrapper)."""
        return self._run_async(self._async_api.edit(*args, **kwargs))

    def forget(self, *args, **kwargs):
        """Forget entity (sync wrapper)."""
        return self._run_async(self._async_api.forget(*args, **kwargs))

    def query(self, *args, **kwargs):
        """Query entities (sync wrapper)."""
        return self._run_async(self._async_api.query(*args, **kwargs))

    def add(self, *args, **kwargs):
        """Add fact (sync wrapper)."""
        return self._run_async(self._async_api.add(*args, **kwargs))

    def send_message(self, *args, **kwargs):
        """Send message (sync wrapper)."""
        return self._run_async(self._async_api.send_message(*args, **kwargs))

    def amend(self, *args, **kwargs):
        """Amend fact (sync wrapper)."""
        return self._run_async(self._async_api.amend(*args, **kwargs))

    def link(self, *args, **kwargs):
        """Create relation (sync wrapper)."""
        return self._run_async(self._async_api.link(*args, **kwargs))

    def unlink(self, *args, **kwargs):
        """Unlink relation (sync wrapper)."""
        return self._run_async(self._async_api.unlink(*args, **kwargs))

    def query_relation(self, *args, **kwargs):
        """Query relations (sync wrapper)."""
        return self._run_async(self._async_api.query_relation(*args, **kwargs))

    def explore(self, *args, **kwargs):
        """Explore graph (sync wrapper)."""
        return self._run_async(self._async_api.explore(*args, **kwargs))

    def enter(self, *args, **kwargs):
        """Enter scope (sync wrapper)."""
        return self._run_async(self._async_api.enter(*args, **kwargs))

    def leave(self, *args, **kwargs):
        """Leave scope (sync wrapper)."""
        return self._run_async(self._async_api.leave(*args, **kwargs))

    def focus(self, *args, **kwargs):
        """Focus scope (sync wrapper)."""
        return self._run_async(self._async_api.focus(*args, **kwargs))

    def search(self, *args, **kwargs):
        """Search (sync wrapper)."""
        return self._run_async(self._async_api.search(*args, **kwargs))

    def track(self, *args, **kwargs):
        """Track (sync wrapper)."""
        return self._run_async(self._async_api.track(*args, **kwargs))

    def commit_artifact_delta(self, *args, **kwargs):
        """Commit artifact delta (sync wrapper)."""
        return self._run_async(self._async_api.commit_artifact_delta(*args, **kwargs))

    def get_artifact_at_commit(self, *args, **kwargs):
        """Get artifact at commit (sync wrapper)."""
        return self._run_async(self._async_api.get_artifact_at_commit(*args, **kwargs))

    def diff_commits(self, *args, **kwargs):
        """Diff commits (sync wrapper)."""
        return self._run_async(self._async_api.diff_commits(*args, **kwargs))

    def fold(self, *args, **kwargs):
        """Fold conversation (sync wrapper)."""
        return self._run_async(self._async_api.fold(*args, **kwargs))

    def get_conversation(self, *args, **kwargs):
        """Get conversation (sync wrapper)."""
        return self._run_async(self._async_api.get_conversation(*args, **kwargs))
