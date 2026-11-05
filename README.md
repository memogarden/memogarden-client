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

## CLI

The package installs a `memogarden` command — a thin authenticated client of
the API, using a verb-first grammar that mirrors the RFC-005 semantic verbs.

Connection settings come from flags or the environment:

```bash
export MEMOGARDEN_URL=http://localhost:8600
export MEMOGARDEN_API_KEY=mg_sk_agent_...
```

Register a data provider (server-side registry, RFC-004 §12.2; requires the
`POST /api/v1/providers` endpoint on the server):

```bash
# Field flags
memogarden register provider \
    --name gmail \
    --module gmail_importer.provider \
    --class GmailProvider \
    --interval 3600 \
    --config batch_limit=1000

# Or a JSON payload file / stdin
memogarden register provider --file payload.json
cat payload.json | memogarden register provider --file -
```

On success the stored manifest is printed as JSON (including server-assigned
fields such as `capabilities`, `registered_at`, `next_due_at`). Exit codes:
0 success, 1 error, 2 usage error.

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
├── providers.py     # ProviderAPI wrapper (/api/v1/providers)
├── cli.py           # memogarden CLI entry point
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
