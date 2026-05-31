"""
synthesize_variants.py — Generate question-variant strings for content types
that don't have human-authored variants (inspirationPageSection, component-banner).

Background
----------
FAQs ship with a `questionVariants` field in Contentful, populated during the
bulk migration by `enrich.py`. Those variants get folded into the FAQ's
`retrieval_header` (question + variants + searchSummary), giving the embedder
multiple intent-shaped signals to lock onto. As a result, FAQ vectors are
TIGHT — short, focused, easy to retrieve.

Inspiration sections and banner components were never designed for retrieval —
they're display-oriented. Their fields are a heading + a long-form content
body, which produces a SMEARED embedding: lots of tokens, no clear topic
signal. They get out-ranked by sharper FAQ vectors even when more on-topic.

This module fixes the asymmetry by synthesising question variants at
index-build time, so non-FAQ content gets the same retrieval shape as FAQs.

Cache strategy
--------------
Cache keyed by `{entry_id}_{locale}` with a content hash. If content hasn't
changed since last build, reuse cached variants — no Claude call. Cache file
lives in `data/retrieval_variants_cache.json` and SHOULD be committed to git
so production rebuilds (Render) get the variants for free.

If a content edit invalidates the hash, the next build pays Claude cost for
that one entry and writes the new variants back. Cheap, deterministic, no
hidden re-cost on every rebuild.

Failure mode
------------
If Claude is unreachable or returns unparseable output, returns an empty list
and logs a warning. Callers fall back to heading-only retrieval headers
(current behaviour). The build never fails because of variant synthesis.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv


# -----------------------------------------------------------------------------
# Config — bump PROMPT_VERSION to invalidate all cached variants in one go
# (e.g. after iterating on the prompt). Cheaper than deleting the cache file.
# -----------------------------------------------------------------------------

PROMPT_VERSION = 1
MODEL_ID = "claude-haiku-4-5-20251001"
MAX_TOKENS = 400
VARIANTS_PER_ENTRY = 4

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

CACHE_PATH = PROJECT_ROOT / "data" / "retrieval_variants_cache.json"
CACHE_PATH.parent.mkdir(exist_ok=True)

LOCALE_LANGUAGE = {
    "en-GB": "British English",
    "es-ES": "Spanish (Spain)",
}


# -----------------------------------------------------------------------------
# Prompt — tuned to produce search-box-shaped questions, not interview-style
# ones. The "natural / conversational / how users actually type" framing is
# load-bearing: without it the model produces grammatically-perfect questions
# that real users would never type ("What are the available options for
# transatlantic travel using Avios?" — nobody types that).
# -----------------------------------------------------------------------------

PROMPT = """You are helping build a search system for Avios, a UK airline loyalty programme covering British Airways, Aer Lingus, and Iberia.

A content section has been published. Generate {n} questions a real user might type into a help search box that should retrieve this content.

Requirements:
- Natural, conversational phrasing — how users actually type, including short/informal versions ("can i fly to nyc with avios" not "Could I please use Avios to fly to New York?")
- Cover different angles: at least one short generic version, at least one with destinations/brand names
- Include proper nouns from the content where relevant (destinations, airlines, partners)
- Standalone questions — don't reference "this article" or "the section above"
- Generate in {language}

Content heading: {heading}

Content body:
{content_snippet}

Return ONLY a JSON array of {n} question strings. No prose, no markdown fences. Example shape: ["question 1", "question 2", "question 3", "question 4"]"""


# -----------------------------------------------------------------------------
# Lazy client + cache singletons
# -----------------------------------------------------------------------------

_client: Anthropic | None = None
_cache: dict | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set — variant synthesis requires it. "
                "Check rag-pipeline/.env."
            )
        _client = Anthropic()
    return _client


def _load_cache() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    if CACHE_PATH.exists():
        try:
            _cache = json.loads(CACHE_PATH.read_text())
        except json.JSONDecodeError:
            print(f"  warning: variants cache at {CACHE_PATH.name} is corrupt — starting fresh")
            _cache = {}
    else:
        _cache = {}
    return _cache


def _save_cache() -> None:
    """Atomic-ish write via tmp file then rename — avoids leaving a half-written
    cache if the process is killed mid-write."""
    if _cache is None:
        return
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_cache, indent=2, sort_keys=True, ensure_ascii=False))
    tmp.replace(CACHE_PATH)


def _content_hash(heading: str, content: str) -> str:
    """
    Hash heading + first 800 chars of content + prompt version.

    First 800 chars is enough to detect meaningful content changes without
    over-invalidating on trivial edits past the truncation point. Bumping
    PROMPT_VERSION invalidates everything in one go.
    """
    h = hashlib.sha256()
    h.update(f"v{PROMPT_VERSION}".encode())
    h.update(heading.encode("utf-8"))
    h.update(content[:800].encode("utf-8"))
    return h.hexdigest()[:16]


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def get_variants(
    entry_id: str,
    locale: str,
    heading: str,
    content: str,
) -> list[str]:
    """
    Return question variants for the given content.

    Returns a (possibly empty) list of question strings. The list is empty if
    Claude failed or returned garbage — callers should fall back to a
    heading-only retrieval header in that case.

    Cache: hits return immediately, no API call. Misses synthesise + persist.
    """
    if not heading and not content:
        return []

    cache = _load_cache()
    cache_key = f"{entry_id}_{locale}"
    expected_hash = _content_hash(heading, content)

    cached = cache.get(cache_key)
    if cached and cached.get("hash") == expected_hash:
        return cached.get("variants", [])

    # Cache miss — synthesise
    language = LOCALE_LANGUAGE.get(locale, "English")
    snippet = content[:800] if content else "(no content body — generate variants from the heading alone)"
    prompt = PROMPT.format(
        n=VARIANTS_PER_ENTRY,
        language=language,
        heading=heading,
        content_snippet=snippet,
    )

    try:
        resp = _get_client().messages.create(
            model=MODEL_ID,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        # Strip markdown fences if model added them despite the instruction
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
        variants = json.loads(raw)
        if not isinstance(variants, list) or not all(isinstance(v, str) for v in variants):
            raise ValueError("Expected JSON array of strings")
        variants = [v.strip() for v in variants if v.strip()]
        if not variants:
            raise ValueError("Empty variants list after cleanup")
    except Exception as e:
        # Log + fall back. The build continues with the heading-only header.
        print(f"  warning: variant synthesis failed for {entry_id} ({locale}): {e}")
        return []

    # Persist with the heading copied in for human readability when reviewing
    # the cache file by eye. Heading is truncated to avoid bloating diffs.
    cache[cache_key] = {
        "hash": expected_hash,
        "heading": heading[:80],
        "locale": locale,
        "variants": variants,
    }
    _save_cache()
    print(f"  synthesised {len(variants)} variants for {entry_id} [{locale}]: {heading[:60]!r}")
    return variants
