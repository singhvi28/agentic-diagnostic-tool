"""Neo4j denser topology tests (skip when Neo4j is down)."""

from __future__ import annotations

from pathlib import Path

import pytest

from diagnostic_engine.analysis.fastapi_routes import extract_fastapi_topology
from diagnostic_engine.config import get_settings
from diagnostic_engine.memory.ingest import ingest
from diagnostic_engine.memory.neo4j_client import Neo4jMemory

ROOT = Path(__file__).resolve().parents[1]
TARGET_ROOT = ROOT / "examples" / "target_app"


@pytest.fixture
def neo():
    get_settings.cache_clear()
    memory = Neo4jMemory()
    if not memory.ping():
        memory.close()
        pytest.skip("Neo4j unavailable")
    yield memory
    memory.close()


def test_upsert_topology_creates_function_decorator_and_depends(neo: Neo4jMemory):
    topology = extract_fastapi_topology(TARGET_ROOT)
    neo.ensure_constraints()
    counts = neo.upsert_topology(topology)
    assert counts["functions"] >= 3
    assert counts["routes"] >= 3

    with neo.driver.session() as session:
        calls = session.run(
            """
            MATCH (f:Function {name: 'checkout'})-[:DEPENDS_ON]->(d)
            RETURN labels(d) AS labels, coalesce(d.name, d.qname) AS name
            """
        )
        rows = [dict(r) for r in calls]
        names = {r["name"] for r in rows}
        assert "get_db" in names

        dec = session.run(
            """
            MATCH (d:Decorator)-[:DEFINES_ROUTE]->(f:Function {name: 'checkout'})
            RETURN d.method AS method, d.path AS path
            """
        ).single()
        assert dec is not None
        assert dec["method"] == "POST"
        assert dec["path"] == "/orders/checkout"

        params = session.run(
            """
            MATCH (f:Function {name: 'checkout'})-[:HAS_PARAM]->(p:Parameter)
            RETURN collect(p.name) AS names
            """
        ).single()
        assert "db" in params["names"]


def test_ingest_seeds_error_patterns(neo: Neo4jMemory):
    result = ingest(TARGET_ROOT)
    assert result["neo4j"].get("skipped") is False
    assert result["neo4j"].get("error_patterns", 0) >= 1

    fragile = neo.fragile_routes()
    handlers = {r["handler"] for r in fragile}
    assert "checkout" in handlers or "get_order" in handlers

    ctx = neo.traverse_from_function("checkout", hops=3)
    assert ctx
    structural = next((c for c in ctx if c.get("kind") == "structural"), ctx[0])
    assert structural.get("function_name") == "checkout" or structural.get("endpoint")
