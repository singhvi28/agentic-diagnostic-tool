"""Ingest target FastAPI app topology into Neo4j (+ seed Qdrant from error logs)."""

from __future__ import annotations

import argparse
from pathlib import Path

from diagnostic_engine.analysis.fastapi_routes import extract_fastapi_topology
from diagnostic_engine.config import get_settings
from diagnostic_engine.memory.neo4j_client import Neo4jMemory
from diagnostic_engine.memory.qdrant_client import QdrantMemory


def ingest(root: Path | None = None) -> dict:
    settings = get_settings()
    app_root = root or settings.target_app_root

    topology = extract_fastapi_topology(app_root)
    neo = Neo4jMemory(settings)
    neo.ensure_constraints()
    counts = neo.upsert_topology(topology)
    neo.close()

    qdrant = QdrantMemory(settings)
    qdrant.ensure_collection()
    seeded = 0
    log_path = settings.error_log_path
    if log_path.exists():
        # Seed each traceback chunk separated by blank lines
        chunks = [c.strip() for c in log_path.read_text(encoding="utf-8").split("\n\n") if c.strip()]
        for chunk in chunks:
            qdrant.upsert_log(chunk, metadata={"source": str(log_path)})
            seeded += 1

    return {"neo4j": counts, "qdrant_seeded": seeded, "root": str(app_root)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest FastAPI app into GraphRAG stores")
    parser.add_argument("--root", type=Path, default=None, help="Target app root")
    args = parser.parse_args()
    result = ingest(args.root)
    print(result)


if __name__ == "__main__":
    main()
