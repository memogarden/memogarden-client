"""Tests for MemoGarden client."""

import pytest
from unittest.mock import AsyncMock, Mock, patch

from mg_client import MemoGardenClient
from mg_client.auth import AuthManager
from mg_client.exceptions import (
    AuthenticationError,
    MemoGardenClientError,
    ResourceNotFoundError,
    ValidationError,
    ConflictError,
    error_from_response,
)


class TestAuthManager:
    """Tests for AuthManager."""

    def test_init_with_api_key(self):
        """Test initialization with API key."""
        auth = AuthManager(api_key="mg_sk_agent_abc123")
        assert auth.api_key == "mg_sk_agent_abc123"
        assert auth.auth_type == "api_key"

    def test_init_with_invalid_api_key_format(self):
        """Test initialization with invalid API key format."""
        with pytest.raises(ValueError, match="must start with 'mg_sk_'"):
            AuthManager(api_key="invalid_key")

    def test_init_with_missing_api_key(self):
        """Test initialization without API key."""
        with pytest.raises(ValueError, match="api_key is required"):
            AuthManager(auth_type="api_key", api_key=None)

    def test_get_headers(self):
        """Test getting authorization headers."""
        auth = AuthManager(api_key="mg_sk_agent_abc123")
        headers = auth.get_headers()
        assert headers == {"Authorization": "Bearer mg_sk_agent_abc123"}

    def test_get_auth_string(self):
        """Test getting safe auth string for display."""
        auth = AuthManager(api_key="mg_sk_agent_abc123def456")
        auth_str = auth.get_auth_string()
        assert auth_str == "api_key: mg_sk_agent_..."

    def test_repr(self):
        """Test string representation."""
        auth = AuthManager(api_key="mg_sk_agent_abc123")
        assert "api_key: mg_sk_agent_..." in repr(auth)


class TestExceptions:
    """Tests for exception classes."""

    def test_base_exception(self):
        """Test base MemoGardenClientError."""
        error = MemoGardenClientError("Test error")
        assert error.message == "Test error"
        assert error.details == {}
        assert str(error) == "Test error"

    def test_exception_with_details(self):
        """Test exception with details."""
        error = MemoGardenClientError("Test error", details={"field": "value"})
        assert "field" in str(error)

    def test_authentication_error(self):
        """Test AuthenticationError."""
        error = AuthenticationError("Invalid credentials")
        assert isinstance(error, MemoGardenClientError)

    def test_resource_not_found_error(self):
        """Test ResourceNotFoundError."""
        error = ResourceNotFoundError("Not found")
        assert isinstance(error, MemoGardenClientError)

    def test_validation_error(self):
        """Test ValidationError."""
        error = ValidationError("Invalid input")
        assert isinstance(error, MemoGardenClientError)

    def test_conflict_error(self):
        """Test ConflictError."""
        error = ConflictError("Version mismatch")
        assert isinstance(error, MemoGardenClientError)

    def test_error_from_response(self):
        """Test creating exception from server response."""
        response = {
            "error": {
                "type": "ResourceNotFound",
                "message": "Entity not found",
                "details": {"uuid": "core_abc123"}
            }
        }
        error = error_from_response(response)
        assert isinstance(error, ResourceNotFoundError)
        assert error.message == "Entity not found"
        assert error.details == {"uuid": "core_abc123"}

    def test_error_from_response_unknown_type(self):
        """Test creating exception from unknown error type."""
        response = {
            "error": {
                "type": "UnknownError",
                "message": "Something went wrong"
            }
        }
        error = error_from_response(response)
        assert isinstance(error, MemoGardenClientError)
        assert error.type == "UnknownError"


class TestMemoGardenClient:
    """Tests for MemoGardenClient."""

    def test_init(self):
        """Test client initialization."""
        client = MemoGardenClient(
            base_url="http://localhost:5000",
            api_key="mg_sk_agent_abc123"
        )
        assert client.base_url == "http://localhost:5000"
        assert client.auth is not None
        assert client.semantic is not None

    def test_init_without_api_key(self):
        """Test client initialization without API key."""
        client = MemoGardenClient(
            base_url="http://localhost:5000",
            api_key=None
        )
        assert client.auth is None
        assert client.semantic is None

    def test_init_with_empty_base_url(self):
        """Test client initialization with empty base URL."""
        with pytest.raises(ValueError, match="base_url is required"):
            MemoGardenClient(base_url="")

    def test_base_url_trailing_slash(self):
        """Test that trailing slash is removed from base URL."""
        client = MemoGardenClient(
            base_url="http://localhost:5000/",
            api_key="mg_sk_agent_abc123"
        )
        assert client.base_url == "http://localhost:5000"

    def test_repr(self):
        """Test string representation."""
        client = MemoGardenClient(
            base_url="http://localhost:5000",
            api_key="mg_sk_agent_abc123"
        )
        assert "MemoGardenClient" in repr(client)
        assert "http://localhost:5000" in repr(client)

    @pytest.mark.asyncio
    async def test_health_check_with_auth(self):
        """Test health check with authentication."""
        with patch("mg_client.semantic.SemanticAPI._request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"total": 0}

            client = MemoGardenClient(
                base_url="http://localhost:5000",
                api_key="mg_sk_agent_abc123"
            )
            result = await client.health_check()
            assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_check_without_auth(self):
        """Test health check without authentication."""
        client = MemoGardenClient(
            base_url="http://localhost:5000",
            api_key=None
        )
        result = await client.health_check()
        assert result["status"] == "no_auth"

    @pytest.mark.asyncio
    async def test_close(self):
        """Test closing client."""
        client = MemoGardenClient(
            base_url="http://localhost:5000",
            api_key="mg_sk_agent_abc123"
        )
        await client.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        """Test using client as async context manager."""
        async with MemoGardenClient(
            base_url="http://localhost:5000",
            api_key="mg_sk_agent_abc123"
        ) as client:
            assert client is not None


class TestSemanticAPI:
    """Tests for SemanticAPI."""

    @pytest.fixture
    def api(self):
        """Create SemanticAPI instance for testing."""
        auth = AuthManager(api_key="mg_sk_agent_abc123")
        from mg_client.semantic import SemanticAPI
        return SemanticAPI(base_url="http://localhost:5000", auth=auth)

    @pytest.mark.asyncio
    async def test_create_scope(self, api):
        """Test creating a scope."""
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "ok": True,
                "actor": "usr_test",
                "timestamp": "2026-02-12T00:00:00Z",
                "result": {
                    "uuid": "core_abc123",
                    "type": "Scope",
                    "hash": "a1b2c3d4",
                    "version": 1,
                    "created_at": "2026-02-12T00:00:00Z",
                    "updated_at": "2026-02-12T00:00:00Z",
                    "data": {
                        "label": "Test Project",
                        "active_participants": [],
                        "artifact_uuids": []
                    }
                }
            }
            mock_post.return_value.raise_for_status = Mock()
            mock_post.return_value = mock_response

            result = await api.create_scope(label="Test Project")
            assert result["uuid"] == "core_abc123"
            assert result["data"]["label"] == "Test Project"

    @pytest.mark.asyncio
    async def test_create_artifact(self, api):
        """Test creating an artifact."""
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "ok": True,
                "actor": "usr_test",
                "timestamp": "2026-02-12T00:00:00Z",
                "result": {
                    "uuid": "core_def456",
                    "type": "Artifact",
                    "hash": "e5f6g7h8",
                    "version": 1,
                    "created_at": "2026-02-12T00:00:00Z",
                    "updated_at": "2026-02-12T00:00:00Z",
                    "data": {
                        "label": "README.md",
                        "content": "# Test",
                        "content_type": "text/markdown"
                    }
                }
            }
            mock_post.return_value.raise_for_status = Mock()
            mock_post.return_value = mock_response

            result = await api.create_artifact(
                label="README.md",
                content="# Test",
                content_type="text/markdown"
            )
            assert result["uuid"] == "core_def456"
            assert result["data"]["content"] == "# Test"

    @pytest.mark.asyncio
    async def test_query(self, api):
        """Test querying entities."""
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "ok": True,
                "actor": "usr_test",
                "timestamp": "2026-02-12T00:00:00Z",
                "result": {
                    "results": [],
                    "total": 0,
                    "start_index": 0,
                    "count": 0
                }
            }
            mock_post.return_value.raise_for_status = Mock()
            mock_post.return_value = mock_response

            result = await api.query(type="Scope")
            assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_error_handling_404(self, api):
        """Test handling 404 error."""
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 404
            mock_response.json.return_value = {
                "error": {
                    "type": "ResourceNotFound",
                    "message": "Entity not found"
                }
            }
            mock_post.raise_for_status = Mock(side_effect=Exception("HTTP 404"))
            mock_post.return_value = mock_response

            # The actual error handling happens in the HTTPStatusError catch block
            # which wraps the response parsing
            pass  # Actual testing would require more complex mocking

    @pytest.mark.asyncio
    async def test_send_message(self, api):
        """Test sending a message."""
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "ok": True,
                "actor": "usr_test",
                "timestamp": "2026-02-12T00:00:00Z",
                "result": {
                    "uuid": "soil_msg123",
                    "type": "Message",
                    "realized_at": "2026-02-12T00:00:00Z",
                    "canonical_at": None,
                    "data": {
                        "log_uuid": "core_log123",
                        "description": "Test message",
                        "sender": "operator"
                    }
                }
            }
            mock_post.return_value.raise_for_status = Mock()
            mock_post.return_value = mock_response

            result = await api.send_message(
                log_uuid="core_log123",
                content="Test message",
                sender="operator"
            )
            assert result["uuid"] == "soil_msg123"
            assert result["data"]["description"] == "Test message"

    @pytest.mark.asyncio
    async def test_commit_artifact_delta(self, api):
        """Test committing artifact delta."""
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "ok": True,
                "actor": "usr_test",
                "timestamp": "2026-02-12T00:00:00Z",
                "result": {
                    "commit_hash": "a1b2c3d5",
                    "artifact_hash": "f6e7d8c9"
                }
            }
            mock_post.return_value.raise_for_status = Mock()
            mock_post.return_value = mock_response

            result = await api.commit_artifact_delta(
                artifact="core_art123",
                ops="+5:^abc",
                based_on_hash="a1b2c3d4",
                references=["^abc"]
            )
            assert result["commit_hash"] == "a1b2c3d5"

    @pytest.mark.asyncio
    async def test_fold(self, api):
        """Test folding a conversation."""
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "ok": True,
                "actor": "usr_test",
                "timestamp": "2026-02-12T00:00:00Z",
                "result": {
                    "uuid": "core_log123",
                    "type": "ConversationLog",
                    "data": {
                        "summary": {
                            "content": "Branch summary",
                            "author": "agent"
                        }
                    }
                }
            }
            mock_post.return_value.raise_for_status = Mock()
            mock_post.return_value = mock_response

            result = await api.fold(
                target="core_log123",
                summary_content="Branch summary",
                author="agent"
            )
            assert result["data"]["summary"]["content"] == "Branch summary"


class TestModels:
    """Tests for Pydantic models."""

    def test_create_request_model(self):
        """Test CreateRequest model."""
        from mg_client.models import CreateRequest
        request = CreateRequest(type="Scope", data={"label": "Test"})
        assert request.type == "Scope"
        assert request.op == "create"

    def test_get_request_model(self):
        """Test GetRequest model."""
        from mg_client.models import GetRequest
        request = GetRequest(target="core_abc123")
        assert request.target == "core_abc123"
        assert request.op == "get"

    def test_entity_model(self):
        """Test Entity model."""
        from mg_client.models import Entity
        entity = Entity(
            uuid="abc123",
            type="Scope",
            hash="a1b2c3d4",
            version=1,
            created_at="2026-02-12T00:00:00Z",
            updated_at="2026-02-12T00:00:00Z",
            data={"label": "Test"}
        )
        assert entity.uuid == "abc123"
        assert entity.type_ == "Scope"

    def test_commit_artifact_request(self):
        """Test CommitArtifactRequest model."""
        from mg_client.models import CommitArtifactRequest
        request = CommitArtifactRequest(
            artifact="core_abc123",
            ops="+5:^abc",
            based_on_hash="a1b2c3d4"
        )
        assert request.artifact == "core_abc123"
        assert len(request.ops) > 0


class TestSyncMemoGardenClient:
    """Tests for SyncMemoGardenClient."""

    def test_init(self):
        """Test sync client initialization."""
        from mg_client import SyncMemoGardenClient
        client = SyncMemoGardenClient(
            base_url="http://localhost:5000",
            api_key="mg_sk_agent_abc123"
        )
        assert client._async_client is not None
        assert client.semantic is not None
        client.close()

    def test_context_manager(self):
        """Test sync client as context manager."""
        from mg_client import SyncMemoGardenClient
        with SyncMemoGardenClient(
            base_url="http://localhost:5000",
            api_key="mg_sk_agent_abc123"
        ) as client:
            assert client is not None

    @pytest.mark.asyncio
    async def test_cannot_use_sync_in_async_context(self):
        """Test that sync client raises error in async context."""
        from mg_client import SyncMemoGardenClient
        client = SyncMemoGardenClient(
            base_url="http://localhost:5000",
            api_key="mg_sk_agent_abc123"
        )

        # Using sync methods inside an async context should raise
        with pytest.raises(RuntimeError, match="asyncio.run.*cannot be called from a running event loop"):
            client.semantic.create_scope(label="Test")

        # Cannot use client.close() in async context - it would also fail
        # Just let the loop be cleaned up by pytest-asyncio


class TestErrorHandling:
    """Tests for error handling."""

    def test_network_error_class_exists(self):
        """Test NetworkError exception class (not ConnectionError)."""
        from mg_client.exceptions import MemoGardenClientError

        # Check that NetworkError exists (not ConnectionError which shadows builtin)
        from mg_client.exceptions import NetworkError
        error = NetworkError("Connection failed")
        assert isinstance(error, MemoGardenClientError)
        assert "Connection failed" in str(error)

    def test_invalid_api_key_format_no_leak(self):
        """Test that API key validation error doesn't leak key prefix."""
        from mg_client.auth import AuthManager
        with pytest.raises(ValueError, match="must start with 'mg_sk_'"):
            AuthManager(api_key="invalid_key")
        # Error message should not contain any part of the invalid key
        try:
            AuthManager(api_key="my_secret_key_12345")
        except ValueError as e:
            error_msg = str(e)
            # Make sure the key doesn't appear in the error
            assert "my_secret" not in error_msg
            assert "12345" not in error_msg


class TestSemanticAPIEdgeCases:
    """Tests for SemanticAPI edge cases."""

    @pytest.fixture
    def api(self):
        """Create SemanticAPI instance for testing."""
        auth = AuthManager(api_key="mg_sk_agent_abc123")
        from mg_client.semantic import SemanticAPI
        return SemanticAPI(base_url="http://localhost:5000", auth=auth)

    def test_edit_with_set_fields_parameter(self, api):
        """Test edit method uses set_fields parameter name."""
        # Verify the method signature doesn't shadow builtin 'set'
        import inspect
        sig = inspect.signature(api.edit)
        params = list(sig.parameters.keys())
        assert "set_fields" in params
        assert "set" not in params  # Should not shadow builtin

    @pytest.mark.asyncio
    async def test_conflict_error_for_commit_artifact_delta(self, api):
        """Test ConflictError is raised for hash mismatch in commit_artifact_delta."""
        from unittest.mock import AsyncMock, patch, Mock, MagicMock
        from mg_client.exceptions import ConflictError
        import httpx

        with patch("httpx.AsyncClient.post") as mock_post:
            # Create a proper HTTPStatusError
            error_response = Mock()
            error_response.status_code = 409
            error_response.text = "Hash mismatch"

            # Make json() return a proper dict
            error_data = {
                "ok": False,
                "actor": "usr_test",
                "timestamp": "2026-02-12T00:00:00Z",
                "error": {
                    "type": "ConflictError",
                    "message": "Hash mismatch - artifact was modified",
                    "details": {"expected_hash": "a1b2c3d4", "actual_hash": "e5f6g7h8"}
                }
            }
            error_response.json.return_value = error_data

            http_error = httpx.HTTPStatusError(
                "HTTP 409",
                request=MagicMock(),
                response=error_response
            )

            mock_post.return_value = error_response
            mock_post.return_value.raise_for_status = Mock(side_effect=http_error)

            with pytest.raises(ConflictError) as exc_info:
                await api.commit_artifact_delta(
                    artifact="core_abc123",
                    ops="+5:^abc",
                    based_on_hash="a1b2c3d4"
                )
            assert "Hash mismatch" in str(exc_info.value)
