The thing a member earns avios with or redeems against. Modelled on the HSBC Offers *merchant*.

## Schema

| Field         | Type   | Example                                              | Notes                                                                    |
| ------------- | ------ | ---------------------------------------------------- | ------------------------------------------------------------------------ |
| `id`          | string | `partner_bluesky_airlines`                           | Stable unique ID. snake_case, prefixed `partner_`.                       |
| `name`        | string | "BlueSky Airlines"                                   | Display name. Fictional.                                                 |
| `type`        | enum   | `airline`                                            | airline \| hotel \| retailer \| car_hire \| financial \| dining \| other |
| `categories`  | list   | ["travel", "premium"]                                | Tag taxonomy.                                                            |
| `markets`     | list   | ["uk", "es", "ie"]                                   | ISO-ish market codes.                                                    |
| `description` | string | "Long-haul carrier partnered with AVIOS since 2018." | Short, plain English.                                                    |
| `synthetic`   | bool   | `true`                                               | Mandatory.                                                               |

---

## Partners

### BlueSky Airlines

| Field         | Type   | Example                                                                  |
| ------------- | ------ | ------------------------------------------------------------------------ |
| `id`          | string | `partner_bluesky_airlines`                                               |
| `name`        | string | "BlueSky Airlines"                                                       |
| `type`        | enum   | `airline`                                                                |
| `categories`  | list   | ["travel", "premium"]                                                    |
| `markets`     | list   | ["uk", "ie"]                                                             |
| `description` | string | "Long-haul carrier and AVIOS partner. Voted best air carrier in 2025."   |
| `synthetic`   | bool   | `true`                                                                   |

### RedSky Airlines

| Field         | Type   | Example                                                                                |
| ------------- | ------ | -------------------------------------------------------------------------------------- |
| `id`          | string | `partner_redsky_airlines`                                                              |
| `name`        | string | "RedSky Airlines"                                                                      |
| `type`        | enum   | `airline`                                                                              |
| `categories`  | list   | ["travel"]                                                                             |
| `markets`     | list   | ["es"]                                                                                 |
| `description` | string | "Spanish carrier serving European and long-haul routes. AVIOS partner in Spain."       |
| `synthetic`   | bool   | `true`                                                                                 |

### Clover Airlines

| Field         | Type   | Example                                                              |
| ------------- | ------ | -------------------------------------------------------------------- |
| `id`          | string | `partner_clover_airlines`                                            |
| `name`        | string | "Clover Airlines"                                                    |
| `type`        | enum   | `airline`                                                            |
| `categories`  | list   | ["travel"]                                                           |
| `markets`     | list   | ["ie"]                                                               |
| `description` | string | "Irish short-haul carrier. AVIOS partner serving European routes."   |
| `synthetic`   | bool   | `true`                                                               |

### Viking Airlines

| Field         | Type   | Example                                                                          |
| ------------- | ------ | -------------------------------------------------------------------------------- |
| `id`          | string | `partner_viking_airlines`                                                        |
| `name`        | string | "Viking Airlines"                                                                |
| `type`        | enum   | `airline`                                                                        |
| `categories`  | list   | ["travel"]                                                                       |
| `markets`     | list   | ["uk"]                                                                           |
| `description` | string | "Nordic-focused carrier with routes from London to Iceland and Scandinavia."     |
| `synthetic`   | bool   | `true`                                                                           |

### Comfy Hotels

| Field         | Type   | Example                                                                       |
| ------------- | ------ | ----------------------------------------------------------------------------- |
| `id`          | string | `partner_comfy_hotels`                                                        |
| `name`        | string | "Comfy Hotels"                                                                |
| `type`        | enum   | `hotel`                                                                       |
| `categories`  | list   | ["travel"]                                                                    |
| `markets`     | list   | ["uk", "es", "ie"]                                                            |
| `description` | string | "Mid-tier hotel chain with city-centre and airport properties across Europe." |
| `synthetic`   | bool   | `true`                                                                        |

### Meridian Hotels

| Field         | Type   | Example                                                                         |
| ------------- | ------ | ------------------------------------------------------------------------------- |
| `id`          | string | `partner_meridian_hotels`                                                       |
| `name`        | string | "Meridian Hotels"                                                               |
| `type`        | enum   | `hotel`                                                                         |
| `categories`  | list   | ["travel", "premium"]                                                           |
| `markets`     | list   | ["uk", "es", "ie"]                                                              |
| `description` | string | "Boutique premium hotel group with 40 properties across Europe."                |
| `synthetic`   | bool   | `true`                                                                          |

### Cheap Cars

| Field         | Type   | Example                                                                                          |
| ------------- | ------ | ------------------------------------------------------------------------------------------------ |
| `id`          | string | `partner_cheap_cars`                                                                             |
| `name`        | string | "Cheap Cars"                                                                                     |
| `type`        | enum   | `car_hire`                                                                                       |
| `categories`  | list   | ["travel"]                                                                                       |
| `markets`     | list   | ["uk", "es", "ie"]                                                                               |
| `description` | string | "Global car rental partner with over 3,150 locations and 24-hour customer support."              |
| `synthetic`   | bool   | `true`                                                                                           |

### Lux Store

| Field         | Type   | Example                                                                                       |
| ------------- | ------ | --------------------------------------------------------------------------------------------- |
| `id`          | string | `partner_lux_store`                                                                           |
| `name`        | string | "Lux Store"                                                                                   |
| `type`        | enum   | `retailer`                                                                                    |
| `categories`  | list   | ["shopping", "premium"]                                                                       |
| `markets`     | list   | ["uk"]                                                                                        |
| `description` | string | "Luxury department store offering personal stylist services and an exclusive product range."  |
| `synthetic`   | bool   | `true`                                                                                        |

### Just Flowers

| Field         | Type   | Example                                                                          |
| ------------- | ------ | -------------------------------------------------------------------------------- |
| `id`          | string | `partner_just_flowers`                                                           |
| `name`        | string | "Just Flowers"                                                                   |
| `type`        | enum   | `retailer`                                                                       |
| `categories`  | list   | ["shopping"]                                                                     |
| `markets`     | list   | ["uk"]                                                                           |
| `description` | string | "Florist with same-day UK delivery and a monthly bouquet subscription service."  |
| `synthetic`   | bool   | `true`                                                                           |

---

## Notes

- All nine partners are referenced in `Offer.md`, `Reward.md` or `Transfer.md`, so every `partner_id` reference now resolves.
- Partner IDs use the convention `partner_{snake_case_name}` consistently with the rest of the model.
- Note the small overlap: BlueSky, RedSky, Clover and Viking appear as both **partners** (where you earn avios) and **transfer partners** (where avios can be transferred to their own loyalty programmes). That is realistic — most airlines run both arrangements with AVIOS — and the two roles live in separate entities so the relationships stay clean.
- Markets here reflect where the partner is *available to AVIOS members*, not where the partner operates globally.
