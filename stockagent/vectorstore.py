"""Chroma collection access, shared by ingestion and retrieval."""
import chromadb

import config


def get_collection():
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    return client.get_or_create_collection(
        name=config.CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine", "embed_model": config.EMBED_MODEL},
    )
