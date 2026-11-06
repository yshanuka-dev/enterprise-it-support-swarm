"""Text embedders behind one small interface, chosen by the `embedding` block in models.yaml.

Providers:
    fastembed  local ONNX model, no API key, no rate limits, lowest latency (recommended)
    google     Gemini embedding API
"""

from __future__ import annotations

from typing import Protocol

from incident_swarm.config import Embedding, Settings, get_settings, load_model_config


class Embedder(Protocol):
    """Anything that turns a list of texts into equally sized vectors."""

    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text."""
        ...


class FastEmbedEmbedder:
    """Local embeddings via fastembed (the model downloads once on first use)."""

    def __init__(self, model: str, dimensions: int):
        self.model_name = model
        self.dimensions = dimensions
        self._model = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts locally."""
        if self._model is None:
            from fastembed import TextEmbedding  # imported lazily: heavy and optional

            self._model = TextEmbedding(self.model_name)
        return [vec.tolist() for vec in self._model.embed(texts)]


class GoogleEmbedder:
    """Gemini embeddings via the google-genai SDK."""

    def __init__(self, model: str, dimensions: int, api_key: str):
        from google import genai

        self.model_name = model
        self.dimensions = dimensions
        self._client = genai.Client(api_key=api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts with the Gemini API."""
        from google.genai import types

        response = self._client.models.embed_content(
            model=self.model_name,
            contents=texts,
            config=types.EmbedContentConfig(output_dimensionality=self.dimensions),
        )
        return [list(e.values) for e in response.embeddings]


def get_embedder(cfg: Embedding | None = None, settings: Settings | None = None) -> Embedder:
    """Build the embedder described in config/models.yaml."""
    cfg = cfg or load_model_config().embedding
    if cfg.provider == "fastembed":
        return FastEmbedEmbedder(cfg.model, cfg.dimensions)
    if cfg.provider == "google":
        settings = settings or get_settings()
        if not settings.google_api_key:
            raise ValueError("GOOGLE_API_KEY is required for the google embedder")
        return GoogleEmbedder(cfg.model, cfg.dimensions, settings.google_api_key)
    raise ValueError(f"unknown embedding provider: {cfg.provider}")
