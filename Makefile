# =============================================================================
# Enterprise IT Support Swarm — Makefile
# Shell: Git Bash (MINGW64) on Windows
# Usage: make <target>
# =============================================================================

export MSYS_NO_PATHCONV := 1

UV          := uv
VENV        := .venv
# Windows path (Git Bash)
VENV_BIN    := $(VENV)/Scripts
PYTHON      := $(VENV_BIN)/python.exe
PIP         := $(VENV_BIN)/pip.exe
PYTEST      := $(VENV_BIN)/pytest.exe
RUFF        := $(VENV_BIN)/ruff.exe

TRANSCRIPT_DIR := data/raw/historical_transcripts

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# HELP
# ---------------------------------------------------------------------------
.PHONY: help
help:
	@echo ""
	@echo "  Enterprise IT Support Swarm — available targets"
	@echo "  ------------------------------------------------"
	@echo "  Environment"
	@echo "    make venv          Create .venv with uv"
	@echo "    make install       Install project + dev deps into .venv"
	@echo "    make install-extra Install boto3 + extra pipeline deps"
	@echo ""
	@echo "  Database (PostgreSQL via Docker on port 5433)"
	@echo "    make db-up         Start Postgres container (detached)"
	@echo "    make db-down       Stop Postgres container (keep volume)"
	@echo "    make db-reset      Wipe volume and re-seed from scratch"
	@echo "    make db-check      Verify row counts and env keys"
	@echo "    make db-shell      Open psql shell inside the container"
	@echo ""
	@echo "  Data / Transcripts"
	@echo "    make profile       Profile transcript distribution"
	@echo "    make profile-pairs Profile transcripts + show problem/resolution pairs"
	@echo "    make upload-dry    Validate transcripts for S3 (dry-run, no upload)"
	@echo "    make upload-s3     Upload transcripts to S3 (requires S3_BUCKET in .env)"
	@echo ""
	@echo "  Distillation (requires GOOGLE_API_KEY + S3_BUCKET in .env)"
	@echo "    make distill-sample  Run distillation on 50 random transcripts (pilot)"
	@echo "    make distill-review  Review quality of distilled_sample.jsonl by eye"
	@echo "    make distill-full    Run distillation on all 1110 transcripts"
	@echo ""
	@echo "  Day 4: Ingestion & Procedural Memory (Qdrant)"
	@echo "    make ingest-distill  Concurrently distil all transcripts from S3 (resumable)"
	@echo "    make ingest-stats    Show quality metrics and indexable count for distilled data"
	@echo "    make ingest-index    Embed and index qualified resolutions into Qdrant"
	@echo "    make memory-search   Query procedural memory (e.g. make memory-search QUERY=\"...\")"
	@echo ""
	@echo "  Day 5: Knowledge, CAG & Runbook RAG"
	@echo "    make index-runbooks  Index official markdown runbooks into Qdrant"
	@echo "    make index-faq       Index Tier 1 FAQ questions into Qdrant CAG collection"
	@echo "    make tune-cag        Evaluate CAG semantic threshold against test questions"
	@echo "    make ask             Query routing check (CAG vs RAG, e.g. make ask QUERY=\"...\")"
	@echo "    make tag-canonical   Tag distilled records with canonical incident IDs & runbooks"
	@echo "    make memory-search-canonical  Procedural search with duplicate hits collapsed"
	@echo ""
	@echo "  Day 6: Agent Graph, FastAPI Service & Voice Agent"
	@echo "    make run-api         Start the FastAPI server on port 8000"
	@echo "    make run-voice       Start LiveKit Voice Agent worker (dev mode)"
	@echo "    make voice-console   Run Voice Agent in interactive CLI console mode"
	@echo ""
	@echo "  Docker & Compose (Whole System: Postgres + API + Voice Agent)"
	@echo "    make docker-up       Build and start all 3 services (Postgres, API, Voice)"
	@echo "    make docker-down     Stop all Docker Compose services"
	@echo "    make docker-ps       Check status and health of containers"
	@echo "    make docker-logs     Follow logs from all running containers"
	@echo "    make docker-logs-api Follow logs from the FastAPI Ops Console"
	@echo "    make docker-logs-voice Follow logs from the LiveKit Voice Agent"
	@echo "    make docker-test     Run full pytest suite inside the Docker container"
	@echo "    make docker-reset    Wipe DB volume, re-seed, and rebuild whole stack"
	@echo "    make docker-check    Test http://localhost:8000/health endpoint"
	@echo ""
	@echo "  AWS ECS & SSM Deployment"
	@echo "    make ssm-sync        Push .env secrets to AWS SSM as SecureString"
	@echo "    make aws-ecr-create  Create Amazon ECR repository"
	@echo "    make aws-ecr-push    Build & push Docker image to Amazon ECR"
	@echo "    make aws-iam-setup   Configure IAM roles for ECS Fargate & SSM access"
	@echo "    make aws-ecs-deploy  Deploy ECS Fargate service & ALB"
	@echo "    make aws-verify      Verify live health and chat on ALB"
	@echo ""
	@echo "  Quality"
	@echo "    make test          Run pytest"
	@echo "    make lint          Run ruff linter"
	@echo "    make fmt           Auto-fix ruff lint issues"
	@echo ""
	@echo "  Misc"
	@echo "    make clean         Remove .venv and __pycache__ dirs"
	@echo ""

# ---------------------------------------------------------------------------
# ENVIRONMENT
# ---------------------------------------------------------------------------
.PHONY: venv
venv:
	$(UV) venv $(VENV)
	@echo "✅  .venv created — activate with: source .venv/Scripts/activate"

.PHONY: install
install:
	$(UV) pip install -e ".[dev]"
	@echo "✅  Project + dev deps installed"

.PHONY: install-extra
install-extra:
	$(UV) pip install boto3 langchain-core qdrant-client openai
	@echo "✅  Extra pipeline deps installed"

# ---------------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------------
.PHONY: db-up
db-up:
	docker compose up -d postgres
	@echo "✅  Postgres started on port 5433 — wait a few seconds then run: make db-check"

.PHONY: db-down
db-down:
	docker compose down
	@echo "🛑  Postgres stopped (volume preserved)"

.PHONY: db-reset
db-reset:
	docker compose down -v
	docker compose up -d postgres
	@echo "🔄  Postgres wiped and re-seeding — wait ~10 seconds then run: make db-check"

.PHONY: db-check
db-check:
	$(PYTHON) scripts/check_setup.py

.PHONY: db-shell
db-shell:
	docker exec -it swarm-postgres psql -U postgres -d ops_db

# ---------------------------------------------------------------------------
# DATA / TRANSCRIPTS
# ---------------------------------------------------------------------------
.PHONY: profile
profile:
	$(PYTHON) scripts/profile_transcripts.py $(TRANSCRIPT_DIR)

.PHONY: profile-pairs
profile-pairs:
	$(PYTHON) scripts/profile_transcripts.py $(TRANSCRIPT_DIR) --pairs

.PHONY: upload-dry
upload-dry:
	$(PYTHON) -m incident_swarm.pipeline.s3_upload \
		--source $(TRANSCRIPT_DIR) \
		--dry-run

.PHONY: upload-s3
upload-s3:
	$(PYTHON) -m incident_swarm.pipeline.s3_upload \
		--source $(TRANSCRIPT_DIR) \
		--create-bucket

# ---------------------------------------------------------------------------
# DISTILLATION
# ---------------------------------------------------------------------------
DISTILL_SAMPLE := data/distilled_sample.jsonl
DISTILL_FULL   := data/distilled_full.jsonl

.PHONY: distill-sample
distill-sample:
	$(PYTHON) -m incident_swarm.pipeline.distill \
		--provider gemini \
		--limit 50 \
		--out $(DISTILL_SAMPLE)
	@echo "✅  Sample done → review with: make distill-review"

.PHONY: distill-review
distill-review:
	$(PYTHON) -m incident_swarm.pipeline.distill \
		--review $(DISTILL_SAMPLE)

.PHONY: distill-full
distill-full:
	$(PYTHON) -m incident_swarm.pipeline.distill \
		--provider gemini \
		--limit 1110 \
		--out $(DISTILL_FULL)
	@echo "✅  Full distillation done → $(DISTILL_FULL)"

# ---------------------------------------------------------------------------
# DAY 4: INGESTION & PROCEDURAL MEMORY (QDRANT)
# ---------------------------------------------------------------------------
INGEST_FILE ?= data/distilled_full.jsonl
WORKERS     ?= 4
LIMIT       ?= 0
QUERY       ?= Redis out of memory

.PHONY: ingest-distill
ingest-distill:
	$(PYTHON) -m incident_swarm.pipeline.ingest distill \
		--workers $(WORKERS) \
		--limit $(LIMIT) \
		--out $(INGEST_FILE)
	@echo "✅  Concurrent distillation completed → $(INGEST_FILE)"

.PHONY: ingest-stats
ingest-stats:
	$(PYTHON) -m incident_swarm.pipeline.ingest stats \
		--file $(INGEST_FILE)

.PHONY: ingest-index
ingest-index:
	$(PYTHON) -m incident_swarm.pipeline.ingest index \
		--file $(INGEST_FILE)
	@echo "✅  Distilled records indexed into Qdrant procedural memory"

.PHONY: memory-search
memory-search:
	$(PYTHON) -m incident_swarm.pipeline.ingest search "$(QUERY)"

# ---------------------------------------------------------------------------
# DAY 5: KNOWLEDGE, CAG & RUNBOOK RAG
# ---------------------------------------------------------------------------
.PHONY: index-runbooks
index-runbooks:
	$(PYTHON) -m incident_swarm.pipeline.knowledge index-runbooks
	@echo "✅  Runbooks chunked and indexed into Qdrant"

.PHONY: index-faq
index-faq:
	$(PYTHON) -m incident_swarm.pipeline.knowledge index-faq
	@echo "✅  FAQ entries indexed into Qdrant CAG collection"

.PHONY: tune-cag
tune-cag:
	$(PYTHON) -m incident_swarm.pipeline.knowledge tune

.PHONY: ask
ask:
	$(PYTHON) -m incident_swarm.pipeline.knowledge ask "$(QUERY)"

.PHONY: tag-canonical
tag-canonical:
	$(PYTHON) -m incident_swarm.pipeline.knowledge tag-canonical \
		--file $(INGEST_FILE)
	@echo "✅  Canonical incident ids and runbooks tagged in $(INGEST_FILE)"

.PHONY: memory-search-canonical
memory-search-canonical:
	$(PYTHON) -m incident_swarm.pipeline.knowledge memory-search "$(QUERY)"

# ---------------------------------------------------------------------------
# DAY 6: AGENT GRAPH, FASTAPI SERVICE & VOICE AGENT
# ---------------------------------------------------------------------------
.PHONY: run-api
run-api:
	$(PYTHON) -m uvicorn incident_swarm.app.api:app --reload --host 0.0.0.0 --port 8000

.PHONY: run-voice
run-voice:
	set PREFER_IPV4=1 && set LIVEKIT_DISABLE_REGION_SELECTION=1 && $(PYTHON) -m incident_swarm.voice.agent dev

.PHONY: voice-console
voice-console:
	set PREFER_IPV4=1 && set LIVEKIT_DISABLE_REGION_SELECTION=1 && $(PYTHON) -m incident_swarm.voice.agent console

# ---------------------------------------------------------------------------
# DOCKER & DOCKER COMPOSE (WHOLE SYSTEM: POSTGRES + API + VOICE WORKER)
# ---------------------------------------------------------------------------
IMAGE_NAME := incident-swarm-api:latest

.PHONY: docker-build
docker-build:
	docker build -t $(IMAGE_NAME) .
	@echo "✅  Docker image built: $(IMAGE_NAME)"

.PHONY: docker-run
docker-run:
	docker run --rm -it -p 8000:8000 --env-file .env $(IMAGE_NAME)

.PHONY: docker-up compose-up
docker-up compose-up:
	docker compose up --build -d
	@echo "✅  Postgres, FastAPI Swarm API, and Voice Agent running!"
	@echo "👉  Ops Console: http://localhost:8000"
	@echo "👉  Health check: http://localhost:8000/health"

.PHONY: docker-down compose-down
docker-down compose-down:
	docker compose down
	@echo "🛑  Compose services stopped"

.PHONY: docker-ps compose-ps
docker-ps compose-ps:
	docker compose ps

.PHONY: docker-logs compose-logs
docker-logs compose-logs:
	docker compose logs -f

.PHONY: docker-logs-api
docker-logs-api:
	docker compose logs -f api

.PHONY: docker-logs-voice
docker-logs-voice:
	docker compose logs -f voice

.PHONY: docker-test
docker-test:
	docker compose run --rm api pytest -v

.PHONY: docker-reset
docker-reset:
	docker compose down -v
	docker compose up --build -d
	@echo "🔄  Stack reset, database re-seeded, and containers restarted."

.PHONY: docker-check
docker-check:
	@curl -f http://localhost:8000/health && echo " - API is healthy and reachable"

.PHONY: ssm-verify
ssm-verify:
	$(PYTHON) scripts/setup_ssm.py --verify

.PHONY: ssm-dry
ssm-dry:
	$(PYTHON) scripts/setup_ssm.py --dry-run

.PHONY: ssm-sync
ssm-sync:
	$(PYTHON) scripts/setup_ssm.py --sync

.PHONY: aws-ecr-create
aws-ecr-create:
	bash infra/scripts/01_create_ecr.sh

.PHONY: aws-ecr-push
aws-ecr-push:
	bash infra/scripts/02_build_and_push_ecr.sh

.PHONY: aws-iam-setup
aws-iam-setup:
	bash infra/scripts/03_setup_iam_roles.sh

.PHONY: aws-ecs-deploy
aws-ecs-deploy:
	bash infra/scripts/04_deploy_ecs_fargate.sh

.PHONY: aws-verify
aws-verify:
	bash infra/scripts/05_verify_deployment.sh

.PHONY: aws-stop
aws-stop:
	@echo "Pausing Fargate container (desired-count=0)..."
	aws ecs update-service --cluster incident-swarm-cluster --service incident-swarm-api-service --desired-count 0 --region $$(aws configure get region || echo "ap-south-1") >/dev/null
	@echo "✅ Fargate container paused. No compute charges while paused."

.PHONY: aws-start
aws-start:
	@echo "Resuming Fargate container (desired-count=1)..."
	aws ecs update-service --cluster incident-swarm-cluster --service incident-swarm-api-service --desired-count 1 --region $$(aws configure get region || echo "ap-south-1") >/dev/null
	@echo "✅ Fargate container resumed."

.PHONY: aws-destroy
aws-destroy:
	bash infra/scripts/99_teardown_aws.sh

# ---------------------------------------------------------------------------
# QUALITY
# ---------------------------------------------------------------------------
.PHONY: test
test:
	$(PYTEST) -v

.PHONY: lint
lint:
	$(RUFF) check src tests scripts

.PHONY: fmt
fmt:
	$(RUFF) check --fix src tests scripts
	@echo "✅  Lint issues auto-fixed"

# ---------------------------------------------------------------------------
# MISC
# ---------------------------------------------------------------------------
.PHONY: clean
clean:
	rm -rf $(VENV)
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@echo "🧹  Cleaned .venv and cache dirs"

