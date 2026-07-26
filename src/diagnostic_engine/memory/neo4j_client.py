"""Neo4j client for FastAPI topology GraphRAG."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from neo4j import Driver, GraphDatabase

from diagnostic_engine.config import Settings, get_settings


def _message_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _diff_hash(files: dict[str, Any] | str) -> str:
    raw = files if isinstance(files, str) else str(sorted(files.items()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


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
            "CREATE CONSTRAINT function_qname IF NOT EXISTS "
            "FOR (f:Function) REQUIRE f.qname IS UNIQUE",
            "CREATE CONSTRAINT error_pattern_key IF NOT EXISTS "
            "FOR (e:ErrorPattern) REQUIRE e.key IS UNIQUE",
            "CREATE CONSTRAINT decorator_key IF NOT EXISTS "
            "FOR (d:Decorator) REQUIRE d.key IS UNIQUE",
            "CREATE CONSTRAINT parameter_key IF NOT EXISTS "
            "FOR (p:Parameter) REQUIRE p.key IS UNIQUE",
            "CREATE CONSTRAINT diagnostic_session_id IF NOT EXISTS "
            "FOR (s:DiagnosticSession) REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT patch_id IF NOT EXISTS "
            "FOR (p:Patch) REQUIRE p.id IS UNIQUE",
        ]
        with self.driver.session() as session:
            for stmt in statements:
                try:
                    session.run(stmt)
                except Exception:
                    pass

    def upsert_topology(self, topology: dict[str, Any]) -> dict[str, int]:
        """Ingest routes, dependencies, and denser Function/Decorator/CALLS graph."""
        routes = topology.get("routes", [])
        deps = topology.get("dependencies", [])
        functions = topology.get("functions", [])
        name_to_qname: dict[str, str] = {f["name"]: f["qname"] for f in functions}

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
                # Mirror DI providers as Function nodes for Function-DEPENDS_ON-Function
                qname = name_to_qname.get(dep["name"]) or f"{dep['name']}"
                session.run(
                    """
                    MERGE (f:Function {qname: $qname})
                    SET f.name = $name,
                        f.file = $file,
                        f.line = $line,
                        f.is_async = $is_async,
                        f.is_dependency = true
                    """,
                    qname=qname,
                    name=dep["name"],
                    file=dep["file"],
                    line=dep["line"],
                    is_async=dep["is_async"],
                )

            for fn in functions:
                session.run(
                    """
                    MERGE (f:Function {qname: $qname})
                    SET f.name = $name,
                        f.file = $file,
                        f.line = $line,
                        f.is_async = $is_async
                    """,
                    qname=fn["qname"],
                    name=fn["name"],
                    file=fn["file"],
                    line=fn["line"],
                    is_async=fn["is_async"],
                )

                for dec in fn.get("decorators") or []:
                    dec_key = f"{fn['qname']}::{dec.get('raw') or dec.get('name')}"
                    session.run(
                        """
                        MERGE (d:Decorator {key: $key})
                        SET d.name = $name,
                            d.raw = $raw,
                            d.module = $module,
                            d.is_route = $is_route,
                            d.method = $method,
                            d.path = $path
                        WITH d
                        MATCH (f:Function {qname: $qname})
                        MERGE (f)-[:DECORATED_WITH]->(d)
                        """,
                        key=dec_key,
                        name=dec.get("name") or "",
                        raw=dec.get("raw") or "",
                        module=dec.get("module"),
                        is_route=bool(dec.get("is_route")),
                        method=dec.get("method"),
                        path=dec.get("path"),
                        qname=fn["qname"],
                    )
                    if dec.get("is_route") and dec.get("method") and dec.get("path"):
                        session.run(
                            """
                            MATCH (d:Decorator {key: $key})
                            MATCH (f:Function {qname: $qname})
                            MERGE (d)-[r:DEFINES_ROUTE]->(f)
                            SET r.method = $method, r.path = $path
                            """,
                            key=dec_key,
                            qname=fn["qname"],
                            method=dec["method"],
                            path=dec["path"],
                        )

                for param in fn.get("params") or []:
                    param_key = f"{fn['qname']}::{param['name']}"
                    session.run(
                        """
                        MERGE (p:Parameter {key: $key})
                        SET p.name = $name,
                            p.annotation = $annotation,
                            p.default = $default
                        WITH p
                        MATCH (f:Function {qname: $qname})
                        MERGE (f)-[:HAS_PARAM]->(p)
                        """,
                        key=param_key,
                        name=param["name"],
                        annotation=param.get("annotation"),
                        default=param.get("default"),
                        qname=fn["qname"],
                    )
                    ann = param.get("annotation") or ""
                    # Link DI typing when annotation mentions a known function name
                    for dep_name, dep_qname in name_to_qname.items():
                        if dep_name in ann or f"Depends({dep_name})" in (param.get("default") or ""):
                            session.run(
                                """
                                MATCH (p:Parameter {key: $key})
                                MATCH (tf:Function {qname: $tqname})
                                MERGE (p)-[r:TYPED_AS]->(tf)
                                SET r.annotation = $annotation
                                """,
                                key=param_key,
                                tqname=dep_qname,
                                annotation=ann,
                            )

                for call in fn.get("calls") or []:
                    to_name = call.get("to")
                    to_qname = name_to_qname.get(to_name)
                    if not to_qname:
                        continue
                    session.run(
                        """
                        MATCH (a:Function {qname: $from_q})
                        MATCH (b:Function {qname: $to_q})
                        MERGE (a)-[r:CALLS]->(b)
                        SET r.line = $line
                        """,
                        from_q=fn["qname"],
                        to_q=to_qname,
                        line=call.get("line"),
                    )

                for dep_name in fn.get("depends_on") or []:
                    short = dep_name.split(".")[-1]
                    to_qname = name_to_qname.get(short) or name_to_qname.get(dep_name)
                    if to_qname:
                        session.run(
                            """
                            MATCH (a:Function {qname: $from_q})
                            MATCH (b:Function {qname: $to_q})
                            MERGE (a)-[:DEPENDS_ON]->(b)
                            """,
                            from_q=fn["qname"],
                            to_q=to_qname,
                        )
                    session.run(
                        """
                        MATCH (a:Function {qname: $from_q})
                        MERGE (d:Dependency {name: $dep_name})
                        MERGE (a)-[:DEPENDS_ON]->(d)
                        """,
                        from_q=fn["qname"],
                        dep_name=short,
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
                handler_q = name_to_qname.get(route["function_name"])
                if handler_q:
                    session.run(
                        """
                        MATCH (e:Endpoint {key: $key})
                        MATCH (f:Function {qname: $qname})
                        MERGE (e)-[:HANDLED_BY]->(f)
                        """,
                        key=key,
                        qname=handler_q,
                    )
                for dep_name in route.get("dependencies", []):
                    short = dep_name.split(".")[-1]
                    session.run(
                        """
                        MERGE (e:Endpoint {key: $key})
                        MERGE (d:Dependency {name: $dep_name})
                        MERGE (e)-[:DEPENDS_ON]->(d)
                        """,
                        key=key,
                        dep_name=short,
                    )

        return {
            "routes": len(routes),
            "dependencies": len(deps),
            "functions": len(functions),
        }

    def upsert_error_patterns(self, chunks: list[dict[str, Any]]) -> int:
        """Create ErrorPattern nodes linked ORIGINATED_IN Function.

        Each chunk: exception_type, message, function_name, line (optional), file (optional).
        """
        count = 0
        now = datetime.now(timezone.utc).isoformat()
        with self.driver.session() as session:
            for chunk in chunks:
                exc_type = chunk.get("exception_type") or "Unknown"
                message = chunk.get("message") or chunk.get("exception_message") or ""
                fn_name = chunk.get("function_name")
                line = chunk.get("line")
                msg_hash = _message_hash(f"{exc_type}:{message}")
                key = f"{exc_type}:{fn_name or 'unknown'}:{msg_hash}"
                session.run(
                    """
                    MERGE (e:ErrorPattern {key: $key})
                    ON CREATE SET e.first_seen = $now
                    SET e.exception_type = $exception_type,
                        e.message_hash = $message_hash,
                        e.message = $message,
                        e.function_name = $function_name,
                        e.last_seen = $now
                    """,
                    key=key,
                    now=now,
                    exception_type=exc_type,
                    message_hash=msg_hash,
                    message=message[:500],
                    function_name=fn_name,
                )
                if fn_name:
                    session.run(
                        """
                        MATCH (e:ErrorPattern {key: $key})
                        MATCH (f:Function)
                        WHERE f.name = $function_name
                        MERGE (e)-[r:ORIGINATED_IN]->(f)
                        SET r.line = $line
                        """,
                        key=key,
                        function_name=fn_name,
                        line=line,
                    )
                count += 1
        return count

    def upsert_diagnostic_session(
        self,
        session_id: str,
        *,
        outcome: str | None = None,
        exception_type: str | None = None,
        failing_function: str | None = None,
        timestamp: str | None = None,
        error_pattern_key: str | None = None,
    ) -> None:
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        with self.driver.session() as session:
            session.run(
                """
                MERGE (s:DiagnosticSession {id: $id})
                SET s.timestamp = coalesce(s.timestamp, $timestamp),
                    s.outcome = $outcome,
                    s.exception_type = $exception_type,
                    s.failing_function = $failing_function
                """,
                id=str(session_id),
                timestamp=ts,
                outcome=outcome,
                exception_type=exception_type,
                failing_function=failing_function,
            )
            if error_pattern_key:
                session.run(
                    """
                    MATCH (s:DiagnosticSession {id: $id})
                    MATCH (e:ErrorPattern {key: $key})
                    MERGE (e)-[:DIAGNOSED_BY]->(s)
                    """,
                    id=str(session_id),
                    key=error_pattern_key,
                )
            elif failing_function and exception_type:
                session.run(
                    """
                    MATCH (s:DiagnosticSession {id: $id})
                    MATCH (e:ErrorPattern)
                    WHERE e.function_name = $fn AND e.exception_type = $exc
                    MERGE (e)-[:DIAGNOSED_BY]->(s)
                    """,
                    id=str(session_id),
                    fn=failing_function,
                    exc=exception_type,
                )

    def upsert_patch(
        self,
        patch_id: str,
        session_id: str,
        *,
        files: dict[str, Any] | None = None,
        applied: bool = False,
        modified_functions: list[str] | None = None,
    ) -> None:
        diff_h = _diff_hash(files or {})
        applied_at = datetime.now(timezone.utc).isoformat() if applied else None
        with self.driver.session() as session:
            session.run(
                """
                MERGE (p:Patch {id: $id})
                SET p.diff_hash = $diff_hash,
                    p.applied = $applied,
                    p.applied_at = CASE WHEN $applied THEN $applied_at ELSE p.applied_at END
                WITH p
                MERGE (s:DiagnosticSession {id: $session_id})
                MERGE (s)-[:PRODUCED]->(p)
                """,
                id=str(patch_id),
                diff_hash=diff_h,
                applied=applied,
                applied_at=applied_at,
                session_id=str(session_id),
            )
            for fn_name in modified_functions or []:
                session.run(
                    """
                    MATCH (p:Patch {id: $id})
                    MATCH (f:Function)
                    WHERE f.name = $fn OR f.qname ENDS WITH $suffix
                    MERGE (p)-[:MODIFIED]->(f)
                    """,
                    id=str(patch_id),
                    fn=fn_name,
                    suffix=f"::{fn_name}" if "::" not in fn_name else fn_name,
                )

    def traverse_from_function(self, function_name: str, hops: int = 3) -> list[dict[str, Any]]:
        """Structural context: endpoint, DI chain, decorators, prior ErrorPatterns."""
        hops = max(1, min(hops, 5))
        cypher = f"""
        MATCH (f:Function {{name: $func_name}})
        OPTIONAL MATCH (e:Endpoint)-[:HANDLED_BY]->(f)
        OPTIONAL MATCH (f)-[:DECORATED_WITH]->(dec:Decorator)
        OPTIONAL MATCH (f)-[:DEPENDS_ON*1..{hops}]->(dep)
        OPTIONAL MATCH (err:ErrorPattern)-[:ORIGINATED_IN]->(f)
        OPTIONAL MATCH (p:Patch)-[:MODIFIED]->(f)
        WITH f, e, dec, dep, err, p
        RETURN f.name AS function_name,
               f.qname AS qname,
               e.path AS endpoint,
               e.method AS method,
               collect(DISTINCT {{
                   type: labels(dep)[0],
                   name: coalesce(dep.name, dep.qname)
               }}) AS dependencies,
               collect(DISTINCT {{
                   decorator: dec.name,
                   method: dec.method,
                   path: dec.path,
                   is_route: dec.is_route
               }}) AS decorators,
               collect(DISTINCT {{
                   exception_type: err.exception_type,
                   message_hash: err.message_hash
               }}) AS error_patterns,
               collect(DISTINCT p.id) AS patches
        """
        # Also keep legacy Endpoint-centric rows for callers expecting flat edges
        legacy = f"""
        MATCH (e:Endpoint {{function_name: $func_name}})-[r:DEPENDS_ON|CALLS*1..{hops}]-(dependency)
        RETURN e.path AS endpoint,
               e.method AS method,
               e.function_name AS function_name,
               labels(dependency) AS type,
               dependency.name AS name
        """
        with self.driver.session() as session:
            rich = [dict(record) for record in session.run(cypher, func_name=function_name)]
            flat = [dict(record) for record in session.run(legacy, func_name=function_name)]
            if rich and any(
                rich[0].get("endpoint")
                or rich[0].get("dependencies")
                or rich[0].get("decorators")
                or rich[0].get("error_patterns")
            ):
                return [{"kind": "structural", **rich[0]}, *[{"kind": "edge", **row} for row in flat]]
            return flat

    def fragile_routes(self) -> list[dict[str, Any]]:
        """Killer query: routes with errors, DI chain, and optional patches."""
        cypher = """
        MATCH (e:ErrorPattern)-[:ORIGINATED_IN]->(f:Function)
        OPTIONAL MATCH (d:Decorator)-[:DEFINES_ROUTE]->(f)
        OPTIONAL MATCH (ep:Endpoint)-[:HANDLED_BY]->(f)
        OPTIONAL MATCH (f)-[:DEPENDS_ON*1..3]->(dep:Function)
        OPTIONAL MATCH (p:Patch)-[:MODIFIED]->(f)
        RETURN coalesce(d.path, ep.path) AS route,
               coalesce(d.method, ep.method) AS method,
               f.name AS handler,
               collect(DISTINCT dep.name) AS dependencies,
               e.exception_type AS error,
               collect(DISTINCT p.id)[0] AS last_patch
        ORDER BY route
        """
        with self.driver.session() as session:
            return [dict(record) for record in session.run(cypher)]

    def ping(self) -> bool:
        try:
            with self.driver.session() as session:
                session.run("RETURN 1")
            return True
        except Exception:
            return False
