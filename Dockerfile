# =============================================================================
# Production Dockerfile for Enterprise IT Support Swarm API
# Multi-stage build with uv and non-root execution
# =============================================================================

# --- Stage 1: Builder ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install project dependencies first (for Docker layer caching)
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /app/.venv && \
    uv pip install --no-cache -r pyproject.toml

# Copy project files
COPY src/ ./src/
COPY config/ ./config/
COPY runbooks/ ./runbooks/
COPY data/ ./data/

# Install the project itself in editable/wheel mode
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --no-cache -e .

# --- Stage 2: Final Runtime Image ---
FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install runtime dependencies (curl for container healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create a non-root system user for security
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Copy virtual environment and application code from builder
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser src/ /app/src/
COPY --chown=appuser:appuser config/ /app/config/
COPY --chown=appuser:appuser runbooks/ /app/runbooks/
COPY --chown=appuser:appuser data/ /app/data/
COPY --chown=appuser:appuser pyproject.toml /app/pyproject.toml

USER appuser

EXPOSE 8000

# Health check endpoint for ALB and Docker daemon
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "incident_swarm.app.api:app", "--host", "0.0.0.0", "--port", "8000"]

