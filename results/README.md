# Results

Raw output of `scripts/benchmark.py` and `scripts/pool_wait_benchmark.py`.
Columns: `version, mode (write/read), threads, ops (total), wall_s, tps, p50_ms, p95_ms, p99_ms, lock_wait_peak, lock_wait_total`.

| File | What |
|---|---|
| `write_path_runs.csv` | Final write-path runs (2026-10-27): materialized × 3 rounds, sharded × 3 rounds. These are the numbers in the main README. |
| `three_version_runs.csv` | Earlier runs (2026-10-23) of all 3 versions including the unoptimized baseline, 3 rounds each. Exploratory; not the final reported numbers. |
| `pool_wait_summary.csv` | Connection-pool wait demo: direct connections vs pool size 2 vs pool size 8. |

`lock_wait_peak` / `lock_wait_total` are 0.2 s snapshots of `performance_schema.data_lock_waits`, not true event counts. The lock numbers in the main README come from `Innodb_row_lock%` deltas (see `docs/methodology.md`).

The read-path numbers in the main README come from `performance_schema` (`sql/03_read_path_metrics.sql`), not from these files.
