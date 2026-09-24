-- ============================================================================
-- 05_train_models.sql — Model training (IsolationForest first, RUL AFT later)
-- Traces to: FR-OPS-02a, FR-FS-00/00a/00f, LLD Module 10 §3, Module 5 §1,
-- docs/05-Epics.md EPIC-SKELETON §4.4
-- Jira: SH-22 (S-MODEL-2, IsolationForest) ; RUL training (S-RUL-3, SH-44) is P1
-- Status: Built (v1 -- IsolationForest only)
--
-- Must run after 04_pipeline_run_phase1 (needs feast.training_dataset_iso
-- built, which itself needs feast.fct_sensor_features_train) and before
-- 06_pipeline_run_phase2 (inference tables reference isolation_forest_model
-- by name at CREATE time).
--
-- IsolationForest is a built-in Snowpark ML Modeling type, so it defaults to
-- volatility=IMMUTABLE (FR-FS-00f) without needing an explicit override --
-- only the RUL model's custom/generic xgboost.Booster path will need that
-- set explicitly, when it's added here in EPIC-RUL.
--
-- DEVIATION FROM MODULE 5 §1 (found empirically, flagged there as a build-time
-- API surface to verify, not assumed -- FR-FS-00): the LLD draft used
-- snowflake.ml.modeling.ensemble.IsolationForest's distributed .fit(train_df)
-- path. That path spins up its own ephemeral nested stored procedure at
-- runtime, which repeatedly failed with "ModuleNotFoundError: No module named
-- 'pandas'" inside that generated procedure regardless of this procedure's
-- own PACKAGES clause or session.add_packages() -- a real, reproduced
-- limitation of running Snowpark ML Modeling's distributed trainer from
-- inside a stored procedure in this account, not a typo. Given this thin
-- dataset is tiny (1440 rows x 15 features), switched to plain in-memory
-- sklearn.ensemble.IsolationForest on a .to_pandas() pull -- scikit-learn is
-- itself a directly-supported Model Registry built-in type, so logging is
-- unaffected. Revisit the distributed path if/when EPIC-FULLDATA's full-scale
-- dataset makes an in-memory pull impractical.
--
-- enable_explainability=True + 'shap' in PACKAGES (confirmed live, 2026-09-24):
-- an earlier pass disabled explainability entirely after hitting
-- "ModuleNotFoundError: No module named 'shap'" and assumed it was an
-- environment limitation. It wasn't -- `shap` (up to 0.51.0) is available in
-- Snowflake's Anaconda channel for this account
-- (SELECT * FROM information_schema.packages WHERE package_name ILIKE 'shap'),
-- it was simply missing from this procedure's own PACKAGES list. With it
-- added, log_model succeeds and the resulting model version exposes a real
-- !explain method returning per-feature SHAP values -- verified live via
-- `SELECT ... FROM t, TABLE(MODEL(...)!explain(t.col1, t.col2, ...)) e`
-- (a lateral join -- !explain must be called as a table function with
-- individual scalar columns, not `MODEL(...)!explain` as a plain scalar UDF
-- and not with a subquery/ROW(...) argument; both alternate call shapes
-- were tried and rejected by Snowflake with different errors before finding
-- this one). This unlocks S-PERSONA-1's future `explain_prediction` tool
-- (EPIC-PERSONAS) without needing to revisit training at all.
-- ============================================================================

CREATE OR REPLACE PROCEDURE snowcomotive.cons.sp_train_isolation_forest()
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python', 'snowflake-ml-python', 'pandas', 'scikit-learn', 'shap')
HANDLER = 'train'
AS
$$
import datetime

from sklearn.ensemble import IsolationForest
from snowflake.ml.registry import Registry
from snowflake.snowpark.functions import col


def train(session):
    # fully-qualified + dbt's actual table name (feast__training_dataset_iso,
    # not the LLD's abstract "training_dataset_iso" -- dbt keeps the file-name
    # prefix; caught the same way as the Module 3/4 config-key deviations).
    train_df = session.table("snowcomotive.feast.feast__training_dataset_iso").filter(col("dataset_split") == "train")
    row_count = train_df.count()
    if row_count == 0:
        return "SKIPPED: 0 rows in feast.training_dataset_iso WHERE dataset_split = 'train'"

    sensor_feature_cols = [
        "vibration_z", "vibration_rolling_1h_z", "vibration_rolling_8h_z", "vibration_rolling_24h_z", "vibration_rolling_7d_z",
        "temperature_z", "temperature_rolling_1h_z", "temperature_rolling_8h_z", "temperature_rolling_24h_z", "temperature_rolling_7d_z",
        "rpm_z", "rpm_rolling_1h_z", "rpm_rolling_8h_z", "rpm_rolling_24h_z", "rpm_rolling_7d_z",
    ]
    train_pdf = train_df.select(sensor_feature_cols).to_pandas()

    model = IsolationForest(n_estimators=100, contamination=0.05, random_state=42)
    model.fit(train_pdf)

    registry = Registry(session=session, database_name="SNOWCOMOTIVE", schema_name="CONS")
    version_name = "V_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    registry.log_model(
        model,
        model_name="isolation_forest_model",
        version_name=version_name,
        sample_input_data=train_pdf.head(1000),
        # embed_local_ml_library bundles the currently-running snowflake-ml-python
        # into the model artifact instead of requiring the Anaconda channel to
        # resolve a matching standalone package version for it -- without this,
        # log_model failed with "Packages not found: snowflake-ml-python[version=...]"
        # (confirmed empirically, not a hypothetical fix).
        #
        # enable_explainability=True: requires 'shap' in PACKAGES above (see
        # header note) -- builds a SHAP background-data explainer from
        # sample_input_data, exposing !explain alongside !predict/
        # !decision_function/!score_samples on the logged model version.
        options={"enable_explainability": True, "embed_local_ml_library": True},
    )

    # Registry does NOT auto-promote a newly logged version to default -- the
    # first version ever logged stays default forever otherwise (confirmed
    # empirically: after 2 log_model calls, is_default_version was still true
    # only on the first). Inference (S-MODEL-3) calls MODEL(isolation_forest_model)
    # with no explicit version, so it always needs this set explicitly here.
    registry.get_model("isolation_forest_model").default = version_name

    return f"Trained isolation_forest_model {version_name} on {row_count} rows"
$$;

CALL snowcomotive.cons.sp_train_isolation_forest();

