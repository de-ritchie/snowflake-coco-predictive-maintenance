"""Shared logging + Snowflake connection helpers for impact_analysis.

Local-only, read-only analysis -- not part of the production app/pipeline.
"""
import logging
import os
from pathlib import Path

import snowflake.connector

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

CONNECTION_NAME = os.environ.get("SNOWFLAKE_CONNECTION_NAME", "snow-co-cat-alyst-snowcomotive")


def get_logger(name: str) -> logging.Logger:
    """File + console logger, one log file per script name."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # avoid duplicate handlers on re-import
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = logging.FileHandler(LOG_DIR / f"{name}.log", mode="w")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    return logger


def get_conn():
    return snowflake.connector.connect(
        connection_name=CONNECTION_NAME, role="snowcomotive_role", database="snowcomotive"
    )


def query_df(sql: str):
    import pandas as pd

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        cols = [c[0].lower() for c in cur.description]
        rows = cur.fetchall()
        return pd.DataFrame(rows, columns=cols)
    finally:
        conn.close()
