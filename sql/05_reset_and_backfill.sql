-- 05_reset_and_backfill.sql
-- Restore the original 10,000,000 rows after a write benchmark and
-- rebuild both summary tables from scratch.

USE sql_group;

-- Remove rows added by benchmark.py
DELETE FROM transactions WHERE txn_id > 10000000;

-- Full rebuild of the summaries
TRUNCATE TABLE merchant_balance;
TRUNCATE TABLE merchant_balance_sharded;

INSERT INTO merchant_balance (merchant_id, balance, txn_count)
SELECT merchant_id, SUM(amount), COUNT(*)
FROM transactions
GROUP BY merchant_id;

INSERT INTO merchant_balance_sharded (merchant_id, bucket, balance, txn_count)
SELECT merchant_id, txn_id % 16, SUM(amount), COUNT(*)
FROM transactions
GROUP BY merchant_id, txn_id % 16;

-- Row-lock counters used for the write path (read before and after a run)
SHOW GLOBAL STATUS LIKE 'Innodb_row_lock%';
