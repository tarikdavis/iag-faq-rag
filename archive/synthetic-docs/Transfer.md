Other loyalty programmes avios can be converted to. Loyalty-specific.

## Schema note

Transfer partners use `transfer_{snake_case}` IDs to keep them distinct from earn/redeem `partner_*` IDs. Several airlines (BlueSky, RedSky, Clover, Viking) and one hotel chain (Meridian) appear as both — once as a partner where you earn or redeem avios, once as a transfer partner where you convert avios into their own programme's points. Both are realistic and they live in separate entities so the relationships stay clean.

| Field          | Type   | Example                     |
| -------------- | ------ | --------------------------- |
| `id`           | string | `transfer_bluesky_rewards`  |
| `name`         | string | "BlueSky Rewards"           |
| `ratio`        | string | "1 avios = 1 BlueSky point" |
| `min_transfer` | int    | 1000                        |
| `markets`      | list   | ["uk", "ie"]                |
| `tc_ids`       | list   | [`tc_ids_collect`]          |
| `synthetic`    | bool   | `true`                      |

| Field          | Type   | Example                     |
| -------------- | ------ | --------------------------- |
| `id`           | string | `transfer_redsky_rewards`   |
| `name`         | string | "RedSky Rewards"            |
| `ratio`        | string | "1 avios = 1 RedSky point"  |
| `min_transfer` | int    | 5000                        |
| `markets`      | list   | ["es"]                      |
| `tc_ids`       | list   | [`tc_ids_collect`]          |
| `synthetic`    | bool   | `true`                      |

| Field          | Type   | Example                      |
| -------------- | ------ | ---------------------------- |
| `id`           | string | `transfer_clover_rewards`    |
| `name`         | string | "Clover Rewards"             |
| `ratio`        | string | "1 avios = 6 Clover points"  |
| `min_transfer` | int    | 2000                         |
| `markets`      | list   | ["ie"]                       |
| `tc_ids`       | list   | [`tc_ids_collect`]           |
| `synthetic`    | bool   | `true`                       |

| Field          | Type   | Example                      |
| -------------- | ------ | ---------------------------- |
| `id`           | string | `transfer_viking_rewards`    |
| `name`         | string | "Viking Rewards"             |
| `ratio`        | string | "1 avios = 3 Viking points"  |
| `min_transfer` | int    | 3000                         |
| `markets`      | list   | ["uk"]                       |
| `tc_ids`       | list   | [`tc_ids_collect`]           |
| `synthetic`    | bool   | `true`                       |

| Field          | Type   | Example                     |
| -------------- | ------ | --------------------------- |
| `id`           | string | `transfer_comfy_rewards`    |
| `name`         | string | "Comfy Rewards"             |
| `ratio`        | string | "1 avios = 2 Comfy points"  |
| `min_transfer` | int    | 2000                        |
| `markets`      | list   | ["uk", "es", "ie"]          |
| `tc_ids`       | list   | [`tc_ids_estore`]           |
| `synthetic`    | bool   | `true`                      |

| Field          | Type   | Example                       |
| -------------- | ------ | ----------------------------- |
| `id`           | string | `transfer_meridian_rewards`   |
| `name`         | string | "Meridian Rewards"            |
| `ratio`        | string | "1 avios = 8 Meridian points" |
| `min_transfer` | int    | 1500                          |
| `markets`      | list   | ["uk", "es", "ie"]            |
| `tc_ids`       | list   | [`tc_ids_estore`]             |
| `synthetic`    | bool   | `true`                        |

| Field          | Type   | Example                    |
| -------------- | ------ | -------------------------- |
| `id`           | string | `transfer_cheap_rewards`   |
| `name`         | string | "Cheap Rewards"            |
| `ratio`        | string | "1 avios = 1 Cheap point"  |
| `min_transfer` | int    | 1000                       |
| `markets`      | list   | ["uk", "es", "ie"]         |
| `tc_ids`       | list   | [`tc_ids_collect`]         |
| `synthetic`    | bool   | `true`                     |

| Field          | Type   | Example                  |
| -------------- | ------ | ------------------------ |
| `id`           | string | `transfer_lux_rewards`   |
| `name`         | string | "Lux Rewards"            |
| `ratio`        | string | "1 avios = 3 Lux points" |
| `min_transfer` | int    | 10000                    |
| `markets`      | list   | ["uk", "es", "ie"]       |
| `tc_ids`       | list   | [`tc_ids_instore`]       |
| `synthetic`    | bool   | `true`                   |

| Field          | Type   | Example                       |
| -------------- | ------ | ----------------------------- |
| `id`           | string | `transfer_flowers_rewards`    |
| `name`         | string | "Flowers Rewards"             |
| `ratio`        | string | "1 avios = 3 Flower points"   |
| `min_transfer` | int    | 4000                          |
| `markets`      | list   | ["uk", "es", "ie"]            |
| `tc_ids`       | list   | [`tc_ids_instore`]            |
| `synthetic`    | bool   | `true`                        |

---

## What changed from the previous draft

- Replaced display-name IDs (`BlueSky Airlines`, `RedSky Airlines`) with snake_case (`transfer_bluesky_rewards`, `transfer_redsky_rewards`) to match the convention used everywhere else in the model.
- Fixed a few small inconsistencies: `tc_ids` values had stray characters, capitalisation of "Bluesky" / "Redsky" / "comfy", and consistent pluralisation of "points".
- Added a short schema note at the top explaining why airlines appear in both Partner and Transfer entities. That overlap is realistic and worth being explicit about — at interview it's a good "I noticed and modelled this deliberately" beat.
