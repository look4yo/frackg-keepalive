#!/usr/bin/env python3
"""Keep the FRAC-KG deployment warm and prove the Aura database is still queryable.

The Streamlit ping is best effort: a sleeping or login-gated app is an inconvenience,
not a data-loss risk. The Aura query is authoritative -- if it fails, or if the graph
has fewer nodes than MIN_NODES, the script exits non-zero so GitHub notifies you.

That threshold is the point of this file. The 2026-08 incident was silent: Aura paused
the instance, then deleted its contents, and nothing complained until the GUI was
opened weeks later. A count check that fails the run turns that into an email.

Environment:
  NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD   required
  NEO4J_DATABASE                          optional; auto-detected when empty
  APP_URL                                 Streamlit app to wake up
  MIN_NODES                                 alert threshold (default 200)
"""
from __future__ import annotations

import os
import sys

import requests
from neo4j import GraphDatabase

COUNTS = """
MATCH (n)
WITH count(n) AS nodes
OPTIONAL MATCH ()-[r]->()
RETURN nodes, count(r) AS rels
"""

LABEL_BREAKDOWN = "MATCH (n:`{label}`) RETURN count(n) AS c"


def resolve_database(driver, configured: str) -> str:
    """Newer Aura instances name their database after the instance ID, not 'neo4j'."""
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
    except Exception as exc:  # restricted auth or a server without multi-db rights
        print(f"WARNING: could not probe databases ({exc}); using 'neo4j'")
        return "neo4j"
    if "neo4j" in standard:
        return "neo4j"
    if len(standard) == 1:
        print(f"note: auto-selected database '{standard[0]}'")
        return standard[0]
    print(f"WARNING: ambiguous databases {standard}; using 'neo4j'")
    return "neo4j"


def ping_app(url: str) -> None:
    if not url:
        print("APP_URL not set -- skipping the Streamlit wake-up")
        return
    try:
        resp = requests.get(url, timeout=60)
        print(f"Streamlit: HTTP {resp.status_code} -> {resp.url}")
        if "/-/auth/" in str(resp.url):
            print("WARNING: the app redirected to a login page, so it is not public. "
                  "The container may not have been woken, and readers of the paper "
                  "cannot open it either.")
    except Exception as exc:
        # Deliberately not fatal: the database is what we are protecting.
        print(f"WARNING: Streamlit ping failed ({exc}) -- continuing with the Aura check")


def main() -> int:
    uri = os.environ.get("NEO4J_URI", "")
    user = os.environ.get("NEO4J_USER", "")
    password = os.environ.get("NEO4J_PASSWORD", "")
    missing = [name for name, value in (
        ("NEO4J_URI", uri), ("NEO4J_USER", user), ("NEO4J_PASSWORD", password)) if not value]
    if missing:
        print(f"ERROR: missing secrets: {', '.join(missing)}", file=sys.stderr)
        return 1

    try:
        min_nodes = int(os.environ.get("MIN_NODES", "200"))
    except ValueError:
        print("ERROR: MIN_NODES must be an integer", file=sys.stderr)
        return 1

    ping_app(os.environ.get("APP_URL", ""))

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        database = resolve_database(driver, os.environ.get("NEO4J_DATABASE", ""))
        with driver.session(database=database) as session:
            row = session.run(COUNTS).single()
            nodes, rels = row["nodes"], row["rels"]
            labels = [rec["label"] for rec in session.run(
                "CALL db.labels() YIELD label RETURN label ORDER BY label")]
            breakdown = {label: session.run(LABEL_BREAKDOWN.format(label=label)).single()["c"]
                         for label in labels}
            try:
                # Aura returns two components (Neo4j Kernel and Cypher); take the kernel.
                recs = session.run(
                    "CALL dbms.components() YIELD name, versions, edition "
                    "RETURN name, versions[0] AS version, edition").data()
                kernel = next((r for r in recs if "kernel" in r["name"].lower()),
                              recs[0] if recs else None)
                server = (f"{kernel['name']}/{kernel['version']}/{kernel['edition']}"
                          if kernel else "unknown")
            except Exception:
                server = "unknown"
    except Exception as exc:
        print(f"ERROR: Neo4j query failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        driver.close()

    print(f"Neo4j: {server}  database={database}")
    print(f"nodes={nodes} relationships={rels}")
    print("per label: " + ", ".join(f"{k}={v}" for k, v in sorted(breakdown.items())))

    if nodes < min_nodes:
        print(f"ALERT: node count {nodes} is below the expected minimum {min_nodes}. "
              "The instance may have been paused and wiped -- restore from "
              "backups/FRAC_KG_latest.cypher.", file=sys.stderr)
        return 1
    print(f"OK: {nodes} nodes >= MIN_NODES={min_nodes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
