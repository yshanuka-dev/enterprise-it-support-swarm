# =============================================================================
# Enterprise IT Support Swarm — Makefile
# Shell: Git Bash (MINGW64) on Windows
# Usage: make <target>
# =============================================================================

PYTHON      := python
UV          := uv
VENV        := .venv
VENV_BIN    := $(VENV)/Scripts   # Windows path (Git Bash)
PIP         := $(VENV_BIN)/pip
PYTEST      := $(VENV_BIN)/pytest
RUFF        := $(VENV_BIN)/ruff

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

