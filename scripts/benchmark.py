#!/usr/bin/env python3
"""
A2 3.1 压测脚本 v1
三版本对比：baseline（无优化） / materialized（物化汇总表） / sharded（分片汇总表）

用法：
    python3 benchmark.py --version baseline   --threads 2 --ops 100 --reads 5      # 冒烟
    python3 benchmark.py --version materialized --threads 8 --ops 2000 --reads 50 # 正式

每版本跑一次（脚本自动切换触发器）。结果写入 压测结果/summary.csv（追加），
同时存一份带时间戳的独立 CSV。
"""

import argparse
import csv
import datetime
import os
import threading
import time

import pymysql

# 数据库连接从环境变量读取，不在代码里写密码（见 README）
DB_CONFIG = dict(host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
                 port=int(os.environ.get("MYSQL_PORT", "3306")),
                 user=os.environ.get("MYSQL_USER", "root"),
                 password=os.environ.get("MYSQL_PASSWORD", ""),
                 database=os.environ.get("MYSQL_DATABASE", "sql_group"),
                 charset="utf8mb4", autocommit=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_DIR = os.path.join(BASE_DIR, "压测结果")

# 版本 -> (读路径 SQL, 触发器创建 DDL，None 表示不建触发器)
READ_SQL = {
    "baseline": "SELECT SUM(amount), COUNT(*) FROM transactions WHERE merchant_id = %s",
    "materialized": "SELECT balance, txn_count FROM merchant_balance WHERE merchant_id = %s",
    "sharded": "SELECT SUM(balance), SUM(txn_count) FROM merchant_balance_sharded WHERE merchant_id = %s",
}

TRIGGER_DDL = {
    "materialized": """
        CREATE TRIGGER trg_transactions_ai_balance
        AFTER INSERT ON transactions
        FOR EACH ROW
        BEGIN
            INSERT INTO merchant_balance (merchant_id, balance, txn_count)
            VALUES (NEW.merchant_id, NEW.amount, 1)
            ON DUPLICATE KEY UPDATE balance = balance + NEW.amount,
                                    txn_count = txn_count + 1;
        END
    """,
    "sharded": """
        CREATE TRIGGER trg_transactions_ai_balance_shard
        AFTER INSERT ON transactions
        FOR EACH ROW
        BEGIN
            INSERT INTO merchant_balance_sharded (merchant_id, bucket, balance, txn_count)
            VALUES (NEW.merchant_id, NEW.txn_id % 16, NEW.amount, 1)
            ON DUPLICATE KEY UPDATE balance = balance + NEW.amount,
                                    txn_count = txn_count + 1;
        END
    """,
}

DROP_TRIGGERS_SQL = """
    DROP TRIGGER IF EXISTS trg_transactions_ai_balance;
    DROP TRIGGER IF EXISTS trg_transactions_ai_balance_shard;
"""

# baseline 版本没有触发器维护汇总表，跑完必须全表重算回填
# （这也是「无优化方案对账/重建成本」的演示点）
BACKFILL_SQL = [
    "DELETE FROM merchant_balance",
    "INSERT INTO merchant_balance (merchant_id, balance, txn_count) "
    "SELECT merchant_id, SUM(amount), COUNT(*) FROM transactions GROUP BY merchant_id",
    "DELETE FROM merchant_balance_sharded",
    "INSERT INTO merchant_balance_sharded (merchant_id, bucket, balance, txn_count) "
    "SELECT merchant_id, txn_id % 16, SUM(amount), COUNT(*) FROM transactions "
    "GROUP BY merchant_id, txn_id % 16",
]


def new_conn():
    return pymysql.connect(**DB_CONFIG)


def percentile(sorted_vals, p):
    """线性插值分位数，输入必须是升序 list"""
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


class LockMonitor(threading.Thread):
    """写压测期间后台轮询 performance_schema.data_lock_waits，记录峰值+累计"""

    def __init__(self, interval=0.2):
        super().__init__(daemon=True)
        self.interval = interval
        self.stop_event = threading.Event()
        self.samples = []

    def run(self):
        conn = new_conn()
        try:
            while not self.stop_event.is_set():
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM performance_schema.data_lock_waits")
                    self.samples.append(cur.fetchone()[0])
                self.stop_event.wait(self.interval)
        finally:
            conn.close()

    @property
    def peak(self):
        return max(self.samples) if self.samples else 0

    @property
    def total(self):
        return sum(self.samples)


def writer_thread(thread_id, ops, merchant, amount, barrier, latencies):
    conn = new_conn()
    try:
        barrier.wait()
        for _ in range(ops):
            t0 = time.perf_counter()
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO transactions (merchant_id, amount, txn_time) "
                    "VALUES (%s, %s, NOW())",
                    (merchant, amount),
                )
            latencies.append(time.perf_counter() - t0)
    finally:
        conn.close()


def reader_thread(thread_id, reads, merchant, sql, barrier, latencies):
    conn = new_conn()
    try:
        barrier.wait()
        for _ in range(reads):
            t0 = time.perf_counter()
            with conn.cursor() as cur:
                cur.execute(sql, (merchant,))
                cur.fetchall()
            latencies.append(time.perf_counter() - t0)
    finally:
        conn.close()


def run_write_phase(version, threads, ops, merchant, amount):
    latencies = []
    monitor = LockMonitor()
    monitor.start()
    barrier = threading.Barrier(threads)
    workers = [threading.Thread(target=writer_thread,
                                args=(i, ops, merchant, amount, barrier, latencies))
               for i in range(threads)]
    t0 = time.perf_counter()
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    wall = time.perf_counter() - t0
    monitor.stop_event.set()
    monitor.join(timeout=2)
    return latencies, wall, monitor


def run_read_phase(version, threads, reads, merchant):
    latencies = []
    barrier = threading.Barrier(threads)
    workers = [threading.Thread(target=reader_thread,
                                args=(i, reads, merchant, READ_SQL[version], barrier, latencies))
               for i in range(threads)]
    t0 = time.perf_counter()
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    wall = time.perf_counter() - t0
    return latencies, wall


def switch_version(conn, version):
    """切触发器状态：DROP 两个 -> 按版本 CREATE（baseline 不建）"""
    with conn.cursor() as cur:
        for stmt in DROP_TRIGGERS_SQL.split(";"):
            if stmt.strip():
                cur.execute(stmt)
        ddl = TRIGGER_DDL.get(version)
        if ddl:
            cur.execute(ddl)


def backfill(conn):
    for stmt in BACKFILL_SQL:
        with conn.cursor() as cur:
            cur.execute(stmt)


def write_csv_row(path, row, header=False):
    new_file = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if header or new_file:
            writer.writerow(["version", "mode", "threads", "ops", "wall_s", "tps",
                             "p50_ms", "p95_ms", "p99_ms",
                             "lock_wait_peak", "lock_wait_total"])
        writer.writerow(row)


def summarize(version, mode, threads, ops, wall, latencies, monitor):
    s = sorted(latencies)
    tps = ops / wall if wall > 0 else 0.0
    row = [
        version, mode, threads, ops, round(wall, 3), round(tps, 2),
        round(percentile(s, 50) * 1000, 2),
        round(percentile(s, 95) * 1000, 2),
        round(percentile(s, 99) * 1000, 2),
        monitor.peak, monitor.total,
    ]
    print(f"[{version:>12}] {mode:<5} ops={ops:<6} wall={wall:>8.3f}s "
          f"tps={tps:>9.2f} p50={row[6]:>8}ms p95={row[7]:>8}ms p99={row[8]:>8}ms "
          f"lock_peak={monitor.peak} lock_total={monitor.total}")
    return row


def main():
    ap = argparse.ArgumentParser(description="A2 3.1 三版本压测")
    ap.add_argument("--version", required=True,
                    choices=["baseline", "materialized", "sharded"])
    ap.add_argument("--threads", type=int, default=8, help="并发线程数")
    ap.add_argument("--ops", type=int, default=2000, help="每线程 INSERT 笔数")
    ap.add_argument("--reads", type=int, default=50, help="每线程读查询次数")
    ap.add_argument("--merchant", default="M001", help="目标商户（模拟热点）")
    ap.add_argument("--amount", type=float, default=9.90, help="每笔金额")
    args = ap.parse_args()

    os.makedirs(RESULT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    single_path = os.path.join(RESULT_DIR, f"benchmark_{args.version}_{stamp}.csv")
    summary_path = os.path.join(RESULT_DIR, "summary.csv")

    conn = new_conn()
    try:
        print(f"=== 版本 {args.version}：切换触发器状态 ===")
        switch_version(conn, args.version)

        print(f"=== 写压测：{args.threads} 线程 x {args.ops} 笔，砸 {args.merchant} ===")
        lat, wall, monitor = run_write_phase(args.version, args.threads,
                                             args.ops, args.merchant, args.amount)
        write_row = summarize(args.version, "write", args.threads,
                              args.threads * args.ops, wall, lat, monitor)

        print(f"=== 读压测：{args.threads} 线程 x {args.reads} 次 ===")
        lat, wall = run_read_phase(args.version, args.threads, args.reads, args.merchant)
        read_monitor = type("M", (), {"peak": 0, "total": 0})()
        read_row = summarize(args.version, "read", args.threads,
                             args.threads * args.reads, wall, lat, read_monitor)

        if args.version == "baseline":
            print("=== baseline 收尾回填汇总表（重建成本演示点） ===")
            t0 = time.perf_counter()
            backfill(conn)
            print(f"回填耗时 {time.perf_counter() - t0:.2f}s")
        else:
            # 其余版本收尾也回填：当前版本的触发器只维护自己那张汇总表，
            # 另一张在压测期间脱节，回填保证三版本跑完数据库始终一致
            backfill(conn)

        write_csv_row(single_path, write_row, header=True)
        write_csv_row(single_path, read_row)
        write_csv_row(summary_path, write_row)
        write_csv_row(summary_path, read_row)
        print(f"结果已写入 {single_path} 和 {summary_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
