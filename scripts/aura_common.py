#!/usr/bin/env python3
"""Shared Neo4j helpers for the keepalive scripts.

Deliberately free of third-party imports at module level: the backup job installs only
`neo4j`, and importing this module must not drag in `requests` (that coupling broke the
first backup run on 2026-08-27).
"""
from __future__ import annotations

COUNTS = """
MATCH (n)
WITH count(n) AS nodes
OPTIONAL MATCH ()-[r]->()
RETURN nodes, count(r) AS rels
"""


def resolve_database(driver, configured: str = "") -> str:
    """Return the business database to query.

    Newer Aura instances name their default database after the instance ID (e.g.
    "a91d1092") instead of "neo4j"; a session that omits database= then fails with
    Neo.ClientError.Database.DatabaseNotFound.
    """
    if configured:
        return configured
    try:
        with driver.session(database="system") as session:
            standard = [
                rec["name"]
                for rec in session.run(
                    "SHOW DATABASES YIELD name, type, currentStatus "
                    "RETURN name, type, currentStatus"
                )
                if rec["type"] == "standard" and rec["currentStatus"] == "online"
            ]
    except Exception as exc:  # restricted auth or a single-database server
        print(f"WARNING: could not probe databases ({exc}); using 'neo4j'")
        return "neo4j"
    if "neo4j" in standard:
        return "neo4j"
    if len(standard) == 1:
        print(f"note: auto-selected database '{standard[0]}'")
        return standard[0]
    print(f"WARNING: ambiguous databases {standard}; using 'neo4j'")
    return "neo4j"


def server_version(session) -> str:
    """Aura reports two components (Neo4j Kernel and Cypher); take the kernel row."""
    try:
        recs = session.run(
            "CALL dbms.components() YIELD name, versions, edition "
            "RETURN name, versions[0] AS version, edition").data()
    except Exception:
        return "unknown"
    kernel = next((r for r in recs if "kernel" in r["name"].lower()),
                  recs[0] if recs else None)
    if not kernel:
        return "unknown"
    return f"{kernel['name']}/{kernel['version']}/{kernel['edition']}"
