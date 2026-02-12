"""Test fixtures for MemoGarden client tests."""

import pytest


@pytest.fixture
def sample_scope_response():
    """Sample scope entity response."""
    return {
        "ok": True,
        "actor": "usr_test",
        "timestamp": "2026-02-12T00:00:00Z",
        "result": {
            "uuid": "core_test_scope_123",
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


@pytest.fixture
def sample_artifact_response():
    """Sample artifact entity response."""
    return {
        "ok": True,
        "actor": "usr_test",
        "timestamp": "2026-02-12T00:00:00Z",
        "result": {
            "uuid": "core_test_artifact_456",
            "type": "Artifact",
            "hash": "e5f6g7h8",
            "version": 1,
            "created_at": "2026-02-12T00:00:00Z",
            "updated_at": "2026-02-12T00:00:00Z",
            "data": {
                "label": "README.md",
                "content": "# Welcome\n\nThis is a test artifact.",
                "content_type": "text/markdown",
                "snapshot_hash": None,
                "deltas": []
            }
        }
    }


@pytest.fixture
def sample_message_response():
    """Sample message item response."""
    return {
        "ok": True,
        "actor": "usr_test",
        "timestamp": "2026-02-12T00:00:00Z",
        "result": {
            "uuid": "soil_test_message_789",
            "type": "Message",
            "realized_at": "2026-02-12T00:00:00Z",
            "canonical_at": None,
            "data": {
                "log_uuid": "core_test_log_123",
                "description": "This is a test message with a ^abc fragment reference.",
                "sender": "operator",
                "fragments": [
                    {"id": "^abc", "content": "fragment reference"}
                ]
            },
            "supersedes": None
        }
    }


@pytest.fixture
def error_response():
    """Sample error response."""
    return {
        "ok": False,
        "actor": "usr_test",
        "timestamp": "2026-02-12T00:00:00Z",
        "error": {
            "type": "ResourceNotFound",
            "message": "Entity not found",
            "details": {"uuid": "core_nonexistent"}
        }
    }


@pytest.fixture
def conflict_response():
    """Sample conflict error response."""
    return {
        "ok": False,
        "actor": "usr_test",
        "timestamp": "2026-02-12T00:00:00Z",
        "error": {
            "type": "ConflictError",
            "message": "Artifact hash mismatch - optimistic lock failed",
            "details": {
                "expected_hash": "a1b2c3d4",
                "actual_hash": "e5f6g7h8"
            }
        }
    }
