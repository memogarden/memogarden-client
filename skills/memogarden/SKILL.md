---
name: memogarden
description: Work with MemoGarden — the personal memory system for financial transactions with an agent-first API. Use when authenticating as an agent to MemoGarden, reading or writing memogarden data (facts, transactions, artifacts, notes via the Semantic API), running the memogarden CLI or mg_client, developing/deploying/debugging the memogarden repos, or managing agent identity and API keys. Triggers include "memogarden", "mg_client", "semantic API", "/mg", "X-API-Key", "agent identity", "soil", "core.db", "provider", "ingest", "memogarden skill".
---

# memogarden

Personal memory system for financial transactions: immutable facts (Soil) +
mutable beliefs (Core), exposed through an agent-first Semantic API.
**Agents are external consumers** — there are no memogarden agents inside the
repos; any agent CLI (ZCode, Claude Code, ...) connects over HTTP with an
API key.

This skill is the **router + operational delta**. The authoritative docs live
in the workspace — always read them for anything beyond this file:

| Need | Read |
|------|------|
| Workspace map, repo boundaries, commit rules | `~/memogarden/AGENTS.md` |
| API reference (Semantic `/mg`, REST, auth, agents) | `~/memogarden/docs/api.md` |
| Development workflow, constraints, anti-patterns | `~/memogarden/.claude/skills/memogarden-development/SKILL.md` (Claude Code skill — read as a document) |
| Testing philosophy + workflows | `~/memogarden/.claude/skills/memogarden-testing/SKILL.md` |
| Repo-by-repo structure | `~/memogarden/docs/repositories.md` |
| Platform PRD / RFC index | `~/memogarden/plan/memogarden_prd_v0_11_0.md`, `plan/rfc_00*_*.md` |
| Agent identity design | `plan/rfc_010_agent_identity_v1.md` |

## Using MemoGarden as an agent (consumer)

**Credentials live OUTSIDE memogarden, in operator-held config:**
`~/.config/memogarden/agents.d/<agent-label>.env` (mode 0600) holds
`MEMOGARDEN_URL` + `MEMOGARDEN_API_KEY`. Load it and verify identity:

```bash
set -a; source ~/.config/memogarden/agents.d/js-xiaomi-zcode.env; set +a
curl -s "$MEMOGARDEN_URL/auth/me" -H "X-API-Key: $MEMOGARDEN_API_KEY"
# → {"actor_type": "agent", "actor": "core_...", "label": "js-xiaomi-zcode", ...}
```

- **Transport**: `X-API-Key` header on every call. `Authorization: Bearer` is
  JWT-only (human login) — API keys are REJECTED as Bearer by design.
- **Semantic API** (primary, agent-oriented): `POST $MEMOGARDEN_URL/mg` with
  `{"op": "create|get|edit|query|search|...", ...}`. Reference: docs/api.md;
  verb design: RFC-005.
- **Python**: `mg_client.MemoGardenClient(base_url=..., api_key=...)` (this
  repo, `memogarden-client`; installed on PATH as the `memogarden` CLI via uv).
- **CLI**: `memogarden <verb> <noun>` — currently `memogarden register
  provider`; data operations go through the Semantic API.
- **Attribution**: a key bound to an Agent entity (RFC-010) attributes every
  operation to it — Action facts and the `/mg` envelope carry
  `actor = core_<agent uuid>`, `actor_type: "agent"`. Trace with
  `{"op": "query", "target_type": "fact", "type": "Action",
  "filters": {"actor": "core_..."}}`; attribution gaps:
  `filters={"actor_type": "api_key"}`.
- **Never use the operator's admin key** (`~/memogarden/.env`) for agent
  work — it is unbound and collapses all attribution into the username.

## Developing MemoGarden (contributor)

**Workspace**: `~/memogarden/` is a multi-repo workspace. Every sibling
directory has its own git history — check `pwd` and `git status` before
committing (see AGENTS.md for the full rules).

**Testing** (always `run_tests.sh`, never ad-hoc pytest):

```bash
./memogarden-api/run_tests.sh --tb=no -q 2>&1 | tail -7     # summary only
./memogarden-system/run_tests.sh
./memogarden-client/run_tests.sh
```

**Agent worktrees** — the repos have poetry path dependencies, so a worktree
must MIRROR the sibling layout or nothing installs:

```bash
mkdir -p ~/wt/memogarden
git -C ~/memogarden/memogarden-system worktree add ~/wt/memogarden/memogarden-system -b feat/<slug>
git -C ~/memogarden/memogarden-api worktree add ~/wt/memogarden/memogarden-api -b feat/<slug>
ln -s ~/memogarden/memogarden-utils ~/wt/memogarden/memogarden-utils   # unmodified deps
ln -s ~/memogarden/scripts ~/wt/memogarden/scripts                     # run_tests.sh needs ../scripts/test_entrypoint.sh
awt register --path <wt> --task "<task>" --agent zcode
```

Then `poetry install` inside each worktree (fresh venv per worktree).

**Deploy** (service runs from the MAIN checkouts, not worktrees):

```bash
git -C ~/memogarden/memogarden-system pull origin main
git -C ~/memogarden/memogarden-api pull origin main
systemctl --user restart memogarden        # user unit — plain systemctl won't find it
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8600/health   # expect 200
```

⚠️ **If auth starts returning 500 after a deploy**: the service venv has
stale non-editable package snapshots shadowing the repos. Diagnose:

```bash
~/memogarden/memogarden-api/.venv/bin/python -c "import system; print(system.__file__)"
# must resolve to ~/memogarden/memogarden-system/system/__init__.py
```

If not: `cd ~/memogarden/memogarden-api && .venv/bin/pip uninstall -y
memogarden-system memogarden-utils && poetry install`, then restart.

**Live system facts**: server = gunicorn user unit `memogarden.service` on
`127.0.0.1:8600` (loopback only); data = `~/.local/share/memogarden/{core,soil}.db`;
ingestion = `memogarden-ingest.timer` (hourly) poking `POST /api/v1/providers/run`,
gmail provider executes in-process; agent key custody =
`~/.config/memogarden/agents.d/` (external to memogarden — agents are an
external concern).

**GitHub**: org `memogarden`; squash-merge PR convention (`(#N)` suffix);
`gh auth switch --user ngjunsiang` at session start (active account flips
to `nycomp` between sessions).

## Master copy

This file's master lives in `~/memogarden/memogarden-client/skills/memogarden/SKILL.md`
(versioned with the CLI). Installed copies at `~/.zcode/skills/memogarden`
and `~/.agents/skills/memogarden` are symlinks to it — edit the master, not
the links. Last verified against the deployed system: 2026-11-07.
