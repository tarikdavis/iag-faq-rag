# FAQ RAG pipeline

A local RAG pipeline over the **real Avios FAQ sandbox corpus** in Contentful. Loads ~164 FAQs (in 2 locales) from the Delivery API, embeds them into a per-locale ChromaDB index, and serves cited grounded answers through a CLI and a Flask web UI.

The point of this pipeline is to validate the v4.1.1 spec's **hard OpCo filter** end-to-end: an AerClub user must never see a BA-only answer, enforced at the vector store level before retrieval, not via post-rank re-ranking.

> Historical note: this folder started as a synthetic-data demo built for an interview. That code is now archived in `archive/synthetic-data/` and `archive/synthetic-docs/`. The patterns we kept (cited-grounded-answer prompt, ChromaDB scaffolding, audit logging, CLI ergonomics) are reused; the data loader, chunk shape, and graph layer have been replaced for the FAQ domain.

## What it does

1. **Loads** the FAQ corpus from Contentful via the Delivery API (one query per content type — `servicingHub`, `faqTopic`, `faq`), joined client-side. Both `en-GB` and `es-ES` content arrive in a single round trip.
2. **Builds** one ChromaDB collection per locale (`faqs_en_gb`, `faqs_es_es`). Each chunk's embedded text is the **retrieval header** (question + variants + searchSummary). The answer body markdown lives in chunk metadata.
3. **Retrieves** top-k chunks for a question. **OpCo is a HARD pre-retrieval filter** applied as a ChromaDB `where` clause — chunks whose `applicable_opcos` doesn't include the user's OpCo are excluded before vector similarity is even computed.
4. **Generates** a cited answer via Claude (Haiku 4.5) using the grounded prompt — model is instructed to answer ONLY from the provided context and refuse to guess.
5. **Logs** every query to `logs/<timestamp>_<query-id>.json` with system prompt hash, retrieval results, full user message, and the model's response. Self-contained audit trail.

## Setup

Requires Python 3.11+ and `pip`. Recommend a venv.

```bash
cd rag-pipeline
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install chromadb anthropic python-dotenv flask
cp .env.example .env
# Edit .env with your Contentful space ID + Delivery token + Anthropic key
```

## Build the index

Fetches the live corpus and builds the per-locale ChromaDB collections.

```bash
python src/build_index.py
```

Output is verbose — counts per content type, per locale, plus a warning if any FAQ has no OpCos (a chunk with empty `applicable_opcos` is invisible to every opco's queries, which is almost always a content bug worth fixing).

The build wipes `chroma_db/` and rebuilds from scratch. Fine for a corpus of ~164 entries; takes ~10s.

## Ask a question — CLI

```bash
# Default: en-GB, no opco filter
python src/ask.py "I'm missing my Avios — what should I do?"

# OpCo-filtered (the headline use case for validating the spec)
python src/ask.py "Forgotten password" --opco aer-lingus
python src/ask.py "How do I link my accounts?" --opco british-airways
python src/ask.py "How do I cancel my subscription?" --opco iberia

# Spanish
python src/ask.py "He perdido mis Avios" --locale es-ES --opco iberia

# Hub-scoped (any chunk metadata field can be equality-filtered)
python src/ask.py "Can I cancel my order?" --filter hub_slug=avios-shop
```

Output is four blocks: retrieved chunks (with distances + opcos), the answer, audit (cited IDs, tokens, cost), and the path of the JSON log written.

## Ask a question — web UI

```bash
python src/server.py
# open http://127.0.0.1:5000
```

Vanilla HTML + JS, no build step. Form has an **OpCo selector** front and centre, a locale selector, and the question box. Results render the answer, the retrieved chunks (with opco badges, distances, and live links to avios.com for citation verification), and an audit panel.

## Project layout

```
rag-pipeline/
├── README.md
├── .env.example
├── src/
│   ├── load_faqs.py     ← Contentful CDA loader (replaces load_data.py)
│   ├── build_index.py   ← per-locale ChromaDB build
│   ├── retrieve.py      ← opco hard filter + locale routing
│   ├── generate.py      ← Claude prompt + answer parsing
│   ├── log_query.py     ← per-query audit JSON
│   ├── ask.py           ← CLI entry point
│   └── server.py        ← Flask API + static file server
├── static/
│   ├── index.html
│   └── app.js           ← vanilla JS, no framework
├── chroma_db/           ← gitignored; built by build_index.py
├── logs/                ← gitignored; per-query JSON audits
└── archive/             ← historical synthetic-data demo
    ├── synthetic-data/
    └── synthetic-docs/
```

## The OpCo filter — how to verify it works

The whole point of this pipeline is the OpCo guarantee. Two ways to check:

```bash
# Same question, three opcos. The retrieved chunks should differ where
# the underlying content is opco-specific (e.g. forgotten-password has
# distinct BA and Aer entries).
python src/ask.py "Forgotten password" --opco british-airways
python src/ask.py "Forgotten password" --opco aer-lingus
python src/ask.py "Forgotten password" --opco iberia
```

In the retrieved-chunks output, the opco badges on every chunk should include the OpCo you queried with. No chunk in the BA result set should have only `aer-lingus` as its opcos.

The hard filter is a ChromaDB `where` clause on metadata, applied PRE-retrieval — see `src/retrieve.py::_where_for()`. If the filter regresses, the test cases above will surface it.

## Deploy to Render (share with your team)

The repo includes a `render.yaml` so Render's "New Web Service" flow auto-detects the build and start commands.

### Step 1 — push to GitHub

```bash
cd rag-pipeline
git init   # if you haven't already
git add .
git commit -m "Initial FAQ RAG pipeline"
git remote add origin https://github.com/<you>/iag-faq-rag.git
git push -u origin main
```

Either commit the whole project root and let Render's `rootDir: rag-pipeline` pick up the subfolder, or push just `rag-pipeline/` as its own repo — either works.

### Step 2 — create the service on Render

1. https://dashboard.render.com → **New → Web Service**
2. Connect the GitHub repo
3. Render reads `render.yaml` and pre-fills the build + start commands. Confirm:
   - Build command: `pip install -r requirements.txt && python src/build_index.py`
   - Start command: `gunicorn 'src.server:create_app()' --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
4. **Add environment variables** (Render dashboard → Environment):
   - `CONTENTFUL_SPACE_ID`, `CONTENTFUL_DELIVERY_TOKEN`, `CONTENTFUL_ENVIRONMENT=master`
   - `ANTHROPIC_API_KEY` (your real key — costs money per query)
   - `APP_PASSWORD` (the shared password your team types to get in — anything memorable)
   - `COOKIE_SECRET` (any random 32+ character string; locks sessions to this deploy)
5. Click **Deploy**. First build takes ~5 mins (heavy ChromaDB install).

### Step 3 — share with your team

Once deployed, Render gives you a URL like `https://iag-faq-rag-demo.onrender.com`. Tell colleagues:

> Open `https://iag-faq-rag-demo.onrender.com`, enter password `<APP_PASSWORD>`. The first page load after ~15 mins of inactivity takes ~30s because the free tier spins down — once it's warm, it's instant.

### Updating content after a Contentful edit

The index is built INTO the deploy artifact during `python src/build_index.py`, so editing a FAQ in Contentful doesn't automatically refresh the running app. Two options:

- **Manual redeploy** (easiest): Render dashboard → your service → **Manual Deploy → Deploy latest commit**. Triggers a fresh build, pulls latest from Contentful, ~5 min.
- **Push an empty commit**: `git commit --allow-empty -m "Refresh index" && git push` — auto-deploy picks it up.

### Cost considerations

- Render: **free** for this workload. Upgrade to Starter ($7/mo) only if you want always-on (no cold starts) or persistent disk.
- Anthropic: ~$0.0025 per query on Haiku 4.5. The password protection prevents bill spikes from leaked URLs.
- Contentful: Delivery API is free (read-only, cached at the edge).

### Why a password and not full auth?

This is an internal sandbox — the asset being protected is the Anthropic API budget, not user data. A shared password is appropriate for "people I've told the password to can use it; the rest of the internet can't run up the bill." Auth lives in `src/server.py::_login_page()`; one signed cookie, no session store, no user management.

## Notes + caveats

- **Embeddings**: defaults to ChromaDB's bundled sentence-transformer (`all-MiniLM-L6-v2`). For better Spanish retrieval, swap for a multilingual model — but the spec recommends per-locale indexes anyway, so the gain is incremental.
- **Listing-shaped queries** ("what are all the ways to earn Avios?") get k automatically bumped to 15. Imperfect heuristic; proper fix is query routing.
- **Markdown**: answers are returned as markdown, rendered lightly in the UI. The model is instructed to keep formatting minimal.
- **Cost**: ~$0.0025 per query on Haiku 4.5. Build_index has no API cost (local embeddings).
