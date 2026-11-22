"""Step 3: chunk parsed sections, embed with bge-m3 (Ollama), store in Chroma with metadata.

Chunking: paragraph-aware, about CHUNK_CHARS characters (~600 tokens) with CHUNK_OVERLAP_CHARS
overlap, never crossing a section boundary. Each chunk is embedded with a short header
("AAPL 10-Q filed 2026-07-31, section MD&A") so the vector itself carries company and period;
the stored document text is the chunk alone.
Incremental: documents whose doc_id is already in the collection are skipped (--force rebuilds).

Run from the project root:
    python scripts/03_ingest.py [--force]
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent.embeddings import embed  # noqa: E402
from stockagent.vectorstore import get_collection  # noqa: E402

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


def split_long(paragraph: str, limit: int) -> list[str]:
    """Split a paragraph longer than limit at sentence boundaries (hard cut as a last resort)."""
    pieces, current = [], ""
    for sentence in SENTENCE_SPLIT.split(paragraph):
        while len(sentence) > limit:            # a single giant "sentence" (usually a table)
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        if current and len(current) + len(sentence) + 1 > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def chunk_text(text: str, min_chars: int = config.MIN_CHUNK_CHARS) -> list[str]:
    paragraphs = []
    for p in text.split("\n"):
        p = p.strip()
        if p:
            paragraphs.extend(split_long(p, config.CHUNK_CHARS) if len(p) > config.CHUNK_CHARS else [p])

    chunks, current = [], []
    for p in paragraphs:
        if current and sum(len(x) + 1 for x in current) + len(p) > config.CHUNK_CHARS:
            chunks.append("\n".join(current))
            # Overlap: carry the last paragraphs (up to CHUNK_OVERLAP_CHARS) into the next chunk.
            carry, size = [], 0
            for prev in reversed(current):
                if size + len(prev) > config.CHUNK_OVERLAP_CHARS:
                    break
                carry.insert(0, prev)
                size += len(prev) + 1
            current = carry
        current.append(p)
    if current:
        chunks.append("\n".join(current))
    return [c for c in chunks if len(c) >= min_chars]


def main() -> None:
    force = "--force" in sys.argv
    collection = get_collection()
    parsed_files = sorted(config.PARSED_DIR.glob("*.json"))
    total_new, t0 = 0, time.time()

    for path in parsed_files:
        doc = json.loads(path.read_text())
        already = collection.get(where={"doc_id": doc["doc_id"]}, limit=1, include=[])["ids"]
        if already and not force:
            continue
        if already:
            collection.delete(where={"doc_id": doc["doc_id"]})

        ids, texts, header_texts, metas = [], [], [], []
        news = doc["form_type"] == "news"   # a headline + summary is short; keep it whole
        for section in doc["sections"]:
            for i, chunk in enumerate(chunk_text(section["text"], 40 if news else config.MIN_CHUNK_CHARS)):
                slug = re.sub(r"[^a-z]+", "-", section["section"].lower()).strip("-")
                ids.append(f"{doc['doc_id']}::{slug}::{i:04d}")
                texts.append(chunk)
                header = (f"{doc['ticker']} news, {doc.get('publisher', '')}, published {doc['filing_date']}" if news
                          else f"{doc['ticker']} {doc['form_type']} filed {doc['filing_date']}, "
                               f"section {section['section']}")
                header_texts.append(f"{header}:\n{chunk}")
                metas.append({
                    "doc_id": doc["doc_id"],
                    "ticker": doc["ticker"],
                    "form_type": doc["form_type"],
                    "filing_date": doc["filing_date"],
                    "filing_date_int": int(doc["filing_date"].replace("-", "")),   # for $lte as-of filters
                    "period_end": doc["period_end"] or "",
                    "section": section["section"],
                    "chunk_index": i,
                    "source_url": doc["source_url"],
                    "accession": doc["accession"],
                })
        if not ids:
            print(f"{doc['doc_id']:38} no chunks (missing sections: {doc.get('missing_sections')})")
            continue

        t_doc = time.time()
        vectors = embed(header_texts)
        collection.add(ids=ids, embeddings=vectors, documents=texts, metadatas=metas)
        total_new += len(ids)
        avg = sum(len(t) for t in texts) // len(texts)
        print(f"{doc['doc_id']:38} {len(ids):>4} chunks  avg {avg:,} chars  {time.time() - t_doc:6.1f}s")

    print(f"\nAdded {total_new} chunks in {time.time() - t0:.0f}s. Collection '{config.CHROMA_COLLECTION}' "
          f"now holds {collection.count()} chunks at {config.CHROMA_DIR.relative_to(config.PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
