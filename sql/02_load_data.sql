-- 02_load_data.sql
-- Bulk-load the generated CSV (see scripts/gen_transactions.py).
--
-- MySQL only reads files from its secure_file_priv folder. Find it with:
--     SHOW VARIABLES LIKE 'secure_file_priv';
-- Copy transactions.csv there, then replace the path below with your own.

USE sql_group;

LOAD DATA INFILE '/path/to/secure_file_priv/transactions.csv'
    INTO TABLE transactions
    FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
    LINES TERMINATED BY '\n'
    IGNORE 1 LINES
    (txn_id, merchant_id, amount, txn_time);

-- If the triggers from 01_schema.sql were active during the load, both
-- summary tables are already filled. Otherwise backfill them with
-- 05_reset_and_backfill.sql (this is the "full rebuild" cost the
-- triggers avoid).
