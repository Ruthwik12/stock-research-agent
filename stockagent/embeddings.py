"""Embeddings through the local Ollama server (bge-m3)."""
import time

import requests

import config


class EmbeddingError(RuntimeError):
    pass


def embed(texts: list[str], batch_size: int = 16, retries: int = 3) -> list[list[float]]:
    """Embed texts with config.EMBED_MODEL. Retries transient failures; raises EmbeddingError otherwise."""
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        for attempt in range(1, retries + 1):
            try:
                resp = requests.post(
                    f"{config.OLLAMA_URL}/api/embed",
                    json={"model": config.EMBED_MODEL, "input": batch, "options": {"num_ctx": 8192}},
                    timeout=600,
                )
                resp.raise_for_status()
                out = resp.json()["embeddings"]
                if len(out) != len(batch):
                    raise EmbeddingError(f"asked for {len(batch)} embeddings, got {len(out)}")
                vectors.extend(out)
                break
            except (requests.RequestException, KeyError, EmbeddingError) as exc:
                if attempt == retries:
                    raise EmbeddingError(f"Ollama embedding failed after {retries} attempts: {exc}. "
                                         f"Is Ollama running and is '{config.EMBED_MODEL}' pulled?") from exc
                time.sleep(2 * attempt)
    return vectors
