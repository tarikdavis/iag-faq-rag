"""
log_query.py — Write a complete audit trail for every query.

One JSON file per query in logs/, containing everything needed to
reconstruct what happened: the user input, the retrieval results, the
full assembled prompt, the model's answer, the citations, and the token
counts. Self-contained — if the index is rebuilt, the prompt is changed,
or the model is upgraded, the log still tells the full story of that
query as it happened.

System prompt is hashed (sha256, first 16 chars) so prompt drift between
log files is easy to spot — useful for compliance reviews.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.generate import GenerationResult, SYSTEM_PROMPT
from src.retrieve import RetrievalResult


# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _system_prompt_hash() -> str:
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:16]


def _result_to_dict(r: RetrievalResult) -> dict[str, Any]:
    # Drop the full body_markdown from the log — it's already in the
    # user_message field, and including it twice bloats every log file
    # by ~5KB per chunk.
    d = asdict(r)
    d.pop("body_markdown", None)
    return d


def _filename(timestamp: datetime, query_id: str) -> str:
    iso_ts = timestamp.strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{iso_ts}_{query_id[:8]}.json"


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------

def log_query(
    *,
    question: str,
    locale: str,
    opco: str | None,
    extra_filters: dict[str, Any] | None,
    retrieval: list[RetrievalResult],
    generation: GenerationResult | None,
    user_message: str,
) -> Path:
    """Write an audit log for a single query and return the path written."""
    LOGS_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now(timezone.utc)
    query_id = str(uuid.uuid4())

    log_entry: dict[str, Any] = {
        "query_id": query_id,
        "timestamp": timestamp.isoformat(),
        "question": question,
        "locale": locale,
        "opco": opco,
        "extra_filters": extra_filters or {},
        "system_prompt": {
            "text": SYSTEM_PROMPT,
            "hash": _system_prompt_hash(),
        },
        "user_message": user_message,
        "retrieval": {
            "result_count": len(retrieval),
            "results": [_result_to_dict(r) for r in retrieval],
        },
        "generation": (
            {
                "answer": generation.answer,
                "cited_sources": generation.cited_sources,
                "model": generation.model,
                "input_tokens": generation.input_tokens,
                "output_tokens": generation.output_tokens,
            }
            if generation is not None
            else None
        ),
    }

    path = LOGS_DIR / _filename(timestamp, query_id)
    with path.open("w", encoding="utf-8") as f:
        json.dump(log_entry, f, indent=2, ensure_ascii=False)
    return path
