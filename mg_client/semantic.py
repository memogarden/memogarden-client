"""Semantic API wrapper for MemoGarden client.

Provides high-level methods for each Semantic API operation.
Handles request building, response parsing, and error handling.
"""

import logging
from typing import Any, Literal

import httpx

from mg_client.auth import AuthManager
from mg_client.exceptions import (
    ConflictError,
    MemoGardenClientError,
    ResourceNotFoundError,
    ValidationError,
    error_from_response,
)
from mg_client.models import (
    AddRequest,
    AmendRequest,
    CommitArtifactRequest,
    CreateRequest,
    DiffCommitsRequest,
    EditRequest,
    EnterRequest,
    ExploreRequest,
    FocusRequest,
    FoldRequest,
    ForgetRequest,
    GetArtifactAtCommitRequest,
    GetRequest,
    LeaveRequest,
    LinkRequest,
    QueryRelationRequest,
    QueryRequest,
    SearchRequest,
    SemanticResponse,
    TrackRequest,
    UnlinkRequest,
    ActorType,
)

logger = logging.getLogger(__name__)


class SemanticAPI:
    """Wrapper for MemoGarden Semantic API operations.

    Provides type-safe methods for each Semantic API verb.
    Handles HTTP communication, error detection, and response parsing.

    Attributes:
        base_url: API base URL (e.g., "http://localhost:5000")
        auth: AuthManager instance for authentication
        timeout: Request timeout in seconds

    Example:
        >>> auth = AuthManager(api_key="mg_sk_agent_...")
        >>> api = SemanticAPI(base_url="http://localhost:5000", auth=auth)
        >>> scope = api.create_scope(label="My Project")
        >>> print(scope.uuid)
    """

    def __init__(
        self,
        base_url: str,
        auth: AuthManager,
        timeout: float = 30.0,
    ):
        """Initialize Semantic API wrapper.

        Args:
            base_url: API base URL (e.g., "http://localhost:5000")
            auth: AuthManager instance for authentication
            timeout: Request timeout in seconds (default: 30)
        """
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = timeout
        self._endpoint = f"{self.base_url}/mg"

    async def _request(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Send request to Semantic API.

        Args:
            request_data: Request dict with 'op' field

        Returns:
            Response result dict

        Raises:
            MemoGardenClientError: On API errors
        """
        headers = self.auth.get_headers()
        headers["Content-Type"] = "application/json"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self._endpoint,
                    json=request_data,
                    headers=headers,
                )
                response.raise_for_status()
                response_data = response.json()

        except httpx.HTTPStatusError as e:
            # Try to parse error response
            try:
                error_data = e.response.json()
                raise error_from_response(error_data) from None
            except ValueError:
                raise MemoGardenClientError(
                    f"HTTP {e.response.status_code}: {e.response.text}"
                ) from e

        except httpx.RequestError as e:
            raise MemoGardenClientError(f"Request failed: {e}") from e

        # Check response envelope
        parsed = SemanticResponse(**response_data)
        if not parsed.ok:
            if parsed.error:
                raise error_from_response({"error": parsed.error})
            raise MemoGardenClientError("Operation failed")

        return parsed.result or {}

    # ========================================================================
    # Core Entity Operations
    # ========================================================================

    async def create_entity(
        self,
        type: str,
        data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new entity.

        Args:
            type: Entity type (e.g., "Scope", "Artifact", "Contact")
            data: Entity-specific fields
            metadata: Optional app-defined metadata

        Returns:
            Created entity dict with uuid, type, hash, etc.

        Example:
            >>> scope = await api.create_entity(
            ...     type="Scope",
            ...     data={"label": "My Project", "active_participants": []}
            ... )
        """
        request = CreateRequest(type=type, data=data or {}, metadata=metadata)
        return await self._request(request.model_dump())

    async def create_scope(
        self,
        label: str,
        active_participants: list[str] | None = None,
        artifact_uuids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a Scope entity.

        Args:
            label: Human-readable name
            active_participants: Optional list of participant UUIDs
            artifact_uuids: Optional list of artifact UUIDs
            metadata: Optional metadata

        Returns:
            Created Scope entity dict
        """
        data = {"label": label}
        if active_participants:
            data["active_participants"] = active_participants
        if artifact_uuids:
            data["artifact_uuids"] = artifact_uuids

        return await self.create_entity("Scope", data=data, metadata=metadata)

    async def create_artifact(
        self,
        label: str,
        content: str,
        content_type: str = "text/plain",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create an Artifact entity.

        Args:
            label: Artifact name/label
            content: Artifact content
            content_type: MIME type (default: text/plain)
            metadata: Optional metadata

        Returns:
            Created Artifact entity dict
        """
        data = {
            "label": label,
            "content": content,
            "content_type": content_type,
        }
        return await self.create_entity("Artifact", data=data, metadata=metadata)

    async def get(self, target: str) -> dict[str, Any]:
        """Get an entity, fact, or relation by UUID.

        Args:
            target: UUID with or without prefix

        Returns:
            Entity/fact/relation dict

        Example:
            >>> entity = await api.get("core_abc123...")
        """
        request = GetRequest(target=target)
        return await self._request(request.model_dump())

    async def edit(
        self,
        target: str,
        set_fields: dict[str, Any] | None = None,
        unset: list[str] | None = None,
    ) -> dict[str, Any]:
        """Edit an entity or relation.

        Args:
            target: Entity or relation UUID
            set_fields: Fields to add/update
            unset: Field names to remove

        Returns:
            Updated entity dict

        Example:
            >>> await api.edit(
            ...     target="core_abc123...",
            ...     set_fields={"data.label": "New Label"}
            ... )
        """
        request = EditRequest(target=target, set=set_fields, unset=unset)
        return await self._request(request.model_dump())

    async def forget(self, target: str) -> dict[str, Any]:
        """Soft delete an entity.

        Args:
            target: Entity UUID to forget

        Returns:
            Confirmation dict
        """
        request = ForgetRequest(target=target)
        return await self._request(request.model_dump())

    async def query(
        self,
        type: str | None = None,
        filters: dict[str, Any] | None = None,
        target_type: Literal["entity", "fact", "relation"] = "entity",
        start_index: int = 0,
        count: int = 20,
    ) -> dict[str, Any]:
        """Query entities, facts, or relations.

        Args:
            type: Filter by exact type name
            filters: Field-value filters (equality)
            target_type: Query target type
            start_index: Pagination start
            count: Max results

        Returns:
            Query result with results list, total, start_index, count

        Example:
            >>> result = await api.query(
            ...     type="Scope",
            ...     filters={"data.label": "My Project"}
            ... )
        """
        request = QueryRequest(
            type=type,
            filters=filters,
            target_type=target_type,
            start_index=start_index,
            count=count,
        )
        return await self._request(request.model_dump())

    # ========================================================================
    # Fact Operations (Soil)
    # ========================================================================

    async def add(
        self,
        type: str,
        data: dict[str, Any],
        canonical_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add a fact (Item) to Soil.

        Args:
            type: Item type (e.g., "Message", "Note", "ToolCall")
            data: Item-specific fields
            canonical_at: Optional subjective time
            metadata: Optional metadata

        Returns:
            Created Item dict
        """
        request = AddRequest(type=type, data=data, canonical_at=canonical_at, metadata=metadata)
        return await self._request(request.model_dump())

    async def send_message(
        self,
        log_uuid: str,
        content: str,
        sender: str,
        fragments: list[dict[str, Any]] | None = None,
        canonical_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a message to a conversation log.

        Args:
            log_uuid: ConversationLog UUID to append to
            content: Message content
            sender: Sender identifier
            fragments: Optional fragment references
            canonical_at: Optional subjective time
            metadata: Optional metadata

        Returns:
            Created Message Item dict
        """
        data = {
            "log_uuid": log_uuid,
            "description": content,
            "sender": sender,
        }
        if fragments:
            data["fragments"] = fragments

        return await self.add(
            type="Message",
            data=data,
            canonical_at=canonical_at,
            metadata=metadata,
        )

    async def amend(
        self,
        target: str,
        data: dict[str, Any],
        canonical_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Amend a fact (create superseding fact).

        Args:
            target: Item UUID to amend
            data: New/corrected data
            canonical_at: Optional updated time
            metadata: Optional metadata

        Returns:
            Created amendment Item dict
        """
        request = AmendRequest(target=target, data=data, canonical_at=canonical_at, metadata=metadata)
        return await self._request(request.model_dump())

    # ========================================================================
    # Relation Operations
    # ========================================================================

    async def link(
        self,
        source: str,
        target: str,
        source_type: Literal["item", "entity", "artifact"],
        target_type: Literal["item", "entity", "artifact", "fragment"],
        initial_horizon_days: int = 7,
        evidence: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a user relation.

        Args:
            source: Source UUID
            target: Target UUID
            source_type: Source type
            target_type: Target type
            initial_horizon_days: Time horizon in days
            evidence: Optional evidence
            metadata: Optional metadata

        Returns:
            Created relation dict
        """
        request = LinkRequest(
            source=source,
            target=target,
            source_type=source_type,
            target_type=target_type,
            initial_horizon_days=initial_horizon_days,
            evidence=evidence,
            metadata=metadata,
        )
        return await self._request(request.model_dump())

    async def unlink(self, target: str) -> dict[str, Any]:
        """Remove a user relation.

        Args:
            target: Relation UUID to remove

        Returns:
            Confirmation dict
        """
        request = UnlinkRequest(target=target)
        return await self._request(request.model_dump())

    async def query_relation(
        self,
        source: str | None = None,
        target: str | None = None,
        kind: str | None = None,
        source_type: str | None = None,
        target_type: str | None = None,
        alive_only: bool = True,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Query user relations.

        Args:
            source: Filter by source UUID
            target: Filter by target UUID
            kind: Filter by relation kind
            source_type: Filter by source type
            target_type: Filter by target type
            alive_only: Only return alive relations
            limit: Max results

        Returns:
            Query result with relations list
        """
        request = QueryRelationRequest(
            source=source,
            target=target,
            kind=kind,
            source_type=source_type,
            target_type=target_type,
            alive_only=alive_only,
            limit=limit,
        )
        return await self._request(request.model_dump())

    async def explore(
        self,
        anchor: str,
        direction: Literal["outgoing", "incoming", "both"] = "both",
        radius: int | None = None,
        kind: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Explore/graph expand from an anchor.

        Args:
            anchor: Starting UUID
            direction: Traversal direction
            radius: Max hop distance
            kind: Filter by relation kind
            limit: Max results

        Returns:
            Exploration result with connected entities
        """
        request = ExploreRequest(
            anchor=anchor,
            direction=direction,
            radius=radius,
            kind=kind,
            limit=limit,
        )
        return await self._request(request.model_dump())

    # ========================================================================
    # Context Operations (RFC-003)
    # ========================================================================

    async def enter(self, scope: str) -> dict[str, Any]:
        """Enter a scope - add to active set.

        Args:
            scope: Scope UUID to enter

        Returns:
            Updated context dict
        """
        request = EnterRequest(scope=scope)
        return await self._request(request.model_dump())

    async def leave(self, scope: str) -> dict[str, Any]:
        """Leave a scope - remove from active set.

        Args:
            scope: Scope UUID to leave

        Returns:
            Updated context dict
        """
        request = LeaveRequest(scope=scope)
        return await self._request(request.model_dump())

    async def focus(self, scope: str) -> dict[str, Any]:
        """Focus a scope - switch primary scope.

        Args:
            scope: Scope UUID to focus (must be in active set)

        Returns:
            Updated context dict
        """
        request = FocusRequest(scope=scope)
        return await self._request(request.model_dump())

    # ========================================================================
    # Search and Track
    # ========================================================================

    async def search(
        self,
        query: str,
        target_type: Literal["entity", "fact", "all"] = "all",
        coverage: Literal["names", "content", "full"] = "content",
        effort: Literal["quick", "standard", "deep"] = "standard",
        strategy: Literal["fuzzy", "auto"] = "auto",
        continuation_token: str | None = None,
        limit: int = 20,
        threshold: float | None = None,
    ) -> dict[str, Any]:
        """Search entities and facts.

        Args:
            query: Search query text
            target_type: Target type to search
            coverage: Search coverage level
            effort: Search effort mode
            strategy: Search strategy
            continuation_token: Pagination token
            limit: Max results
            threshold: Minimum similarity score

        Returns:
            Search results dict
        """
        request = SearchRequest(
            query=query,
            target_type=target_type,
            coverage=coverage,
            effort=effort,
            strategy=strategy,
            continuation_token=continuation_token,
            limit=limit,
            threshold=threshold,
        )
        return await self._request(request.model_dump())

    async def track(self, target: str, depth: int | None = None) -> dict[str, Any]:
        """Track causal chain from entity to originating facts.

        Args:
            target: Entity UUID to track
            depth: Hop limit (None = unlimited)

        Returns:
            Causal chain tree structure
        """
        request = TrackRequest(target=target, depth=depth)
        return await self._request(request.model_dump())

    # ========================================================================
    # Artifact Delta Operations (Session 17)
    # ========================================================================

    async def commit_artifact_delta(
        self,
        artifact: str,
        ops: str,
        based_on_hash: str,
        references: list[str] | None = None,
        source_message: str | None = None,
    ) -> dict[str, Any]:
        """Commit artifact delta with optimistic locking.

        Args:
            artifact: Artifact UUID
            ops: Delta operations string (e.g., "+5:^abc\\n-23")
            based_on_hash: Current artifact hash (8-char prefix)
            references: List of fragment/artifact UUIDs
            source_message: Optional source Message UUID for triggers

        Returns:
            Created ArtifactDelta dict with new commit hash

        Raises:
            ConflictError: If hash doesn't match (optimistic lock)

        Example:
            >>> delta = await api.commit_artifact_delta(
            ...     artifact="core_abc123...",
            ...     ops="+5:^abc\\n-10",
            ...     based_on_hash="a1b2c3d4",
            ...     references=["^abc"]
            ... )
        """
        request = CommitArtifactRequest(
            artifact=artifact,
            ops=ops,
            based_on_hash=based_on_hash,
            references=references or [],
            source_message=source_message,
        )
        try:
            return await self._request(request.model_dump())
        except MemoGardenClientError as e:
            if e.type == "ConflictError":
                raise ConflictError(e.message, e.details, e.type) from None
            raise

    async def get_artifact_at_commit(
        self,
        artifact: str,
        commit_hash: str,
    ) -> dict[str, Any]:
        """Retrieve artifact state at specific commit.

        Args:
            artifact: Artifact UUID
            commit_hash: Target commit hash (8-char prefix)

        Returns:
            Artifact content at commit
        """
        request = GetArtifactAtCommitRequest(artifact=artifact, commit_hash=commit_hash)
        return await self._request(request.model_dump())

    async def diff_commits(
        self,
        artifact: str,
        commit_a: str,
        commit_b: str,
    ) -> dict[str, Any]:
        """Compare two artifact commits.

        Args:
            artifact: Artifact UUID
            commit_a: First commit hash
            commit_b: Second commit hash

        Returns:
            Structured diff for UI rendering
        """
        request = DiffCommitsRequest(artifact=artifact, commit_a=commit_a, commit_b=commit_b)
        return await self._request(request.model_dump())

    # ========================================================================
    # Conversation Operations (Session 18)
    # ========================================================================

    async def fold(
        self,
        target: str,
        summary_content: str,
        author: ActorType = "agent",
        fragment_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fold a conversation branch with summary.

        Args:
            target: ConversationLog UUID to fold
            summary_content: Summary text
            author: Who created the summary
            fragment_ids: Fragment IDs referenced in summary

        Returns:
            Updated ConversationLog dict
        """
        request = FoldRequest(
            target=target,
            summary_content=summary_content,
            author=author,
            fragment_ids=fragment_ids or [],
        )
        return await self._request(request.model_dump())

    async def get_conversation(self, target: str) -> dict[str, Any]:
        """Get conversation log details.

        Args:
            target: ConversationLog UUID

        Returns:
            ConversationLog dict with items, summary, etc.
        """
        request = GetRequest(target=target)
        return await self._request(request.model_dump())
