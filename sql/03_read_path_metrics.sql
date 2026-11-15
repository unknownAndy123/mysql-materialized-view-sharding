-- 03_read_path_metrics.sql
-- Measure the read path of the 3 versions with performance_schema.
-- Steps: (1) enable CPU stats  (2) clear stats  (3) run the 3 queries
-- alternately, 3 times each  (4) read the summary.

USE sql_group;

-- 1. Enable CPU statistics (re-run after every MySQL restart)
UPDATE performance_schema.setup_consumers
SET ENABLED = 'YES'
WHERE NAME = 'events_statements_cpu';

-- 2. Clear the statement summary so counts start from 0 (run once)
TRUNCATE TABLE performance_schema.events_statements_summary_by_digest;

-- 3. Run these three alternately, 3 times in total
-- Unoptimized: aggregate ~1,000,000 rows on every query
SELECT merchant_id, SUM(amount) AS sum_amount, COUNT(*) AS cnt
FROM transactions
WHERE merchant_id = 'M001';

-- Materialized view: read 1 row by primary key
SELECT *
FROM merchant_balance
WHERE merchant_id = 'M001';

-- Sharded MV: read and sum 16 bucket rows
SELECT merchant_id, SUM(balance) AS sum_amount, SUM(txn_count) AS cnt
FROM merchant_balance_sharded
WHERE merchant_id = 'M001';

-- 4. Summary (timers are in picoseconds; / 1e9 = milliseconds)
SELECT DIGEST_TEXT,
       COUNT_STAR                                AS exec_count,
       ROUND(AVG_TIMER_WAIT / 1e9, 3)            AS avg_ms,
       ROUND(MIN_TIMER_WAIT / 1e9, 3)            AS min_ms,
       ROUND(MAX_TIMER_WAIT / 1e9, 3)            AS max_ms,
       ROUND(SUM_CPU_TIME / COUNT_STAR / 1e9, 3) AS avg_cpu_ms,
       SUM_ROWS_EXAMINED / COUNT_STAR            AS avg_rows_examined,
       SUM_ROWS_SENT / COUNT_STAR                AS avg_rows_sent,
       SUM_CREATED_TMP_TABLES                    AS tmp_tables
FROM performance_schema.events_statements_summary_by_digest
WHERE SCHEMA_NAME = 'sql_group'
  AND DIGEST_TEXT LIKE 'SELECT%'
  AND DIGEST_TEXT NOT LIKE '%performance_schema%'
ORDER BY AVG_TIMER_WAIT DESC;

-- Optional: look at the plan of each version
EXPLAIN ANALYZE
SELECT SUM(amount) FROM transactions WHERE merchant_id = 'M001';

EXPLAIN ANALYZE
SELECT SUM(balance) FROM merchant_balance_sharded WHERE merchant_id = 'M001';
