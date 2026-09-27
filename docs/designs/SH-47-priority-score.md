# Design: SH-47 — Priority score (S-RUL-6)

Status: **Design frozen (2026-09-26)** → `Developer-agent` build → `Reviewer-agent` initial PASS (2026-09-26, join structure/grain/formula-shape clean) → calibration bug found post-initial-review (`rul_urgency`'s hardcoded `720`h cap zeroed the term for all 3 machines, ~40% of the score's weight) → fix (p95-derived fleet-wide cap, §2) → `Reviewer-agent` re-review → **final clean PASS (2026-09-26), all live-verified** (§9). `Documenter-agent` reconciliation complete.
Branch: `feature/SH-4-47-priority-score`
Epic: EPIC-RUL | Story: SH-47 (S-RUL-6: "Priority score")
Traces to: [docs/04-1-LLD.md](../04-1-LLD.md) (grain spec for `CONS.FCT_PRIORITY_SCORE`, updated by this story — see §6 below), [docs/03-HLD.md](../03-HLD.md) §5 (priority-score formula, FR-FS-08), `predictive_maintenance_dbt/models/consumption/cons__fct_rul_prediction.sql`, `cons__fct_order.sql`, `cons__fct_inventory_fg.sql`, `cons__fct_inventory_spare.sql`, `docs/05-Epics.md` EPIC-RUL / S-RUL-6, S-RUL-7.

**Follows**: SH-45 (merged, `cons.fct_rul_prediction` — the per-tick RUL stream this table's `rul_urgency` term reads), SH-50 (merged, anomaly-derived RUL features), SH-69 (merged, `cons.fct_order`/`fct_inventory_fg`/`fct_inventory_spare`).

---

## 1. Scope

Build `CONS.FCT_PRIORITY_SCORE`, a dynamic table computing a single 0-100 composite priority score per sensor-enabled machine, per HLD §5's formula (FR-FS-08):

```
priority_score = 0.40 * rul_urgency         -- normalized inverse of predicted RUL hours
               + 0.25 * demand_pressure      -- firm near-term order volume, next 2-4 weeks, normalized
               + 0.20 * inventory_buffer     -- inverse of days-of-supply, normalized
               + 0.15 * spare_part_readiness -- binary/lead-time modifier
```

This is the prescriptive layer's headline number — "which machines need attention right now" — chaining off `cons.fct_rul_prediction` (dynamic, per-tick) plus `cons.fct_order`/`fct_inventory_fg`/`fct_inventory_spare` (static, weekly-grain).

**Not in scope**: Jira-ticket-aware deprioritization (a machine with an open Jira ticket already tracking its issue arguably shouldn't keep climbing the ranking) — deferred, no story yet. Jira-closed→CMMS sync (closing a ticket should presumably reflect back into maintenance history) — deferred, no story yet. The semantic-view `PriorityScore` entity's *verified query* and the S-RUL-7 Priority Queue Streamlit page that consumes it — S-RUL-7's job; this story only makes the entity mappable (the table exists, with the right grain and columns) but does not write the verified query or the page.

---

## 2. Per-term source and normalization

| Term | Weight | Source | Normalization |
|---|---|---|---|
| `rul_urgency` | 0.40 | `cons.fct_rul_prediction.predicted_rul_hours`, latest tick per equipment (§4) | Inverse-normalized: lower `predicted_rul_hours` → higher urgency. `1 - LEAST(predicted_rul_hours, cap) / cap` clamped to `[0, 1]`, then the composite scales the weighted sum to 0-100. **As shipped** (post-calibration-fix, see Status above and §9): `cap` is `PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY predicted_rul_hours)` computed fleet-wide over the *full* `cons.fct_rul_prediction` history (~172k rows, not just the latest-per-equipment slice), via a `rul_cap` CTE cross-joined (no fan-out) into the main query. This replaced an initial hardcoded `720`h literal that turned out to zero the term for all 3 machines (a real calibration bug — every machine's actual RUL exceeded 720h). The p95 choice is deliberate: **fleet-wide** (not per-machine) for cross-machine ranking comparability; **historically-derived** (not hardcoded) so it self-adjusts as more data is generated; **p95** (not `MAX`) to stay robust to a single outlier prediction. Live-verified cap = 12,315.43h (§9). |
| `demand_pressure` | 0.25 | `cons.fct_order.order_units`, summed over the next 2-4 firm weeks for the machine's `product_id`/`variant` (via `cons.dim_equipment`'s routing FK), latest available week as the window anchor | Normalized against the product's own trailing order-volume range (min-max or z-score-then-clip, decided at build time against real generated data — HLD only fixes the weight/direction, not the exact scaling constant) |
| `inventory_buffer` | 0.20 | `cons.fct_inventory_fg.fg_units_on_hand` ÷ that product's own recent average weekly draw (derived days-of-supply), latest available week | Inverse-normalized: lower days-of-supply → higher pressure to protect that machine's output, same `[0,1]` clamp pattern as `rul_urgency` |
| `spare_part_readiness` | 0.15 | `cons.fct_inventory_spare.units_on_hand`/`lead_time_days` for the machine's spare parts, latest available week | Binary/lead-time modifier: `0` if spares are on-hand (no readiness risk), scaling toward `1` as `lead_time_days` grows and `units_on_hand` shrinks — exact step function is a build-time detail, not re-litigated here beyond HLD §5's "binary/lead-time modifier" framing |

All four terms are pre-normalized to `[0, 1]` before the weighted sum, so the final `priority_score` naturally lands in `[0, 100]` once multiplied through.

---

## 3. Join structure — filter to latest BEFORE joining, not after

This is the central design decision for this story, and it drives §5/§6 below.

**Wrong shape (rejected)**: join `cons.fct_rul_prediction`'s full per-tick history (172k+ rows, SH-45 §9) against the order/inventory tables' own latest-week rows, then truncate the *output* to latest-per-equipment. This computes the full cross-product of RUL history × latest order/inventory before throwing away everything but the last row per equipment — wasteful, and it means the join itself still has to operate at 172k-row scale every refresh.

**Correct shape (this design)**: pre-filter *every* input to latest-per-key *before* any join happens, so the whole computation — join included — operates on a handful of rows end-to-end:

```sql
WITH latest_rul AS (
    SELECT equipment_id, reading_ts, predicted_rul_hours
    FROM {{ ref('cons__fct_rul_prediction') }}
    QUALIFY ROW_NUMBER() OVER (PARTITION BY equipment_id ORDER BY reading_ts DESC) = 1
),

latest_order AS (
    SELECT product_id, variant, order_week, order_units
    FROM {{ ref('cons__fct_order') }}
    QUALIFY ROW_NUMBER() OVER (PARTITION BY product_id, variant ORDER BY order_week DESC) = 1
    -- demand_pressure's "next 2-4 weeks" window is a separate aggregation over
    -- fct_order's firm near-term rows, anchored off this latest-available week --
    -- exact window boundaries are a build-time detail, not fixed here.
),

latest_inventory_fg AS (
    SELECT product_id, variant, period_week, fg_units_on_hand
    FROM {{ ref('cons__fct_inventory_fg') }}
    QUALIFY ROW_NUMBER() OVER (PARTITION BY product_id, variant ORDER BY period_week DESC) = 1
),

latest_inventory_spare AS (
    SELECT equipment_id, spare_part_name, period_week, units_on_hand, lead_time_days
    FROM {{ ref('cons__fct_inventory_spare') }}
    QUALIFY ROW_NUMBER() OVER (PARTITION BY equipment_id, spare_part_name ORDER BY period_week DESC) = 1
)

SELECT
    eq.equipment_id,
    lr.reading_ts AS score_ts,
    ... -- rul_urgency, demand_pressure, inventory_buffer, spare_part_readiness, priority_score
FROM {{ ref('cons__dim_equipment') }} eq
JOIN latest_rul lr ON lr.equipment_id = eq.equipment_id
LEFT JOIN latest_order lo ON lo.product_id = eq.product_id AND lo.variant = eq.variant
LEFT JOIN latest_inventory_fg lifg ON lifg.product_id = eq.product_id AND lifg.variant = eq.variant
LEFT JOIN latest_inventory_spare lis ON lis.equipment_id = eq.equipment_id
WHERE eq.is_sensor_enabled
```

(Sketch only — exact CTE bodies, the `demand_pressure` 2-4 week aggregation, and `spare_part_readiness`'s multi-spare-part-per-equipment collapsing are build-time details for `Developer-agent`, not frozen SQL.)

Every CTE is already latest-per-key before the joins run — `latest_rul` is `QUALIFY`-filtered exactly like `latest_order`/`latest_inventory_fg`/`latest_inventory_spare` always were designed to be. This is what actually removes the 172k-row join risk (§5), not merely the output grain.

---

## 4. Deviation from LLD: grain is latest-per-equipment, not per-tick

`docs/04-1-LLD.md`'s original table (line 63-64, pre-this-story) specified `CONS.FCT_PRIORITY_SCORE` at **1 row / equipment / tick** — the same per-tick grain as `cons.fct_rul_prediction`, its upstream driver. This story changes that to **1 row / equipment, always latest** (a handful of rows — one per sensor-enabled machine, ~3 rows at this project's scale). `docs/04-1-LLD.md` itself is updated to reflect this (§6 below).

**Why this changed, in full — this is the actual justification, not just a preference call:**

1. **The join shape needed here is genuinely new and unproven for incremental refresh.** Computing `demand_pressure`/`inventory_buffer`/`spare_part_readiness` requires picking the "latest available week" row per product/equipment from `cons.fct_order`/`cons.fct_inventory_fg`/`cons.fct_inventory_spare` (via `QUALIFY ROW_NUMBER() OVER (...) = 1`), then joining that to the per-tick RUL stream. Nothing else in this codebase has proven a `QUALIFY ROW_NUMBER()`-filtered CTE joined against a live dynamic table incrementalizes correctly — it's structurally different from the sliding-window pattern SH-45 §4 already proved works (a `ROWS BETWEEN ... PRECEDING` frame reading history but producing one new row per new input row). A `ROW_NUMBER() = 1` filter, by contrast, can in principle change its *entire result set* when a new row arrives that outranks the previous "latest" — a fundamentally different incrementalization question than a trailing window frame, and not one this project has tested.

2. **Shrinking the output to latest-per-equipment grain makes that risk essentially moot.** Even in the worst case — Snowflake decides this table can't incrementalize and falls back to `refresh_mode='full'` — recomputing a handful of rows (one per machine) on every 15-minute refresh is trivially cheap. This is not a workaround to dodge verifying the risk properly (§3 still does the right thing structurally, pre-filtering before joining); it is a design that makes the worst case cheap enough not to matter, given what this table is actually for (item 3).

3. **The only known downstream consumer wants "right now," not history.** Per `docs/05-Epics.md`'s S-RUL-6/S-RUL-7 entries, the only consumer of `cons.fct_priority_score` is S-RUL-7's future Priority Queue Streamlit page — inherently "which machines need attention right now," a ranked current-state list. Latest-per-equipment is the better semantic fit for that use case, not merely a performance compromise forced by item 1/2.

4. **No history is actually lost anywhere that matters.** The underlying `predicted_rul_hours` (`cons.fct_rul_prediction`) and `anomaly_score`/`is_anomaly` (`cons.fct_anomaly_result`) both remain fully per-tick historical in their own tables, untouched by this decision. Only the *derived composite* `priority_score` is latest-only. If a future story needs historical priority-score trending, it can be re-derived retroactively from those two tables' full history — a different materialization choice for the composite, not a one-way information loss.

5. **This matches an existing codebase convention, but this table is different enough to justify diverging from it.** `oee_command_center_app/pages/1_Overview.py:64` already computes "current state" by taking a `MAX(reading_ts)`-filtered query-time slice of `cons.fct_anomaly_result`'s full persisted history — i.e. "hold full history, filter to latest at query time" is this project's established pattern for other current-state views. The distinction here: `fct_priority_score`'s own *construction* requires joining against order/inventory tables that are themselves already latest-filtered snapshots, and performing that join at full 172k-row RUL-history scale (deferring the filter to query time, the Overview page's pattern) is exactly what introduces the unproven-incrementalization risk in item 1. Pre-filtering the RUL/anomaly side to latest-per-equipment *before* combining (§3) is what's different about this table, and why it gets its own grain choice instead of copying the Overview page's pattern by default.

---

## 5. Incremental refresh — risk and mitigation

Dependency chain: `cons.fct_rul_prediction` (dynamic, incremental, per-tick) + `cons.fct_order`/`fct_inventory_fg`/`fct_inventory_spare` (static tables, rebuilt per pipeline run) → `cons.fct_priority_score` (this table, dynamic).

**Known risk, not yet empirically verified (as of design freeze)**: whether a `QUALIFY ROW_NUMBER() OVER (...) = 1` CTE sourced from a live dynamic table (`latest_rul`, reading `cons.fct_rul_prediction`) incrementalizes under Snowflake's dynamic-table engine, or forces `refresh_mode='full'`. This is exactly the kind of thing this project's own discipline (LLD Module 5's "verify before relying on it") says to confirm empirically at build time, not assume — `Developer-agent` should run `SHOW DYNAMIC TABLES` on the real built table and record `refresh_mode`/`refresh_mode_reason`, same procedure SH-45 §4/§9 followed for `cons.fct_rul_prediction` itself.

**Why this risk is acceptable to leave open at design time (§4 item 2)**: latest-per-equipment grain caps the blast radius. If it falls back to `FULL`, that's a full rescore of ~3 rows every 15 minutes — negligible cost, unlike what a `FULL` fallback would have cost at the original per-tick (172k-row) grain.

**Final outcome (live-verified, post-calibration-fix)**: `refresh_mode = FULL` (config: `refresh_mode='auto'`, not `'incremental'`). This is *not* the same finding as the interim build achieved before the p95 cap fix — the interim build (hardcoded `720`h cap) did achieve `INCREMENTAL` refresh, empirically confirming the `QUALIFY ROW_NUMBER()`-per-CTE join shape itself incrementalizes fine. The switch to `FULL` is a direct, necessary consequence of the calibration fix specifically: Snowflake's dynamic-table engine does not support change tracking on queries containing `PERCENTILE_CONT` (`"Change tracking is not supported on queries containing the function 'PERCENTILE_CONT'."`), and the fleet-wide p95 cap (§2) requires exactly that function. Per the risk-acceptance framing above, this remains fully acceptable: the table's output is still ~3 rows, so even `FULL` refresh every 15 minutes is trivially cheap — the tradeoff is for a materially more correct/robust cap calculation, not a regression that needs revisiting.

---

## 6. LLD update (companion change)

`docs/04-1-LLD.md`'s `CONS.FCT_PRIORITY_SCORE` row is updated in the same edit as this design doc's freeze — see the diff described in this story's Jira comment / PR description. Summary: grain column changed from `1 row / equipment / tick` to `1 row / equipment, always latest`, with an inline footnote pointing to this section (§4) for the full reasoning. Column list (`equipment_id, score_ts, rul_urgency, demand_pressure, inventory_buffer, spare_part_readiness, priority_score`) is unchanged — `score_ts` now means "the `reading_ts` of the RUL prediction this row's `rul_urgency` was computed from," not "one row per historical tick," but the column itself still carries a timestamp so downstream consumers can see how fresh the score is.

---

## 7. Semantic view — in scope for the entity mapping, verified query deferred

Per `docs/03-HLD.md` §5 and `docs/04-6-LLD.md`'s `PriorityScore` entity row (`cons.fct_priority_score`, keyed on `equipment_id`, `score_ts`), this story's table build makes the `PriorityScore` semantic entity mappable for the first time (`docs/designs/SH-27-28-30-31-32-33-semantic-view-agent-chat.md` §"Decision" explicitly deferred `PriorityScore` pending this table's existence). **In scope for this story**: the table itself exists with the right shape for that mapping to eventually happen. **Deferred to S-RUL-7 / a future semantic-view update story**: actually adding `PriorityScore` to the `CREATE SEMANTIC VIEW` statement, any verified query built against it, and the Priority Queue Streamlit page. This story does not touch the semantic view DDL.

---

## 8. Explicitly deferred

- Jira-ticket-aware deprioritization (a machine with an open ticket already being tracked shouldn't necessarily keep climbing the ranking) — no story yet.
- Jira-closed→CMMS sync (closing a ticket reflecting back into maintenance history) — no story yet.
- `PriorityScore` semantic-view entity wiring, verified queries, and the S-RUL-7 Priority Queue Streamlit page — S-RUL-7's job.
- Exact normalization constants (`demand_pressure`'s min-max range, `inventory_buffer`'s days-of-supply threshold, `spare_part_readiness`'s step function) — build-time details against real generated data, not frozen here; HLD §5 fixes only the weights and term directions. (`rul_urgency`'s cap is no longer open — resolved to the p95-derived value in §2/§9.)
- ~~Empirical confirmation of whether the `QUALIFY ROW_NUMBER()`-per-CTE join shape incrementalizes (§5)~~ — resolved: it does (interim build, before the p95 fix, achieved `INCREMENTAL`). The *final* shipped table is `FULL` for the unrelated `PERCENTILE_CONT` reason in §5 — that's a separate, already-accepted tradeoff, not an open question.

---

## 9. Live-verified results (final, post-calibration-fix)

Verified against the live built table (`cons.fct_priority_score`) and independently hand-recomputed by both `Developer-agent` and `Reviewer-agent` on separate machines — all recomputations matched exactly.

- `rul_cap` (fleet-wide p95 of `predicted_rul_hours`, full ~172k-row `cons.fct_rul_prediction` history): **12,315.43 hours**.
- `CNC_BORING`: `rul_urgency = 0.8019`, `priority_score = 71.376`.
- `CNC_MILLING`: `rul_urgency = 0.9258`, `priority_score = 76.334`.
- `CNC_HORIZONTAL`: `rul_urgency = 0.0` — correctly floors at zero, since this machine's actual RUL (16,922.7h) legitimately exceeds the fleet's p95 cap. `priority_score = 27.629`.
- `dbt test`: 5/5 pass.
- `refresh_mode = FULL` (`refresh_mode='auto'` config) — see §5 for why.

**Operational gotcha — read before editing this model's SELECT body in future**: dbt's Snowflake dynamic-table adapter change-detection only diffs *configuration* (warehouse, `target_lag`, `refresh_mode`) against the live object via `SHOW DYNAMIC TABLES` — it does **not** diff the compiled SQL body against the object's stored definition. `Reviewer-agent` independently reproduced this during this story's review: appended a trailing SQL comment to the model file, ran `dbt run` without `--full-refresh`, and dbt silently issued only `ALTER ... SET WAREHOUSE`, leaving the *old* query body in place on the live object with no error or warning. **Any future edit to this model's `SELECT` body requires an explicit `dbt run --full-refresh`** — a plain `dbt run` will silently no-op on the body change and leave stale logic live.
