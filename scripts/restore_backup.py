#!/usr/bin/env python3
"""Replay a backups/*.cypher file into a Neo4j instance.

Refuses to wipe a non-empty database unless --reset is given explicitly, so the script
can be run by hand without destroying the thing you were trying to inspect.

Environment: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, optional NEO4J_DATABASE.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).resolve().parent))
from keepalive import resolve_database  # noqa: E402

SPLIT = re.compile(r";\s*\n")


def statements_of(text: str) -> list[str]:
    out = []
    in_literal = False
    for i, ch in enumerate(text):
        if ch == "'":
            if i and text[i - 1] == "\\":
                continue
            in_literal = not in_literal
        elif ch == ";" and in_literal:
            raise SystemExit("Aborting: ';' inside a string literal; the splitter "
                             "would corrupt the import.")
    if in_literal:
        raise SystemExit("Aborting: unbalanced single quotes.")
    for chunk in SPLIT.split(text):
        body = "\n".join(line for line in chunk.splitlines()
                         if not line.lstrip().startswith("//")).strip()
        if body:
            out.append(body)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cypher", help="backup file, e.g. backups/FRAC_KG_latest.cypher")
    ap.add_argument("--reset", action="store_true",
                    help="DETACH DELETE every node first (required to overwrite a non-empty db)")
    ap.add_argument("--dry-run", action="store_true", help="parse and list only")
    args = ap.parse_args()

    path = Path(args.cypher)
    if not path.exists():
        print(f"ERROR: no such file: {path}", file=sys.stderr)
        return 2
    statements = statements_of(path.read_text(encoding="utf-8"))
    print(f"{path.name}: {len(statements)} statement(s)")
    if args.dry_run:
        for i, stmt in enumerate(statements, 1):
            print(f"  {i:>3}: {stmt.splitlines()[0][:88]}")
        return 0

    uri = os.environ.get("NEO4J_URI", "")
    user = os.environ.get("NEO4J_USER", "")
    password = os.environ.get("NEO4J_PASSWORD", "")
    if not (uri and user and password):
        print("ERROR: set NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD", file=sys.stderr)
        return 2

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        database = resolve_database(driver, os.environ.get("NEO4J_DATABASE", ""))
        with driver.session(database=database) as session:
            before = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            if before and not args.reset:
                print(f"ERROR: database '{database}' already holds {before} nodes. "
                      "Re-run with --reset to replace them, or point NEO4J_URI at an "
                      "empty instance.", file=sys.stderr)
                return 3
            if before and args.reset:
                print(f"reset: DETACH DELETE on {before} existing nodes ...")
                session.run("MATCH (n) DETACH DELETE n")
            for i, stmt in enumerate(statements, 1):
                session.run(stmt)
                if i % 10 == 0 or i == len(statements):
                    print(f"  [{i}/{len(statements)}] done")
            nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rels = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    finally:
        driver.close()

    print(f"restored into '{database}': nodes={nodes} relationships={rels}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
