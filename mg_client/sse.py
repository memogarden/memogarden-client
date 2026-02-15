"""Server-Sent Events (SSE) client for MemoGarden.

Session 20B: Event Integration
Provides SSE client for real-time updates from MemoGarden API:
- artifact_delta: Artifact modification events
- message_sent: New message events
- context_updated: ContextFrame change events
- frame_updated: Participant frame focus events
- scope_created: New scope events
- scope_modified: Scope modification events
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Set
from urllib.parse import urlencode

import httpx

from .exceptions import MemoGardenClientError


logger = logging.getLogger(__name__)


# Valid SSE event types (must match api/events.py::EVENT_TYPES)
EVENT_TYPES = {
    "artifact_delta",
    "message_sent",
    "context_updated",
    "frame_updated",
    "scope_created",
    "scope_modified",
    "relation_created",
    "relation_modified",
}


@dataclass
class SSEEvent:
    """Represents a Server-Sent Event.

    Attributes:
        type: Event type (artifact_delta, message_sent, etc.)
        data: Event payload (JSON-decoded dict)
    """
    type: str
    data: dict[str, Any]


@dataclass
class SSESubscription:
    """Represents an active SSE subscription.

    Manages connection lifecycle and event routing.
    """
    base_url: str
    scopes: Set[str] = field(default_factory=set)
    _client: Optional[httpx.AsyncClient] = field(default=None, init=False, repr=False)
    _task: Optional[asyncio.Task] = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False)

    # Event listeners by type
    _listeners: dict[str, list[Callable[[SSEEvent], Awaitable[None]]] = field(
        default_factory=dict, init=False, repr=False
    )

    async def __aenter__(self):
        """Enter context manager - start SSE connection."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager - close SSE connection."""
        await self.disconnect()

    async def connect(self) -> None:
        """Connect to SSE stream.

        Raises:
            MemoGardenClientError: If connection fails
        """
        if self._running:
            logger.warning("SSE connection already running")
            return

        self._running = True
        self._client = httpx.AsyncClient(timeout=None)

        # Build URL with scope subscription
        params = {}
        if self.scopes:
            params["scopes"] = ",".join(self.scopes)

        url = f"{self.base_url}/mg/events"
        if params:
            url += f"?{urlencode(params)}"

        logger.info(f"Connecting to SSE: {url}")

        # Create event streaming task
        self._task = asyncio.create_task(self._stream_events(url))

    async def disconnect(self) -> None:
        """Disconnect from SSE stream."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._client:
            await self._client.aclose()

        logger.info("SSE connection closed")

    async def _stream_events(self, url: str) -> None:
        """Stream events from SSE endpoint.

        Args:
            url: Full SSE URL with query parameters
        """
        try:
            async with self._client.stream("GET", url) as response:
                response.raise_for_status()

                # Process SSE stream
                async for line in response.aiter_lines():
                    if not self._running:
                        break

                    line = line.strip()

                    # Skip empty lines and comments
                    if not line or line.startswith(":"):
                        continue

                    # Parse event type
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                        continue

                    # Parse event data
                    if line.startswith("data: "):
                        try:
                            data_json = line[6:].strip()
                            data = json.loads(data_json)

                            # Dispatch to listeners
                            await self._dispatch_event(event_type, data)

                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse SSE data: {e}")

        except httpx.HTTPStatusError as e:
            if self._running:
                logger.error(f"SSE HTTP error: {e}")
                raise MemoGardenClientError(f"SSE connection failed: {e}") from e

        except Exception as e:
            if self._running:
                logger.error(f"SSE connection error: {e}")
                raise MemoGardenClientError(f"SSE error: {e}") from e

    async def _dispatch_event(self, event_type: str, data: dict) -> None:
        """Dispatch event to registered listeners.

        Args:
            event_type: Type of SSE event
            data: Event payload
        """
        event = SSEEvent(type=event_type, data=data)

        # Notify listeners for this event type
        listeners = self._listeners.get(event_type, [])
        if listeners:
            for listener in listeners:
                try:
                    result = listener(event)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error(
                        f"Error in event listener for {event_type}: {e}"
                    )

    def on(
        self,
        event_type: str,
        listener: Callable[[SSEEvent], Awaitable[None]]
    ) -> Callable[[SSEEvent], Awaitable[None]]:
        """Register event listener.

        Args:
            event_type: Event type to listen for (artifact_delta, message_sent, etc.)
            listener: Callback function that receives SSEEvent

        Returns:
            The listener function (for removal if needed later)

        Raises:
            ValueError: If event_type is not recognized
        """
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event type: {event_type}")

        if event_type not in self._listeners:
            self._listeners[event_type] = []

        self._listeners[event_type].append(listener)
        return listener

    def off(
        self,
        event_type: str,
        listener: Callable[[SSEEvent], Awaitable[None]]
    ) -> None:
        """Unregister event listener.

        Args:
            event_type: Event type
            listener: Callback function to remove
        """
        listeners = self._listeners.get(event_type, [])
        if listener in listeners:
            listeners.remove(listener)

    @property
    def is_connected(self) -> bool:
        """Check if SSE connection is active."""
        return self._running and self._task is not None


async def connect_sse(
    base_url: str,
    auth_manager: Optional[Any] = None,
    scopes: Optional[Set[str]] = None,
) -> SSESubscription:
    """Convenience function to create and connect SSE subscription.

    Args:
        base_url: MemoGarden API base URL
        auth_manager: Optional auth manager for API key/JWT
        scopes: Optional set of scope UUIDs to subscribe to

    Returns:
        Connected SSESubscription

    Example:
        >>> async with connect_sse(
        ...     base_url="http://localhost:5000",
        ...     scopes=["core_abc"]
        ... ) as sse:
        ...     @sse.on("artifact_delta")
        ...     def on_artifact_delta(event):
        ...         print(f"Artifact changed: {event.data}")
    """
    subscription = SSESubscription(base_url=base_url, scopes=scopes or set())

    # Add auth if provided
    if auth_manager:
        subscription._client = httpx.AsyncClient(
            headers=auth_manager.get_headers()
        )

    await subscription.connect()
    return subscription
