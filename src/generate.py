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

1. Answer ONLY from the context provided below. The context contains a mix of source types: FAQ entries, inspiration page sections (educational guides), and banner components (feature explainers). Do not invent facts, rates, dates, partner names, claim windows, or URLs that don't appear in the context.

2. The context has been pre-filtered for the user's loyalty programme (OpCo). Every item shown is applicable to them — you don't need to caveat by OpCo unless the source itself does.

3. Handling partial vs missing information — read carefully:
   - If NONE of the context is relevant to the question, reply with exactly: "I don't have that information in the help centre — please contact customer service for the most accurate answer." Nothing else.
   - If the context PARTIALLY answers the question (e.g. explains *how* something works but not the exact figure they asked for), answer with what you do have and note plainly what you can't tell them — e.g. "The exact amount depends on route and dates, which I can't look up here — use the Reward Flight finder for live prices." Do NOT use the verbatim "I don't have that information…" sentence in this case; it is reserved for total refusals.
   - Never combine the verbatim refusal sentence with an actual answer. Pick one mode.

4. Cite every fact. End your answer with a "Sources" block listing the source IDs you drew from (one per line, with the source's title as the label):

   Sources:
   - <source-id>: <Title>

5. Style: neutral, factual, plain language. Short paragraphs. Use markdown lightly (bullet lists OK, no headers). Don't oversell or use marketing language. Don't add sign-offs like "Hope that helps!".

6. After the Sources block, append a "Follow-ups" block listing 2-3 short questions the user might naturally ask next, related to what they just asked. These should be questions the help centre is likely to answer (adjacent topics, common next steps), NOT tangents. Phrase them like real user search-box queries — short, conversational. Format:

   Follow-ups:
   - <natural follow-up question 1>
   - <natural follow-up question 2>
   - <natural follow-up question 3>

If the user explicitly asked for something the context doesn't contain (the total refusal case from rule 3), skip the Follow-ups block.

If the question is in a language the context isn't, answer in the language of the context and add a brief note that you've answered in the original language."""


# -----------------------------------------------------------------------------
# Generation
# -----------------------------------------------------------------------------

@dataclass
class GenerationResult:
    answer: str
    cited_sources: list[str] = field(default_factory=list)
    # Suggested follow-up questions parsed out of the model's trailing
    # 'Follow-ups:' block. Used by the UI to render clickable chips so
    # users can keep exploring the corpus without thinking up next queries.
    suggested_followups: list[str] = field(default_factory=list)
    model: str = MODEL_ID
    input_tokens: int = 0
    output_tokens: int = 0
    user_message: str = ""  # the assembled user prompt (for audit log)
    # Multi-turn additions: when this generation came from a chat turn, we
    # record the rewritten retrieval query (if any) so the audit log shows
    # what we actually searched for, not just what the user typed.
    retrieval_query: str = ""
    was_rewritten: bool = False


_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


# -----------------------------------------------------------------------------
# Query rewriting for multi-turn follow-ups.
#
# When the user says 'what about for Aer Lingus?', the literal message isn't
# a useful retrieval query — it has no nouns to match against. We use Haiku
# to rewrite the latest message as a standalone search query, resolving
# pronouns and bringing in context from prior turns.
#
# Only fires when there ARE prior turns. First-turn questions are passed
# through unchanged — no rewrite needed and saves an API call.
# -----------------------------------------------------------------------------

REWRITE_PROMPT = """Given this conversation, rewrite the LAST user message as a standalone, self-contained search query suitable for retrieval against an FAQ corpus. Resolve all pronouns and implied context. Be concise. Return ONLY the rewritten query — no quotes, no explanation, no prefix.

Conversation:
{conversation}

Rewritten standalone query:"""


def rewrite_query_for_retrieval(history: list[dict[str, str]]) -> str:
    """
    Given a list of {role, content} messages, return a standalone search
    query for the latest user message. If there's no prior context (first
    user message), return the message verbatim.

    history is in chronological order, ending with a user message.
    """
    user_msgs = [m for m in history if m.get("role") == "user"]
    if not user_msgs:
        return ""
    latest = user_msgs[-1]["content"]
    if len(history) <= 1:
        return latest  # first turn, no rewrite needed

    convo_lines = []
    for m in history:
        role = "User" if m.get("role") == "user" else "Assistant"
        convo_lines.append(f"{role}: {m.get('content', '')}")
    convo = "\n".join(convo_lines)

    try:
        resp = _get_client().messages.create(
            model=MODEL_ID,
            max_tokens=200,
            messages=[{"role": "user", "content": REWRITE_PROMPT.format(conversation=convo)}],
        )
        rewritten = resp.content[0].text.strip().strip('"').strip("'").strip()
        return rewritten or latest
    except Exception:
        # If rewrite fails, fall back to the original message — better to
        # retrieve on a noisy query than to fail the request.
        return latest


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


def _strip_trailing_blocks(answer: str) -> str:
    """
    Remove the trailing 'Sources:' AND/OR 'Follow-ups:' blocks from the
    model's answer before it's sent to the UI.

    Why we strip rather than tell the model not to emit them: we still need
    _extract_citations + _extract_followups to pull them out for the audit
    log and the chip UI respectively. The web UI shows retrieved sources in
    a per-turn expander and follow-ups as clickable chips, so duplicating
    the raw text inline in the answer body is just noise.

    Pattern matches whichever block appears FIRST and strips from there to
    end of message — works whether the model emits Sources then Follow-ups,
    Follow-ups then Sources, or just one of them. Intentionally permissive
    on formatting (plain `Sources:`, `**Sources:**`, `## Sources`).
    """
    pattern = re.compile(
        r"\n+\s*(?:#{1,6}\s*)?(?:\*\*\s*)?(?:Sources?|Follow-?ups?)\s*:?\s*(?:\*\*)?\s*\n.*$",
        re.DOTALL | re.IGNORECASE,
    )
    return pattern.sub("", answer).rstrip()


def _extract_followups(answer: str) -> list[str]:
    """
    Pull suggested follow-up questions out of the trailing 'Follow-ups:' block.

    Matches the header (with optional bold/heading decoration), then captures
    bullet items until a blank line or end of message. Returns up to 3 to
    keep the chip row tidy regardless of how many the model emits.
    """
    m = re.search(
        r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:\*\*\s*)?Follow-?ups?\s*:?\s*(?:\*\*)?\s*\n(.+?)(?:\n\s*\n|$)",
        answer,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return []
    block = m.group(1)
    # Bullets can be `-`, `*`, or numbered `1.` — accept any
    items = re.findall(r"^\s*(?:[-*]|\d+\.)\s+(.+?)\s*$", block, re.MULTILINE)
    cleaned = [s.strip().rstrip("?").rstrip() + "?" for s in items if s.strip()]
    # Drop the trailing-? trick if the question already ended without one
    # (e.g. an imperative phrase) — only re-add if it looks like a question
    cleaned = [_normalise_followup(s) for s in cleaned]
    return cleaned[:3]


def _normalise_followup(s: str) -> str:
    """Light cleanup: strip wrapping quotes/asterisks and re-jig terminal punctuation."""
    s = s.strip().strip('"').strip("'").strip("*").strip()
    # If the model emitted "How do I X?" preserve the ?. If "How do I X" add one.
    # If "Cancel a flight" (imperative), leave as-is.
    if not s.endswith(("?", ".", "!")) and any(s.lower().startswith(w + " ") for w in
        ["how", "what", "when", "where", "why", "can", "do", "does", "is", "are", "will", "should"]):
        s += "?"
    return s


def _extract_citations(answer: str) -> list[str]:
    """
    Pull source IDs out of the Sources block. The model is instructed to
    put them there; we extract programmatically rather than re-parsing
    the answer body, so the audit log is durable even if the model bends
    the format slightly.

    Matches several shapes:
      - `faq-<slug>` (with optional `-<8charhash>` suffix for hashed sys.ids)
      - `inspiration-section-<slug>` / `section-<slug>` (inspirationPageSection IDs)
      - `banner-<slug>` (component-banner IDs)
      - Any Contentful sys.id that's 22 chars of [a-zA-Z0-9]
    """
    patterns = [
        r"\bfaq-[a-z0-9-]+(?:-[a-f0-9]{8})?\b",
        r"\b(?:inspiration[-_]section|section)-[a-z0-9-]+\b",
        r"\bbanner-[a-z0-9-]+\b",
        r"\b[a-zA-Z0-9]{22}\b",  # raw Contentful sys.ids
    ]
    found: list[str] = []
    for p in patterns:
        found.extend(re.findall(p, answer))
    return list(dict.fromkeys(found))


def generate(question: str, results: list[RetrievalResult]) -> GenerationResult:
    """Single-turn version — kept for the CLI (ask.py) which is one-shot."""
    user_message = build_user_message(question, results)
    response = _get_client().messages.create(
        model=MODEL_ID,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    answer_text = response.content[0].text if response.content else ""
    # Order matters: extract from the raw answer (which still has the
    # Sources + Follow-ups blocks) BEFORE stripping them for display.
    cited = _extract_citations(answer_text)
    followups = _extract_followups(answer_text)
    answer_text = _strip_trailing_blocks(answer_text)
    return GenerationResult(
        answer=answer_text,
        cited_sources=cited,
        suggested_followups=followups,
        model=MODEL_ID,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        user_message=user_message,
        retrieval_query=question,
        was_rewritten=False,
    )


def generate_chat(
    history: list[dict[str, str]],
    results: list[RetrievalResult],
    retrieval_query: str,
    was_rewritten: bool,
) -> GenerationResult:
    """
    Multi-turn version. Receives the full conversation history (alternating
    user/assistant messages, ending in the user message we just received),
    plus the retrieval results for that latest message.

    Strategy: pass prior turns as message history to Claude, then append a
    user message that wraps the retrieved chunks around the latest question.
    Claude treats the chunks as fresh context for the new question while
    still remembering the conversation.
    """
    if not history or history[-1].get("role") != "user":
        raise ValueError("history must end with a user message")
    latest_question = history[-1]["content"]
    prior = history[:-1]

    # Wrap chunks around the latest question
    user_message_with_chunks = build_user_message(latest_question, results)

    # Build the messages array sent to Claude. Prior turns pass through
    # verbatim (the model "remembers" the conversation). The latest user
    # turn carries the freshly retrieved chunks.
    messages = list(prior) + [{"role": "user", "content": user_message_with_chunks}]

    response = _get_client().messages.create(
        model=MODEL_ID,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    answer_text = response.content[0].text if response.content else ""
    cited = _extract_citations(answer_text)
    followups = _extract_followups(answer_text)
    answer_text = _strip_trailing_blocks(answer_text)
    return GenerationResult(
        answer=answer_text,
        cited_sources=cited,
        suggested_followups=followups,
        model=MODEL_ID,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        user_message=user_message_with_chunks,
        retrieval_query=retrieval_query,
        was_rewritten=was_rewritten,
    )
