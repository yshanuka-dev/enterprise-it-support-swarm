# Enterprise IT Support & Incident Resolution Swarm

Enterprise IT Support & Incident Resolution Swarm (AEE Bootcamp final hackathon).
A LangGraph multi-agent system that triages, investigates and resolves IT incidents using
MCP tools, RAG and memory over historical transcripts, with a LiveKit voice escalation path,
Langfuse observability and an AWS ECS Fargate deployment.

> Status: Day 1 (foundation). Architecture diagram, setup details and results will be added as the build progresses.

## Quick start (local)

```bash
cp .env.example .env            # set POSTGRES_PASSWORD and DATABASE_URL to match
docker compose up -d postgres   # seeds the DB from db/init/01_ops_seed.sql
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python scripts/check_setup.py   # verifies the seed data and config
pytest
```

## Layout

| Path | Purpose |
|---|---|
| `src/incident_swarm/app` | API, LangGraph graph, nodes, state |
| `src/incident_swarm/mcp_servers` | Ticketing, Observability, Action MCP servers |
| `src/incident_swarm/memory` | Short-term, long-term, episodic, procedural memory |
| `src/incident_swarm/pipeline` | S3 upload, transcript distillation, CAG builder |
| `src/incident_swarm/voice` | LiveKit agent, barge-in, latency |
| `config/models.yaml` | Model tiers, role routing, limits |
| `runbooks/` | Runbook markdown files (indexed into Qdrant) |
| `db/init/` | Seed SQL (6 tables) |

## Secrets

No secrets in code or images. Local: `.env` (git-ignored). Production: AWS SSM Parameter Store.
