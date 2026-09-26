-- ============================================================================
-- 06_train_rul_model.sql — Model training (RUL AFT, raw xgboost.Booster)
-- Traces to: FR-OPS-02a, FR-FS-02/00f, LLD Module 5 §2, docs/05-Epics.md
-- EPIC-RUL §4.4, docs/designs/SH-44-train-rul-aft-model.md
-- Jira: SH-44 (S-RUL-3)
-- Status: Built (v1)
--
-- Must run after scripts/04_pipeline_run_phase1.sql's
-- `dbt run --select feast__training_dataset_rul` step (this script reads that
-- table) and before the `dbt run --select tag:inference+` phase-2 dbt run
-- (per manage.py's updated sequence, docs/designs/SH-44-train-rul-aft-model.md
-- §2). Kept in its own numbered file rather than appended to
-- 05_train_models.sql, per that same design doc §2.
--
-- Raw xgboost.Booster (not Snowpark ML Modeling) -- custom/generic model
-- path, so volatility must be set explicitly (FR-FS-00f) via
-- options={"volatility": Volatility.IMMUTABLE} on log_model(). Registry does
-- NOT auto-promote a newly logged version to default -- explicitly set
-- .default = version_name after logging, same gotcha as isolation_forest_model
-- (scripts/05_train_models.sql).
--
-- Censoring convention (matches SH-51's spike + LLD Module 5 §2 exactly):
-- y_upper = NULL (right-censored) -> +inf via .fillna(np.inf) before
-- set_float_info("label_upper_bound", ...) -- XGBoost's own convention for
-- "failure time unknown, but known to be beyond y_lower" under survival:aft.
-- ============================================================================

CREATE OR REPLACE PROCEDURE snowcomotive.cons.sp_train_rul_aft_model()
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python', 'snowflake-ml-python', 'pandas', 'xgboost', 'shap')
HANDLER = 'train'
AS
$$
import datetime

import numpy as np
import xgboost as xgb
from snowflake.ml.model import Volatility
from snowflake.ml.registry import Registry
from snowflake.snowpark.functions import col


def train(session):
    # dbt's actual table name (feast__training_dataset_rul), same file-name
    # prefix convention already caught in 05_train_models.sql for the ISO table.
    train_df = session.table("snowcomotive.feast.feast__training_dataset_rul").filter(
        col("dataset_split") == "train"
    )
    row_count = train_df.count()
    if row_count == 0:
        return "SKIPPED: 0 rows in feast.training_dataset_rul WHERE dataset_split = 'train'"

    feature_cols = [
        "vibration_z", "vibration_rolling_1h_z", "vibration_rolling_8h_z", "vibration_rolling_24h_z", "vibration_rolling_7d_z",
        "temperature_z", "temperature_rolling_1h_z", "temperature_rolling_8h_z", "temperature_rolling_24h_z", "temperature_rolling_7d_z",
        "rpm_z", "rpm_rolling_1h_z", "rpm_rolling_8h_z", "rpm_rolling_24h_z", "rpm_rolling_7d_z",
        "hours_since_last_service", "hours_since_install",
        "is_anomaly", "anomaly_score",
        "any_anomaly_flagged_72h", "min_anomaly_score_72h", "pct_anomalous_ticks_72h",
    ]

    train_pdf = train_df.select(feature_cols + ["y_lower", "y_upper"]).to_pandas()
    train_pdf.columns = [c.lower() for c in train_pdf.columns]

    # Boolean columns must be cast to numeric before entering the DMatrix --
    # XGBoost's raw Python API expects numeric input (design doc §3).
    train_pdf["is_anomaly"] = train_pdf["is_anomaly"].astype(int)
    train_pdf["any_anomaly_flagged_72h"] = train_pdf["any_anomaly_flagged_72h"].astype(int)

    dtrain = xgb.DMatrix(train_pdf[feature_cols])
    dtrain.set_float_info("label_lower_bound", train_pdf["y_lower"].values)
    # y_upper = NULL (right-censored) -> +inf, not left as NaN/dropped/0.
    dtrain.set_float_info("label_upper_bound", train_pdf["y_upper"].fillna(np.inf).values)

    params = {
        "objective": "survival:aft",
        "aft_loss_distribution": "normal",
        "aft_loss_distribution_scale": 1.0,
        "tree_method": "hist",
        "max_depth": 4,
    }
    booster = xgb.train(params, dtrain, num_boost_round=100)

    registry = Registry(session=session, database_name="SNOWCOMOTIVE", schema_name="CONS")
    version_name = "V_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    registry.log_model(
        booster,
        model_name="rul_aft_model",
        version_name=version_name,
        sample_input_data=train_pdf[feature_cols].head(1000),
        options={
            "enable_explainability": True,
            "embed_local_ml_library": True,  # same reason as isolation_forest_model --
                                              # avoids "Packages not found:
                                              # snowflake-ml-python[version=...]"
            "volatility": Volatility.IMMUTABLE,  # NOT the default for a raw Booster --
                                                  # required for inference-side dynamic
                                                  # table refresh (FR-FS-09)
        },
    )

    # Registry does NOT auto-promote a newly logged version to default (same
    # gotcha as isolation_forest_model, scripts/05_train_models.sql).
    registry.get_model("rul_aft_model").default = version_name

    return f"Trained rul_aft_model {version_name} on {row_count} rows"
$$;

CALL snowcomotive.cons.sp_train_rul_aft_model();
