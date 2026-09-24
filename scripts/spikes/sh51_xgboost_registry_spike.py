"""SH-51 spike (S-RUL-0): raw xgboost.Booster via Snowflake Model Registry,
called with MODEL(...)!predict()/!explain() directly inside SQL.

Scratch/throwaway script, not part of the numbered scripts/0N_*.sql lifecycle.
See docs/designs/SH-51-xgboost-registry-model-spike.md for the frozen plan.

Run with: uv run python scripts/spikes/sh51_xgboost_registry_spike.py
"""

import os

import numpy as np
import pandas as pd
import xgboost as xgb
from snowflake.ml.model import Volatility
from snowflake.ml.registry import Registry
from snowflake.snowpark import Session

CONNECTION_NAME = os.environ.get("SNOWFLAKE_CONNECTION_NAME", "snow-co-cat-alyst")
MODEL_NAME = "RUL_SPIKE_TOY_MODEL"
MODEL_DATABASE = "SNOWCOMOTIVE"
MODEL_SCHEMA = "CONS"  # same schema isolation_forest_model already lives in


def main():
    # --- §3: toy data + toy AFT booster, trained standalone (no stored proc) ---
    toy_df = pd.DataFrame({
        "feature_1": [0.1, 0.5, -0.3, 1.2, -0.8, 0.4, 0.9, -0.1],
        "feature_2": [1.0, 0.2, 0.7, -0.4, 0.3, -0.9, 0.1, 0.6],
        "y_lower":   [100,  50, 200,  30, 150,  80, 120,  60],
        "y_upper":   [100, np.nan, 200, np.nan, 150, np.nan, 120, np.nan],
    })
    feature_cols = ["feature_1", "feature_2"]

    dtrain = xgb.DMatrix(toy_df[feature_cols])
    dtrain.set_float_info("label_lower_bound", toy_df["y_lower"].values)
    dtrain.set_float_info("label_upper_bound", toy_df["y_upper"].fillna(np.inf).values)

    params = {
        "objective": "survival:aft",
        "aft_loss_distribution": "normal",
        "aft_loss_distribution_scale": 1.0,
        "tree_method": "hist",
        "max_depth": 2,
    }
    booster = xgb.train(params, dtrain, num_boost_round=20)

    python_preds = booster.predict(dtrain)
    print("=== Python-side booster.predict(dtrain) ===")
    print(python_preds)

    # --- §4: log to Model Registry via generic/custom-model path ---
    session = Session.builder.config("connection_name", CONNECTION_NAME).create()
    session.sql(f"USE ROLE snowcomotive_role").collect()
    session.sql(f"USE WAREHOUSE snowcomotive_wh").collect()
    session.sql(f"USE DATABASE {MODEL_DATABASE}").collect()
    session.sql(f"USE SCHEMA {MODEL_SCHEMA}").collect()

    registry = Registry(session=session, database_name=MODEL_DATABASE, schema_name=MODEL_SCHEMA)

    session.sql(f"DROP MODEL IF EXISTS {MODEL_NAME}").collect()

    mv = registry.log_model(
        booster,
        model_name=MODEL_NAME,
        version_name="V1",
        sample_input_data=toy_df[feature_cols],
        options={
            "enable_explainability": True,
            "volatility": Volatility.IMMUTABLE,
        },
    )
    print(f"=== Logged model version: {mv.model_name} / {mv.version_name} ===")

    # --- §2.1 / §5: predict via SQL, compare row-by-row against Python ---
    values_clause = ",\n    ".join(
        f"({row.feature_1}, {row.feature_2})" for _, row in toy_df.iterrows()
    )
    predict_sql = f"""
    WITH toy_rows AS (
        SELECT * FROM VALUES
        {values_clause}
        AS t(feature_1, feature_2)
    )
    SELECT
        feature_1, feature_2,
        {MODEL_NAME}!predict(feature_1, feature_2) AS predict_output
    FROM toy_rows
    ORDER BY feature_1
    """
    print("=== predict SQL ===")
    print(predict_sql)
    predict_result = session.sql(predict_sql).collect()
    print("=== SQL predict() result ===")
    for r in predict_result:
        print(r.as_dict())

    # --- §2.2: confirm volatility=IMMUTABLE actually holds ---
    # NOTE: SHOW VERSIONS IN MODEL / DESC MODEL do NOT surface a volatility
    # column at all -- the only place it's actually recorded is the deployed
    # MANIFEST.yml, retrievable via ModelVersion.export(export_mode=FULL).
    show_versions = session.sql(f"SHOW VERSIONS IN MODEL {MODEL_NAME}").collect()
    print("=== SHOW VERSIONS IN MODEL result (raw rows; no volatility column exposed here) ===")
    for r in show_versions:
        print(r.as_dict())

    import shutil
    import tempfile

    from snowflake.ml.model._client.model.model_version_impl import ExportMode

    export_dir = tempfile.mkdtemp(prefix="sh51_manifest_")
    mv.export(target_path=export_dir, export_mode=ExportMode.FULL)
    manifest_path = os.path.join(export_dir, "MANIFEST.yml")
    print(f"=== MANIFEST.yml (from {manifest_path}) ===")
    with open(manifest_path) as f:
        manifest_text = f.read()
        print(manifest_text)
    shutil.rmtree(export_dir, ignore_errors=True)

    # --- Stretch: explain() as a lateral table function ---
    # NOTE: explain()'s compiled signature requires explicit DOUBLE inputs (see
    # MANIFEST.yml `inputs: [{name: FEATURE_1, type: DOUBLE}, ...]`) -- unlike
    # predict(), which resolves an implicit NUMBER->DOUBLE cast, explain() needs
    # an explicit ::DOUBLE cast on the literal NUMBER(2,1) values or it fails
    # with SQL compilation error 42P13 (invalid argument types).
    explain_sql = f"""
    WITH toy_rows AS (
        SELECT * FROM VALUES
        {values_clause}
        AS t(feature_1, feature_2)
    )
    SELECT t.feature_1, t.feature_2, e.*
    FROM toy_rows t,
         TABLE({MODEL_NAME}!explain(t.feature_1::DOUBLE, t.feature_2::DOUBLE)) e
    """
    print("=== explain SQL ===")
    print(explain_sql)
    try:
        explain_result = session.sql(explain_sql).collect()
        print("=== SQL explain() result ===")
        for r in explain_result:
            print(r.as_dict())
    except Exception as e:
        print(f"=== explain() FAILED: {type(e).__name__}: {e} ===")

    session.close()


if __name__ == "__main__":
    main()
