"""
build_index.py — Build the Chroma vector index from the live FAQ corpus.

Replaces the old multi-entity synthetic-data builder. New shape, per the
v4.1.1 RAG readiness spec:

  - One Chroma collection PER LOCALE (faqs_en_gb, faqs_es_es). The spec
    is explicit that mixed-locale indexes hurt retrieval quality; language
    drift confuses the embedder.

  - One CHUNK per FAQ per locale. (~164 FAQs × 2 locales = ~328 chunks.)
    The chunk's embedded text is the retrieval header — question + variants
    + searchSummary — NOT the full answer body. Answers are often long and
    bury the signal. Retrieval headers are tight, intent-shaped, and that's
    what users actually type.

  - The answer body (markdown) lives in chunk metadata so it can be returned
    to the generation step without re-fetching. ChromaDB stores metadata
    alongside vectors.

  - applicable_opcos lives in metadata as a COMMA-JOINED STRING because
    Chroma's `where` clause doesn't natively support "list contains value"
    — we use a substring trick: store "british-airways,iberia" and query
    with $contains "british-airways". Lossy but works for our 3-opco set.

    Alternative would be one boolean column per opco (opco_british_airways=true,
    etc.) — cleaner schema but more rigid when new opcos are added. The
    string trick keeps the schema flat as the opco set grows.

Re-indexing strategy: total rebuild on every run. For ~328 chunks this
takes ~10s on a laptop. Production would do incremental updates from
Contentful webhooks.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import chromadb
from chromadb.config import Settings

# Allow `python src/build_index.py` to find the src package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.load_faqs import FaqChunk, LOCALES, load_all_chunks


# -----------------------------------------------------------------------------
# Paths and naming
# -----------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHROMA_DIR = PROJECT_ROOT / "chroma_db"
MANIFEST_PATH = PROJECT_ROOT / "build_manifest.json"


def collection_name_for(locale: str) -> str:
    """`en-GB` -> `faqs_en_gb`. Chroma names must be lowercase, no hyphens."""
    return f"faqs_{locale.lower().replace('-', '_')}"


# -----------------------------------------------------------------------------
# Build
# -----------------------------------------------------------------------------

def build() -> None:
    """Wipe the existing index and rebuild from the current Contentful state."""
    chunks = load_all_chunks()
    if not chunks:
        print("No chunks to index — exiting without touching ChromaDB.")
        return

    # Wipe — total rebuild is correct for a corpus this size
    if CHROMA_DIR.exists():
        print(f"\nWiping {CHROMA_DIR.relative_to(PROJECT_ROOT)} ...")
        shutil.rmtree(CHROMA_DIR)

    client = chromadb.PersistentClient(
        path=str(CHROMA_DIR),
        settings=Settings(anonymized_telemetry=False),
    )

    # Bucket chunks by locale — one collection per locale
    by_locale: dict[str, list[FaqChunk]] = {loc: [] for loc in LOCALES}
    for c in chunks:
        by_locale.setdefault(c.locale, []).append(c)

    print()
    for locale, locale_chunks in by_locale.items():
        if not locale_chunks:
            print(f"  {locale}: 0 chunks, skipping collection")
            continue
        name = collection_name_for(locale)
        print(f"  {locale}: building '{name}' with {len(locale_chunks)} chunks ...")
        collection = client.create_collection(
            name=name,
            metadata={"locale": locale},
        )

        # Build the batch payloads — Chroma accepts parallel arrays
        ids = [c.chunk_id for c in locale_chunks]
        documents = [c.retrieval_header for c in locale_chunks]
        metadatas = [_metadata_for(c) for c in locale_chunks]

        collection.add(ids=ids, documents=documents, metadatas=metadatas)

    # Write a small manifest the running server can read at startup so the
    # UI footer can show 'built X · N chunks'. Useful for confirming a fresh
    # build picked up the Contentful changes you just made.
    chunks_by_source: dict[str, int] = {}
    for c in chunks:
        chunks_by_source[c.source_type] = chunks_by_source.get(c.source_type, 0) + 1

    manifest = {
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_chunks": len(chunks),
        "chunks_by_locale": {loc: len(items) for loc, items in by_locale.items()},
        "chunks_by_source": chunks_by_source,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote build manifest: {MANIFEST_PATH.relative_to(PROJECT_ROOT)}")

    print("\nDone.")


def _metadata_for(c: FaqChunk) -> dict[str, str | int | bool]:
    """
    Flat metadata dict for ChromaDB. Only primitive types allowed
    (strings, numbers, booleans). Lists get joined into strings.

    OpCo filter uses BOOLEAN COLUMNS, one per opco. Earlier attempt used
    a comma-joined CSV + `$contains`, which silently returned zero results
    because Chroma's `where` clause doesn't support `$contains` on metadata
    (only on `where_document`). Booleans are idiomatic and query as `$eq True`.
    """
    return {
        "faq_id": c.faq_id,
        "internal_name": c.internal_name,
        "slug": c.slug,
        "locale": c.locale,
        "question": c.question,
        "short_answer": c.short_answer or "",
        "body_markdown": c.body,  # the full answer — returned to the generator
        "canonical_url": c.canonical_url,
        "hub_id": c.hub_id or "",
        "hub_name": c.hub_name or "",
        "hub_slug": c.hub_slug or "",
        "topic_id": c.topic_id or "",
        "topic_name": c.topic_name or "",
        "topic_slug": c.topic_slug or "",
        # Additional topics (Option C) — CSV for compactness. Display-only
        # in the current retrieval; we don't filter on these.
        "additional_topic_ids": ",".join(c.additional_topic_ids),
        "additional_topic_names": ",".join(c.additional_topic_names),
        # The load-bearing opco filter — three booleans, one per opco.
        # Query: where={"opco_british_airways": True}
        "opco_british_airways": "british-airways" in c.applicable_opcos,
        "opco_aer_lingus": "aer-lingus" in c.applicable_opcos,
        "opco_iberia": "iberia" in c.applicable_opcos,
        # Display-only CSV — read back into the RetrievalResult's applicable_opcos list
        # so the UI can render badges. Not used for filtering.
        "applicable_opcos_csv": ",".join(c.applicable_opcos),
        "last_reviewed_at": c.last_reviewed_at or "",
        "related_faq_ids": ",".join(c.related_faq_ids),
        # source_type drives the badge in the chunk UI and lets us add
        # per-source filtering at query time later (currently no filter).
        "source_type": c.source_type,
    }


if __name__ == "__main__":
    build()
