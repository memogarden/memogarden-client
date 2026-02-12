"""Pydantic models for MemoGarden API requests and responses.

These models mirror the server-side schemas in memogarden-api/api/schemas/semantic.py
and provide type safety and validation for client code.
"""

from typing import Any, Literal
from pydantic import BaseModel, Field


# ============================================================================
# Common Types
# ============================================================================

ActorType = Literal["operator", "agent", "system"]
RelationKind = Literal["explicit_link"]
RelationSourceTargetType = Literal["item", "entity", "artifact"]
RelationTargetType = Literal["item", "entity", "artifact", "fragment"]
ScopeDirection = Literal["outgoing", "incoming", "both"]
SearchCoverage = Literal["names", "content", "full"]
SearchEffort = Literal["quick", "standard", "deep"]
SearchStrategy = Literal["fuzzy", "auto"]
SearchTargetType = Literal["entity", "fact", "all"]
QueryTargetType = Literal["entity", "fact", "relation"]


# ============================================================================
# Response Envelope
# ============================================================================

class SemanticResponse(BaseModel):
    """Base Semantic API response envelope.

    All responses include ok, actor, timestamp, and either result or error.
    """
    ok: bool = Field(..., description="True if operation succeeded")
    actor: str = Field(..., description="Actor UUID (usr_xxx or agt_xxx)")
    timestamp: str = Field(..., description="ISO 8601 timestamp")
    result: dict[str, Any] | None = Field(default=None, description="Operation result")
    error: dict[str, Any] | None = Field(default=None, description="Error details")


class QueryResult(BaseModel):
    """Response envelope for query operations."""
    results: list[dict[str, Any]] = Field(default_factory=list)
    total: int
    start_index: int
    count: int


# ============================================================================
# Entity Operations
# ============================================================================

class CreateRequest(BaseModel):
    """Request to create an entity."""
    op: Literal["create"] = "create"
    type: str = Field(..., description="Entity type")
    data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] | None = Field(default=None)


class GetRequest(BaseModel):
    """Request to get an entity, fact, or relation by UUID."""
    op: Literal["get"] = "get"
    target: str = Field(..., description="UUID with or without prefix")


class EditRequest(BaseModel):
    """Request to edit an entity or relation."""
    op: Literal["edit"] = "edit"
    target: str = Field(..., description="Entity or relation UUID")
    set: dict[str, Any] | None = Field(default=None)
    unset: list[str] | None = Field(default=None)


class ForgetRequest(BaseModel):
    """Request to soft delete an entity."""
    op: Literal["forget"] = "forget"
    target: str = Field(..., description="Entity UUID to forget")


class QueryRequest(BaseModel):
    """Request to query entities with filters."""
    op: Literal["query"] = "query"
    target_type: QueryTargetType = Field(default="entity")
    type: str | None = Field(default=None)
    filters: dict[str, Any] | None = Field(default=None)
    start_index: int = Field(default=0, ge=0)
    count: int = Field(default=20, ge=1, le=100)


# ============================================================================
# Fact Operations
# ============================================================================

class AddRequest(BaseModel):
    """Request to add a fact (Item) to Soil."""
    op: Literal["add"] = "add"
    type: str = Field(..., description="Item type (e.g., 'Note', 'Message')")
    data: dict[str, Any] = Field(default_factory=dict)
    canonical_at: str | None = Field(default=None)
    metadata: dict[str, Any] | None = Field(default=None)


class AmendRequest(BaseModel):
    """Request to amend a fact (Item) in Soil."""
    op: Literal["amend"] = "amend"
    target: str = Field(..., description="UUID of Item to amend")
    data: dict[str, Any] = Field(..., description="New/corrected data")
    canonical_at: str | None = Field(default=None)
    metadata: dict[str, Any] | None = Field(default=None)


# ============================================================================
# Relation Operations
# ============================================================================

class LinkRequest(BaseModel):
    """Request to create a user relation."""
    op: Literal["link"] = "link"
    kind: RelationKind = Field(default="explicit_link")
    source: str = Field(..., description="Source UUID")
    source_type: RelationSourceTargetType = Field(...)
    target: str = Field(..., description="Target UUID")
    target_type: RelationTargetType = Field(...)
    initial_horizon_days: int = Field(default=7, ge=1)
    evidence: dict[str, Any] | None = Field(default=None)
    metadata: dict[str, Any] | None = Field(default=None)


class UnlinkRequest(BaseModel):
    """Request to unlink/remove a user relation."""
    op: Literal["unlink"] = "unlink"
    target: str = Field(..., description="Relation UUID to remove")


class QueryRelationRequest(BaseModel):
    """Request to query user relations with filters."""
    op: Literal["query_relation"] = "query_relation"
    source: str | None = Field(default=None)
    target: str | None = Field(default=None)
    kind: str | None = Field(default=None)
    source_type: str | None = Field(default=None)
    target_type: str | None = Field(default=None)
    alive_only: bool = Field(default=True)
    limit: int = Field(default=100, ge=1, le=1000)


class ExploreRequest(BaseModel):
    """Request to explore/graph expand from an anchor."""
    op: Literal["explore"] = "explore"
    anchor: str = Field(..., description="Starting UUID")
    direction: ScopeDirection = Field(default="both")
    radius: int | None = Field(default=None, ge=1)
    kind: str | None = Field(default=None)
    limit: int = Field(default=100, ge=1, le=1000)


# ============================================================================
# Context Operations (RFC-003)
# ============================================================================

class EnterRequest(BaseModel):
    """Request to enter a scope - add to active set."""
    op: Literal["enter"] = "enter"
    scope: str = Field(..., description="Scope UUID to enter")


class LeaveRequest(BaseModel):
    """Request to leave a scope - remove from active set."""
    op: Literal["leave"] = "leave"
    scope: str = Field(..., description="Scope UUID to leave")


class FocusRequest(BaseModel):
    """Request to focus a scope - switch primary scope."""
    op: Literal["focus"] = "focus"
    scope: str = Field(..., description="Scope UUID to focus")


# ============================================================================
# Search and Track Operations
# ============================================================================

class SearchRequest(BaseModel):
    """Request to search entities and facts."""
    op: Literal["search"] = "search"
    query: str = Field(..., min_length=1)
    target_type: SearchTargetType = Field(default="all")
    coverage: SearchCoverage = Field(default="content")
    effort: SearchEffort = Field(default="standard")
    strategy: SearchStrategy = Field(default="auto")
    continuation_token: str | None = Field(default=None)
    limit: int = Field(default=20, ge=1, le=100)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class TrackRequest(BaseModel):
    """Request to track causal chain from entity to originating facts."""
    op: Literal["track"] = "track"
    target: str = Field(..., description="Entity UUID to track")
    depth: int | None = Field(default=None, ge=1)


# ============================================================================
# Artifact Delta Operations (Session 17)
# ============================================================================

class CommitArtifactRequest(BaseModel):
    """Request to commit artifact delta with optimistic locking."""
    op: Literal["commit_artifact"] = "commit_artifact"
    artifact: str = Field(..., description="Artifact UUID")
    ops: str = Field(..., min_length=1, description="Delta operations string")
    references: list[str] = Field(default_factory=list)
    based_on_hash: str = Field(..., min_length=8, max_length=8)
    source_message: str | None = Field(default=None)


class GetArtifactAtCommitRequest(BaseModel):
    """Request to retrieve artifact state at specific commit."""
    op: Literal["get_artifact_at_commit"] = "get_artifact_at_commit"
    artifact: str = Field(..., description="Artifact UUID")
    commit_hash: str = Field(..., min_length=8, max_length=8)


class DiffCommitsRequest(BaseModel):
    """Request to compare two artifact commits."""
    op: Literal["diff_commits"] = "diff_commits"
    artifact: str = Field(..., description="Artifact UUID")
    commit_a: str = Field(..., min_length=8, max_length=8)
    commit_b: str = Field(..., min_length=8, max_length=8)


# ============================================================================
# Conversation Operations (Session 18)
# ============================================================================

class FoldRequest(BaseModel):
    """Request to fold a conversation branch."""
    op: Literal["fold"] = "fold"
    target: str = Field(..., description="ConversationLog UUID to fold")
    summary_content: str = Field(..., min_length=1)
    author: ActorType = Field(...)
    fragment_ids: list[str] = Field(default_factory=list)


# ============================================================================
# Entity Model Wrappers
# ============================================================================

class Entity(BaseModel):
    """MemoGarden entity wrapper.

    Provides convenient access to entity properties.
    """
    uuid: str
    type_: str = Field(..., alias="type")
    hash: str
    version: int
    created_at: str
    updated_at: str
    data: dict[str, Any]

    model_config = {"populate_by_name": True}

    @property
    def prefix(self) -> str:
        """Get the UUID prefix with type."""
        return f"core_{self.uuid}" if not self.uuid.startswith("core_") else self.uuid


class Scope(Entity):
    """Scope entity (RFC-003, RFC-009)."""

    @property
    def label(self) -> str:
        return self.data.get("label", "")

    @property
    def active_participants(self) -> list[str]:
        return self.data.get("active_participants", [])

    @property
    def artifact_uuids(self) -> list[str]:
        return self.data.get("artifact_uuids", [])


class Artifact(Entity):
    """Artifact entity (Project Studio)."""

    @property
    def label(self) -> str:
        return self.data.get("label", "")

    @property
    def content_type(self) -> str:
        return self.data.get("content_type", "text/plain")

    @property
    def content(self) -> str:
        return self.data.get("content", "")

    @property
    def snapshot_hash(self) -> str | None:
        return self.data.get("snapshot_hash")

    @property
    def deltas(self) -> list[str]:
        return self.data.get("deltas", [])


class ConversationLog(Entity):
    """ConversationLog entity (Project Studio)."""

    @property
    def parent_uuid(self) -> str | None:
        return self.data.get("parent_uuid")

    @property
    def items(self) -> list[str]:
        return self.data.get("items", [])

    @property
    def summary(self) -> dict[str, Any] | None:
        return self.data.get("summary")


class Item(BaseModel):
    """Soil Item (fact)."""
    uuid: str
    type_: str = Field(..., alias="type")
    realized_at: str
    canonical_at: str | None = None
    data: dict[str, Any]
    supersedes: str | None = None

    model_config = {"populate_by_name": True}

    @property
    def prefix(self) -> str:
        """Get the UUID prefix with type."""
        return f"soil_{self.uuid}" if not self.uuid.startswith("soil_") else self.uuid


class Message(Item):
    """Message Item type."""

    @property
    def sender(self) -> str:
        return self.data.get("sender", "")

    @property
    def description(self) -> str:
        return self.data.get("description", "")

    @property
    def fragments(self) -> list[dict[str, Any]]:
        return self.data.get("fragments", [])
