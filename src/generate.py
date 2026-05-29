"""
generate.py — Send retrieved FAQ chunks to Claude and return a grounded,
cited answer.

Adapted from the synthetic-demo version. Same shape (cited grounded
answer pattern, Anthropic SDK, Haiku 4.5), new prompt tuned for the FAQ
domain: the model is instructed to answer in the voice of an Avios
help-centre response, cite the source FAQs by ID, and refuse to guess if
the context doesn't support an answer.

The system prompt is the most important content artefact in this file.
Every clause is deliberate.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from src.retrieve import RetrievalResult


# -----------------------------------------------------------------------------
# API key
# -----------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

if not os.getenv("ANTHROPIC_API_KEY"):
    raise RuntimeError(
        "ANTHROPIC_API_KEY not set. Check .env at the project root."
    )


# -----------------------------------------------------------------------------
# Model
# -----------------------------------------------------------------------------

MODEL_ID = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1024


# -----------------------------------------------------------------------------
# System prompt — content design, concentrated.
#
# In order:
#
# 1. Role + domain: positions the model as a help-centre assistant for
#    Avios. Bounds vocabulary and tone.
#
# 2. Grounding constraint — the single most important instruction in any
#    regulated-content RAG prompt. "Answer ONLY from the provided
#    context." Without this the model invents plausible-looking earning
#    rates, claim windows, and partner names.
#
# 3. OpCo guarantee. The retrieval layer has already filtered to the
#    user's opco — the model can trust that every chunk it sees is
#    applicable. This avoids needing the model to second-guess.
#
# 4. Refusal-to-guess phrasing. "I don't have that information here"
#    is precise. "I'm not sure" is vague and seeps into low-confidence
#    speculation.
#
# 5. Citation requirement. Sources block at the end, FAQ IDs not chunk IDs.
#    Citations are also extracted programmatically (see _extract_citations)
#    so audit logs capture them durably.
#
# 6. Style guidance. Avios is a regulated programme, so neutral and
#    factual beats salesy. Short paragraphs, plain language.
# -----------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a helpful Avios help-centre assistant.

You answer questions for members of the Avios loyalty programme (operated by IAG Loyalty, covering British Airways Club, AerClub, and Iberia Club).

ANSWERING RULES:

1. Answer ONLY from the provided FAQ context below. Do not invent facts, rates, dates, partner names, claim windows, or URLs that don't appear in the context.

2. The context has been pre-filtered for the user's loyalty programme (OpCo). Every FAQ shown is applicable to them — you don't need to caveat by OpCo unless the FAQ itself does.

3. If the context doesn't contain the information needed to answer the question, say: "I don't have that information in the help centre — please contact customer service for the most accurate answer." Do not guess.

4. Cite every fact. End your answer with a "Sources" block listing the FAQ IDs you drew from (one per line, with the FAQ's question as the label):

   Sources:
   - faq-<slug>: <Question text>

5. Style: neutral, factual, plain language. Short paragraphs. Use markdown lightly (bullet lists OK, no headers). Don't oversell or use marketing language. Don't add sign-offs like "Hope that helps!".

If the question is in a language the context isn't, answer in the language of the context and add a brief note that you've answered in the original language."""


# -----------------------------------------------------------------------------
# Generation
# -----------------------------------------------------------------------------

@dataclass
class GenerationResult:
    answer: str
    cited_sources: list[str] = field(default_factory=list)
    model: str = MODEL_ID
    input_tokens: int = 0
    output_tokens: int = 0
    user_message: str = ""  # the assembled user prompt (for audit log)


_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


def build_user_message(question: str, results: list[RetrievalResult]) -> str:
    """
    Assemble the user message that wraps the retrieved chunks around the
    question. We put the context BEFORE the question — recency bias means
    the model weights the question more when it comes last.
    """
    if not results:
        return (
            f"User question: {question}\n\n"
            f"(No FAQs were retrieved for this question.)"
        )

    chunks: list[str] = []
    for r in results:
        chunks.append(
            f"--- FAQ id={r.internal_name} ---\n"
            f"Question: {r.question}\n"
            f"URL: {r.canonical_url}\n"
            f"Applicable opcos: {', '.join(r.applicable_opcos)}\n"
            f"Hub › Topic: {r.hub_name} › {r.topic_name}\n\n"
            f"{r.body_markdown}"
        )
    context = "\n\n".join(chunks)
    return (
        f"FAQ context (retrieved by similarity to the user's question, "
        f"already filtered to the user's OpCo):\n\n"
        f"{context}\n\n"
        f"---\n\n"
        f"User question: {question}"
    )


def _extract_citations(answer: str) -> list[str]:
    """
    Pull `faq-<slug>` IDs out of the Sources block. The model is
    instructed to put them there; we extract them programmatically
    rather than re-parsing the answer body so the audit log is durable
    even if the model bends the format slightly.
    """
    # Match faq IDs that look like 'faq-...' — they appear in the
    # Sources block as `- faq-im-missing-avios: Question text`
    return list(dict.fromkeys(re.findall(r"\bfaq-[a-z0-9-]+(?:-[a-f0-9]{8})?\b", answer)))


def generate(question: str, results: list[RetrievalResult]) -> GenerationResult:
    """Run the full retrieve → prompt → Claude → parse pipeline."""
    user_message = build_user_message(question, results)
    response = _get_client().messages.create(
        model=MODEL_ID,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    answer_text = response.content[0].text if response.content else ""
    return GenerationResult(
        answer=answer_text,
        cited_sources=_extract_citations(answer_text),
        model=MODEL_ID,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        user_message=user_message,
    )
