-- 01_schema.sql
-- Create the database, the 3 tables, and the 2 triggers.
-- Run with:  mysql -u root -p < sql/01_schema.sql

DROP DATABASE IF EXISTS sql_group;
CREATE DATABASE sql_group;
USE sql_group;

-- Main table: one row per transaction (amount > 0 = credit, < 0 = debit)
CREATE TABLE transactions
(
    txn_id      BIGINT         NOT NULL AUTO_INCREMENT,
    merchant_id VARCHAR(20)    NOT NULL,
    amount      DECIMAL(12, 2) NOT NULL,
    txn_time    DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (txn_id),
    KEY idx_merchant (merchant_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4;

-- "Materialized view": one summary row per merchant
CREATE TABLE merchant_balance
(
    merchant_id VARCHAR(20)    NOT NULL,
    balance     DECIMAL(18, 2) NOT NULL DEFAULT 0, -- = SUM(amount)
    txn_count   BIGINT         NOT NULL DEFAULT 0, -- = COUNT(*)
    PRIMARY KEY (merchant_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4;

-- Sharded summary: 16 bucket rows per merchant (bucket = txn_id % 16)
CREATE TABLE merchant_balance_sharded
(
    merchant_id VARCHAR(20)    NOT NULL,
    bucket      TINYINT        NOT NULL, -- 0 ~ 15
    balance     DECIMAL(18, 2) NOT NULL DEFAULT 0,
    txn_count   BIGINT         NOT NULL DEFAULT 0,
    PRIMARY KEY (merchant_id, bucket)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4;

DELIMITER $$

-- Trigger 1: keep the single-row summary in sync (creates a hot row)
CREATE TRIGGER trg_transactions_ai_balance
    AFTER INSERT
    ON transactions
    FOR EACH ROW
BEGIN
    INSERT INTO merchant_balance (merchant_id, balance, txn_count)
    VALUES (NEW.merchant_id, NEW.amount, 1)
    ON DUPLICATE KEY UPDATE balance   = balance + NEW.amount,
                            txn_count = txn_count + 1;
END$$

-- Trigger 2: keep the sharded summary in sync (spreads writes over 16 rows)
CREATE TRIGGER trg_transactions_ai_balance_shard
    AFTER INSERT
    ON transactions
    FOR EACH ROW
BEGIN
    INSERT INTO merchant_balance_sharded (merchant_id, bucket, balance, txn_count)
    VALUES (NEW.merchant_id, NEW.txn_id % 16, NEW.amount, 1)
    ON DUPLICATE KEY UPDATE balance   = balance + NEW.amount,
                            txn_count = txn_count + 1;
END$$

DELIMITER ;

-- Note: benchmark.py drops both triggers and re-creates only the one
-- for the version being tested, so the two never compete in a benchmark.
