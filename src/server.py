"""
server.py — Flask backend for the FAQ RAG demo browser UI.

Wraps the existing pipeline (retrieve → generate → log) behind a single
HTTP endpoint. Serves the static HTML/JS front-end from /static.

Pure presentation layer — imports retrieve.py and generate.py unchanged.
The CLI (ask.py) and the UI run off the same primitives.

Usage:
    python src/server.py
    # then open http://127.0.0.1:5000

Choices worth being honest about:
  - Single endpoint, single round trip per query. Streaming the answer
    as Claude generates would be a separate engineering project.
  - The web UI applies the SAME opco hard filter as the CLI — same
    safety mechanism, same code path. The browser is just a different
    way to call it.
  - No CSS framework, no JS framework, no build step.
"""

from __future__ import annotations

import hmac
import json as jsonlib
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any

# Allow running as `python src/server.py` from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, make_response, redirect, request, send_from_directory

from src.generate import build_user_message, generate, generate_chat, rewrite_query_for_retrieval
from src.log_query import log_query
from src.retrieve import RetrievalResult, _get_collection, retrieve


# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"
MANIFEST_PATH = PROJECT_ROOT / "build_manifest.json"

# Password protection: when APP_PASSWORD is set, every page requires a
# correct password (set once via the login form, stored in a signed cookie).
# When unset (typical for local dev), the app is open. Production deploys
# MUST set APP_PASSWORD — without it anyone on the internet could rack up
# Anthropic API costs.
APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()
# Cookie secret — used to sign the auth cookie so it can't be forged.
# Stable per-deploy if you set it; otherwise random per-process (forces
# re-login on every restart, fine for low-traffic demos).
COOKIE_SECRET = os.getenv("COOKIE_SECRET", "").strip() or secrets.token_urlsafe(32)
COOKIE_NAME = "rag_demo_auth"
COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 7  # 7 days


# -----------------------------------------------------------------------------
# Listing-query detection — duplicated from ask.py. Kept in sync deliberately.
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


def _looks_like_listing_query(q: str) -> bool:
    return any(p.search(q) for p in LISTING_PATTERNS)


# -----------------------------------------------------------------------------
# Auth — shared password, signed cookie
# -----------------------------------------------------------------------------
# Why a shared password and not full auth? This is an internal sandbox,
# not a product. The asset being protected is the Anthropic API budget,
# not user data. A shared password is appropriate for "people I've told
# the password to can use it; the rest of the internet can't run up the bill".

def _cookie_token() -> str:
    """The expected cookie value when the user is authenticated."""
    return hmac.new(
        COOKIE_SECRET.encode(),
        b"authed:" + APP_PASSWORD.encode(),
        "sha256",
    ).hexdigest()


def _is_authed(req) -> bool:
    if not APP_PASSWORD:
        return True  # auth disabled (local dev)
    presented = req.cookies.get(COOKIE_NAME, "")
    return hmac.compare_digest(presented, _cookie_token())


def _login_page(error: str | None = None, next_url: str = "/") -> str:
    err_html = ""
    if error:
        err_html = f'<div class="err">{error}</div>'
    return f"""<!doctype html>
<html lang="en-GB">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Sign in — FAQ RAG demo</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap" rel="stylesheet" />
    <style>
      body {{ font-family: 'Poppins', system-ui, sans-serif; background: #f8f8fa; color: #000; margin: 0; padding: 0; display: flex; min-height: 100vh; align-items: center; justify-content: center; }}
      .card {{ background: #fff; padding: 36px 40px; border-radius: 12px; border: 1px solid rgba(149,147,160,0.25); width: 360px; max-width: 90vw; }}
      h1 {{ font-size: 20px; font-weight: 700; margin: 0 0 6px; }}
      .sub {{ color: #737177; font-size: 13px; margin-bottom: 24px; }}
      label {{ display: block; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: #737177; margin-bottom: 4px; }}
      input[type=password] {{ width: 100%; padding: 12px 14px; font: inherit; font-size: 15px; background: #fff; border: 1px solid rgba(149,147,160,0.25); border-radius: 8px; }}
      input[type=password]:focus {{ outline: none; border-color: #011dac; box-shadow: 0 0 0 3px #e2e8ff; }}
      button {{ width: 100%; margin-top: 14px; padding: 12px 24px; font: inherit; font-weight: 600; font-size: 14px; background: #011dac; color: white; border: 0; border-radius: 9999px; cursor: pointer; }}
      button:hover {{ opacity: 0.9; }}
      .err {{ background: rgba(213,43,30,0.08); color: #d52b1e; padding: 10px 12px; border-radius: 6px; font-size: 13px; margin-bottom: 14px; }}
    </style>
  </head>
  <body>
    <form class="card" method="post" action="/login">
      <h1>FAQ RAG demo</h1>
      <div class="sub">Internal Avios sandbox. Enter the password your team-mate shared.</div>
      {err_html}
      <input type="hidden" name="next" value="{next_url}" />
      <label for="pw">Password</label>
      <input id="pw" name="password" type="password" autofocus required />
      <button type="submit">Sign in</button>
    </form>
  </body>
</html>
"""


# -----------------------------------------------------------------------------
# Flask app
# -----------------------------------------------------------------------------

def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)  # we serve /static ourselves

    # Apply auth to every request EXCEPT the login routes
    PUBLIC_PATHS = {"/login", "/healthz"}

    @app.before_request
    def require_auth():
        if request.path in PUBLIC_PATHS:
            return None
        # Static assets need to be reachable from the login page (fonts etc.)
        # so we let them through unauthed too.
        if request.path.startswith("/static/"):
            return None
        if _is_authed(request):
            return None
        return _login_page(next_url=request.path), 401

    @app.get("/login")
    def login_get():
        next_url = request.args.get("next", "/")
        if _is_authed(request):
            return redirect(next_url)
        return _login_page(next_url=next_url)

    @app.post("/login")
    def login_post():
        next_url = request.form.get("next", "/") or "/"
        submitted = request.form.get("password", "")
        if not APP_PASSWORD:
            # Auth is disabled — accept anything
            return redirect(next_url)
        if not hmac.compare_digest(submitted, APP_PASSWORD):
            return _login_page(error="Incorrect password.", next_url=next_url), 401
        resp = make_response(redirect(next_url))
        resp.set_cookie(
            COOKIE_NAME, _cookie_token(),
            max_age=COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            samesite="Lax",
            secure=request.is_secure,
        )
        return resp

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/graph")
    def graph_page():
        return send_from_directory(STATIC_DIR, "graph.html")

    @app.get("/static/<path:filename>")
    def static_files(filename: str):
        return send_from_directory(STATIC_DIR, filename)

    @app.get("/api/info")
    def api_info():
        """Tiny endpoint the UI hits on load to render the build-info footer."""
        info: dict[str, Any] = {
            "auth_required": bool(APP_PASSWORD),
            "manifest": None,
        }
        try:
            if MANIFEST_PATH.exists():
                info["manifest"] = jsonlib.loads(MANIFEST_PATH.read_text())
        except Exception:
            pass
        return jsonify(info)

    @app.get("/api/graph")
    def api_graph():
        """
        Return the FAQ relationship cluster as cytoscape-compatible JSON.

        Nodes: every FAQ in the requested locale's collection.
        Edges: every relatedFaqs link between FAQs.

        Currently relatedFaqs is sparse — the bulk migration didn't populate
        them, but the field exists and the graph picks them up when editors
        do. Visual grouping by hub gives a topology view even when the
        explicit edges are few.
        """
        locale = request.args.get("locale", "en-GB")
        if locale not in ("en-GB", "es-ES"):
            return jsonify({"error": f"unsupported locale: {locale}"}), 400

        try:
            coll = _get_collection(locale)
            # .get() with no IDs returns everything — fine for ~164 chunks
            raw = coll.get(include=["metadatas"])
        except Exception as e:
            return jsonify({"error": f"could not read collection: {e}"}), 500

        nodes: list[dict[str, Any]] = []
        # Map faq_id -> chunk_id so we can dedupe related-FAQ edges that
        # might reference faq_ids not in this locale (e.g. ES translation missing)
        faq_to_chunk: dict[str, str] = {}
        for cid, meta in zip(raw["ids"], raw["metadatas"]):
            faq_id = meta.get("faq_id") or cid
            faq_to_chunk[faq_id] = cid
            opcos_csv = meta.get("applicable_opcos_csv") or ""
            opcos = [o for o in opcos_csv.split(",") if o]
            nodes.append({
                "data": {
                    "id": cid,
                    "faq_id": faq_id,
                    "internal_name": meta.get("internal_name", ""),
                    "label": meta.get("question", "")[:80],
                    "question": meta.get("question", ""),
                    "short_answer": meta.get("short_answer", ""),
                    "hub_id": meta.get("hub_id", ""),
                    "hub_name": meta.get("hub_name", "") or "(no hub)",
                    "topic_id": meta.get("topic_id", ""),
                    "topic_name": meta.get("topic_name", "") or "(no topic)",
                    "applicable_opcos": opcos,
                    "canonical_url": meta.get("canonical_url", ""),
                },
            })

        edges: list[dict[str, Any]] = []
        for cid, meta in zip(raw["ids"], raw["metadatas"]):
            related_csv = meta.get("related_faq_ids") or ""
            for rel_faq_id in [r for r in related_csv.split(",") if r]:
                target = faq_to_chunk.get(rel_faq_id)
                if not target:
                    continue
                edges.append({
                    "data": {
                        "id": f"{cid}->{target}",
                        "source": cid,
                        "target": target,
                        "kind": "related",
                    },
                })

        return jsonify({
            "locale": locale,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
        })

    @app.post("/api/chat")
    def api_chat():
        """
        Multi-turn chat endpoint.

        Body:
          {
            "messages": [
              {"role": "user", "content": "..."},
              {"role": "assistant", "content": "..."},
              ...
              {"role": "user", "content": "..."}  // latest
            ],
            "locale": "en-GB" | "es-ES",
            "opco": "british-airways" | "aer-lingus" | "iberia" | null
          }

        For follow-up turns we ask Claude to rewrite the latest message as
        a standalone retrieval query (so 'what about for Aer?' gets enough
        context to retrieve). First-turn queries pass through unchanged.
        """
        payload = request.get_json(silent=True) or {}
        messages = payload.get("messages") or []
        locale: str = payload.get("locale") or "en-GB"
        opco: str | None = payload.get("opco") or None
        if opco in ("", "all"):
            opco = None

        if not isinstance(messages, list) or not messages:
            return jsonify({"error": "messages array is required"}), 400
        if messages[-1].get("role") != "user":
            return jsonify({"error": "last message must be from user"}), 400
        latest_user_msg = (messages[-1].get("content") or "").strip()
        if not latest_user_msg:
            return jsonify({"error": "latest user message is empty"}), 400
        if locale not in ("en-GB", "es-ES"):
            return jsonify({"error": f"unsupported locale: {locale}"}), 400
        if opco not in (None, "british-airways", "aer-lingus", "iberia"):
            return jsonify({"error": f"unknown opco: {opco}"}), 400

        # Step 1: figure out the retrieval query.
        # First turn → use the message verbatim. Follow-ups → rewrite via Haiku.
        is_followup = len(messages) > 1
        if is_followup:
            retrieval_query = rewrite_query_for_retrieval(messages)
            was_rewritten = (retrieval_query.strip() != latest_user_msg)
        else:
            retrieval_query = latest_user_msg
            was_rewritten = False

        # Step 2: bump k for listing-shaped queries (uses the rewritten query)
        k = LISTING_K if _looks_like_listing_query(retrieval_query) else DEFAULT_K

        # Step 3: retrieve
        try:
            results = retrieve(retrieval_query, locale=locale, opco=opco, k=k)
        except Exception as e:
            return jsonify({"error": f"retrieval failed: {e}"}), 500

        # Step 4: generate with full conversation history
        generation = None
        answer_text = ""
        cited = []
        if results:
            try:
                generation = generate_chat(messages, results, retrieval_query, was_rewritten)
                answer_text = generation.answer
                cited = generation.cited_sources
            except Exception as e:
                return jsonify({"error": f"generation failed: {e}"}), 500
        else:
            # No chunks retrieved — graceful fallback, don't burn an LLM call
            answer_text = (
                "I don't have anything in the help centre that matches that question"
                f"{' for ' + opco if opco else ''}. Try rephrasing or removing the OpCo filter."
            )

        # Audit log
        log_path = log_query(
            question=latest_user_msg,
            locale=locale,
            opco=opco,
            extra_filters={"retrieval_query": retrieval_query, "was_rewritten": was_rewritten,
                           "turn": len(messages)},
            retrieval=results,
            generation=generation,
            user_message=(generation.user_message if generation
                          else build_user_message(latest_user_msg, results)),
        )

        return jsonify({
            "answer": answer_text,
            "cited_sources": cited,
            "retrieval_query": retrieval_query,
            "was_rewritten": was_rewritten,
            "k": k,
            "locale": locale,
            "opco": opco,
            "results": [_result_to_json(r) for r in results],
            "tokens": (
                {"input": generation.input_tokens, "output": generation.output_tokens}
                if generation else None
            ),
            "log_path": str(log_path.relative_to(PROJECT_ROOT)),
        })

    @app.post("/api/ask")
    def api_ask():
        payload = request.get_json(silent=True) or {}
        question: str = (payload.get("question") or "").strip()
        locale: str = payload.get("locale") or "en-GB"
        opco: str | None = payload.get("opco") or None
        # Cast empty string from form to None
        if opco == "" or opco == "all":
            opco = None

        if not question:
            return jsonify({"error": "question is required"}), 400
        if locale not in ("en-GB", "es-ES"):
            return jsonify({"error": f"unsupported locale: {locale}"}), 400
        if opco not in (None, "british-airways", "aer-lingus", "iberia"):
            return jsonify({"error": f"unknown opco: {opco}"}), 400

        # k handling: explicit > listing-bump > default
        k_raw = payload.get("k")
        if isinstance(k_raw, int) and k_raw > 0:
            k = k_raw
            listing_detected = False
        elif _looks_like_listing_query(question):
            k = LISTING_K
            listing_detected = True
        else:
            k = DEFAULT_K
            listing_detected = False

        try:
            results = retrieve(question, locale=locale, opco=opco, k=k)
        except Exception as e:
            return jsonify({"error": f"retrieval failed: {e}"}), 500

        # Generation (only if we have something to ground on)
        generation = None
        answer_text = ""
        cited = []
        if results:
            try:
                generation = generate(question, results)
                answer_text = generation.answer
                cited = generation.cited_sources
            except Exception as e:
                return jsonify({"error": f"generation failed: {e}"}), 500

        # Audit log
        log_path = log_query(
            question=question,
            locale=locale,
            opco=opco,
            extra_filters=None,
            retrieval=results,
            generation=generation,
            user_message=(generation.user_message if generation
                          else build_user_message(question, results)),
        )

        return jsonify({
            "question": question,
            "locale": locale,
            "opco": opco,
            "k": k,
            "listing_detected": listing_detected,
            "results": [_result_to_json(r) for r in results],
            "answer": answer_text,
            "cited_sources": cited,
            "tokens": (
                {
                    "input": generation.input_tokens,
                    "output": generation.output_tokens,
                } if generation else None
            ),
            "log_path": str(log_path.relative_to(PROJECT_ROOT)),
        })

    return app


def _result_to_json(r: RetrievalResult) -> dict[str, Any]:
    return {
        "rank": r.rank,
        "chunk_id": r.chunk_id,
        "faq_id": r.faq_id,
        "internal_name": r.internal_name,
        "question": r.question,
        "short_answer": r.short_answer,
        "canonical_url": r.canonical_url,
        "hub_name": r.hub_name,
        "topic_name": r.topic_name,
        "additional_topic_names": r.additional_topic_names,
        "applicable_opcos": r.applicable_opcos,
        "distance": r.distance,
        "source_type": r.source_type,
    }


if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=5000, debug=True)
