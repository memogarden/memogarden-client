# MemoGarden Client

Python SDK for MemoGarden API access.

## Overview

This package provides a type-safe Python client for interacting with the MemoGarden Semantic API. It supports all Semantic API operations including entity CRUD, fact operations, relations, context management, and Project Studio features like artifact deltas and conversation folding.

## Installation

```bash
poetry install
```

## Usage

### Async Client

```python
import asyncio
from mg_client import MemoGardenClient

async def main():
    async with MemoGardenClient(
        base_url="http://localhost:5000",
        api_key="mg_sk_agent_..."
    ) as client:
        # Create a scope
        scope = await client.semantic.create_scope(label="My Project")
        print(f"Created scope: {scope['uuid']}")

        # Create an artifact
        artifact = await client.semantic.create_artifact(
            label="README.md",
            content="# Welcome\n",
            content_type="text/markdown"
        )

        # Send a message
        message = await client.semantic.send_message(
            log_uuid=scope['uuid'],
            content="Let's use ^abc approach",
            sender="operator"
        )

        # Commit delta
        delta = await client.semantic.commit_artifact_delta(
            artifact=artifact['uuid'],
            ops="+5:^abc",
            based_on_hash=artifact['hash'],
            references=["^abc"]
        )

asyncio.run(main())
```

### Sync Client

For non-async contexts, use `SyncMemoGardenClient`:

```python
from mg_client import SyncMemoGardenClient

with SyncMemoGardenClient(
    base_url="http://localhost:5000",
    api_key="mg_sk_agent_..."
) as client:
    scope = client.semantic.create_scope(label="My Project")
    print(f"Created scope: {scope['uuid']}")
```

## Testing

Run tests:

```bash
cd memogarden-client
poetry run pytest
```

With coverage:

```bash
poetry run pytest --cov=mg_client --cov-report=html
```

## Structure

```
mg_client/
├── __init__.py      # Package exports
├── client.py        # Main MemoGardenClient class
├── semantic.py      # SemanticAPI wrapper
├── auth.py          # AuthManager for API key auth
├── models.py        # Pydantic request/response models
└── exceptions.py    # Client exception classes
```

## API Operations

### Entity Operations
- `create_entity()` - Create entity
- `create_scope()` - Create Scope entity
- `create_artifact()` - Create Artifact entity
- `get()` - Get entity/fact/relation by UUID
- `edit()` - Edit entity (set/unset)
- `forget()` - Soft delete entity
- `query()` - Query with filters

### Fact Operations (Soil)
- `add()` - Add fact to Soil
- `send_message()` - Send message to conversation
- `amend()` - Amend fact (superseding)

### Relation Operations
- `link()` - Create user relation
- `unlink()` - Remove relation
- `query_relation()` - Query relations
- `explore()` - Graph traversal

### Context Operations (RFC-003)
- `enter()` - Enter scope
- `leave()` - Leave scope
- `focus()` - Focus scope

### Search and Track
- `search()` - Semantic search
- `track()` - Causal chain tracking

### Artifact Deltas (Project Studio)
- `commit_artifact_delta()` - Apply delta operations
- `get_artifact_at_commit()` - Get historical state
- `diff_commits()` - Compare commits

### Conversation (Project Studio)
- `fold()` - Fold conversation branch
- `get_conversation()` - Get conversation details
