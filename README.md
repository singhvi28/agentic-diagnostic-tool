# Agentic FastAPI Diagnostic Engine

Autonomous root-cause analysis (RCA) and remediation for FastAPI apps, using:

1. **Memory (GraphRAG)** — Neo4j for code topology; Postgres + pgvector for sessions, patch history, and semantic log search  
2. **Tools (FastMCP)** — AST checks, sandboxed code reads, hybrid RCA, test runner  
3. **Orchestration (LangGraph)** — parse → retrieve → diagnose/patch → test → optional apply → finalize  

## Quick start

```bash
# 1. Python env
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# 2. Backends (Neo4j + Postgres/pgvector). Postgres is mapped to host port 5433.
docker compose up -d
alembic upgrade head

# 3. Ingest the sample buggy app into Neo4j (+ seed pgvector from logs)
diagnostic-ingest

# 4. Unit tests (skip integration when Docker is down)
pytest -q -m "not integration"

# Integration suite (needs Neo4j + Postgres from compose)
pytest -q -m integration

# 5. Run the diagnostic agent (spawns MCP over stdio by default)
diagnostic-agent --log logs/app_errors.log

# Apply unified diffs after tests pass (prompts per file; use -y to skip prompts)
diagnostic-agent --log logs/app_errors.log --apply
diagnostic-agent --log logs/app_errors.log --apply --yes
```

Requires GNU `patch` on `PATH`. With `--apply`, each proposed unified diff is printed; confirm with `y` unless `--yes`/`-y` is set. Backups go under `PATCH_BACKUP_ROOT/{session_id}/{timestamp}/`; failed applies restore the backup.


### LangGraph ↔ MCP transport

LangGraph nodes call FastMCP tools over the wire (not via direct Python imports).

```bash
# A) one-shot — agent spawns diagnostic-mcp via stdio (default)
MCP_TRANSPORT=stdio diagnostic-agent --log logs/app_errors.log

# B) split processes — long-lived HTTP MCP server
diagnostic-mcp --transport http --host 127.0.0.1 --port 8000
MCP_TRANSPORT=http MCP_URL=http://127.0.0.1:8000/mcp diagnostic-agent --log logs/app_errors.log
```

| Env | Meaning |
|-----|---------|
| `MCP_TRANSPORT` | `stdio` (default), `http`, or `inprocess` (tests) |
| `MCP_URL` | Streamable HTTP endpoint when transport is `http` |
| `MCP_STDIO_COMMAND` | Command the agent spawns for stdio (default `diagnostic-mcp`) |

Optional: run the target app to generate live errors:

```bash
uvicorn examples.target_app.main:app --reload --port 8001
```

## Layout

```text
src/diagnostic_engine/
  analysis/     # traceback, async-blocking AST, FastAPI routes, ASGI runner
  analyzers/    # plugin findings (FASTAPI-001 / DI / validation)
  db/           # SQLAlchemy models, sessions, patches, pgvector rows
  memory/       # Neo4j + PgVectorMemory + ingest CLI
  mcp/          # FastMCP server (tools + resources)
  agent/        # LangGraph + MCP client facade + CLI + apply_patch
  llm/          # OpenAI / Gemini / Cursor LLM clients
alembic/        # migrations
examples/target_app/   # intentionally buggy FastAPI app
logs/app_errors.log    # sample tracebacks
```

## LLM providers

Set `LLM_PROVIDER` and the matching API key in `.env`:

| Provider | Env key | Default model |
|----------|---------|---------------|
| `openai` | `OPENAI_API_KEY` | `gpt-4o` |
| `gemini` | `GEMINI_API_KEY` | `gemini-3-flash-preview` |
| `cursor` | `CURSOR_API_KEY` | `composer-2.5` |

Optional: `LLM_MODEL=...` overrides the provider default. Embeddings: `EMBEDDING_PROVIDER=hash|openai|gemini|fastrp|auto` (without an API key, FastRP structural vectors are preferred over hash when Neo4j GDS is available).

## MCP surface

| Kind | URI / name | Purpose |
|------|------------|---------|
| Resource | `logs://runtime/errors` | Tail of error log |
| Resource | `routes://app/endpoints` | Static FastAPI topology |
| Resource | `sessions://recent` | Recent diagnostic sessions |
| Resource | `sessions://{session_id}` | Session events + patches |
| Tool | `read_code` | Sandboxed source slices |
| Tool | `analyze_ast_for_async_blocking` | Sync I/O inside `async def` |
| Tool | `run_static_analyzers` | Plugin analyzer suite |
| Tool | `search_similar_bugs` | pgvector similarity |
| Tool | `hybrid_root_cause_analysis` | pgvector + Neo4j context |
| Tool | `run_reproduction_test_tool` | In-process ASGI tests (JSON suite or `test_*`) |

## Notes

- LangGraph retrieve/fetch/execute nodes call MCP tools via `DiagnosticMcpClient` (stdio/HTTP); session DB + LLM diagnose stay local.
- Reproduction tests run **in-process** via `httpx.ASGITransport` (JSON HTTP suite or Python `test_*` with injected `client`) — no pytest tempfile subprocess.
- Default embeddings prefer **Neo4j FastRP** structural vectors when OpenAI/Gemini keys are missing (falls back to hash if GDS/Neo4j unavailable). Set `EMBEDDING_PROVIDER=openai|gemini|fastrp|hash|auto`.
- Without the active provider’s API key, the diagnose/patch node returns a stub RCA so the graph still runs and sessions are recorded.
- Cursor provider uses the Cursor SDK (`Agent.prompt` + Composer 2.5) and asks for JSON-only output without file edits.
- `--apply` applies LLM **unified diffs** only (not full files): review gate by default, `--yes` to auto-confirm, timestamped backups + rollback on `patch` failure.
- Integration tests (`pytest -m integration`) need `docker compose up -d` (Neo4j + Postgres); they skip when backends are down. Use `pytest -m "not integration"` for unit-only runs.
- Sample bugs in `examples/target_app`: blocking `time.sleep` in async handler, DI yield without `try/finally`, unhandled index error → 500.
