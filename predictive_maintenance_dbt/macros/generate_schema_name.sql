{#
    Standard dbt community override: without this, dbt prefixes custom
    schemas (`std`, `cons`) with the target schema (e.g. `raw_std`).
    Returning the custom schema name literally lands models in the
    already-created `snowcomotive.std`/`snowcomotive.cons` schemas.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}

    {%- set default_schema = target.schema -%}
    {%- if custom_schema_name is none -%}

        {{ default_schema }}

    {%- else -%}

        {{ custom_schema_name | trim }}

    {%- endif -%}

{%- endmacro %}
