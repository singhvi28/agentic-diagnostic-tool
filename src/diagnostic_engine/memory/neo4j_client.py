"""Neo4j client for FastAPI topology GraphRAG."""

from __future__ import annotations

from typing import Any

from neo4j import Driver, GraphDatabase

from diagnostic_engine.config import Settings, get_settings


class Neo4jMemory:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._driver: Driver | None = None

    @property
    def driver(self) -> Driver:
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self.settings.neo4j_uri,
                auth=(self.settings.neo4j_user, self.settings.neo4j_password),
            )
        return self._driver

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def ensure_constraints(self) -> None:
        statements = [
            "CREATE CONSTRAINT endpoint_key IF NOT EXISTS "
            "FOR (e:Endpoint) REQUIRE e.key IS UNIQUE",
            "CREATE CONSTRAINT dependency_name IF NOT EXISTS "
            "FOR (d:Dependency) REQUIRE d.name IS UNIQUE",
            "CREATE CONSTRAINT service_name IF NOT EXISTS "
            "FOR (s:Service) REQUIRE s.name IS UNIQUE",
            "CREATE CONSTRAINT model_name IF NOT EXISTS "
            "FOR (m:Model) REQUIRE m.name IS UNIQUE",
        ]
        with self.driver.session() as session:
            for stmt in statements:
                try:
                    session.run(stmt)
                except Exception:
                    pass

    def upsert_topology(self, topology: dict[str, Any]) -> dict[str, int]:
        """Ingest routes + dependencies from extract_fastapi_topology()."""
        routes = topology.get("routes", [])
        deps = topology.get("dependencies", [])
        with self.driver.session() as session:
            for dep in deps:
                session.run(
                    """
                    MERGE (d:Dependency {name: $name})
                    SET d.is_async = $is_async,
                        d.is_generator = $is_generator,
                        d.has_try_finally = $has_try_finally,
                        d.file = $file,
                        d.line = $line
                    """,
                    **dep,
                )
            for route in routes:
                key = f"{route['method']} {route['path']}"
                session.run(
                    """
                    MERGE (e:Endpoint {key: $key})
                    SET e.path = $path,
                        e.method = $method,
                        e.function_name = $function_name,
                        e.file = $file,
                        e.line = $line,
                        e.is_async = $is_async
                    """,
                    key=key,
                    path=route["path"],
                    method=route["method"],
                    function_name=route["function_name"],
                    file=route["file"],
                    line=route["line"],
                    is_async=route["is_async"],
                )
                for dep_name in route.get("dependencies", []):
                    session.run(
                        """
                        MERGE (e:Endpoint {key: $key})
                        MERGE (d:Dependency {name: $dep_name})
                        MERGE (e)-[:DEPENDS_ON]->(d)
                        """,
                        key=key,
                        dep_name=dep_name,
                    )
        return {"routes": len(routes), "dependencies": len(deps)}

    def traverse_from_function(self, function_name: str, hops: int = 3) -> list[dict[str, Any]]:
        hops = max(1, min(hops, 5))
        cypher = f"""
        MATCH (e:Endpoint {{function_name: $func_name}})-[r:DEPENDS_ON|CALLS*1..{hops}]-(dependency)
        RETURN e.path AS endpoint,
               e.method AS method,
               e.function_name AS function_name,
               labels(dependency) AS type,
               dependency.name AS name
        """
        with self.driver.session() as session:
            return [dict(record) for record in session.run(cypher, func_name=function_name)]

    def ping(self) -> bool:
        try:
            with self.driver.session() as session:
                session.run("RETURN 1")
            return True
        except Exception:
            return False
