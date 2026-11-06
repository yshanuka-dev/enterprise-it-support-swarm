"""Settings (from environment / .env) and model-tier routing (from config/models.yaml)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
MODELS_YAML = ROOT / "config" / "models.yaml"


class Settings(BaseSettings):
    """Runtime settings. Locally read from .env; on ECS injected from SSM Parameter Store."""

    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = Field(..., description="Postgres connection string")

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    google_api_key: str | None = None
    xai_api_key: str | None = None      # Grok / xAI
    groq_api_key: str | None = None     # Groq Cloud (free tier, OpenAI-compatible)

    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    qdrant_url: str | None = None
    qdrant_api_key: str | None = None

    aws_region: str = "ap-southeast-1"
    s3_bucket: str | None = None

    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Return cached settings. Call get_settings.cache_clear() in tests after changing env."""
    return Settings()


class Tier(BaseModel):
    """One model tier (small / medium / large)."""

    provider: str
    model: str
    max_output_tokens: int


class Embedding(BaseModel):
    """Embedding configuration for Qdrant / procedural memory."""

    provider: str
    model: str
    dimensions: int


class ModelConfig(BaseModel):
    """Parsed config/models.yaml."""

    tiers: dict[str, Tier]
    roles: dict[str, str]
    limits: dict[str, float]
    embedding: Embedding | None = None

    @model_validator(mode="after")
    def _roles_use_known_tiers(self) -> ModelConfig:
        unknown = {r: t for r, t in self.roles.items() if t not in self.tiers}
        if unknown:
            raise ValueError(f"roles reference unknown tiers: {unknown}")
        return self

    def tier_for(self, role: str) -> Tier:
        """Return the model tier configured for an agent role."""
        return self.tiers[self.roles[role]]


def load_model_config(path: Path = MODELS_YAML) -> ModelConfig:
    """Load and validate the model routing config."""
    with open(path, encoding="utf-8") as fh:
        return ModelConfig(**yaml.safe_load(fh))
