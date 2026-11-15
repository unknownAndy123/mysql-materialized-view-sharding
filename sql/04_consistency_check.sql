-- 04_consistency_check.sql
-- Check that the trigger-maintained summaries equal a fresh SUM over
-- the transactions table. Pass = totals equal AND 0 rows in each diff query.

USE sql_group;

-- 1. Size
SELECT COUNT(*) AS txn_cnt FROM transactions;
SELECT COUNT(*) AS bal_rows FROM merchant_balance;
SELECT COUNT(*) AS shard_rows FROM merchant_balance_sharded;

-- 2. Grand totals
SELECT (SELECT SUM(amount)  FROM transactions)             AS flow_sum,
       (SELECT SUM(balance) FROM merchant_balance)         AS balance_sum,
       (SELECT SUM(balance) FROM merchant_balance_sharded) AS sharded_sum;

-- 3. Per-merchant differences: single-row summary (expect 0 rows)
SELECT t.merchant_id, t.flow_sum, t.flow_cnt, b.balance, b.txn_count
FROM (SELECT merchant_id, SUM(amount) AS flow_sum, COUNT(*) AS flow_cnt
      FROM transactions
      GROUP BY merchant_id) t
         LEFT JOIN merchant_balance b ON b.merchant_id = t.merchant_id
WHERE b.merchant_id IS NULL
   OR b.balance <> t.flow_sum
   OR b.txn_count <> t.flow_cnt;

-- 4. Per-merchant differences: sharded summary (expect 0 rows)
SELECT t.merchant_id, t.flow_sum, t.flow_cnt, s.balance, s.txn_count
FROM (SELECT merchant_id, SUM(amount) AS flow_sum, COUNT(*) AS flow_cnt
      FROM transactions
      GROUP BY merchant_id) t
         LEFT JOIN (SELECT merchant_id, SUM(balance) AS balance, SUM(txn_count) AS txn_count
                    FROM merchant_balance_sharded
                    GROUP BY merchant_id) s ON s.merchant_id = t.merchant_id
WHERE s.merchant_id IS NULL
   OR s.balance <> t.flow_sum
   OR s.txn_count <> t.flow_cnt;
