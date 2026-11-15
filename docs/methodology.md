# Methodology: metrics and measurement

## Setup

- `transactions`: 10,000,000 rows, 100 merchants. All tests target the hot merchant **M001** (1,000,000 rows).
- `merchant_balance`: 100 rows (1 per merchant). `merchant_balance_sharded`: 1,600 rows (16 buckets per merchant, `bucket = txn_id % 16`).
- MySQL Server 26.7 on a single Windows machine. Write path measured 2026-10-27, read path 2026-10-28.

## Read path

All read metrics come from `performance_schema.events_statements_summary_by_digest`. It groups executions of the same SQL shape into one row with count / avg / min / max.

| Metric | Column | Why it matters |
|---|---|---|
| Exec time (avg/min/max) | `*_TIMER_WAIT` (ps, / 1e9 → ms) | What the user actually waits for; min/max show stability |
| CPU time | `SUM_CPU_TIME / COUNT_STAR` | Unoptimized CPU ≈ 92% of exec time: it is slow because it adds up 1M rows, not because it waits for disk |
| Rows examined | `SUM_ROWS_EXAMINED / COUNT_STAR` | Hardware-independent measure of work; maps directly to Big-O (1M vs 1 vs 16) |
| Rows sent | `SUM_ROWS_SENT / COUNT_STAR` | All versions return 1 row, which confirms they return the same result. Unoptimized reads 1M rows to return 1 |
| Temp tables | `SUM_CREATED_TMP_TABLES` | 0 for all versions, so there is no hidden extra cost |

Note: on Windows the CPU timer resolution is 15.625 ms, so values below it show as 0.

Steps (see `sql/03_read_path_metrics.sql`):
1. Enable `events_statements_cpu`.
2. Truncate the digest table.
3. Run the three queries alternately, 3 times each.
4. Query the summary.

## Write path

Throughput and latency come from `scripts/benchmark.py`. Lock metrics come from the InnoDB global counters `Innodb_row_lock%`.

| Metric | How measured | Why it matters |
|---|---|---|
| TPS | total inserts / wall-clock time of the write phase | Capacity under a burst of orders on one merchant (a flash-sale scenario) |
| p50 / p95 / p99 | each INSERT timed with `time.perf_counter()`, linear-interpolated percentiles | An average hides slow requests. Lock queues show up first in the tail |
| Row-lock waits | delta of `Innodb_row_lock_waits` before/after | Direct evidence of hot-row contention |
| Total lock wait | delta of `Innodb_row_lock_time` | Turns contention into a time cost (summed across threads, so it can exceed wall time) |
| Avg / max wait | `Innodb_row_lock_time_avg`, `_max` | Avg wait ≈ p50 latency, which shows the slowdown is mostly lock queueing |

The lock-wait sampling inside `benchmark.py` (`lock_wait_peak`, `lock_wait_total`) polls `performance_schema.data_lock_waits` every 0.2 s. It is a snapshot sum, not a true event count, so the reported numbers use the `Innodb_row_lock%` deltas instead.

The unoptimized version is not compared on the write path. Each insert gets its own auto-increment key and never updates a shared row, so there is no row-lock contention to measure.

Steps per round (3 rounds per version):
1. Restart MySQL.
2. Single-thread warm-up.
3. Restore to 10M rows.
4. Record lock counters.
5. Run the benchmark.
6. Record lock counters again.
7. Restore to 10M rows.

`benchmark.py` drops both triggers and creates only the one for the version under test.

## Consistency

After loading 10M rows with the trigger active, the summary total matched the transaction total exactly (7,998,268,632.83). The per-merchant diff query returned 0 rows (see `sql/04_consistency_check.sql`).

## Things tried and set aside

- **`PARTITION BY`**: not used. The buckets are 16 logical rows in one InnoDB table, not physical partitions.
- **Parallel query execution**: explored early on and not pursued.
- **InnoDB config tuning**: changing configuration switches gave no noticeable improvement in early tests.
