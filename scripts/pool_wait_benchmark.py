#!/usr/bin/env python3
"""
A2 连接池耗尽演示脚本 v1
对比两种取连接方式：
    direct  — 每个操作新建一条连接（无排队，但每次付建连开销）
    pool    — DBUtils.PooledDB 小池（池满时 acquire 阻塞排队 → 测排队等待时长）

用法：
    python3 pool_wait_benchmark.py --mode direct --threads 8 --ops 20 --hold-ms 200 --think-ms 100
    python3 pool_wait_benchmark.py --mode pool   --threads 8 --ops 20 --pool-size 2 --hold-ms 200 --think-ms 100
    python3 pool_wait_benchmark.py --mode pool   --threads 8 --ops 20 --pool-size 8 --hold-ms 200 --think-ms 100

推荐演示组合（连接池耗尽 → 池扩容消除等待）：
    direct（基准） / pool-size 2（耗尽） / pool-size 8（与线程数持平）

每个操作 = acquire → 执行 SELECT SLEEP(hold_ms)（真实占用连接）→ 归还
          → 业务思考间隔 think_ms（不持连接）。
think 间隔模拟真实请求之间的间隙：没有它，刚归还连接的线程会立刻把
连接抢回（DBUtils 唤醒非 FIFO），排队集中在少数线程的首次 acquire，
p50 会被大量「瞬得」拉低、掩盖排队真相。
结果追加写入 压测结果/pool_wait_summary.csv，同时存一份带时间戳的独立 CSV。
"""

import argparse
import csv
import datetime
import os
import threading
import time

import pymysql
from dbutils.pooled_db import PooledDB

# 数据库连接从环境变量读取，不在代码里写密码（见 README）
DB_CONFIG = dict(host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
                 port=int(os.environ.get("MYSQL_PORT", "3306")),
                 user=os.environ.get("MYSQL_USER", "root"),
                 password=os.environ.get("MYSQL_PASSWORD", ""),
                 database=os.environ.get("MYSQL_DATABASE", "sql_group"),
                 charset="utf8mb4", autocommit=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_DIR = os.path.join(BASE_DIR, "压测结果")

WAIT_THRESHOLD_MS = 5.0  # acquire 超过该时长计为「排队等待」


def percentile(sorted_vals, p):
    """线性插值分位数，输入必须是升序 list"""
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def do_work(conn, hold_s):
    """占用连接做一次真实查询（SLEEP 模拟业务持有连接时长）"""
    with conn.cursor() as cur:
        cur.execute("SELECT SLEEP(%s)", (hold_s,))
        cur.fetchall()


def direct_worker(ops, hold_s, think_s, barrier, acquire_latencies):
    barrier.wait()
    for _ in range(ops):
        t0 = time.perf_counter()
        conn = pymysql.connect(**DB_CONFIG)
        acquire_latencies.append(time.perf_counter() - t0)
        try:
            do_work(conn, hold_s)
        finally:
            conn.close()
        time.sleep(think_s)


def pool_worker(pool, ops, hold_s, think_s, barrier, acquire_latencies):
    barrier.wait()
    for _ in range(ops):
        t0 = time.perf_counter()
        conn = pool.connection()  # 池满时 blocking 排队，时长即连接池等待
        acquire_latencies.append(time.perf_counter() - t0)
        try:
            do_work(conn, hold_s)
        finally:
            conn.close()  # 归还池
        time.sleep(think_s)


def run(mode, pool_size, threads, ops, hold_s, think_s):
    latencies = []
    barrier = threading.Barrier(threads)
    if mode == "pool":
        pool = PooledDB(creator=pymysql, maxconnections=pool_size,
                        mincached=0, maxcached=pool_size,
                        blocking=True, **DB_CONFIG)
        target = lambda i: pool_worker(pool, ops, hold_s, think_s, barrier,
                                       latencies)
    else:
        target = lambda i: direct_worker(ops, hold_s, think_s, barrier,
                                         latencies)
    workers = [threading.Thread(target=target, args=(i,)) for i in range(threads)]
    t0 = time.perf_counter()
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    wall = time.perf_counter() - t0
    return latencies, wall


def summarize(mode, pool_size, threads, ops, hold_ms, think_ms, latencies, wall):
    s = sorted(latencies)
    ms = [v * 1000 for v in s]
    waited = sum(1 for v in ms if v > WAIT_THRESHOLD_MS)
    total_ops = threads * ops
    row = [
        mode, pool_size, threads, total_ops, hold_ms, think_ms,
        round(percentile(ms, 50), 2),
        round(percentile(ms, 95), 2),
        round(percentile(ms, 99), 2),
        round(max(ms), 2),
        round(waited / total_ops * 100, 1),
        round(wall, 3),
        round(total_ops / wall, 2),
    ]
    label = f"{mode}" + (f"/pool={pool_size}" if mode == "pool" else "")
    print(f"[{label:<14}] threads={threads} ops={ops} hold={hold_ms}ms "
          f"think={think_ms}ms acquire p50={row[6]:>8}ms p95={row[7]:>8}ms "
          f"p99={row[8]:>8}ms max={row[9]:>8}ms 排队占比={row[10]:>5}% "
          f"wall={wall:.1f}s tps={row[12]}")
    return row


def write_csv_row(path, row, header=False):
    new_file = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if header or new_file:
            writer.writerow(["mode", "pool_size", "threads", "ops", "hold_ms",
                             "think_ms", "acquire_p50_ms", "acquire_p95_ms",
                             "acquire_p99_ms", "acquire_max_ms", "waited_pct",
                             "wall_s", "tps"])
        writer.writerow(row)


def main():
    ap = argparse.ArgumentParser(description="A2 连接池耗尽演示")
    ap.add_argument("--mode", required=True, choices=["direct", "pool"])
    ap.add_argument("--pool-size", type=int, default=2, help="池最大连接数（pool 模式）")
    ap.add_argument("--threads", type=int, default=8, help="并发线程数")
    ap.add_argument("--ops", type=int, default=20, help="每线程操作次数")
    ap.add_argument("--hold-ms", type=int, default=200, help="每次操作持有连接时长")
    ap.add_argument("--think-ms", type=int, default=100,
                    help="两次操作间不持连接的业务间隔")
    args = ap.parse_args()

    os.makedirs(RESULT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    single_path = os.path.join(RESULT_DIR, f"pool_wait_{args.mode}_{stamp}.csv")
    summary_path = os.path.join(RESULT_DIR, "pool_wait_summary.csv")

    lat, wall = run(args.mode, args.pool_size, args.threads, args.ops,
                    args.hold_ms / 1000.0, args.think_ms / 1000.0)
    row = summarize(args.mode, args.pool_size, args.threads, args.ops,
                    args.hold_ms, args.think_ms, lat, wall)
    write_csv_row(single_path, row, header=True)
    write_csv_row(summary_path, row)
    print(f"结果已写入 {single_path} 和 {summary_path}")


if __name__ == "__main__":
    main()
