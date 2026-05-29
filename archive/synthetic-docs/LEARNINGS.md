# Learnings — AVIOS RAG Demo

A running log of the content design decisions made building this prototype, and the questions they raised. Captured as we went, not retrofitted at the end.

The single most useful sentence in this document, if you read nothing else: **the content model decides what's queryable, how it's queryable, and how trustworthy the answers can be**. The schema is the thing.

---

## Why this project exists

A weekend exercise to close a hands-on gap before a Content Designer: AI Specialist interview. Premise: I had read about RAG and done the upstream structured-content work it depends on, but had never wired up retrieval over a vector store and a graph myself. The goal was to do it once, with a synthetic loyalty dataset modelled on systems I've actually shipped (the HSBC Offers tool), so I could speak to the trade-offs from experience rather than from reading.

The code is incidental. The interesting outputs are the design decisions in this file.

---

## The frame: structural metadata vs semantic content

The recurring decision through every entity is whether each field is **structural** (used for filtering — "in the UK", "tier=Gold", "category=hotel") or **semantic** (used for similarity search — descriptions, titles, prose).

In a Chroma-style vector store, this distinction is literal — there are two parameters when you index a record:

- `documents` — the prose that gets embedded and similarity-searched against
- `metadatas` — structured key-value pairs used for `where`-clause filtering at query time

Choosing what goes where is a content design decision, not an engineering one. Every field on a record either goes into one bucket, the other, both, or neither. That choice is what makes records queryable in particular ways.

A graph (NetworkX in this prototype, Neo4j or similar in production) extends the same logic. Structural relationships — `offer WITH_PARTNER partner`, `member HAS_TIER tier`, `partner IN_MARKET market` — let retrieval narrow to a candidate set before vector similarity ranks within it. The graph and the vector store aren't competitors; they're complementary tools, and the schema decides how labour is divided between them.

---

## Per-entity decisions

### Market — graph only

Market is structural metadata. Nobody asks an AVIOS chatbot "tell me about the United Kingdom" — they ask "what offers exist in the UK?", which is a query *about offers* filtered *by market*. Market is the filter, not the content.

So `markets.json` exists, gets loaded into the graph as nodes (so `IN_MARKET` edges resolve), but is **not indexed into the vector store**. Three records: UK, Ireland, Spain.

> **Generalisable principle**: Not all content belongs in the vector store. Structural metadata — markets, language codes, currency codes — should live in the graph as filterable context, not retrievable content. Including it would risk pulling "United Kingdom: en-GB, GBP" back as a top result, displacing actual content.

### Tier — graph only, prose-enriched benefits

Tiers are mostly structural (a member has a tier; a tier gates entitlement) but `benefits` is the interesting field. A user might ask "which tier gets lounge access?" (structural — graph traversal answers it) or "I travel a lot for work, which tier should I aim for?" (semantic — needs prose to match against).

Solution: model each benefit as `{slug, description}` where slug is the structural identifier and description is plain English. The graph filters by slug; if we later decide tier descriptions should be retrievable, we push descriptions into the vector store without changing the schema.

Also: differentiated benefits per tier rather than the placeholder uniform set. Blue gets one benefit (earn-only), Bronze adds two (priority check-in, baggage), Silver adds two more (lounge, priority boarding), Gold adds two more (seat selection, upgrade vouchers), Gold Guest List adds three more (concierge, guest lounge access, first-class voucher). Each tier inherits and extends — realistic shape, more interesting retrieval queries.

> **Generalisable principle**: Defer indexing decisions, not modelling decisions. A schema that *can* be indexed semantically is cheap; one that has to be re-shaped to allow it later is expensive.

### Member — query context, not content

This is the decision I'd most want to defend at interview. Members aren't content the way offers and rewards are. A member is a customer; member data would come from an identity system, not a CMS. Real loyalty queries are authenticated — the system already knows who the user is. The clever bit isn't retrieving *the user*; it's retrieving the **content** that's relevant to *them*.

So `members.json` exists as a small lookup, but is **not indexed into Chroma and not loaded into the same content graph as offers and rewards**. The CLI takes a member ID, looks up tier and market, and uses those as graph filters when querying offers. Member is parameter, not corpus.

> **Generalisable principle**: Distinguish content from context. Conflating them at index time leaks customer concepts into a content store, which is wrong architecturally and risky from a privacy perspective.

### Partner — graph node + single vector chunk

First entity where the answer is "yes, this needs to be in the vector store." Partner is conceptually atomic — a partner *is* one thing — so one record = one chunk. Embedding text is built at index time from `name + type + description + categories`, concatenated as natural language. Metadata carries `id`, `type`, `categories`, `markets` for filtering.

> **Generalisable principle**: Build embedding text from a few human-readable fields, not from JSON-as-string. Embedding models understand sentences, not field names.

### Reward — graph node + single vector chunk, eligibility and market in document text

Same shape as Partner with one wrinkle. Numeric fields (cost, top-up) are usually structural, but users do ask about cost in natural language ("what can I redeem with 26,000 avios?"). So the cost is **both** metadata (for structural cost filters) **and** rendered into the embedding text in human-readable form.

Eligibility (tier list) and market also live in both places, after a salience fix described in the next section. Briefly: when those fields lived only in metadata, the model summarised them wrong; when they were promoted into the document text, accuracy improved immediately. Same data, different placement, different result.

> **Generalisable principle**: A field can travel in both buckets when users ask about it both structurally and semantically. The cost is "26,000 avios" as a fact and "26,000 avios" as a phrase — both indexings serve different queries.

### Transfer Partner — graph node + lightweight vector chunk, with `partner_type` duplicated

Transfer partner queries lean structural ("can I transfer to a hotel?", "what's the minimum to Meridian?"), so the embedding text is shorter and rule-focused rather than descriptive.

The interesting decision: I added a `partner_type` field even though it duplicates information that could in theory be derived from the corresponding Partner entity via graph traversal. Defensible because for a demo the duplication is five seconds of work and saves a graph hop at retrieval time. In production this is a normalisation-vs-denormalisation call that depends on read/write ratios.

> **Generalisable principle**: Denormalise for read-heavy workloads when the cost of being out of sync is low. Document the denormalisation explicitly so future-you knows it's intentional.

### Offer — graph node + three vector chunks per offer

The most structurally interesting entity in the model, and the only one where one record produces multiple retrievable chunks. The full design is documented in `docs/Offer.md`; the highlights:

**Why chunked.** A real offer record contains three different kinds of content woven together — rules ("Earn 2 avios per £1 between 1 June and 31 August"), marketing ("Voted best long-haul carrier in 2025"), and structured data (rate tables, eligibility, dates). Embedding all three as one chunk averages those very different semantic vectors, diluting precision. Splitting them lets each chunk earn salience for the queries it's actually relevant to.

**The chunks.** Each offer produces three documents in the vector store — `need_to_know` (rules), `why_we_love` (marketing), `rates` (earn details). Each chunk's ID is `{offer_id}#{chunk_type}`, e.g. `offer_bluesky_2x_summer26#rates`. All three carry the same offer-level metadata plus a `chunk_type` discriminator. Each chunk's embedding text starts with the offer title — a chunk retrieved alone needs to identify the offer it belongs to.

**Dual-mode citations.** The audit captures both offer-level (`cited_sources`) and chunk-level (`cited_chunks`) IDs. The user-visible Sources block uses offer-level only — the user doesn't need to know about internal chunking. The compliance reviewer sees both, and can pinpoint which section of an offer informed any answer. The system prompt instructs Claude to cite offer-level; the parser normalises chunk IDs back to offer IDs as a safety net if the model slips.

**Why this is the most interview-relevant entity.** The chunking strategy turns out to be the thing that earns its keep most visibly in queries. See the partner-flavour query in the exploratory queries section below — the `why_we_love` chunk surfaced exactly because the query was about the partner's character, not the rules. With single-chunk indexing, the rules and rates noise would have averaged into the embedding and dragged the partner-flavour content down the rankings.

> **Generalisable principle**: A chunk should be the smallest unit that's meaningfully retrievable on its own. When one record contains structurally distinct kinds of content, chunking by section beats chunking by record. Three chunks of an offer compete with each other in retrieval, which is fine — that competition is how the right content surfaces for the right query.

> **Generalisable principle**: Citations have two audiences with different needs. Users want offer-level cleanliness; auditors want chunk-level precision. Don't conflate them — capture both, surface different ones.

---

## The salience fix — schema beats prompt

Caught a real bug in flight. Asked the system "what flights to JFK can I redeem?" and the answer scrambled tier eligibility — claiming Blue and Bronze members could book Business when in fact they can't, while burying the actual eligibility split (Economy: all five tiers; Business: Silver/Gold/Gold Guest List).

The cause was salience. Eligibility was in metadata only, at the bottom of each retrieved record. The model glanced at it, generalised across two records, produced a single fluent sentence that fit neither. **Information at the top of a record (in the document string) is treated as authoritative content; information at the bottom (in metadata) gets skimmed.** That's the salience principle.

The fix: promote `eligible_tiers` and `markets` into the embedding text itself. One line of change in the indexer, plus a small display-name lookup so slugs render as "Silver, Gold, Gold Guest List" instead of "silver, gold, gold_guest_list". Re-ran the indexer, re-ran the same query — the model produced the correct two-way split immediately. Same prompt, same model, same data, different schema, correct answer.

The temptation when an LLM gets something wrong is to tighten the prompt. The right move was to fix the schema. Prompt engineering is a patch; content design is a fix.

A small bonus: distance scores actually *improved* after the fix (0.842 → 0.785 on the JFK query). The expanded document text is more semantically aligned with travel/flight queries, not just longer. Bigger embedding text isn't always worse — when the additions are relevant, retrieval improves.

> **Generalisable principle**: Salience is a content-design lever, not just an engineering setting. Information that the model needs to state correctly belongs in the document text, where it's treated as authoritative content, not in metadata where it's a footnote. If a field gets summarised wrong by the model, the schema is probably the place to fix it before the prompt is.

---

## What exploratory queries taught me

After the build, I ran a series of queries against the system to feel out where it was solid and where it wobbled. Each one is a different teaching moment.

### Query 1 — "I'm a Bronze member in the UK. What hotel rewards can I redeem?"

Worked correctly. Two-stage filtering: a structural filter at retrieval (`kind=reward`) narrowed the candidate pool, then the model filtered at generation time using the user's tier. Two retrieved records with `Eligible tiers: Gold, Gold Guest List` were correctly excluded; three Bronze-eligible records were cited.

This works because of two things acting together. The retrieval-time filter is precise but doesn't understand intent. The generation-time filter handles conversational context like "I'm a Bronze member" — but only because the salience fix put eligibility in the document text where the model can see it. Without that fix, this query would probably have been wrong too.

> **Generalisable principle**: Filtering happens at two layers. Structural filters at retrieval are deterministic but rigid; generation-time filtering by the model handles conversational intent but depends on schema salience. Production systems would push tier filtering down to retrieval (deterministic, one less thing the model has to get right). For a demo, both layers cooperating is itself a useful illustration of why both exist.

### Query 2 — "what's the cheapest reward?"

Confidently wrong by an order of magnitude. The model said the cheapest reward was the JFK Economy flight at 26,000 avios. The actual cheapest is the Comfy continental breakfast at 2,000 avios — *thirteen times less* — and it wasn't even in the retrieved candidate set.

The retriever pulled records that were semantically close to "cheap" in vector space. That meant records with "Cheap" in the brand name (`transfer_cheap_rewards`, `reward_cheapcars_30_percent_off`), records mentioning discounts ("40% off"), and records described as "Economy". None of these are actually the cheapest. The model then made a fluent, confident, completely wrong claim from the wrong candidate set.

The fix isn't smarter retrieval, isn't better prompting, isn't tagging. **The user asked a superlative — "cheapest" — which is a global aggregate, not a similarity match.** Vector search is great for "find me records like X" and fundamentally bad at "find me the record where X is most extreme". You can patch around the edges with tags ("budget", "value") for adjective queries, but the unconstrained superlative case needs an `MIN(avios_cost)` or graph traversal, not nearest-neighbour search.

A refinement worth noting: a later query — *"what's the cheapest flight to JFK?"* — worked correctly. Same superlative shape, but with structural scope tight enough that all candidates were guaranteed to be in the retrieved set. So aggregates aren't categorically broken; they fail when the candidate set is unconstrained, succeed when the scope narrows the corpus to where the answer must be in the top-K. That nuance matters for routing decisions.

> **Generalisable principle**: Aggregate queries fail when scope is unconstrained, succeed when scope is narrow enough that the answer must be in the candidate set. Superlatives over the whole dataset need structured query (`MIN`, `MAX`, `ORDER BY`); scoped superlatives can be handled by retrieval if the scope guarantees coverage. A production system would detect both shapes at the routing layer.

### Query 3 — "do I get free coffee at British Airways lounges?"

Refused cleanly. British Airways isn't a partner in our synthetic dataset; "free coffee" isn't anywhere. The model produced the exact refusal phrase from the system prompt, verbatim, and cited zero sources.

What's interesting is what *almost* happened. The third-ranked retrieval was `reward_comfy_continental_breakfast` — the closest thing in our dataset to "coffee at a partner venue". A weaker prompt or model could easily have produced something like *"While there's no direct British Airways lounge information, you can enjoy continental breakfast at Comfy Hotels for 2,000 avios."* That's **fluent stitching**: using retrieved context to plausibly answer a question the context can't actually answer, by gluing together fragments. Every fact in the answer would technically be true; the answer as a whole would be misleading.

Three things together prevented that: the strict grounding rule in the system prompt, the precise refusal phrase to fall back on, and the audit-trail visibility that made the near-miss obvious in retrospect. None of those alone would be enough.

> **Generalisable principle**: The most insidious AI failure mode in regulated content is fluent stitching, not outright fabrication. The model retrieves something semantically adjacent and constructs a confident answer by gluing fragments to the user's framing. The defence is layered: grounded prompts, a precise refusal phrase, and audit logging. None alone is sufficient; together they make the system fail honestly.

### Query 4 — "can I transfer my avios to a hotel programme?"

Worked correctly with a subtle twist. The retrieval pulled five records: three transfer partners (Comfy, Cheap, Meridian) AND two regular Comfy Hotels rewards. The model correctly identified that "transfer my avios" means transfer partners specifically, excluded the regular hotel rewards from the answer, also excluded the car-hire transfer partner, and cited only the two hotel transfer partners.

This is intent-based filtering at generation time, working cleanly. The model used the schema cues we'd put in the document text — `Transfer partner (hotel)` vs `Category: hotel. Partner: Comfy Hotels` — to distinguish the two structurally similar records. Without those cues in the text, the answer might have mixed redemption and transfer in a confusing way.

> **Generalisable principle**: When two record types are structurally similar but semantically distinct (a partner where you redeem vs a partner you transfer to), the schema needs to make the distinction visible in the document text, not just in metadata. Generation-time intent filtering depends on the schema giving the model something to reason against.

### Query 5 — "I want to fly to Iceland with my avios."

Worked correctly with a content-design observation worth capturing. The actual answer (Viking flights to Reykjavik/KEF) surfaced cleanly. But neither the word "Iceland" nor "Reykjavik" appears in any document in the dataset — the embedding model bridged the gap because of the brand name "Viking", which carries strong Nordic associations from its training data.

That's the third time in this exercise we've seen brand vocabulary leaking into retrieval, and the second time it's *helped*. The first two — "Lux Store" dominating "luxury" queries, "Cheap Rewards" dominating "cheapest" queries — were false positives. This time, the leak was useful.

But it was useful by accident, not by design. If the partner were called "Atlantic Airways" instead of "Viking Airlines", the same query might have failed. **A schema that depends on accidental embedding behaviour is fragile.** A production audit would catch this either by deliberately tagging records with their semantic associations (so the system doesn't depend on brand luck), or by scrubbing brand-name leak at index time (so retrieval ranks by content, not by association).

> **Generalisable principle**: Brand names that carry implicit semantic weight interact with vector retrieval unpredictably — sometimes helping, sometimes hurting. A content audit should explicitly identify which brand names leak into search and decide whether to design for the leak (tagging) or against it (scrubbing). Don't depend on accidental embedding behaviour for retrieval correctness.

### Query 6 — "tell me about BlueSky Airlines as a partner" (the strongest demo)

This is the clearest single piece of evidence in the demo that the chunking strategy earns its keep. Top two retrievals were the Partner record (distance 0.555) and the offer's `why_we_love` chunk (distance 0.565), nearly tied, with a clear cliff to 0.750 at rank 3. Two semantically-related-but-distinct pieces of content competed cleanly for the same query.

The model wove five different record types into one coherent answer — Partner record, offer's marketing chunk, two reward flights, and a transfer partner — producing a comprehensive partner profile that no single record could have answered alone. Five citations in the user-visible Sources block, all offer-level (the chunk's `#why_we_love` suffix correctly normalised).

What this query proves: **section-level chunking lets the right content surface for the right query**. With single-chunk offer indexing, the rules and rates noise would have averaged into the embedding and the partner-flavour content would have been drowned out. Each chunk represents one semantic shape, so each can win the queries it's right for and lose the queries it isn't.

> **Generalisable principle**: Chunking by content type (rules vs marketing vs structured data) gives each chunk a coherent semantic identity that competes cleanly in retrieval. Records that mix content types compete poorly because their embedding is an average of incompatible signals.

### Summary of what the queries collectively prove

Most worked correctly. Two failed informatively. One had a subtle near-miss. The diversity is the point — a clean six-out-of-six demo would prove only that the system handles common cases. The mixed result demonstrates *where* the system's edges are, which is exactly what an interviewer wants to see.

The single architectural insight running through all of them: **the schema does most of the work**. When salient fields are in document text, the model reasons correctly about them. When the query shape doesn't match the retrieval shape (aggregates), no amount of schema or prompt fixes the problem. When the schema makes intent visible (transfer vs redemption), the model filters correctly. When retrieval depends on accidental brand-name semantics, the system is fragile. When chunks have coherent semantic shape, the right content surfaces for the right query. None of these are LLM problems. They're content problems.

---

## A second failure mode: listing queries

A query — *"I'm a gold member, what rewards can I get?"* — failed in a different way to the cheapest-reward case but for the same underlying architectural reason. The model returned **2 rewards out of the ~16 the user is actually eligible for**. Confidently incomplete rather than confidently wrong, but the same shape of failure.

The cause: the query is a *listing query* (give me all the X that match Y), not a *similarity query* (find me the best K matches for X). With `k=5`, the retriever returned only 5 records, and 3 of those were transfer partners (because their names contain the word "Rewards", which the user's query also contained). Only 2 actual rewards survived. The model dutifully reported them as if they were the universe of options.

> **Generalisable principle**: There are at least two query shapes that vector retrieval handles badly. **Aggregates** (where the answer is a single record selected by a function across all records — "the cheapest", "the most expensive"). **Listings** (where the answer is multiple records selected by a structural filter — "all rewards I'm eligible for", "every UK hotel partner"). Both need graph traversal or structured query, not nearest-neighbour search. A production system would detect these query shapes at the routing layer.

### The partial fix — listing-query detection (broken, then fixed)

The proper answer is a query-shape classifier or routing layer. For the demo, I added a regex heuristic in the CLI: if the question matches a listing pattern (`"what X can I get"`, `"which X are there"`, `"show me all the X"`, etc.), bump `k` from 5 to 30. Larger candidate set, more chance the relevant records are in it.

The first version of the regex had a real bug. The pattern was `\bwhat\s+\w+\s+(can|am|are|do)\b` — exactly one word between `what` and the auxiliary verb. That fired on *"what offers can I"* but not on *"what summer offers can I"*, because the latter has two words between `what` and `can`. I caught it later when I ran the offers-shaped query and saw no `NOTE:` line in the output.

Fix: change `\w+\s+` to `\w+(\s+\w+)*\s+` so the pattern matches one *or more* words. Same query re-ran, detector fired, retrieval expanded from 5 to 30 records, and the offer chunks (which had been pushed off the bottom of the top-5) surfaced into the candidate set. The fix took 30 seconds; the bug took the demo with it for the duration it was missed.

What the broken-then-fixed cycle taught me: **regex heuristics for natural language are fragile by definition**. A pattern that handles 80% of phrasings looks like it works until you run a real query with the 20% case. The fix is fine for a demo, but in production this would be a query classifier (fine-tuned model or LLM-based router), not a regex.

After the fix, re-ran the original query: 30 records retrieved, 16 cited, all the Gold-eligible UK rewards cleanly listed and grouped by category. Cost roughly 4× a normal query (~$0.0075 instead of ~$0.0018) — still fractions of a cent.

The CLI prints `NOTE: Listing-shaped query detected — k bumped to 30` whenever it fires, so the demo viewer can see the system noticing and adapting. Transparency over magic.

> **Generalisable principle**: When the proper architectural fix is too much work for a demo, ship the heuristic and surface its operation visibly in the UI. *"NOTE: listing-shaped query detected"* is content design — it tells the user the system noticed something and adapted. Without it, the user would just see 30 results and be surprised.

> **Generalisable principle**: A regex pattern that "looks right" can still fail on real input. Test query-classification heuristics with the queries users actually ask, not the queries the heuristic was designed against.

### Silent scope expansion — a recurring observation across listing queries

Across three listing queries, the model consistently expanded the user's filter rather than respecting it strictly:

- *"What rewards can I get?"* (Gold member) — model excluded Spanish RedSky flights without saying so. The user said "Gold member" not "UK Gold member", but the model inferred UK from context and silently filtered.
- *"What summer offers can I redeem?"* (initial failed run) — model offered to fall back to *"all rewards available to Silver members in the UK"* when the candidate set didn't contain enough offer chunks.
- *"What summer offers can I redeem?"* (post-listing-fix) — model returned ten rewards plus the BlueSky summer offer, treating "summer" as a soft hint rather than a hard filter.

It's the same pattern in three different costumes. The model is being a good chatbot — interpreting the user's intent generously, providing fallbacks, expanding scope when the strict reading would be unhelpful. In a regulated audit context, that helpfulness is a problem. With ten cited rewards and zero "but the actual summer-tagged offer is only this one" caveat, a quality reviewer reading the answer would reasonably conclude *"the system thought all ten were summer offers"* — which is false.

> **Generalisable principle**: The model's silent scope expansion is helpful in chatbot contexts and problematic in auditable contexts. The same answer can be "good" by one standard and "wrong" by another. Production answer: push the user's filter down to retrieval as a mandatory structural clause, not a soft hint at generation. If "summer" matters, it should be `categories=summer` at retrieval; if "UK" matters, it should be `markets=uk` at retrieval. Make the audit log show the filter that was applied.

### Completeness can fail even with k=30

The post-fix summer-offers query surfaced an unexpected variant of the completeness failure. Even with 30 records retrieved (including all relevant offer chunks and rewards), the model cited only the Business class JFK and KEF flights — not their Economy counterparts. Both Economy versions are Silver-eligible; both should have been listed. The Business flights ranked slightly higher (rank 22-23) than the Economy flights (rank 19, 22), and the model picked the higher-ranked of two near-identical records, leaving the gap.

That's a smaller version of the original Gold-member failure (16 cited out of ~16 eligible, at the cost of $0.0075 per query). Same family of bug: **vector-similarity ranking noise affects answer composition, not just answer relevance.** When two records are near-identical from a user-intent perspective, the model leans on the higher-ranked one and may skip the rest.

Larger candidate sets are not a complete fix; they're a partial one. The structural answer is to use the graph for listing queries — traverse the eligibility relationship, return all matching records — rather than relying on vector retrieval at all.

> **Generalisable principle**: Larger `k` partially mitigates listing-query failures but doesn't eliminate them. Even with the right records in the candidate set, ranking noise can mean structurally-equivalent records get unequal treatment in the answer. The honest fix for listings is graph traversal, not vector retrieval at scale.

---

## Bigger questions raised during the build

### How is the graph constructed and inspected?

In this prototype, the graph is rebuilt in-memory from JSON every time the program runs. Pure-Python NetworkX, no persistence, fast enough for 50–100 records that the rebuild cost is invisible. Three inspection patterns built in:

1. A summary printed on load: "loaded N nodes, M edges, K orphans"
2. A flat dump of all edges, one per line, for spotting broken references
3. Optional GraphViz/HTML export for visualising the structure

Production AVIOS would use a dedicated graph database — Neo4j is the most common, AWS Neptune in cloud-native estates. Conceptual model is the same; operational picture is very different.

### How do we keep the model in sync with new and changed data?

Three patterns sit behind this:

- **Scheduled rebuild** — cron job rebuilds the index nightly. Suits stable content; embarrassing for typos.
- **Event-driven incremental** — CMS publish event triggers indexer to add/update/delete just the changed record. Suits frequent changes; the *delete* case is the trap (an expired offer must disappear from search, not linger).
- **Versioned collections** — build new index alongside old, validate, atomic swap. Suits high-stakes content; common in regulated industries. AVIOS would qualify.

For the demo: total rebuild on every run. Crude but correct.

The bit content design owns is **defining what counts as a "content change worth re-indexing"**. A logo URL update probably doesn't matter; a description rewrite does; a market addition affects metadata; a cost change affects both. That list is governance, not engineering, and writing it is content design work.

There's also an embedding cost dimension. Re-embedding has a real bill if you use a paid API. Content fingerprinting (hash the embedding text, only re-embed when the hash changes) is the standard mitigation. Our demo sidesteps this by using Chroma's local sentence-transformer model — no API cost.

### How do we audit and trace responses?

This is the most important question for a regulated context, and the honest answer is: RAG doesn't give you an audit trail for free. You build one. Four things to log per query:

1. **The user's input**, verbatim, timestamped
2. **The retrieval step** — which records came back, in what order, with what scores, and what filters were applied
3. **The full prompt sent to the model** — including retrieved context, system instruction, everything
4. **The model's response**, verbatim, plus model version

Log all four and you can reconstruct any answer. In the failure scenario "model said 100 avios, reality is 1000 avios", you can immediately see whether the bug was in the data, the retrieval, or the model. Different fixes for each.

For chunked records (offers in this demo), the audit needs **dual citation granularity**: offer-level for user-visible answers, chunk-level for compliance review. Without chunk-level capture, the audit can't pinpoint *which section of an offer* informed a given answer.

This isn't theoretical. The salience bug was caught precisely because the audit trail showed what was retrieved and what the model produced. The cheapest-reward failure was caught the same way — the audit showed the retrieved records didn't include the actual cheapest one. The listing-query failure was caught from audit too — only 2 of 5 retrieved records were rewards, and 14 Gold-eligible records were missing entirely. The BA-lounge near-miss is visible in the audit too: the retrieval distances (all 1.2+) explain why refusal was the right behaviour. **Without the audit, all four of these would have been silent failures.**

The system instruction does heavy lifting alongside the logging. A line like *"Answer only from the provided context. After your answer, list the source IDs you drew from. If the context doesn't support an answer, say so — never guess. Never invent rates, costs, or terms."* — that's the single most important sentence in the system for a regulated use case.

But the prompt only works if the retrieval gave it the right facts to refuse against. Which is where the schema work comes back: atomic, tagged, retrievable records make the refusal-to-guess instruction enforceable.

In production, audit logs go to a centralised store with retention rules, access controls, and PII handling. For the demo, local files in `logs/` are fine.

### Distance scores are a quality signal, not just an ordering one

One thing that became clear from the exploratory queries: distance numbers in vector search aren't decoration. With tight filters that narrow the candidate set, the best match drops to sub-1.0 (the JFK economy reward at 0.785, the Comfy hotel transfer at 0.750, the BlueSky partner-flavour query at 0.555). With loose queries on irrelevant topics, all five results cluster at 1.4-1.8 (the BA-lounge query). That spread is a refusal cue.

A retrieval pipeline that returns top-K results unconditionally needs a layer above it that can recognise "all five of these are bad" and refuse. A naive distance threshold (e.g. "refuse if best result > 1.5") only works reliably *after* structural filters have narrowed the candidate set — without filters, vector search always finds something at 1.0-1.2, however irrelevant.

> **Generalisable principle**: Distance thresholds for refusal-to-guess only work meaningfully when filters narrow the candidate set first. A threshold against unfiltered retrieval rarely fires because vector search always finds *something*. Honest refusal depends on graph-aware filtering, not on prompt instructions alone.

### A small lesson in using LLM assistance honestly

Worth capturing because it happened during the build itself. While writing test queries, I (the assistant helping with the build) generated example filter syntax using SQL-style operators (`$like '%silver%'`) that aren't supported in Chroma's filter language. The queries failed at runtime with a clear error message; we debugged it, looked up the actual operator set, and fixed the metadata schema to use list-typed fields with `$contains`.

That's a microcosm of a broader failure mode: LLMs producing syntactically plausible code that's wrong in specific ways. Three things made it recoverable: the error message was specific (Chroma listed the operators it supports), the test was small enough to debug quickly, and the project had no production exposure. In a regulated AVIOS context, the same kind of hallucinated query syntax could pass tests, ship to production, and silently return wrong results. The defence: validators at the application boundary, prompt constraints that pin the model to a known operator set, and reviewer-in-the-loop for any LLM-generated query language.

> **Generalisable principle**: LLM assistance is wonderful for getting started fast and dangerous for shipping. The failure mode is plausible-looking output that's wrong in specific ways. Mitigations are layered: type-check at the boundary, constrain the prompt to a known vocabulary, and review every LLM-generated piece of query language before it touches production data.

---

## The thread running through everything

These decisions aren't independent. They form a chain:

> **Content schema → retrieval pipeline → prompt design → audit trail → trustworthy answers**

Each link depends on the one before it. Without atomic, tagged content, retrieval can't filter precisely. Without precise retrieval, prompts can't be grounded. Without grounded prompts, audit logs reveal fluent hallucinations. Without trustworthy answers, the whole system is a liability in a regulated context.

That chain is where content design earns its keep in AI products. Content designers don't add value at the end by editing model output; they add value at the start by shaping the model that the rest of the chain relies on.

---

*Last updated: 27 April 2026. Updated as the build progresses.*
