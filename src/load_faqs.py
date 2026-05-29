"""
load_faqs.py — Fetch the FAQ corpus from Contentful's Delivery API and
normalise it into a flat shape ready for indexing.

Replaces the old load_data.py (which read synthetic JSON files). The
Delivery API is read-only and cached at the edge, so this is cheap and
safe to call as often as we want.

What gets fetched:
  - All `faq` entries (one per FAQ)
  - All `faqTopic` entries (to resolve topic context)
  - All `servicingHub` entries (to resolve hub context)
  - For every entry we ask for `locale=*` so both en-GB and es-ES content
    arrive in a single round trip.

Per the v4.1.1 RAG readiness spec, each FAQ becomes one chunk PER LOCALE.
That's two chunks per FAQ if both en-GB and es-ES are populated. We
don't build cross-locale chunks — the spec is explicit that mixed-locale
indexes hurt retrieval quality.

Filtering at load time:
  - Skip entries where `ragInclude` is false (editorial opt-out)
  - Skip entries that are unpublished (CDA returns published-only anyway,
    but we double-check)
  - We do NOT filter by `applicableOpcos` here — that's done at QUERY time
    as a hard metadata filter so the same index serves every opco

Output: a list of `FaqChunk` dataclasses (one per FAQ per locale), ready
for build_index.py to embed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

CDA_HOST = "https://cdn.contentful.com"
SPACE_ID = os.getenv("CONTENTFUL_SPACE_ID")
DELIVERY_TOKEN = os.getenv("CONTENTFUL_DELIVERY_TOKEN")
ENVIRONMENT = os.getenv("CONTENTFUL_ENVIRONMENT", "master")

# Valid OpCo enum values per the v4.1.1 schema. Anything else gets dropped
# from a FAQ's applicable_opcos list — defensive, in case an editor types
# a typo into the field.
VALID_OPCOS: set[str] = {"british-airways", "aer-lingus", "iberia"}

# Both locales we currently care about. Add to this set when new locales come online.
LOCALES: tuple[str, ...] = ("en-GB", "es-ES")


# -----------------------------------------------------------------------------
# Data shapes
# -----------------------------------------------------------------------------

@dataclass
class FaqChunk:
    """
    One FAQ in one locale, normalised for indexing.

    The `retrieval_header` is what gets embedded (question + variants +
    searchSummary). The `body` is the full markdown answer — sent to the
    model at generation time. Metadata fields drive the query-time hard
    filter and citation rendering.
    """
    # Identity
    chunk_id: str            # `{faq_id}#{locale}` — unique across the corpus
    faq_id: str              # Contentful sys.id (e.g. faq-im-missing-avios-... )
    internal_name: str       # Editor-facing label
    slug: str                # URL-safe slug
    locale: str              # 'en-GB' | 'es-ES'

    # Embedded text + answer body
    question: str            # Canonical question
    retrieval_header: str    # question + variants + searchSummary — for embedding
    body: str                # Markdown answer

    # Display + citation
    short_answer: str        # Polished TL;DR for voice / accordion preview
    canonical_url: str       # Live avios.com URL for citation

    # Hierarchy context
    hub_id: str | None
    hub_name: str | None
    hub_slug: str | None
    topic_id: str | None
    topic_name: str | None
    topic_slug: str | None

    # The load-bearing filter signal — HARD pre-retrieval filter in the
    # vector store (never re-rank on this). Per v4.1.1 spec.
    applicable_opcos: list[str] = field(default_factory=list)

    # Provenance
    last_reviewed_at: str | None = None
    related_faq_ids: list[str] = field(default_factory=list)


# -----------------------------------------------------------------------------
# CDA fetch
# -----------------------------------------------------------------------------

def _ensure_config() -> None:
    """Fail fast with a clear message if env vars are missing."""
    if not SPACE_ID or not DELIVERY_TOKEN:
        raise RuntimeError(
            "CONTENTFUL_SPACE_ID and CONTENTFUL_DELIVERY_TOKEN must be set. "
            f"Check {PROJECT_ROOT / '.env'}."
        )


def _cda_get(path: str, params: dict[str, str]) -> dict[str, Any]:
    """Single GET against the Delivery API."""
    qs = urllib.parse.urlencode(params)
    url = f"{CDA_HOST}{path}?{qs}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {DELIVERY_TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"CDA request failed ({e.code}): {url}\n{e.read().decode('utf-8', 'replace')}"
        ) from e


def _fetch_all_entries(content_type: str) -> list[dict[str, Any]]:
    """
    Fetch every entry of a given content type. Pages with limit=1000.
    The full FAQ corpus is ~164 entries so one page covers it, but the
    pagination loop is here for headroom.
    """
    _ensure_config()
    path = f"/spaces/{SPACE_ID}/environments/{ENVIRONMENT}/entries"
    out: list[dict[str, Any]] = []
    skip = 0
    while True:
        body = _cda_get(path, {
            "content_type": content_type,
            "limit": "1000",
            "skip": str(skip),
            "locale": "*",
        })
        items = body.get("items", [])
        out.extend(items)
        if len(items) < 1000:
            break
        skip += 1000
    return out


# -----------------------------------------------------------------------------
# Field readers
#
# When we ask for `locale=*` Contentful wraps even non-localised fields as
# `{ 'en-GB': value }`. The helpers below normalise that.
# -----------------------------------------------------------------------------

def _unwrap_locale(value: Any, locale: str, fallback_locale: str = "en-GB") -> Any:
    """Pick the value for a locale; fall back to en-GB if missing."""
    if not isinstance(value, dict):
        return value
    if locale in value and value[locale] not in (None, ""):
        return value[locale]
    return value.get(fallback_locale)


def _read_string(field: Any, locale: str) -> str:
    v = _unwrap_locale(field, locale)
    return v if isinstance(v, str) else ""


def _read_list(field: Any, locale: str) -> list[Any]:
    v = _unwrap_locale(field, locale)
    return v if isinstance(v, list) else []


def _read_link_id(field: Any) -> str | None:
    """Link fields look like {'en-GB': {'sys': {'id': 'abc'}}} with locale=*."""
    inner = _unwrap_locale(field, "en-GB")
    if isinstance(inner, dict) and "sys" in inner:
        sid = inner["sys"].get("id")
        return sid if isinstance(sid, str) else None
    return None


def _read_bool(field: Any) -> bool:
    return bool(_unwrap_locale(field, "en-GB"))


def _read_opcos(field: Any) -> list[str]:
    raw = _unwrap_locale(field, "en-GB")
    if not isinstance(raw, list):
        return []
    return [o for o in raw if isinstance(o, str) and o in VALID_OPCOS]


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------

def load_faq_chunks() -> list[FaqChunk]:
    """
    Fetch the corpus and produce one FaqChunk per FAQ per locale.

    Filters applied at load time:
      - faq.ragInclude must be true (editorial opt-out)
      - faq.question must be present in the locale (no chunk for an unpopulated locale)
      - faq.answer must be present in the locale (same)

    Filters NOT applied here (applied at QUERY time as a hard ChromaDB
    metadata filter):
      - applicable_opcos — same chunk serves every opco; the filter happens
        when the user asks a question
    """
    print(f"Fetching from space={SPACE_ID}, env={ENVIRONMENT} ...")
    hubs = _fetch_all_entries("servicingHub")
    topics = _fetch_all_entries("faqTopic")
    faqs = _fetch_all_entries("faq")
    print(f"  hubs:   {len(hubs)}")
    print(f"  topics: {len(topics)}")
    print(f"  faqs:   {len(faqs)}")

    # Build lookups
    hub_by_id = {h["sys"]["id"]: h for h in hubs}
    topic_by_id = {t["sys"]["id"]: t for t in topics}

    chunks: list[FaqChunk] = []
    skipped_rag_excluded = 0
    skipped_missing_locale: dict[str, int] = {loc: 0 for loc in LOCALES}
    intersection_narrowed = 0  # count FAQs whose effective opcos < their own opcos

    for entry in faqs:
        fields = entry.get("fields", {})
        faq_id = entry["sys"]["id"]

        if not _read_bool(fields.get("ragInclude")):
            skipped_rag_excluded += 1
            continue

        topic_id = _read_link_id(fields.get("topic"))
        topic = topic_by_id.get(topic_id) if topic_id else None
        hub_id: str | None = None
        hub = None
        if topic:
            hub_id = _read_link_id(topic["fields"].get("hub"))
            hub = hub_by_id.get(hub_id) if hub_id else None

        # OPCO INTERSECTION SEMANTICS (per spec discussion 2026-05-29):
        # A FAQ is visible to an OpCo only when ALL THREE levels permit it.
        # If hub-leshuttle.applicableOpcos = [BA, Aer], every LeShuttle FAQ
        # becomes invisible to Iberia regardless of its own applicableOpcos.
        # This makes hub-level toggling a one-click cascade.
        #
        # If hub or topic is missing the field entirely, treat as 'all opcos'
        # (no restriction) — defensive default to avoid silently nuking content.
        faq_opcos = set(_read_opcos(fields.get("applicableOpcos")))
        hub_opcos = set(_read_opcos(hub["fields"].get("applicableOpcos"))) if hub else set(VALID_OPCOS)
        if not hub_opcos:
            hub_opcos = set(VALID_OPCOS)
        topic_opcos = set(_read_opcos(topic["fields"].get("applicableOpcos"))) if topic else set(VALID_OPCOS)
        if not topic_opcos:
            topic_opcos = set(VALID_OPCOS)
        effective_opcos = faq_opcos & hub_opcos & topic_opcos
        if effective_opcos != faq_opcos and faq_opcos:
            intersection_narrowed += 1
        # `opcos` is what we store on the chunk — the effective set, not the
        # FAQ's raw set. Retrieval filters on this.
        opcos = sorted(effective_opcos)
        slug = _read_string(fields.get("slug"), "en-GB")
        internal_name = _read_string(fields.get("internalName"), "en-GB")
        last_reviewed = _read_string(fields.get("lastReviewedAt"), "en-GB") or None
        related_faqs = [_read_link_id(x) for x in _read_list(fields.get("relatedFaqs"), "en-GB")]
        related_faqs = [rid for rid in related_faqs if rid]

        for locale in LOCALES:
            question = _read_string(fields.get("question"), locale)
            answer = _read_string(fields.get("answer"), locale)
            if not question or not answer:
                skipped_missing_locale[locale] += 1
                continue
            variants = _read_list(fields.get("questionVariants"), locale)
            variants = [v for v in variants if isinstance(v, str) and v.strip()]
            search_summary = _read_string(fields.get("searchSummary"), locale)
            short_answer = _read_string(fields.get("shortAnswer"), locale)

            # Build the retrieval header — what the embedder sees.
            # Concatenate with newlines so each signal carries weight without
            # bleeding into the next. Per the v4.1.1 spec:
            #   retrieval_header = question + variants + searchSummary
            header_parts = [question]
            if variants:
                header_parts.extend(variants)
            if search_summary:
                header_parts.append(search_summary)
            retrieval_header = "\n".join(header_parts)

            # Build the citation URL. Live shape:
            #   https://www.avios.com/{locale}/help/{hub_slug}/{slug}
            # If hub_slug is missing (a FAQ without a topic), fall back to a slug-only URL.
            hub_slug = _read_string(hub["fields"].get("slug"), "en-GB") if hub else ""
            if hub_slug and slug:
                canonical_url = f"https://www.avios.com/{locale}/help/{hub_slug}/{slug}"
            elif slug:
                canonical_url = f"https://www.avios.com/{locale}/help/{slug}"
            else:
                canonical_url = f"https://www.avios.com/{locale}/help"

            chunks.append(FaqChunk(
                chunk_id=f"{faq_id}#{locale}",
                faq_id=faq_id,
                internal_name=internal_name,
                slug=slug,
                locale=locale,
                question=question,
                retrieval_header=retrieval_header,
                body=answer,
                short_answer=short_answer,
                canonical_url=canonical_url,
                hub_id=hub_id,
                hub_name=_read_string(hub["fields"].get("heading"), locale) if hub else None,
                hub_slug=hub_slug or None,
                topic_id=topic_id,
                topic_name=_read_string(topic["fields"].get("name"), locale) if topic else None,
                topic_slug=_read_string(topic["fields"].get("slug"), "en-GB") if topic else None,
                applicable_opcos=opcos,
                last_reviewed_at=last_reviewed,
                related_faq_ids=related_faqs,
            ))

    print(f"\nChunks produced: {len(chunks)}")
    print(f"  Skipped (ragInclude=false): {skipped_rag_excluded}")
    for loc, n in skipped_missing_locale.items():
        print(f"  Skipped (missing {loc} content): {n}")
    if intersection_narrowed:
        print(f"  FAQs narrowed by hub/topic opco intersection: {intersection_narrowed}")

    # Sanity-check the opco distribution — if any chunk has no opcos, the
    # query-time filter will exclude it from EVERY opco's results. That's a
    # data bug worth surfacing loudly.
    no_opco = [c for c in chunks if not c.applicable_opcos]
    if no_opco:
        print(f"\n⚠  {len(no_opco)} chunks have empty applicable_opcos and will be invisible to all opcos:")
        for c in no_opco[:10]:
            print(f"    - {c.internal_name} ({c.locale})")
        if len(no_opco) > 10:
            print(f"    ... and {len(no_opco) - 10} more")

    return chunks


# Demo entry — useful for quick sanity-check from the CLI
if __name__ == "__main__":
    chunks = load_faq_chunks()
    by_locale: dict[str, int] = {}
    for c in chunks:
        by_locale[c.locale] = by_locale.get(c.locale, 0) + 1
    print("\nPer-locale counts:")
    for loc, n in sorted(by_locale.items()):
        print(f"  {loc}: {n}")
    if chunks:
        c = chunks[0]
        print(f"\nSample chunk:")
        print(f"  chunk_id:  {c.chunk_id}")
        print(f"  question:  {c.question}")
        print(f"  hub:       {c.hub_name}  ({c.hub_slug})")
        print(f"  topic:     {c.topic_name}  ({c.topic_slug})")
        print(f"  opcos:     {c.applicable_opcos}")
        print(f"  url:       {c.canonical_url}")
        print(f"  retrieval_header: {c.retrieval_header[:100]}...")
