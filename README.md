# Agentic FastAPI Diagnostic Engine

Autonomous root-cause analysis (RCA) and remediation for FastAPI apps, using:

1. **Memory (GraphRAG)** — Neo4j for code topology, Qdrant for semantic log search  
2. **Tools (FastMCP)** — AST checks, sandboxed code reads, hybrid RCA, test runner  
3. **Orchestration (LangGraph)** — parse → retrieve → diagnose/patch → test → retry  

## Quick start

```bash
# 1. Python env
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# 2. GraphRAG backends
docker compose up -d

# 3. Ingest the sample buggy app into Neo4j (+ seed Qdrant from logs)
diagnostic-ingest

# 4. Run unit tests (no LLM / DBs required for analysis helpers)
pytest -q

# 5. Start the MCP tool server
diagnostic-mcp

# 6. Run the diagnostic agent over the sample error log
diagnostic-agent --log logs/app_errors.log
```

Optional: run the target app to generate live errors:

```bash
uvicorn examples.target_app.main:app --reload --port 8001
```

## Layout

```text
src/diagnostic_engine/
  analysis/     # traceback parser, async-blocking AST, FastAPI route extraction
  memory/       # Neo4j + Qdrant clients, ingest CLI
  mcp/          # FastMCP server (tools + resources)
  agent/        # LangGraph state machine + CLI
examples/target_app/   # intentionally buggy FastAPI app
logs/app_errors.log    # sample tracebacks
```

## MCP surface

| Kind | URI / name | Purpose |
|------|------------|---------|
| Resource | `logs://runtime/errors` | Tail of error log |
| Resource | `routes://app/endpoints` | Static FastAPI topology |
| Tool | `read_code` | Sandboxed source slices |
| Tool | `analyze_ast_for_async_blocking` | Sync I/O inside `async def` |
| Tool | `hybrid_root_cause_analysis` | Qdrant + Neo4j context |
| Tool | `run_reproduction_test` | Execute generated pytest |

## Notes

- Qdrant uses a deterministic hash embedding for offline/dev; swap in a real model for production.
- Without `OPENAI_API_KEY`, the diagnose/patch node returns a stub RCA so the graph still runs.
- Sample bugs in `examples/target_app`: blocking `time.sleep` in async handler, DI yield without `try/finally`, unhandled index error → 500.
