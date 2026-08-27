#!/usr/bin/env python3
"""Export the live FRAC-KG graph to a replayable Cypher script in backups/.

This is the actual safety net. The 2026-08 incident showed that a keepalive ping only
buys time; what recovered the data was a copy that lived outside the cloud instance.

The output is a single rolling file so the repository stays small: git history keeps
one version per day in which the graph actually changed, because the workflow commits
only when the content differs.

Environment: same as scripts/keepalive.py.
  BACKUP_PATH   destination file (default backups/FRAC_KG_latest.cypher)
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aura_common import resolve_database  # noqa: E402

NODE_QUERY = """
MATCH (n)
RETURN labels(n) AS labels, n.name AS name,
       [k IN keys(n) WHERE k <> 'name'] AS otherKeys
ORDER BY labels[0], name
"""

REL_QUERY = """
MATCH (a)-[r]->(b)
RETURN labels(a) AS aLabels, a.name AS aName, type(r) AS relType,
       labels(b) AS bLabels, b.name AS bName, keys(r) AS propKeys
ORDER BY relType, aName, bName
"""

CONSTRAINT = ("CREATE CONSTRAINT {slug}_name_unique IF NOT EXISTS "
              "FOR (n:`{label}`) REQUIRE n.name IS UNIQUE;")


def esc(value: str) -> str:
    return (value.replace("\\", "\\\\").replace("'", "\\'")
            .replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t"))


def lit(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + esc(str(value)) + "'"


def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def build(nodes, rels, exported: str) -> tuple[str, list[str]]:
    lines = [
        "// FRAC-KG graph backup -- exported from the live Neo4j instance by CI",
        f"// Exported : {exported}",
        f"// Source   : {os.environ.get('NEO4J_URI', '')}",
        f"// Nodes    : {len(nodes)}   Relationships: {len(rels)}",
        "// Replay   : set NEO4J_URI/USER/PASSWORD[/DATABASE], then",
        "//            python scripts/restore_backup.py backups/FRAC_KG_latest.cypher --reset",
        "",
    ]
    skipped: list[str] = []

    groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for rec in nodes:
        labels = tuple(rec["labels"] or ())
        name = rec.get("name")
        if not labels or not name:
            skipped.append(f"{labels} keys={rec.get('otherKeys')}")
            continue
        groups[labels].append(name)

    lines.append("// ---------- Constraints ----------")
    for label in sorted({labels[0] for labels in groups}):
        slug = label.replace(" ", "_").lower()
        lines.append(CONSTRAINT.format(slug=slug, label=label))
    lines.append("")

    lines.append("// ---------- Nodes ----------")
    for labels in sorted(groups):
        pattern = "".join(f":`{l}`" for l in labels)
        for part in chunked(sorted(set(groups[labels])), 500):
            names = ", ".join(lit(n) for n in part)
            lines.append(f"UNWIND [{names}] AS name MERGE (n{pattern} {{name:name}});")
    lines.append("")

    lines.append("// ---------- Relationships ----------")
    keyed: dict[tuple[str, str, str], list[tuple[str, str]]] = defaultdict(list)
    for rec in rels:
        a_labels, b_labels = rec["aLabels"] or [], rec["bLabels"] or []
        if not a_labels or not b_labels or not rec.get("aName") or not rec.get("bName"):
            skipped.append(f"rel {rec.get('relType')} with unnamed endpoint")
            continue
        if rec.get("propKeys"):
            # Relationship properties: emit one statement each so they stay addressable.
            lines.append(
                f"MATCH (a:`{a_labels[0]}` {{name:{lit(rec['aName'])}}}), "
                f"(b:`{b_labels[0]}` {{name:{lit(rec['bName'])}}}) "
                f"MERGE (a)-[r:`{rec['relType']}`]->(b);")
            continue
        keyed[(a_labels[0], rec["relType"], b_labels[0])].append(
            (rec["aName"], rec["bName"]))

    for (a_label, rel_type, b_label), pairs in sorted(keyed.items()):
        for part in chunked(pairs, 200):
            body = ",\n  ".join(f"{{src:{lit(a)}, dst:{lit(b)}}}" for a, b in part)
            lines.append(
                f"UNWIND [\n  {body}\n] AS row\n"
                f"MATCH (a:`{a_label}` {{name: row.src}})\n"
                f"MATCH (b:`{b_label}` {{name: row.dst}})\n"
                f"MERGE (a)-[:`{rel_type}`]->(b);")
    lines.append("")

    if skipped:
        lines.append("// ---------- SKIPPED (no unique name property) ----------")
        lines.extend(f"// {item}" for item in skipped)

    return "\n".join(lines).rstrip() + "\n", skipped


def main() -> int:
    uri = os.environ.get("NEO4J_URI", "")
    user = os.environ.get("NEO4J_USER", "")
    password = os.environ.get("NEO4J_PASSWORD", "")
    if not (uri and user and password):
        print("ERROR: NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD are required", file=sys.stderr)
        return 1

    out = Path(os.environ.get("BACKUP_PATH", "backups/FRAC_KG_latest.cypher"))
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        database = resolve_database(driver, os.environ.get("NEO4J_DATABASE", ""))
        with driver.session(database=database) as session:
            nodes = [r.data() for r in session.run(NODE_QUERY)]
            rels = [r.data() for r in session.run(REL_QUERY)]
    except Exception as exc:
        print(f"ERROR: export query failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        driver.close()

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text, skipped = build(nodes, rels, stamp)

    # Guard before writing: a wiped instance must not overwrite the last good backup.
    try:
        min_nodes = int(os.environ.get("MIN_NODES", "200"))
    except ValueError:
        min_nodes = 200
    if len(nodes) < min_nodes:
        print(f"ALERT: only {len(nodes)} nodes exported, below MIN_NODES={min_nodes}. "
              f"{out} was left untouched -- the instance may have been paused and wiped.",
              file=sys.stderr)
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)

    def body_without_timestamp(source: str) -> str:
        # Ignore the export timestamp so an unchanged graph produces no commit.
        return "\n".join(line for line in source.splitlines()
                         if not line.startswith("// Exported"))

    previous = out.read_text(encoding="utf-8") if out.exists() else ""
    changed = body_without_timestamp(previous) != body_without_timestamp(text)

    out.write_text(text, encoding="utf-8")
    print(f"database={database} nodes={len(nodes)} relationships={len(rels)}")
    print(f"wrote {out} ({len(text):,} chars) changed={changed}")
    if skipped:
        print(f"WARNING: {len(skipped)} item(s) skipped for lacking a name property")
    # Consumed by the workflow step that decides whether to commit.
    (out.parent / ".changed").write_text("1" if changed else "0", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
