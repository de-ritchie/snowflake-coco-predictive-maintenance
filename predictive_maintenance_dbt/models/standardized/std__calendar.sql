{{ config(materialized='view', schema='std', tags=['standardized']) }}

-- Type/key conformance only (FR-PL-02) -- thin pass-through, no transforms.
SELECT
    calendar_date,
    is_working_day,
    is_holiday
FROM {{ source('raw', 'calendar') }}
