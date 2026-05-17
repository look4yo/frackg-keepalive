# FRAC KG Keepalive

This repository keeps the deployed FRAC KG Streamlit app and its Neo4j Aura database warm on a conservative schedule.

## What it does

The GitHub Actions workflow runs every 12 hours and:

1. Sends a GET request to the deployed Streamlit app.
2. Connects to Neo4j Aura and executes a lightweight `RETURN 1 AS ok` query.

The Neo4j credentials are not stored in this repository. They must be configured as GitHub Actions secrets.

## Required Secrets

Add these under `Settings -> Secrets and variables -> Actions -> Repository secrets`:

- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`

## Manual Run

The workflow also supports `workflow_dispatch`, so it can be triggered manually from the Actions tab.
