"""
retrieve.py — Query the FAQ index with opco + locale filters.

Replaces the single-collection version with locale-routed lookups and a
HARD opco filter applied at the vector-store level (per v4.1.1 spec:
"never soft re-rank — an AerClub user must never see a BA-only answer").

Two filters, one mandatory, one optional:

  - locale (required): routes to the right collection (en-GB / es-ES).
    Mixed-locale retrieval hurts quality, so we never query across.

  - opco (optional): if set, narrows to chunks where applicable_opcos
    contains the user's opco. Implemented as a substring match on a
    comma-joined CSV string in metadata (see build_index.py for the
    rationale).

Everything else (hub_id, topic_id, slug, etc.) is in metadata and
available for ad-hoc `extra_filters` via the same dict-of-equalities
interface as the synthetic-demo version.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings


# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHROMA_DIR = PROJECT_ROOT / "chroma_db"

VALID_OPCOS = {"british-airways", "aer-lingus", "iberia"}


# -----------------------------------------------------------------------------
# Result shape
# -----------------------------------------------------------------------------

@dataclass
class RetrievalResult:
    """One ranked hit from the FAQ vector store."""
    rank: int                # 1-indexed
    chunk_id: str            # `{faq_id}#{locale}`
    faq_id: str              # Contentful sys.id
    internal_name: str       # editor-facing label
    question: str            # canonical question (en-GB or es-ES as queried)
    short_answer: str        # one-paragraph TL;DR
    body_markdown: str       # the full answer — sent to the generator
    canonical_url: str       # citation URL
    hub_name: str            # 'Shopping online' (FAQ only — empty for sections/banners)
    topic_name: str          # 'Missing Avios' — canonical / primary topic (FAQ only)
    additional_topic_names: list[str]  # extra topic blocks this FAQ appears in
    applicable_opcos: list[str]  # the actual opcos this FAQ serves
    last_reviewed_at: str | None
    distance: float          # lower = closer match. >1.0 = mediocre.
    source_type: str         # 'faq' | 'inspiration_section' | 'banner'


# -----------------------------------------------------------------------------
# Lazy client — open once, reuse
# -----------------------------------------------------------------------------

_client: chromadb.PersistentClient | None = None
_collections: dict[str, chromadb.Collection] = {}


def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
    return _client


def _get_collection(locale: str) -> chromadb.Collection:
    """Lazy lookup of a per-locale collection."""
    if locale not in _collections:
        name = f"faqs_{locale.lower().replace('-', '_')}"
        _collections[locale] = _get_client().get_collection(name)
    return _collections[locale]


# -----------------------------------------------------------------------------
# Filter translation
# -----------------------------------------------------------------------------

# Opco enum -> the boolean metadata column we filter on
_OPCO_COLUMN: dict[str, str] = {
    "british-airways": "opco_british_airways",
    "aer-lingus": "opco_aer_lingus",
    "iberia": "opco_iberia",
}


def _where_for(opco: str | None, extra: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Build a Chroma `where` clause that:
      - filters by opco via a boolean metadata column (opco_<slug> == True)
      - AND-combines any extra equality filters

    The opco filter is the load-bearing safety mechanism. If you pass
    opco='aer-lingus' and a chunk's opco_aer_lingus is False, the chunk
    is excluded BEFORE vector similarity is even computed.

    Earlier version used `$contains` on a comma-joined CSV — silently
    matched zero because Chroma's metadata `where` doesn't support
    `$contains` (only `where_document` does).
    """
    clauses: list[dict[str, Any]] = []
    if opco:
        if opco not in VALID_OPCOS:
            raise ValueError(f"Unknown opco: {opco!r}. Use one of {sorted(VALID_OPCOS)}.")
        clauses.append({_OPCO_COLUMN[opco]: True})
    if extra:
        for key, value in extra.items():
            clauses.append({key: value})
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def retrieve(
    query: str,
    locale: str = "en-GB",
    opco: str | None = None,
    extra_filters: dict[str, Any] | None = None,
    k: int = 5,
) -> list[RetrievalResult]:
    """
    Retrieve up to k FAQ chunks ranked by semantic similarity.

    Args:
        query: the user's natural-language question.
        locale: 'en-GB' or 'es-ES'. Routes to the matching collection.
        opco: 'british-airways' | 'aer-lingus' | 'iberia' | None.
            When set, applies a HARD pre-retrieval filter — chunks whose
            applicable_opcos doesn't include this opco are excluded BEFORE
            the vector search. This is the safety mechanism the spec calls
            out: an AerClub user must never see a BA-only answer.
        extra_filters: optional dict of metadata equality filters (e.g.
            {"hub_slug": "shopping-online"} for hub-scoped queries).
        k: maximum number of results to return.
    """
    collection = _get_collection(locale)
    where = _where_for(opco, extra_filters)

    kwargs: dict[str, Any] = {"query_texts": [query], "n_results": k}
    if where is not None:
        kwargs["where"] = where

    raw = collection.query(**kwargs)
    ids = raw["ids"][0]
    documents = raw["documents"][0]  # the retrieval headers, kept for debugging
    metadatas = raw["metadatas"][0]
    distances = raw["distances"][0]

    results: list[RetrievalResult] = []
    for rank, (cid, _doc, meta, dist) in enumerate(
        zip(ids, documents, metadatas, distances), start=1
    ):
        opcos_csv = meta.get("applicable_opcos_csv") or ""
        opcos = [o for o in opcos_csv.split(",") if o]
        addl_names = [n for n in (meta.get("additional_topic_names") or "").split(",") if n]
        results.append(RetrievalResult(
            rank=rank,
            chunk_id=cid,
            faq_id=meta.get("faq_id", ""),
            internal_name=meta.get("internal_name", ""),
            question=meta.get("question", ""),
            short_answer=meta.get("short_answer", ""),
            body_markdown=meta.get("body_markdown", ""),
            canonical_url=meta.get("canonical_url", ""),
            hub_name=meta.get("hub_name", ""),
            topic_name=meta.get("topic_name", ""),
            additional_topic_names=addl_names,
            applicable_opcos=opcos,
            last_reviewed_at=meta.get("last_reviewed_at") or None,
            distance=dist,
            source_type=meta.get("source_type") or "faq",
        ))
    return results


# -----------------------------------------------------------------------------
# Pretty-print
# -----------------------------------------------------------------------------

def format_results(results: list[RetrievalResult], heading: str | None = None) -> str:
    lines: list[str] = []
    if heading:
        lines.append(f"=== {heading} ===")
    if not results:
        lines.append("(no results)")
        return "\n".join(lines)
    for r in results:
        opcos = ",".join(r.applicable_opcos) or "(none)"
        lines.append(f"[{r.rank}] {r.internal_name}  (distance: {r.distance:.3f}, opcos: {opcos})")
        lines.append(f"    Q: {r.question}")
        lines.append(f"    {r.hub_name} › {r.topic_name}")
        lines.append("")
    return "\n".join(lines)


# Demo — exercise the opco filter against a high-traffic query
if __name__ == "__main__":
    Q = "I'm missing my Avios"

    print(format_results(retrieve(Q, locale="en-GB", k=3),
                          heading=f"en-GB, no opco filter: '{Q}'"))
    for opco in ["british-airways", "aer-lingus", "iberia"]:
        print(format_results(retrieve(Q, locale="en-GB", opco=opco, k=3),
                              heading=f"en-GB, opco={opco}: '{Q}'"))
