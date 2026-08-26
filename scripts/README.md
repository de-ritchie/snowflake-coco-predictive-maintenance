# Environment & Operations Scripts

Lifecycle scripts for the SnowComotive project, per FR-OPS-01 through 06 / LLD Module 10 / `docs/05-Epics.md` §2. Numbered by execution order. These are **versioned, living scripts** — extended in place as later Epics add components, not replaced with new files. See each file's header comment for its current scope and what's still pending.

| # | File | Stage | Status | Jira |
|---|---|---|---|---|
| 01 | `01_setup.sql` | Env spin-up (role/warehouse/db/schemas/stage) | **Built** (v0 scope: S-ENV-1/S-ENV-2) | SH-10, SH-14 (Done) |
| 02 | `02_pipeline_run_phase1.sql` | dbt run — features (Raw→Std→Cons→FEAST) | Not built | SH-15, SH-20, SH-24, SH-26 |
| 03 | `03_train_models.sql` | Model training | Not built | SH-22 (IsolationForest), SH-44 (RUL, P1) |
| 04 | `04_pipeline_run_phase2.sql` | dbt run — inference & downstream | Not built | SH-23 |
| 05 | `05_post_setup.sql` | Semantic view + agent(s) + Streamlit deploy | Not built | SH-30, SH-28, SH-31, SH-33 |
| 06 | `06_demo.sql` | Live tick injection (demo-only manual trigger) | Not built | SH-25 |
| 07 | `07_teardown.sql` | Full teardown (P3, deliberately last) | Not built | SH-68, SH-61 |

Run order: 01 → 02 → 03 → 04 → 05, then 06 on demand during a demo, 07 only when actually tearing down an environment.
