"""Validate every runnable SQL sketch in DATA_MAP.md against live Snowflake.

Parses DATA_MAP.md (single source of truth) to extract element metadata and
SQL blocks, then executes REAL/DERIVED queries and skips SYNTHETIC/NOT BUILDABLE
ones.  Results go to mockup_v2/data_map_validation.log (truncated each run).

Usage:
    uv run python mockup_v2/validate_data_map.py
"""

import logging
import pathlib
import re

import snowflake.connector

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_MAP_PATH = pathlib.Path(__file__).resolve().parent / "DATA_MAP.md"
LOG_PATH = pathlib.Path(__file__).resolve().parent / "data_map_validation.log"

CONNECTION_NAME = "snow-co-cat-alyst-snowcomotive"

logger = logging.getLogger("data_map_validator")
logger.setLevel(logging.DEBUG)
fh = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
logger.addHandler(fh)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_data_map(md_text: str) -> list[dict]:
    """Extract SQL-backed entries from DATA_MAP.md.

    Returns a list of dicts:
        {id, name, section, availability, sql, prerequisite}

    The parser walks the markdown looking for #### headers (element or
    combined-query headers) and fenced ```sql blocks.  Availability is read
    from either the metadata table row (`| **Availability** | ... |`) or from
    a comment inside the SQL block itself.
    """
    entries: list[dict] = []

    # Split into sections at `---` horizontal-rule boundaries (the doc uses
    # them as section separators between every element).
    sections = re.split(r"\n---\n", md_text)

    for section in sections:
        # Look for #### headers
        header_m = re.search(
            r"^####\s+(.+)",
            section,
            re.MULTILINE,
        )
        if not header_m:
            continue

        header_text = header_m.group(1).strip()

        # Extract element ID(s) from the header.  Two shapes:
        #   "PD-A1 — OEE % (value)"          -> single element
        #   "Combined query for SB (all ...)" -> combined, IDs in SQL comment
        element_id_match = re.match(r"([A-Z]{2}-[A-Z]\d+)", header_text)
        if element_id_match:
            element_id = element_id_match.group(1)
        elif "combined query" in header_text.lower():
            # Pull IDs from the SQL comment (e.g. "Elements: PD-B1 through PD-B6")
            element_id = header_text  # placeholder, refined below
        else:
            continue

        # Availability from metadata table
        avail_m = re.search(
            r"\|\s*\*\*Availability\*\*\s*\|\s*(.+?)\s*\|",
            section,
        )
        availability = avail_m.group(1).strip() if avail_m else ""

        # Availability from SQL comment (fallback / override for combined blocks)
        sql_block_m = re.search(r"```sql\s*\n(.*?)```", section, re.DOTALL)
        sql = sql_block_m.group(1).strip() if sql_block_m else None

        if sql:
            # Check for NOT BUILDABLE YET in the SQL comment header
            if "NOT BUILDABLE YET" in sql.split("\n", 5)[0:5].__repr__():
                if "SYNTHETIC" not in availability.upper():
                    availability = "SYNTHETIC (NOT BUILDABLE YET)"

            # Override availability from SQL comment if the metadata table
            # didn't have one (combined-query sections have no table).
            if not availability:
                avail_sql_m = re.search(
                    r"--\s*Availability:\s*(.+)",
                    sql,
                )
                if avail_sql_m:
                    availability = avail_sql_m.group(1).strip()

            # For combined queries, pull element range from SQL comment
            if "combined query" in str(element_id).lower():
                elem_range_m = re.search(
                    r"--\s*Elements?:\s*(.+?)(?:\s*\(|$)",
                    sql,
                )
                if elem_range_m:
                    element_id = elem_range_m.group(1).strip()

        # Prerequisite note (for skip reason on SYNTHETIC elements)
        prereq_m = re.search(
            r"--\s*PREREQUISITE:\s*(.+?)(?:\n(?!--)|$)",
            sql or "",
            re.DOTALL,
        )
        prerequisite = ""
        if prereq_m:
            prerequisite = re.sub(r"\s*\n\s*--\s*", " ", prereq_m.group(1)).strip()

        # Only keep entries that have a SQL block (elements without SQL like
        # PD-A6, PD-A8, PD-D3, PD-D8, PR-C6 are UI-only / N/A).
        if not sql:
            continue

        entries.append({
            "id": element_id,
            "name": header_text,
            "availability": availability,
            "sql": sql,
            "prerequisite": prerequisite,
        })

    return entries


def should_skip(availability: str) -> bool:
    """Return True if the availability tag means we should NOT execute."""
    upper = availability.upper()
    return "SYNTHETIC" in upper or "NOT BUILDABLE" in upper


def prepare_sql(raw_sql: str) -> str:
    """Make a DATA_MAP SQL sketch executable.

    - Strips leading comment lines (-- ...) so the cursor gets a real statement.
    - Replaces :line_filter bind-param placeholders with the literal 'all'
      (the "show everything" default).
    - Strips trailing semicolons (connector doesn't want them).
    """
    # Remove SQL comments that are just metadata headers
    lines = raw_sql.split("\n")
    sql_lines = []
    past_header = False
    for line in lines:
        stripped = line.strip()
        if not past_header and stripped.startswith("--"):
            continue
        past_header = True
        sql_lines.append(line)

    sql = "\n".join(sql_lines).strip()

    # Replace bind-param placeholders
    sql = sql.replace(":line_filter", "'all'")

    # Strip trailing semicolon
    sql = sql.rstrip().rstrip(";")

    return sql


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("=== DATA_MAP.md Validation Run ===")
    logger.info("Source: %s", DATA_MAP_PATH)

    md_text = DATA_MAP_PATH.read_text(encoding="utf-8")
    entries = parse_data_map(md_text)
    logger.info("Parsed %d SQL-backed entries from DATA_MAP.md", len(entries))

    passed = 0
    failed = 0
    skipped = 0

    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME)
    try:
        cur = conn.cursor()
        for entry in entries:
            eid = entry["id"]
            avail = entry["availability"]

            if should_skip(avail):
                reason = entry["prerequisite"] or avail
                logger.info(
                    "SKIPPED  %-45s  availability=%s  reason=%s",
                    eid, avail, reason,
                )
                skipped += 1
                continue

            sql = prepare_sql(entry["sql"])
            logger.info(
                "RUNNING  %-45s  availability=%s",
                eid, avail,
            )
            logger.info("  SQL:\n%s", sql)

            try:
                cur.execute(sql)
                rows = cur.fetchall()
                cols = [c[0] for c in cur.description] if cur.description else []
                logger.info(
                    "PASSED   %-45s  rows=%d  cols=%s",
                    eid, len(rows), cols,
                )
                # Log first few rows for sanity-check
                for i, row in enumerate(rows[:5]):
                    logger.info("  row[%d]: %s", i, row)
                if len(rows) > 5:
                    logger.info("  ... (%d more rows)", len(rows) - 5)
                passed += 1
            except Exception as exc:
                logger.error(
                    "FAILED   %-45s  error=%s",
                    eid, exc,
                )
                failed += 1
    finally:
        conn.close()

    logger.info("")
    logger.info("=== Summary ===")
    logger.info(
        "PASSED: %d / FAILED: %d / SKIPPED: %d / TOTAL: %d",
        passed, failed, skipped, passed + failed + skipped,
    )


if __name__ == "__main__":
    main()
