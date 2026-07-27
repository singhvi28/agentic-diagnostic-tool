"""Ingest target FastAPI app topology into Neo4j (+ seed pgvector from error logs)."""

from __future__ import annotations

import argparse
from pathlib import Path

from diagnostic_engine.analysis.fastapi_routes import extract_fastapi_topology
from diagnostic_engine.analysis.traceback_parser import parse_traceback
from diagnostic_engine.config import get_settings
from diagnostic_engine.memory.neo4j_client import Neo4jMemory
from diagnostic_engine.memory.pgvector_client import PgVectorMemory


def ingest(root: Path | None = None) -> dict:
    settings = get_settings()
    app_root = root or settings.target_app_root

    topology = extract_fastapi_topology(app_root)
    neo = Neo4jMemory(settings)
    neo_counts: dict = {"routes": 0, "dependencies": 0, "functions": 0, "skipped": True}
    error_patterns = 0
    try:
        if neo.ping():
            neo.ensure_constraints()
            neo_counts = neo.upsert_topology(topology)
            neo_counts["skipped"] = False

            log_path = settings.error_log_path
            if log_path.exists():
                chunks = [
                    c.strip()
                    for c in log_path.read_text(encoding="utf-8").split("\n\n")
                    if c.strip()
                ]
                patterns: list[dict] = []
                for chunk in chunks:
                    parsed = parse_traceback(chunk)
                    frame = parsed.failing_frame
                    patterns.append(
                        {
                            "exception_type": parsed.exception_type,
                            "message": parsed.exception_message or "",
                            "function_name": frame.function_name if frame else None,
                            "line": frame.line_number if frame else None,
                            "file": frame.filename if frame else None,
                        }
                    )
                error_patterns = neo.upsert_error_patterns(patterns)
                neo_counts["error_patterns"] = error_patterns

            try:
                fastrp = neo.write_fastrp_embeddings(embedding_dimension=128)
                neo_counts["fastrp"] = fastrp
            except Exception as exc:  # noqa: BLE001
                neo_counts["fastrp"] = {"skipped": True, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        neo_counts = {"error": str(exc)}
    finally:
        neo.close()

    pg = PgVectorMemory(settings)
    seeded = 0
    try:
        if pg.ping():
            pg.ensure_schema()
            log_path = settings.error_log_path
            if log_path.exists():
                chunks = [
                    c.strip()
                    for c in log_path.read_text(encoding="utf-8").split("\n\n")
                    if c.strip()
                ]
                for chunk in chunks:
                    parsed = parse_traceback(chunk)
                    meta = {
                        "source": str(log_path),
                        "exception_type": parsed.exception_type,
                        "function_name": (
                            parsed.failing_frame.function_name if parsed.failing_frame else None
                        ),
                    }
                    pg.upsert_log(chunk, metadata=meta)
                    seeded += 1
    except Exception as exc:  # noqa: BLE001
        return {
            "neo4j": neo_counts,
            "pgvector_seeded": seeded,
            "pgvector_error": str(exc),
            "root": str(app_root),
        }

    return {"neo4j": neo_counts, "pgvector_seeded": seeded, "root": str(app_root)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest FastAPI app into GraphRAG stores")
    parser.add_argument("--root", type=Path, default=None, help="Target app root")
    args = parser.parse_args()
    result = ingest(args.root)
    print(result)


if __name__ == "__main__":
    main()
