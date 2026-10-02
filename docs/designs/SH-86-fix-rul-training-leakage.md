# Design: SH-86 — Fix RUL training label leakage via per-tick snapshot sampling

Status: **Frozen**
Story: SH-86 ("Fix RUL training label leakage via per-tick snapshot sampling")
Traces to: `docs/04-4-LLD.md` §4a (spine), `docs/04-5-LLD.md` §2 (RUL AFT), `docs/designs/SH-44-train-rul-aft-model.md`, `docs/designs/SH-46-rul-model-evaluation.md`, `docs/designs/SH-50-anomaly-into-rul-features.md`
Follows: SH-49 (spine+split), SH-50 (anomaly features), SH-44 (train), SH-45 (inference DT), SH-46 (evaluation)

---

## 1. Root cause

`feast__spine_maintenance_cycle.sql` builds **one row per maintenance cycle**, with `y_lower` = the cycle's total operating-hours duration (tick-count × 0.25). `feast__training_dataset_rul.sql` then ASOF-joins this against the frozen feature snapshot with `MATCH_CONDITION (spine.cycle_end_ts >= feat.reading_ts)` — this always snapshots the feature row at (or just before) the cycle's **end** timestamp. Every training example is therefore an end-of-cycle snapshot, where the feature `hours_since_last_service` is nearly equal to the label `y_lower` by construction. The model learns `duration ≈ f(hours_since_last_service)` instead of reading actual sensor degradation signal. At inference time this learned relationship is applied to every tick (not just end-of-cycle ticks), producing an inverted RUL trajectory that **rises** toward failure instead of falling.

**Confirmed empirically** in `scratchpad/rul_debug/` (local pandas/xgboost experiments, not committed): switching to per-tick sampling with true remaining-time labels yields correlation +0.97 between predicted RUL and true remaining time, vs. −0.85 replicating the current (buggy) methodology, using the exact 22-column production feature set.

---

## 2. Fix overview

Switch from "1 row per cycle, snapshotted at cycle-end" to **"1 row per tick per cycle"**, where each tick's label is the true remaining time from that tick's position to the cycle end. This removes the ASOF-join leakage mechanism entirely — since the spine now carries each tick's own exact `reading_ts`, the downstream join becomes a plain equi-join (exact match) instead of an "ASOF, nearest at-or-before" join. No changes to the inference pipeline (`cons__fct_rul_prediction.sql` already runs per-tick with no leakage).

**Files changed**: `feast__spine_maintenance_cycle.sql`, `feast__training_dataset_rul.sql`, `schema.yml`, `scripts/06_train_rul_model.sql`, `scripts/06b_evaluate_rul_model.sql`.
**Files verified unaffected**: `cons__fct_rul_prediction.sql`, `feast__fct_sensor_features_inference.sql`, `cons__fct_anomaly_result.sql` (§9).

---

## 3. `feast__spine_maintenance_cycle.sql` — per-tick spine

The `events` / `closed_cycles` / `open_cycles` / `all_cycles` CTEs are **unchanged** (cycle boundary logic is correct). The final per-cycle aggregation is replaced with a per-tick CTE.

**ROW_NUMBER indexing note**: SQL's `ROW_NUMBER()` is 1-based. The remaining-hours formula uses `(n_ticks_in_cycle - tick_position + 1) * 0.25` so the **first** tick of a cycle gets `n * 0.25` (= the old per-cycle `y_lower`, total cycle operating hours) and the **last** tick gets `1 * 0.25 = 0.25h`. The `+ 1` is required because XGBoost's `survival:aft` objective internally computes `log(y)` — `y_lower = 0` would produce `log(0) = −∞`, breaking training. The minimum label of 0.25h (one tick-interval before cycle end) is physically meaningful: "one more 15-minute observation interval remains before the cycle-ending event."

```sql
{{ config(
    materialized='table',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    tags=['feast']
) }}

-- Per-tick RUL survival spine (S-RUL-1, LLD Module 4 §4a). One row per sensor
-- tick per maintenance cycle, with per-tick remaining-time labels. Cycle
-- boundaries = every CMMS event (PM or BREAKDOWN) per machine, plus one
-- still-open final cycle per machine.
--
-- SH-86: changed from per-cycle to per-tick grain to eliminate label leakage
-- caused by the old 1-row-per-cycle + ASOF-join pattern, where every training
-- example was an end-of-cycle snapshot whose hours_since_last_service ≈ y_lower
-- by construction. See docs/designs/SH-86-fix-rul-training-leakage.md §1.
--
-- Censoring convention (unchanged from pre-SH-86):
--   BREAKDOWN-ended -> uncensored, y_upper = y_lower
--   PM-ended / still-open -> censored, y_upper = NULL
-- y_lower/y_upper are now PER-TICK remaining operating hours to cycle end,
-- not the cycle's total duration.

WITH events AS (
    SELECT
        equipment_id,
        event_type,
        event_end_ts,
        LAG(event_end_ts) OVER (
            PARTITION BY equipment_id ORDER BY event_start_ts
        ) AS cycle_start_ts
    FROM {{ ref('cons__fct_maintenance_event') }}
),

closed_cycles AS (
    SELECT equipment_id, cycle_start_ts, event_end_ts AS cycle_end_ts, event_type
    FROM events
),

open_cycles AS (
    SELECT
        e.equipment_id,
        MAX(e.event_end_ts) AS cycle_start_ts,
        (SELECT MAX(reading_ts) FROM {{ ref('feast__fct_sensor_features_train') }}) AS cycle_end_ts,
        NULL AS event_type
    FROM {{ ref('cons__fct_maintenance_event') }} e
    GROUP BY e.equipment_id
),

all_cycles AS (
    SELECT * FROM closed_cycles
    UNION ALL
    SELECT * FROM open_cycles
),

cycle_ticks AS (
    SELECT
        c.equipment_id,
        c.cycle_end_ts,
        c.event_type,
        f.reading_ts,
        ROW_NUMBER() OVER (
            PARTITION BY c.equipment_id, c.cycle_end_ts
            ORDER BY f.reading_ts
        ) AS tick_position,
        COUNT(*) OVER (
            PARTITION BY c.equipment_id, c.cycle_end_ts
        ) AS n_ticks_in_cycle
    FROM all_cycles c
    JOIN {{ ref('cons__dim_equipment') }} eq
        ON eq.equipment_id = c.equipment_id
    JOIN {{ ref('feast__fct_sensor_features_train') }} f
        ON f.equipment_id = c.equipment_id
        AND f.reading_ts > COALESCE(c.cycle_start_ts, eq.commissioned_ts)
        AND f.reading_ts <= c.cycle_end_ts
)

SELECT
    equipment_id,
    reading_ts,
    cycle_end_ts,
    (n_ticks_in_cycle - tick_position + 1) * 0.25 AS y_lower,
    CASE WHEN event_type = 'BREAKDOWN'
         THEN (n_ticks_in_cycle - tick_position + 1) * 0.25
         ELSE NULL END AS y_upper
FROM cycle_ticks
```

**Key changes vs. current**:
- `LEFT JOIN` → `JOIN` (INNER) on `feast__fct_sensor_features_train`: cycles with zero ticks are excluded (no features to learn from, and `y_lower = 0` would break AFT anyway). Previously they'd produce a single row with `y_lower = 0`.
- No `GROUP BY` — each tick is its own output row.
- `reading_ts` is now an output column (part of the grain).
- Output grain: `(equipment_id, reading_ts)` with `cycle_end_ts` carried for the downstream train/test split anchor.

---

## 4. `feast__training_dataset_rul.sql` — equi-join + sliding window

The `split_anchor` and `feat_with_anomaly` CTEs are **unchanged**. The `ASOF JOIN` is replaced with a plain equi-join on `(equipment_id, reading_ts)`. The `window_anomaly_agg` grouped-CTE is replaced with sliding window functions computed inline, mirroring `cons__fct_rul_prediction.sql`'s `anomaly_history` CTE exactly (288 ticks = 72h at 15-min cadence).

```sql
{{ config(
    materialized='table',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    tags=['feast']
) }}

-- RUL training set (S-RUL-1/S-RUL-2, LLD Module 4 §4a, Module 5 §2) -- spine
-- equi-joined against the frozen feature snapshot (now including the anomaly
-- model's own output), plus a time-based dataset_split (FR-FS-03).
--
-- SH-86: replaced ASOF JOIN with equi-join on (equipment_id, reading_ts) --
-- the spine now carries per-tick reading_ts (see SH-86 design doc §1/§3),
-- eliminating the end-of-cycle snapshot leakage. The grouped window_anomaly_agg
-- CTE is replaced with sliding window functions matching
-- cons__fct_rul_prediction.sql's anomaly_history pattern (ROWS BETWEEN 287
-- PRECEDING AND CURRENT ROW), which is correct at per-tick grain and also
-- consistent with how inference already computes these aggregates.
--
-- Anomaly columns are computed here directly via MODEL(isolation_forest_model)
-- against FEAST.FCT_SENSOR_FEATURES_TRAIN (frozen) -- deliberately NOT read
-- from cons.fct_anomaly_result / FCT_SENSOR_FEATURES_INFERENCE, which are
-- inference-only, live-refreshing artifacts (Module 4 §5's train/inference
-- asymmetry: training must not depend on inference). See
-- docs/designs/SH-50-anomaly-into-rul-features.md §2 for the full leakage
-- analysis -- a single static split is sufficient here (no walk-forward
-- retraining) because isolation_forest_model is trained only on
-- feast.training_dataset_iso's 'train' split, and that split's cutoff is
-- now anchored to the same MAX(reading_ts) source this model's own cutoff
-- uses (see feast__training_dataset_iso.sql), so iso_cutoff == rul_cutoff
-- by construction -- the anomaly model never sees a tick from this table's
-- own test period during its training.

WITH split_anchor AS (
    SELECT DATEADD('month', -6, MAX(cycle_end_ts)) AS cutoff_ts
    FROM {{ ref('feast__spine_maintenance_cycle') }}
),

feat_with_anomaly AS (
    SELECT
        feat.*,
        (MODEL({{ target.database }}.cons.isolation_forest_model, DEFAULT)!predict(
            vibration_z, vibration_rolling_1h_z, vibration_rolling_8h_z, vibration_rolling_24h_z, vibration_rolling_7d_z,
            temperature_z, temperature_rolling_1h_z, temperature_rolling_8h_z, temperature_rolling_24h_z, temperature_rolling_7d_z,
            rpm_z, rpm_rolling_1h_z, rpm_rolling_8h_z, rpm_rolling_24h_z, rpm_rolling_7d_z
        ):"output_feature_0"::int = -1) AS is_anomaly,
        MODEL({{ target.database }}.cons.isolation_forest_model, DEFAULT)!decision_function(
            vibration_z, vibration_rolling_1h_z, vibration_rolling_8h_z, vibration_rolling_24h_z, vibration_rolling_7d_z,
            temperature_z, temperature_rolling_1h_z, temperature_rolling_8h_z, temperature_rolling_24h_z, temperature_rolling_7d_z,
            rpm_z, rpm_rolling_1h_z, rpm_rolling_8h_z, rpm_rolling_24h_z, rpm_rolling_7d_z
        ):"output_feature_0"::float AS anomaly_score
    FROM {{ ref('feast__fct_sensor_features_train') }} feat
)

SELECT
    spine.equipment_id,
    spine.reading_ts,
    spine.cycle_end_ts,
    spine.y_lower,
    spine.y_upper,
    feat.* EXCLUDE (equipment_id, reading_ts),
    MAX(CASE WHEN feat.is_anomaly THEN 1 ELSE 0 END) OVER (
        PARTITION BY feat.equipment_id ORDER BY feat.reading_ts
        ROWS BETWEEN 287 PRECEDING AND CURRENT ROW
    ) > 0 AS any_anomaly_flagged_72h,
    MIN(feat.anomaly_score) OVER (
        PARTITION BY feat.equipment_id ORDER BY feat.reading_ts
        ROWS BETWEEN 287 PRECEDING AND CURRENT ROW
    ) AS min_anomaly_score_72h,
    AVG(CASE WHEN feat.is_anomaly THEN 1.0 ELSE 0.0 END) OVER (
        PARTITION BY feat.equipment_id ORDER BY feat.reading_ts
        ROWS BETWEEN 287 PRECEDING AND CURRENT ROW
    ) AS pct_anomalous_ticks_72h,
    CASE WHEN spine.cycle_end_ts <= split_anchor.cutoff_ts
         THEN 'train' ELSE 'test' END AS dataset_split
FROM {{ ref('feast__spine_maintenance_cycle') }} spine
CROSS JOIN split_anchor
JOIN feat_with_anomaly feat
    ON feat.equipment_id = spine.equipment_id
    AND feat.reading_ts = spine.reading_ts
```

**Key changes vs. current**:
- `ASOF JOIN ... MATCH_CONDITION (spine.cycle_end_ts >= feat.reading_ts)` → plain `JOIN ... ON feat.reading_ts = spine.reading_ts`. The spine now carries the exact per-tick `reading_ts`, so no approximate point-in-time lookup is needed.
- `window_anomaly_agg` grouped CTE eliminated. The 72h sliding window is now computed directly as window functions in the final SELECT, using `ROWS BETWEEN 287 PRECEDING AND CURRENT ROW` — identical to `cons__fct_rul_prediction.sql`'s `anomaly_history` CTE pattern. `BOOLOR_AGG` is replaced with `MAX(CASE ... 1 ELSE 0 END) > 0` for the same reason documented in `cons__fct_rul_prediction.sql` (BOOLOR_AGG doesn't support sliding ROWS BETWEEN frames in Snowflake).
- `spine.reading_ts` added to output columns (part of the grain).
- `dataset_split` still keyed on `spine.cycle_end_ts` (not `reading_ts`) so all ticks from a cycle land in the same split — no within-cycle train/test contamination.
- The SH-50 leakage-safety invariant (`iso_cutoff == rul_cutoff by construction`) is preserved — only the spine's grain changed, not which rows feed the isolation-forest feature computation.

**Cold-start note for anomaly window functions**: the `ROWS BETWEEN 287 PRECEDING` frame naturally handles cold-start ticks the same way `cons__fct_rul_prediction.sql` does — early ticks have a smaller-than-72h effective window, producing aggregates over fewer ticks rather than NULL. This is a behavioral change from the old per-cycle `window_anomaly_agg` (which joined on a time range and could produce NULL for the zero-tick edge case), but it matches the inference-side behavior and the schema.yml column descriptions already allow for this ("NULL only for the extreme cold-start case").

---

## 5. `schema.yml` changes

Update the two affected models' descriptions and add a `reading_ts: not_null` test to the spine.

### `feast__spine_maintenance_cycle`

```yaml
  - name: feast__spine_maintenance_cycle
    description: >
      One row per sensor tick per maintenance cycle per machine (S-RUL-1,
      SH-86) -- cycle boundaries are every cons.fct_maintenance_event row
      (PM or BREAKDOWN both reset the wear clock) plus one still-open final
      cycle per machine. Right-censored survival labels: y_upper = y_lower
      for BREAKDOWN-ended cycles (observed failure), NULL for PM-ended/open
      cycles (censored). y_lower/y_upper are per-tick remaining operating
      hours to cycle end, reconstructed via tick position within the cycle
      against feast.fct_sensor_features_train, not calendar DATEDIFF.
    columns:
      - name: equipment_id
        tests:
          - not_null
      - name: reading_ts
        tests:
          - not_null
      - name: cycle_end_ts
        tests:
          - not_null
      - name: y_lower
        tests:
          - not_null
```

### `feast__training_dataset_rul`

```yaml
  - name: feast__training_dataset_rul
    description: >
      RUL training set (S-RUL-1/S-RUL-2, SH-86) -- feast__spine_maintenance_cycle
      (one row per tick per cycle) equi-joined against feast__fct_sensor_features_train
      for exact per-tick feature attachment, plus sliding 72h anomaly window
      aggregates (matching cons__fct_rul_prediction.sql's inference-side pattern),
      plus a time-based dataset_split column (FR-FS-03) anchored to MAX(cycle_end_ts)
      in the data. Also carries the isolation_forest_model's anomaly signal
      (single-tick is_anomaly/anomaly_score, computed directly against this table's
      own frozen feature snapshot -- not read from cons.fct_anomaly_result, SH-50).
    columns:
      - name: equipment_id
        tests:
          - not_null
      - name: reading_ts
        tests:
          - not_null
      - name: cycle_end_ts
        tests:
          - not_null
      - name: y_lower
        tests:
          - not_null
      - name: dataset_split
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ["train", "test"]
      - name: is_anomaly
        tests:
          - not_null
      - name: anomaly_score
        tests:
          - not_null
      - name: any_anomaly_flagged_72h
        description: >
          Sliding 72h window aggregate (ROWS BETWEEN 287 PRECEDING) -- never
          NULL with the sliding-window approach (early ticks get a smaller
          effective window, not NULL).
        tests:
          - not_null
      - name: min_anomaly_score_72h
        description: >
          Sliding 72h window aggregate -- never NULL (same reasoning as
          any_anomaly_flagged_72h).
        tests:
          - not_null
      - name: pct_anomalous_ticks_72h
        description: >
          Sliding 72h window aggregate, within [0, 1]. Never NULL.
        tests:
          - not_null
```

**Note**: the three anomaly window columns gain `not_null` tests (previously they allowed NULL for the zero-tick cold-start case). With the new sliding-window approach these columns are never NULL — the `ROWS BETWEEN` frame always includes at least the current row itself.

---

## 6. `scripts/06_train_rul_model.sql` — sort-key fix + hyperparameters

### 6a. Sort-key fix (required)

The current stable sort `.sort("equipment_id", "cycle_end_ts")` is no longer unique per row — many ticks share one `cycle_end_ts`. Change to `.sort("equipment_id", "reading_ts")`, which is unique per row under the new grain. Include `reading_ts` in the select list (so the sort column is available in the projected DataFrame) and drop it before DMatrix construction:

```python
train_pdf = (
    train_df
    .select(feature_cols + ["y_lower", "y_upper", "reading_ts"])
    .sort("equipment_id", "reading_ts")
    .to_pandas()
)
train_pdf.columns = [c.lower() for c in train_pdf.columns]

# Boolean columns must be cast to numeric before entering the DMatrix
train_pdf["is_anomaly"] = train_pdf["is_anomaly"].astype(int)
train_pdf["any_anomaly_flagged_72h"] = train_pdf["any_anomaly_flagged_72h"].astype(int)

dtrain = xgb.DMatrix(train_pdf[feature_cols])  # reading_ts not in feature_cols, excluded automatically
```

The `feature_cols` list itself (22 columns) is **unchanged**. `sample_input_data` in `log_model()` remains `train_pdf[feature_cols].head(1000)` (reading_ts excluded).

### 6b. Hyperparameter alignment (recommended)

Add `eta: 0.05` and raise `num_boost_round` from 100 to 200, matching the scratchpad's empirically validated configuration:

```python
params = {
    "objective": "survival:aft",
    "aft_loss_distribution": "normal",
    "aft_loss_distribution_scale": 1.0,
    "tree_method": "hist",
    "max_depth": 4,
    "eta": 0.05,       # SH-86: lower learning rate (default 0.3 was unset),
                        # empirically validated with per-tick training data
    "seed": 42,
}
booster = xgb.train(params, dtrain, num_boost_round=200)  # SH-86: raised from 100
```

**Rationale**: the per-tick training set is ~150× larger than the old per-cycle set (~30k vs. ~94 rows). A lower learning rate with more rounds allows the model to fit the much richer per-tick signal without overfitting — `eta=0.3` (XGBoost default) with 100 rounds on ~30k diverse-label rows risks under-fitting rather than over-fitting. These values were empirically validated in the scratchpad with the exact same 22-column feature set and AFT configuration, achieving +0.97 correlation.

**This is flagged as recommended, not mandatory**: if `Developer-agent` or `Reviewer-agent` has reason to prefer the existing hyperparameters (e.g., training-time budget concerns on ~30k rows), the sort-key fix alone is sufficient for correctness. The hyperparameters can be adjusted in a follow-up without re-running the dbt models.

---

## 7. `scripts/06b_evaluate_rul_model.sql` — concordance + trajectory correlation

This is the most substantive code change in the story.

> **Updated 2026-10-03 (follow-up rewrite, same PR #38, same Jira SH-86):** §7a/§7b below describe the *originally specified* O(n log n) Fenwick-tree algorithm. That implementation was **not what shipped**. Per explicit user direction in a later conversation (not a developer deviation), the Fenwick tree was removed entirely and replaced with a Snowflake SQL self-join that lets the warehouse compute the same metric natively — see §7a-v3/§7b-v3 immediately below for what was actually built. §7a/§7b are kept verbatim afterward purely as a historical record of the original plan; they do not describe the current implementation. The same follow-up also touched §7c (trajectory window 144h → 500h) and §7e (`_read_split` migrated from pandas to a Snowpark DataFrame) — see §7f/§7g.

### 7a-v3. Concordance index via SQL self-join (as shipped)

The Fenwick-tree approach was abandoned in favor of making the entire evaluation script Snowpark-native — no local Python algorithms at all. `_concordance_index_self_join()` builds a self-join over the scored test set using `row_number()` for a stable total order, joins on `a.rn < b.rn` (every unordered pair exactly once, no self-pairs), and classifies each pair via boolean expressions evaluated server-side, aggregated with a single `sf_sum(when(...))` pass:

```python
scored = scored_df.select(
    row_number().over(Window.order_by("equipment_id", "reading_ts")).alias("rn"),
    col("y_lower"), col("event_observed"), col("predicted_hours"),
)
a, b = scored.alias("a"), scored.alias("b")
pairs = a.join(b, col("a", "rn") < col("b", "rn"))
# comparable / concordant / discordant / tied_risk / tied_time expressions,
# same comparability rules as the original Fenwick-tree design (§7a):
#   both uncensored -> always comparable
#   one uncensored vs one censored -> comparable only if the uncensored
#     row's time is strictly earlier
#   both censored -> never comparable
result = pairs.agg(
    sf_sum(when(concordant_expr, 1).otherwise(0)).alias("concordant"),
    sf_sum(when(discordant_expr, 1).otherwise(0)).alias("discordant"),
    sf_sum(when(tied_risk_expr, 1).otherwise(0)).alias("tied_risk"),
    sf_sum(when(tied_time_expr, 1).otherwise(0)).alias("tied_time"),
).collect()[0]
```

**Comparability rules are unchanged** from the Fenwick-tree design (§7a) and from SH-46 §4 — only the mechanism changed, not the metric semantics.

**Complexity tradeoff (explicit, accepted)**: this trades the Fenwick tree's "stays O(n log n) forever" guarantee for "100% Snowpark, costs grow quadratically (O(n²)) with test-set size" — a self-join over n rows produces ~n²/2 pairs. At the current ~33k-row test-set scale this is ~537M pairs. Measured end-to-end runtime: **53.4 seconds on an X-Small warehouse**, covering model inference + the self-join + MAE/RMSE/trajectory-correlation/outlier-check — no performance problem observed at this scale.

**Scalability mitigation (documented, not applied)**: the script's own header comment specifies the fallback if the test set grows enough to make the O(n²) self-join a real cost/performance problem: add a `.filter(col("y_lower") <= 144)` (or `<= 500`) immediately before the self-join, restricting the concordance computation to a near-event window instead of the full test set. This is **not currently applied** — the full-test-set self-join measured acceptable at the live ~33k-row scale, so the mitigation is left as a documented option rather than pre-emptively implemented.

**Validation evidence**:
- Cross-checked once (not kept in the file) against `lifelines.utils.concordance_index()` — a well-tested, independent implementation available in Snowflake's Anaconda channel — in a throwaway stored procedure (`sp_verify_concordance_lifelines`, run once then dropped). On the full live 32,771-row test set: lifelines = **0.701746**, self-join = **0.701800** — matching within floating-point/tie-handling precision.
- This cross-check also required empirically confirming lifelines' sign convention for its `predicted_scores` argument: it treats the score like a predicted *survival time* (higher = later failure = lower risk) — the same natural meaning as this codebase's raw `predicted_hours` — so it must be passed directly, **not negated**. Negating gives 0.298254 (= 1 − 0.701746), confirming the sign-flip and ruling out an accidental convention mismatch.
- The self-join's `concordant`/`discordant`/`tied_risk`/`tied_time` counts also exactly match the counts produced by the original Fenwick-tree baseline on the same full test set: **323094267 / 137307689 / 17302 / 184266**.

### 7b-v3. Verification method actually used (supersedes §7b)

§7b below specifies a bit-identical cross-check against a brute-force O(n²) Python implementation on the original ~19/24-row test set, with the brute-force code removed afterward. **This did not happen.** Instead, the self-join was verified once against `lifelines.utils.concordance_index()` directly on the full live 32,771-row test set (see validation evidence above), using a one-off stored procedure that was created, run, and dropped — never committed to the repo. No brute-force Python implementation exists anywhere in the final `06b_evaluate_rul_model.sql`; the file now contains only the self-join implementation.

---

### 7a (original plan, historical — not what shipped, see §7a-v3). O(n log n) concordance index via Fenwick tree

The current O(n²) `_concordance_index_censored()` double loop is replaced with an O(n log n) algorithm. The test set grows from ~19 rows (~171 pairs) to ~30k+ rows (~450M pairs) — the O(n²) loop becomes infeasible.

**Algorithm** (preserving the exact same comparability rules the current code encodes):

```python
class FenwickTree:
    """Point-update, prefix-sum BIT over [1..n]."""
    def __init__(self, n):
        self.n = n
        self.tree = [0] * (n + 1)

    def update(self, i, delta=1):
        while i <= self.n:
            self.tree[i] += delta
            i += i & (-i)

    def prefix_sum(self, i):
        s = 0
        while i > 0:
            s += self.tree[i]
            i -= i & (-i)
        return s


def _concordance_index_censored(event_observed, event_time, estimate):
    """O(n log n) concordance index with exact same comparability rules as the
    O(n^2) brute-force version it replaces (SH-86).

    Comparability rules (right-censoring, unchanged from SH-46 §4):
      - both uncensored: always comparable
      - one uncensored (time_i) vs one censored (time_j): comparable only if
        the uncensored one's time is strictly earlier
      - both censored: never comparable

    Sweep direction: process rows in DESCENDING event_time order. For each
    uncensored row, all previously inserted rows (with strictly larger
    event_time) are comparable — regardless of whether the previously
    inserted row is censored or uncensored. Censored rows are never
    comparable to any previously inserted row (those have strictly larger
    time, and the rule requires the censored row's time to be strictly
    LARGER than the uncensored counterpart — which is the opposite of what
    the sweep order guarantees).

    Tied-time handling: rows sharing an identical event_time are processed
    as one atomic batch. Within a batch, only uncensored-uncensored pairs
    are comparable (both-uncensored rule), and since their times are equal,
    they are counted as tied_time (not concordant/discordant/tied_risk).
    All other within-batch pair types (uncensored-censored, both-censored)
    are not comparable.
    """
    import numpy as np

    n = len(event_time)
    if n == 0:
        return float("nan"), 0, 0, 0, 0

    # 1. Rank-compress estimate values to [1..m]
    sorted_unique = sorted(set(estimate))
    rank_map = {v: r + 1 for r, v in enumerate(sorted_unique)}
    m = len(sorted_unique)
    ranks = [rank_map[e] for e in estimate]

    # 2. Group rows into batches by event_time (descending)
    indices_by_time = {}
    for i in range(n):
        t = event_time[i]
        if t not in indices_by_time:
            indices_by_time[t] = []
        indices_by_time[t].append(i)

    sorted_times = sorted(indices_by_time.keys(), reverse=True)

    # 3. Sweep: descending event_time, Fenwick tree over estimate ranks
    bit = FenwickTree(m)
    total_inserted = 0
    concordant = 0
    discordant = 0
    tied_risk = 0
    tied_time = 0

    for t in sorted_times:
        batch = indices_by_time[t]

        # 3a. Query phase: only UNCENSORED rows in this batch query the BIT.
        #     All previously inserted rows have strictly larger event_time.
        #     An uncensored current row (smaller time) is comparable to ALL
        #     of them (both uncensored and censored previously inserted).
        for i in batch:
            if not event_observed[i]:
                continue  # censored row — not comparable to anything already inserted
            r = ranks[i]
            # Concordant: previously inserted rows with rank < r
            #   (lower risk estimate than current → correct, since current
            #   fails sooner and should have higher risk)
            c = bit.prefix_sum(r - 1)
            # Rows at exactly rank r: tied risk
            at_r = bit.prefix_sum(r) - c
            # Discordant: previously inserted rows with rank > r
            d = total_inserted - bit.prefix_sum(r)

            concordant += c
            tied_risk += at_r
            discordant += d

        # 3b. Within-batch tied_time: count of uncensored-uncensored pairs
        k_uncensored = sum(1 for i in batch if event_observed[i])
        tied_time += k_uncensored * (k_uncensored - 1) // 2

        # 3c. Insert ALL rows in this batch (uncensored and censored) into BIT
        for i in batch:
            bit.update(ranks[i])
        total_inserted += len(batch)

    denom = concordant + discordant + tied_risk
    c_index = (concordant + 0.5 * tied_risk) / denom if denom > 0 else float("nan")
    return c_index, concordant, discordant, tied_risk, tied_time
```

**Why ALL rows (including censored) are inserted into the BIT even though censored current rows don't query**: a future uncensored row (processed later, with a smaller event_time) IS comparable to a previously-inserted censored row with a larger event_time. The uncensored row queries the BIT and must find the censored row there. So censored rows must be inserted when encountered, even though they themselves never query.

### 7b (original plan, historical — not what shipped, see §7b-v3). Mandatory verification step (bit-identical cross-check)

The new O(n log n) implementation **must** be verified against the existing O(n²) brute-force on the **current** ~19-row test set **before** the dbt model changes are applied (after the changes, the test set grows to ~30k rows and the O(n²) becomes infeasible). Concrete procedure:

1. `Developer-agent` updates `06b_evaluate_rul_model.sql` first (with both implementations).
2. Run both on the current (unchanged) `feast.training_dataset_rul WHERE dataset_split='test'` (~19 rows).
3. Assert **all five** values are identical: `c_index`, `concordant`, `discordant`, `tied_risk`, `tied_time`.
4. Log the comparison in the stored procedure's return value (e.g., `"O(n^2) cross-check: PASS — c_index=0.9400, concordant=47, discordant=3, tied_risk=0, tied_time=0"`).
5. Only then proceed with the dbt model changes (§3/§4).
6. After verification, remove the O(n²) implementation from the production code — keep only the O(n log n) version. The verification output in the return string serves as the evidence trail.

The O(n²) version kept temporarily for this check is the existing `_concordance_index_censored()` function, renamed to `_concordance_index_censored_bruteforce()`.

### 7c. Trajectory-correlation metric (new, permanent regression guard)

> **Updated 2026-10-03**: the bucket window specified below is 0–144h. As shipped, the window was extended to **0–500h** (bucket width unchanged at 2h), per the same follow-up user direction as the concordance-algorithm change. Rationale was not deeply documented beyond the user's direction; the mechanism (Pearson correlation between bucket center and mean per-bucket prediction) is otherwise unchanged. See §7f for the full note.

This is the single check that would have caught the original bug immediately — a permanent regression guard against inverted-trajectory models.

```python
def _trajectory_correlation(y_lower, predicted_hours, event_observed):
    """Bucket uncensored test ticks by true remaining_hours (2h buckets,
    0–144h), compute mean predicted_hours per bucket, return Pearson
    correlation between bucket center and mean prediction.

    A healthy model produces strong positive correlation (higher true
    remaining time → higher predicted RUL). The original bug produced
    strong NEGATIVE correlation (predicted RUL rose toward failure).
    """
    import numpy as np

    mask = event_observed  # uncensored ticks only
    y = y_lower[mask]
    pred = predicted_hours[mask]

    if len(y) == 0:
        return float("nan")

    # 2h buckets from 0 to 144h (72 buckets)
    bucket_edges = np.arange(0, 146, 2)  # [0, 2, 4, ..., 144]
    bucket_centers = []
    bucket_means = []

    for lo, hi in zip(bucket_edges[:-1], bucket_edges[1:]):
        in_bucket = (y >= lo) & (y < hi)
        if in_bucket.sum() == 0:
            continue
        bucket_centers.append((lo + hi) / 2.0)
        bucket_means.append(pred[in_bucket].mean())

    if len(bucket_centers) < 3:
        return float("nan")  # not enough buckets for meaningful correlation

    return float(np.corrcoef(bucket_centers, bucket_means)[0, 1])
```

**Metric logging**: logged as `trajectory_correlation` via `mv.set_metric(...)`, alongside the existing `concordance_index`, `mae`, `rmse`, `median_ae`.

**Warning threshold**: if `trajectory_correlation < 0.5`, emit a WARNING in the return string:

```
WARNING: trajectory_correlation={value:.4f} (< 0.5 threshold) — predicted RUL
may not decrease toward failure as expected. Investigate model behavior.
```

**Threshold rationale**: the scratchpad's validated fix achieved +0.97; the buggy model had −0.85. A threshold of 0.5 sits well above any plausible regression to negative territory while allowing for the variance inherent in per-bucket aggregation on a finite test set. This threshold is a design decision, not a hard invariant — `Reviewer-agent` should verify it's reasonable given the actual numbers, not treat it as immutable.

### 7d. Per-row breakdown format change

With ~30k test rows, the current per-row formatted table (all 19 rows listed individually) becomes impractical. Replace with:

1. **Summary line**: row count, censored/uncensored split (unchanged format).
2. **Aggregate metrics**: concordance index, MAE/RMSE/median-AE, trajectory correlation (new).
3. **Top-10 worst predictions**: the 10 uncensored rows with largest `|y_lower - predicted_hours|`, in the same per-row format as today's full breakdown. Sufficient for diagnosability without overwhelming the return string.
4. **Outlier root-cause check**: unchanged logic (dynamically identifies the single worst abs-error uncensored row, compares feature values against train-split distribution). Still reads the train split for distribution stats — just one row's comparison, not the full ~30k.

The full per-row iteration (`for _, r in test_pdf.iterrows()`) is removed. MAE/RMSE/median-AE remain vectorized sklearn calls (unchanged, just larger n).

### 7e. `_read_split` changes

> **Updated 2026-10-03**: the signature below returns a pandas DataFrame. As shipped, `_read_split` returns a **Snowpark DataFrame** instead — see §7g for the full note; this section is kept as historical record of the original plan.

Add `reading_ts` to the select list (needed by the training script for sort, and available in the new table). The evaluation script doesn't use `reading_ts` directly (trajectory correlation buckets by `y_lower`, not `reading_ts`), but including it keeps `_read_split` consistent across both scripts and avoids a subtle drift if the evaluation script ever needs temporal ordering:

```python
def _read_split(session, split):
    df = session.table("snowcomotive.feast.feast__training_dataset_rul").filter(col("dataset_split") == split)
    pdf = df.select(FEATURE_COLS + ["equipment_id", "y_lower", "y_upper", "reading_ts"]).to_pandas()
    pdf.columns = [c.lower() for c in pdf.columns]
    pdf["is_anomaly"] = pdf["is_anomaly"].astype(int)
    pdf["any_anomaly_flagged_72h"] = pdf["any_anomaly_flagged_72h"].astype(int)
    return pdf
```

### 7f. Trajectory-correlation window: 0–144h → 0–500h (as shipped)

Per user direction in the same follow-up conversation as the concordance-algorithm change (§7a-v3), `TRAJECTORY_WINDOW_HOURS` was raised from 144 to 500 (bucket width stays 2h, so ~250 buckets instead of ~72). This is unrelated to the concordance-algorithm change but lands in the same PR/commit. The mechanism — Pearson correlation between bucket center and mean per-bucket `predicted_hours`, computed server-side on the small bucketed result — is otherwise unchanged from §7c.

### 7g. Full pandas → Snowpark migration (as shipped)

The entire evaluation script was rewritten to be Snowpark-native end-to-end, beyond just the concordance metric:

- `_read_split` (§7e) now returns a **Snowpark DataFrame**, not a pandas DataFrame. Boolean-to-int casts (`is_anomaly`, `any_anomaly_flagged_72h`) are applied via `.with_column(...).cast("int")` instead of pandas `.astype(int)`.
- Inference runs via `mv.run(test_df, function_name="predict")` directly on the Snowpark DataFrame — confirmed the Model Registry preserves all input columns alongside the prediction column when given a Snowpark DataFrame (`output_with_input_features` behavior), so no `.to_pandas()` round-trip is needed before scoring.
- MAE / RMSE / median-AE: computed via a single Snowpark `.agg(avg(...), sqrt(avg(pow(...))), median(...))` call, not sklearn calls against a pandas DataFrame.
- The outlier root-cause check's train-split distribution stats (min/p25/median/p75/max per feature) are computed via one Snowpark aggregation pass over all 22 features, not a full pull of the train split into pandas.
- Top-10 worst predictions: filtered/sorted/limited server-side (`.order_by(col("abs_error").desc()).limit(10)`), with only the final 10 rows ever collected to Python.
- `scored_df.cache_result()` is used once after inference so the cached result is reused across the concordance self-join, MAE/RMSE, trajectory correlation, top-10, and outlier check, instead of each metric re-running inference or re-reading the base table.

Net effect: only small, already-aggregated results are ever `.collect()`'d to Python (for string formatting in the return value) — no full-test-set-to-pandas pull happens anywhere in the file. This is a more thorough version of the Snowpark-native spirit the original design implied by moving the concordance computation server-side, extended to every other metric in the same script.

---

## 8. `scripts/06_train_rul_model.sql` `_read_split`-equivalent changes

The training script's Snowpark read must also include `reading_ts` for sorting (§6a). No other changes to the read logic beyond the sort-key fix already described in §6a.

---

## 9. Out of scope — verified unaffected

The following files are explicitly **not changed** by this story. Each is verified unaffected:

- **`cons__fct_rul_prediction.sql`**: inference already runs per-tick against live features via `feast__fct_sensor_features_inference` (not the spine), with sliding-window anomaly aggregates computed identically to this story's new training-side pattern (§4). No ASOF join, no spine dependency at inference time. The `!predict()` call's column order is unchanged (feature_cols unchanged). Unaffected.

- **`feast__fct_sensor_features_inference.sql`**: dynamic table driven by `cons__fct_sensor_reading`, no dependency on the spine or training dataset. Unaffected.

- **`cons__fct_anomaly_result.sql`**: reads `feast__fct_sensor_features_inference`, no dependency on the spine or training dataset. Unaffected.

- **`feast__fct_sensor_features_train.sql`** and **`macros/sensor_rolling_features.sql`**: frozen feature snapshot and its macro. Read-only inputs to the spine and training dataset. Unaffected.

- **`cons__fct_maintenance_event.sql`**: lean pass-through of `std__cmms_log`. Read-only input to the spine's cycle-boundary detection. Unaffected.

- **`feast__training_dataset_iso.sql`**: isolation forest training set. No dependency on the RUL spine. Unaffected.

---

## 10. Files to create/modify (Developer-agent's checklist)

**Modified dbt models**:
- `predictive_maintenance_dbt/models/feast/feast__spine_maintenance_cycle.sql` — §3 (per-tick rewrite).
- `predictive_maintenance_dbt/models/feast/feast__training_dataset_rul.sql` — §4 (equi-join + sliding window rewrite).
- `predictive_maintenance_dbt/models/feast/schema.yml` — §5 (description updates, `reading_ts: not_null` tests, `not_null` on anomaly window columns).

**Modified scripts**:
- `scripts/06_train_rul_model.sql` — §6 (sort-key fix, optional hyperparameters).
- `scripts/06b_evaluate_rul_model.sql` — §7 (concordance via SQL self-join as shipped — §7a-v3/§7b-v3 — plus trajectory correlation at the shipped 0-500h window — §7f — per-row breakdown format, and the full pandas→Snowpark migration — §7g).

**No changes**: `cons__fct_rul_prediction.sql`, `feast__fct_sensor_features_inference.sql`, `cons__fct_anomaly_result.sql`, `feast__fct_sensor_features_train.sql`, `macros/sensor_rolling_features.sql`, `cons__fct_maintenance_event.sql`, `feast__training_dataset_iso.sql`, `manage.py` (pipeline order unchanged — spine and training_dataset_rul are both `feast`-tagged, built in the same phase-1 step they already are).

---

## 11. Invariants for Reviewer-agent

1. **No `y_lower = 0` in the spine output**: the `(n_ticks_in_cycle - tick_position + 1) * 0.25` formula must produce a minimum of `0.25` at the last tick of every cycle. `y_lower = 0` breaks XGBoost AFT's `log(y)` computation. Verify empirically on the live data after the dbt build.

2. **Equi-join produces zero dropped rows**: every `(equipment_id, reading_ts)` in the spine must have a matching row in `feat_with_anomaly` (since the spine's ticks come from `feast__fct_sensor_features_train` in the first place). Verify `COUNT(*)` of `feast__training_dataset_rul` equals `COUNT(*)` of `feast__spine_maintenance_cycle`.

3. **`dataset_split` is cycle-level, not tick-level**: all ticks from a given `(equipment_id, cycle_end_ts)` cycle must land in the same `dataset_split` bucket. Verify no cycle has ticks in both `'train'` and `'test'`.

4. **Sliding window matches inference exactly**: the 3 anomaly window function expressions in `feast__training_dataset_rul.sql` must be character-for-character identical to `cons__fct_rul_prediction.sql`'s `anomaly_history` CTE (modulo alias names) — `ROWS BETWEEN 287 PRECEDING AND CURRENT ROW`, `MAX(CASE WHEN ... THEN 1 ELSE 0 END) > 0`, `MIN(anomaly_score)`, `AVG(CASE WHEN ... THEN 1.0 ELSE 0.0 END)`.

5. **Anomaly window columns are never NULL**: with the sliding-window approach (minimum 1 row in frame = current row itself), these 3 columns should have zero NULLs. Verify via `dbt test` (the updated schema.yml adds `not_null` tests) or manual spot-check.

6. **SH-50 leakage-safety invariant preserved**: `iso_cutoff == rul_cutoff` by construction — the `split_anchor` CTE's `MAX(cycle_end_ts)` source is unchanged; verify the cutoff timestamp is identical to `feast__training_dataset_iso`'s own cutoff.

7. **`feature_cols` unchanged**: the 22-column list in `06_train_rul_model.sql` and `06b_evaluate_rul_model.sql` must be identical to each other and to the pre-SH-86 list. No column added or removed.

8. **Sort-key in training**: verify `.sort("equipment_id", "reading_ts")` in `06_train_rul_model.sql`, not the old `.sort("equipment_id", "cycle_end_ts")`.

9. **Concordance cross-check passes** (updated 2026-10-03 — see §7a-v3/§7b-v3; this invariant no longer describes the Fenwick-tree/brute-force cross-check originally specified in §7b, which was not what shipped): the SQL self-join implementation (`_concordance_index_self_join`, §7a-v3) must match an independent reference implementation. The actual gate used was a one-off cross-check against `lifelines.utils.concordance_index()` on the full live 32,771-row test set — lifelines = 0.701746, self-join = 0.701800 (matching within floating-point/tie-handling precision) — run via a throwaway stored procedure (`sp_verify_concordance_lifelines`) that was dropped after use and is not part of the committed file. `Reviewer-agent` should verify this cross-check was actually performed (via chat history / PR description, since no cross-check code remains in the file) rather than looking for a bit-identical brute-force comparison in the code itself.

10. **Trajectory correlation is positive and logged**: after the full pipeline runs with the new per-tick training data, `trajectory_correlation` should be strongly positive (> 0.5 at minimum, realistically > 0.9 based on scratchpad results). A negative or near-zero value means the fix didn't work — treat as a blocking finding.

11. **Censoring convention preserved**: for every tick in a BREAKDOWN-ended cycle, `y_upper = y_lower` (uncensored). For every tick in a PM-ended or open cycle, `y_upper IS NULL` (censored). Verify a sample of each.

12. **No read of `cons.fct_anomaly_result` or `feast__fct_sensor_features_inference`** anywhere in the two modified dbt models or two modified scripts — the training side must remain decoupled from the inference side (SH-50 §2 invariant, unchanged).

---

## 12. Explicitly deferred

- **Hyperparameter tuning beyond §6b's recommendation**: §6b's `eta=0.05`/`num_boost_round=200` is empirically validated but flagged as recommended — further tuning (grid search, validation curves) is out of scope.
- **Persisting per-tick evaluation breakdown to a table**: same decision as SH-46 §7 — diagnostic return-value output only.
- **Walk-forward retraining of isolation_forest_model**: still rejected per SH-50 §2.3's analysis — this story's grain change doesn't affect that conclusion.
- **Updating `cons__fct_rul_prediction.sql`'s column order or `model_version` pattern**: inference side is verified unaffected (§9), no changes needed.

---

## 13. Design decisions made during this doc's authoring

1. **`+ 1` in the remaining-hours formula** (§3): the user's spec used `(n_ticks_in_cycle - tick_position) * 0.25`, which produces `y_lower = 0` at the last tick under SQL's 1-based `ROW_NUMBER()`. Changed to `+ 1` to avoid `log(0) = −∞` in XGBoost AFT. This is the 1-indexed SQL equivalent of the 0-indexed Python formula used in the scratchpad validation.

2. **INNER JOIN instead of LEFT JOIN** in the spine (§3): the old `LEFT JOIN` against `feast__fct_sensor_features_train` could produce a row for zero-tick cycles (with `y_lower = 0`). Changed to `INNER JOIN` to naturally exclude these — they have no features and a label that would break AFT.

3. **Per-row breakdown → top-10 worst** (§7d): with ~30k test rows, listing every row in the return string is impractical. Changed to top-10 worst absolute errors plus summary stats. The trajectory correlation (§7c) is the primary new diagnostic.

4. **Trajectory correlation threshold of 0.5** (§7c): set as a WARNING threshold, not a hard fail gate. Conservative enough to catch any regression toward negative correlation (the original bug's symptom) while allowing for per-bucket variance on finite test data.

5. **`_read_split` includes `reading_ts`** (§7e): not strictly needed by the evaluation script's own metrics, but keeps the function consistent with the training script's needs and avoids a divergence if the evaluation script later needs temporal ordering.
