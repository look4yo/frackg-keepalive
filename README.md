# FRAC KG Keepalive

Keeps the deployed [FRAC KG](https://frackg-s2ka2iaeutit6wnzar23x7.streamlit.app/)
Streamlit app and its Neo4j Aura database alive, and keeps a replayable copy of the
graph in this repository.

## Why this repository exists

On 2026-08-27 Aura deleted the contents of the `FRAC` instance after it had been idle.
The previous version of this workflow was supposed to prevent exactly that, and it ran
successfully every 12 hours for two months -- until GitHub disabled it on 2026-07-16
with `state: disabled_inactivity`. Scheduled workflows are switched off after **60 days
without repository activity**, and a workflow's own successful runs do not count as
activity. The last commit here was 2026-05-17; 2026-05-17 + 60 days is the day the runs
stopped.

So this repository now defends against both failure modes.

## What it does

`keep-frackg-alive` (`.github/workflows/keepalive.yml`), every 12 hours:

1. Sends a best-effort GET to the Streamlit app. A sleeping or login-gated app is a
   warning, not a failure -- it does not lose data.
2. Connects to Aura, resolves the business database, and counts nodes and relationships.
   If the count falls below `MIN_NODES` the run **fails**, so GitHub emails you instead
   of you discovering the loss weeks later.
3. If the latest commit is older than `HEARTBEAT_AFTER_DAYS` (default 14), it pushes a
   `HEARTBEAT.md` commit. That resets the 60-day clock and is the only reason the
   schedule survives indefinitely.

`backup-frackg-graph` (`.github/workflows/backup.yml`), daily at 03:23 UTC:

Exports every node and relationship to `backups/FRAC_KG_latest.cypher` and commits it
when the content changed. The export refuses to write if the node count is below
`MIN_NODES`, so a wiped instance can never overwrite the last good snapshot.

## Required secrets

`Settings -> Secrets and variables -> Actions -> Repository secrets`:

| Secret | Value |
|---|---|
| `NEO4J_URI` | `neo4j+s://<instance-id>.databases.neo4j.io` |
| `NEO4J_USER` | the instance ID (new Aura instances no longer use `neo4j`) |
| `NEO4J_PASSWORD` | the instance password |
| `NEO4J_DATABASE` | optional. New instances name their database after the instance ID, not `neo4j`; when omitted the scripts detect it via `SHOW DATABASES` |

Optional repository **variables** (same settings page, "Variables" tab): `MIN_NODES`
(default `200`) and `HEARTBEAT_AFTER_DAYS` (default `14`).

## Restoring

Point the environment at an empty (or expendable) instance and replay the snapshot:

```bash
export NEO4J_URI="neo4j+s://<id>.databases.neo4j.io"
export NEO4J_USER="<id>"
export NEO4J_PASSWORD="<password>"
pip install neo4j
python scripts/restore_backup.py backups/FRAC_KG_latest.cypher          # dry inspection
python scripts/restore_backup.py backups/FRAC_KG_latest.cypher --reset  # replace contents
```

`--reset` is required to touch a non-empty database.

## Local check

```bash
python -m pip install neo4j requests
python scripts/keepalive.py       # prints node/relationship counts, exits non-zero on loss
python scripts/export_backup.py   # writes backups/FRAC_KG_latest.cypher
```

## Note on the database name

Aura renamed the default database to the instance ID. A session opened without an
explicit `database=` argument looks for `neo4j` and fails with
`Neo.ClientError.Database.DatabaseNotFound`. Every script here resolves the name once
(`resolve_database()` in `scripts/keepalive.py`), and the Streamlit app in
[look4yo/FRAC_KG](https://github.com/look4yo/FRAC_KG) does the same.
