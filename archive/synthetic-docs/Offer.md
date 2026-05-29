# Offer — Entity Design Spec

A time-bound promotion tied to a partner. Modelled on the HSBC Offers *offer* shape, with three loyalty-specific chunking decisions documented below.

This is the most structurally interesting entity in the AVIOS model. It's also the only entity where one record produces multiple retrievable chunks. The decisions in this document drove the implementation in `src/build_index.py:_index_offer()` and the citation handling in `src/generate.py`.

## 1. Why Offer is structurally different

Every other entity we've indexed (Partner, Reward, Transfer Partner) is conceptually atomic — one record represents one thing, and one chunk per record works fine. Offer breaks that pattern.

A real AVIOS offer record contains at least three different kinds of content:

- **Rules** — what the user must do, when, and what they earn. Factual, often dense, sometimes legally constrained.
- **Marketing** — why the partner is worth using. Editorial tone, promotional, often longer than the rules.
- **Structured data** — rate tables, eligibility rules, validity dates, T&C references.

Different content, different audiences, different ideal retrieval behaviour. A user asking *"what's the BlueSky summer offer earn rate?"* wants the rules. A user asking *"why should I book BlueSky?"* wants the marketing. Putting all of this into a single embedding text would average the semantic vectors of three very different concepts, diluting precision. Splitting them into separate chunks lets each one earn salience for the queries it's actually relevant to.

## 2. Chunking strategy: section-level

Each Offer record produces **three chunks** in the vector store, one per content section:

| Chunk type | Purpose | What goes in the embedding text |
|---|---|---|
| `need_to_know` | Rules and mechanics | Title + need-to-know description, eligibility window dates |
| `why_we_love` | Marketing / brand context | Why title + why description |
| `rates` | How earning works | Rate phrases (tiered or channel) + the rates description prose |

The graph holds **one node per offer**. The vector store holds **three chunks per offer**. Chunks carry the same offer-level metadata (offer_id, partner_id, eligible_tiers, markets, validity dates), plus a `chunk_type` discriminator. Filtering by chunk type at retrieval is a structural filter; rendering the right one to a user is a content design call.

> **Design principle**: A chunk should be the smallest unit that's meaningfully retrievable on its own. A rates table retrieved without the offer title would be useless ("£10 per spend over £150 — for what?"). A title plus key facts, retrieved on its own, is meaningful. Each chunk we produce must pass that test.

### Chunk IDs

The chunk ID format is `{offer_id}#{chunk_type}`:

- `offer_bluesky_2x_summer26#need_to_know`
- `offer_bluesky_2x_summer26#why_we_love`
- `offer_bluesky_2x_summer26#rates`

The `#` separator is web-conventional (it signals "part of", like a URL fragment) and avoids collision with the snake_case offer IDs already in use elsewhere in the schema.

## 3. Citation strategy: dual-mode

Citations work at two levels — chunk-level for the audit log, offer-level for the user-visible answer. The two audiences need different things:

- **The user (and customer-service reviewer)** sees `Sources: offer_bluesky_2x_summer26`. They care about which offer informed the answer; the internal chunking is implementation detail they don't need to track.
- **The compliance reviewer** sees `cited_chunks: [offer_bluesky_2x_summer26#need_to_know, offer_bluesky_2x_summer26#rates]`. If the model got something wrong, they can pinpoint which section's text let it through.

The system prompt asks Claude to cite offer-level IDs. The audit log captures both: whatever Claude wrote in the Sources block, plus the chunk IDs of every retrieved record (so we know what the model was given, regardless of what it cited).

> **Design principle**: Audit and user-visible citations serve different audiences. Chunk-level granularity in the audit, offer-level cleanliness in the answer. Don't conflate them.

## 4. Field-by-field model

Common fields across all offers (chunked or not):

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable offer ID, e.g. `offer_bluesky_2x_summer26`. |
| `partner_id` | string | Foreign key to a Partner. |
| `rate_shape` | enum | `tiered` or `channel`. Discriminates which of `tiered_rates` / `channel_rates` is populated. |
| `title` | string | One-line offer headline. |
| `need_to_know_title` | string | Heading for the rules section, usually "What you need to know". |
| `need_to_know_description` | string | Plain-English rules. The longest field on most offers. |
| `tiered_rates` | list \| null | If `rate_shape: tiered`. Array of `{condition, rate}` objects. |
| `channel_rates` | object \| null | If `rate_shape: channel`. Object with `online`, `instore`, `previous` keys; nulls allowed. |
| `avios_rates_description` | string | Prose explaining the rates. Often legalistic — exclusions, edge cases, claim windows. |
| `why_title` | string | Heading for the marketing section, usually "Why we love {Partner}". |
| `why_description` | string | Marketing prose about the partner. |
| `categories` | list | Tag taxonomy, e.g. `["travel", "summer", "double_avios"]`. |
| `eligible_tiers` | list | Slug list, e.g. `["bronze", "silver", "gold", "gold_guest_list"]`. |
| `markets` | list | Lowercase market codes, e.g. `["uk", "ie"]`. |
| `valid_from` | date | ISO date. |
| `valid_to` | date | ISO date. |
| `tc_ids` | list | T&C references. |
| `synthetic` | bool | Mandatory `true`. |

## 5. Worked example: BlueSky summer offer (rich)

This is the offer that demonstrates the full structural complexity. Other offers (Cheap Cars, RedSky) are simpler — they use `channel_rates` instead of `tiered_rates` and have shorter prose fields.

```json
{
  "id": "offer_bluesky_2x_summer26",
  "partner_id": "partner_bluesky_airlines",
  "rate_shape": "tiered",
  "title": "Double avios on long-haul this summer",
  "need_to_know_title": "What you need to know",
  "need_to_know_description": "Earn up to 10 avios per £1 on BlueSky Airlines long-haul flights booked between 1 June and 31 August 2026. Avios are calculated on the base fare excluding taxes, surcharges and seat-selection fees. Rewards appear in your AVIOS account within 14 days of flight completion. Your AVIOS membership number must be added at the time of booking; it cannot be added retrospectively after travel.",
  "tiered_rates": [
    { "condition": "Spend over £150 per booking", "rate": { "currency": "GBP", "avios_per_unit": 10 } },
    { "condition": "Spend under £149.99 per booking", "rate": { "currency": "GBP", "avios_per_unit": 5 } },
    { "condition": "Discounted fares (sale class)", "rate": { "currency": "GBP", "avios_per_unit": 3 } }
  ],
  "avios_rates_description": "The headline rate of 10 avios per £1 applies to bookings over £150 in standard fare classes. Bookings under £149.99 earn at the lower rate. Discounted (sale-class) fares earn at the lowest rate, regardless of price. Award flights and upgrades using avios do not earn during this offer. Rewards are credited 14 days after the flight is taken, not at the point of booking.",
  "why_title": "Why we love BlueSky Airlines",
  "why_description": "BlueSky was voted best long-haul carrier in 2025 for the third year running. Its mainline cabin offers extra legroom as standard, and Business Class includes flat-bed seats and a partner-hosted lounge in London Heathrow. AVIOS members have collected on BlueSky bookings since 2018, and this summer offer is the largest seasonal boost we've ever run.",
  "categories": ["travel", "summer", "double_avios"],
  "eligible_tiers": ["bronze", "silver", "gold", "gold_guest_list"],
  "markets": ["uk", "ie"],
  "valid_from": "2026-06-01",
  "valid_to": "2026-08-31",
  "tc_ids": ["tc_bluesky_summer26"],
  "synthetic": true
}
```

The other two offers in `data/offers.json` (Cheap Cars year-round, RedSky 2026) are deliberately simpler. The variety is part of the demo: real AVIOS offers vary wildly in length and structural complexity, and the chunking strategy needs to work for both extremes without being bloated for the simple cases.

## 6. What chunks look like in practice

For the BlueSky summer offer above, the indexer produces three chunks:

**Chunk 1 — `offer_bluesky_2x_summer26#need_to_know`**

> Embedding text: "Double avios on long-haul this summer. What you need to know: Earn up to 10 avios per £1 on BlueSky Airlines long-haul flights booked between 1 June and 31 August 2026. Avios are calculated on the base fare excluding taxes, surcharges and seat-selection fees. Rewards appear in your AVIOS account within 14 days of flight completion. Your AVIOS membership number must be added at the time of booking; it cannot be added retrospectively after travel. Eligible tiers: Bronze, Silver, Gold, Gold Guest List. Available in: United Kingdom, Ireland. Valid: 1 June 2026 – 31 August 2026."

**Chunk 2 — `offer_bluesky_2x_summer26#why_we_love`**

> Embedding text: "Double avios on long-haul this summer. Why we love BlueSky Airlines: BlueSky was voted best long-haul carrier in 2025 for the third year running. Its mainline cabin offers extra legroom as standard, and Business Class includes flat-bed seats and a partner-hosted lounge in London Heathrow. AVIOS members have collected on BlueSky bookings since 2018, and this summer offer is the largest seasonal boost we've ever run. Partner: BlueSky Airlines."

**Chunk 3 — `offer_bluesky_2x_summer26#rates`**

> Embedding text: "Double avios on long-haul this summer — earn rates. Spend over £150 per booking: 10 avios per £1. Spend under £149.99 per booking: 5 avios per £1. Discounted fares (sale class): 3 avios per £1. The headline rate of 10 avios per £1 applies to bookings over £150 in standard fare classes. Bookings under £149.99 earn at the lower rate. Discounted (sale-class) fares earn at the lowest rate, regardless of price. Award flights and upgrades using avios do not earn during this offer. Rewards are credited 14 days after the flight is taken, not at the point of booking."

Each chunk repeats the offer title at the start. That's deliberate: a chunk retrieved on its own (without sibling chunks) needs to identify the offer it belongs to, otherwise the model has to guess from the surrounding text.

## 7. Metadata on every offer chunk

Every chunk shares the same offer-level metadata, plus the chunk type:

```json
{
  "kind": "offer",
  "id": "offer_bluesky_2x_summer26#rates",
  "offer_id": "offer_bluesky_2x_summer26",
  "chunk_type": "rates",
  "partner_id": "partner_bluesky_airlines",
  "rate_shape": "tiered",
  "categories": ["travel", "summer", "double_avios"],
  "eligible_tiers": ["bronze", "silver", "gold", "gold_guest_list"],
  "markets": ["uk", "ie"],
  "valid_from": "2026-06-01",
  "valid_to": "2026-08-31",
  "synthetic": true
}
```

Note the duplication: `id` is the chunk ID, `offer_id` is the offer ID. The duplication is deliberate — it lets queries filter by either ("show me only rate chunks for offers from BlueSky" → `chunk_type=rates AND partner_id=partner_bluesky_airlines`), and lets the audit log capture both granularities.

## 8. What the offer dataset looks like overall

Three offers, illustrating different shapes:

- **`offer_bluesky_2x_summer26`** — `rate_shape: tiered`, rich prose, summer-seasonal, premium tier-gated.
- **`offer_cheap_cars`** — `rate_shape: channel`, simpler prose, year-round, all tiers.
- **`offer_redsky_3x`** — `rate_shape: channel`, Spanish market only, premium tier-gated.

That mix gives the demo enough variety to show the chunking working across realistic differences in offer complexity.

## 9. What this entity teaches us

The single most interesting interview line about Offer is that **content design is what makes multi-chunk records work**. Without the chunk-type discriminator, the audit can't distinguish which section of an offer informed an answer. Without the `offer_id` field carried on every chunk, a UI can't roll chunks up into the offer they belong to. Without the design decision to repeat the offer title at the top of every chunk's embedding text, retrieved-alone chunks lose their context.

These aren't engineering decisions. They're content-design decisions about what the user experiences when retrieval surfaces a fragment of an offer rather than the whole thing.

## 10. Open questions deferred

- **Validity-window filtering** — should expired offers be filtered out of retrieval automatically, or surface with an "expired" tag? The synthetic data only has 2026 offers so this doesn't bite us, but in production it would.
- **Partner offer pages** — should the chunked offer also be reconstructable into a single page view? The metadata (`offer_id`) makes it possible, but the assembly logic isn't built.
- **Chunk-level vs offer-level retrieval limits** — if a query retrieves all three chunks of one offer, should that count as 1 hit or 3 against the `k` cap? Currently 3, which can mean a single rich offer dominates a small candidate set.

These are real production concerns. They're out of scope for the demo but worth flagging at interview as the obvious next steps.
