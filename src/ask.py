"""
ask.py — End-to-end CLI for the FAQ RAG pipeline.

Usage:
    # Default: en-GB, no opco filter
    python src/ask.py "I'm missing my Avios — what should I do?"

    # Opco-filtered (the headline use case for the spec's hard-filter check)
    python src/ask.py "Forgotten password" --opco aer-lingus
    python src/ask.py "How do I link my accounts?" --opco british-airways

    # Spanish locale
    python src/ask.py "He perdido mis Avios" --locale es-ES --opco iberia

    # Hub-scoped query
    python src/ask.py "Can I cancel my order?" --filter hub_slug=avios-shop

For each call this script:

  1. Detects listing-shaped questions ("what hubs can I earn Avios in?")
     and bumps k upward.
  2. Retrieves top-k FAQ chunks from the per-locale ChromaDB collection,
     with the opco filter applied PRE-retrieval as a hard ChromaDB where
     clause.
  3. Sends retrieved chunks to Claude with the grounded prompt.
  4. Writes a JSON audit log to logs/.
  5. Prints the answer, citations, retrieved IDs, token cost, and log path.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Allow `python src/ask.py ...` to find the src package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.generate import build_user_message, generate
from src.log_query import log_query
from src.retrieve import format_results, retrieve


# -----------------------------------------------------------------------------
# Listing-query heuristic. Carried over from the synthetic-demo version
# because it's still useful for the "what are all the ways to ___" shape.
# Imperfect — proper fix is query routing — but enough for the demo.
# -----------------------------------------------------------------------------

LISTING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bwhat\s+\w+(\s+\w+)*\s+(can|am|are|do)\b", re.IGNORECASE),
    re.compile(r"\bwhich\s+\w+", re.IGNORECASE),
    re.compile(r"\bshow\s+me\s+(all|every|the)\b", re.IGNORECASE),
    re.compile(r"\blist\s+(all|every|the)\b", re.IGNORECASE),
    re.compile(r"\b(all|every)\s+(the\s+)?(ways|hubs|topics|categories|faqs|partners)\b", re.IGNORECASE),
]

LISTING_K = 15
DEFAULT_K = 5


def _looks_like_listing_query(question: str) -> bool:
    return any(p.search(question) for p in LISTING_PATTERNS)


def _parse_filters(filter_args: list[str]) -> dict[str, str]:
    filters: dict[str, str] = {}
    for arg in filter_args:
        if "=" not in arg:
            raise SystemExit(f"Invalid filter '{arg}' — expected key=value.")
        key, value = arg.split("=", 1)
        filters[key.strip()] = value.strip()
    return filters


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ask a question of the Avios FAQ corpus."
    )
    parser.add_argument("question", help="Your natural-language question.")
    parser.add_argument(
        "--opco",
        choices=["british-airways", "aer-lingus", "iberia"],
        default=None,
        help=(
            "Pre-retrieval HARD filter. If set, the user only sees FAQs whose "
            "applicableOpcos contains this opco. Per v4.1.1 spec: 'an AerClub "
            "user must never see a BA-only answer'."
        ),
    )
    parser.add_argument(
        "--locale", choices=["en-GB", "es-ES"], default="en-GB",
        help="Which per-locale ChromaDB collection to query. Default en-GB.",
    )
    parser.add_argument(
        "--k", type=int, default=None,
        help=(
            f"Number of chunks to retrieve. Default {DEFAULT_K}; bumped to "
            f"{LISTING_K} automatically for listing-shaped queries."
        ),
    )
    parser.add_argument(
        "--filter", action="append", default=[], dest="filters",
        help="Equality filter on chunk metadata, e.g. --filter hub_slug=avios-shop. Repeatable.",
    )
    args = parser.parse_args()

    extra_filters = _parse_filters(args.filters) if args.filters else None

    # ---------- Listing detection ----------
    if args.k is not None:
        k = args.k
        listing_detected = False
    elif _looks_like_listing_query(args.question):
        k = LISTING_K
        listing_detected = True
    else:
        k = DEFAULT_K
        listing_detected = False

    # ---------- Retrieval ----------
    results = retrieve(
        args.question,
        locale=args.locale,
        opco=args.opco,
        extra_filters=extra_filters,
        k=k,
    )

    print()
    print("=" * 68)
    print(f"QUESTION: {args.question}")
    print(f"LOCALE:   {args.locale}")
    if args.opco:
        print(f"OPCO:     {args.opco}  (hard pre-retrieval filter)")
    if extra_filters:
        print(f"FILTERS:  {extra_filters}")
    if listing_detected:
        print(f"NOTE:     Listing-shaped query — k bumped to {LISTING_K}.")
    print("=" * 68)

    print()
    print("--- RETRIEVED CHUNKS ---")
    print(format_results(results) if results else "(no results)")

    if not results:
        print("\nNothing retrieved — no context to send to the model.")
        empty_user_message = build_user_message(args.question, [])
        log_path = log_query(
            question=args.question,
            locale=args.locale,
            opco=args.opco,
            extra_filters=extra_filters,
            retrieval=[],
            generation=None,
            user_message=empty_user_message,
        )
        print(f"\nAudit log: {log_path.relative_to(Path.cwd())}")
        return

    # ---------- Generation ----------
    generation = generate(args.question, results)

    print("--- ANSWER ---")
    print(generation.answer)
    print()

    print("--- AUDIT ---")
    print(f"Retrieved IDs : {[r.internal_name for r in results]}")
    print(f"Cited IDs     : {generation.cited_sources}")
    print(f"Model         : {generation.model}")
    print(f"Tokens        : {generation.input_tokens} in / {generation.output_tokens} out")
    cost = (generation.input_tokens * 1.0 / 1_000_000) + (generation.output_tokens * 5.0 / 1_000_000)
    print(f"Cost estimate : ${cost:.5f}")

    # ---------- Logging ----------
    log_path = log_query(
        question=args.question,
        locale=args.locale,
        opco=args.opco,
        extra_filters=extra_filters,
        retrieval=results,
        generation=generation,
        user_message=generation.user_message,
    )
    print(f"Audit log     : {log_path.relative_to(Path.cwd())}")
    print()


if __name__ == "__main__":
    main()
