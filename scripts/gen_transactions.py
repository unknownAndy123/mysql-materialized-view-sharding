#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成银行流水 CSV（纯标准库，无第三方依赖）。

默认参数即你要的场景：
    1000 万条流水，100 个商户，M001 固定 100 万条，其余 900 万条按 Zipf 幂律(1/k)分布。

用法示例：
    python gen_transactions.py \
        --total 10000000 --merchants 100 --m001 1000000 --zipf 1.0 \
        --out transactions.csv --seed 42
"""
import argparse
import datetime
import os
import random


def compute_counts(total, merchants, m001_count, zipf_exp):
    """按 'M001 固定 + 其余 Zipf' 返回每个商户的条数列表（顺序 M001..M{merchants}）。"""
    if merchants < 2:
        raise SystemExit("--merchants 至少为 2")
    if m001_count >= total:
        raise SystemExit("--m001 必须小于 --total")

    rest = total - m001_count
    ranks = range(2, merchants + 1)                      # M002..M{merchants}
    weights = [1.0 / (k ** zipf_exp) for k in ranks]     # Zipf 权重 1/k^s
    wsum = sum(weights)

    counts = [m001_count]
    raw = [rest * w / wsum for w in weights]
    ints = [int(x) for x in raw]
    rem = rest - sum(ints)                               # 余数用最大余数法补足，保证合计精确
    order = sorted(range(len(raw)), key=lambda i: raw[i] - ints[i], reverse=True)
    for i in order[:rem]:
        ints[i] += 1
    counts.extend(ints)
    return counts


def fmt_amount(cents):
    sign = '-' if cents < 0 else ''
    cents = abs(cents)
    return f'{sign}{cents // 100}.{cents % 100:02d}'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--total', type=int, default=10_000_000)
    ap.add_argument('--merchants', type=int, default=100)
    ap.add_argument('--m001', type=int, default=1_000_000)
    ap.add_argument('--zipf', type=float, default=1.0)
    ap.add_argument('--out', default='transactions.csv')
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--no-header', action='store_true')
    args = ap.parse_args()

    counts = compute_counts(args.total, args.merchants, args.m001, args.zipf)
    merchant_ids = [f'M{i:03d}' for i in range(1, args.merchants + 1)]

    rng = random.Random(args.seed)
    base = datetime.datetime(2024, 1, 1)
    max_sec = 366 * 24 * 3600 - 1

    print(f'开始生成：{args.total:,} 条 / {args.merchants} 个商户 -> {args.out}')

    CHUNK = 200_000
    txn_id = 0
    chunk = []

    with open(args.out, 'w', newline='', encoding='utf-8') as f:
        if not args.no_header:
            f.write('txn_id,merchant_id,amount,txn_time\n')
        for mid, cnt in zip(merchant_ids, counts):
            for _ in range(cnt):
                txn_id += 1
                cents = rng.randint(-20000, 180000)        # 金额：-200.00 ~ 1800.00
                secs = rng.randint(0, max_sec)             # 时间：2024 年内随机
                t = base + datetime.timedelta(seconds=secs)
                chunk.append(f'{txn_id},{mid},{fmt_amount(cents)},{t:%Y-%m-%d %H:%M:%S}')
                if len(chunk) >= CHUNK:
                    f.write('\n'.join(chunk) + '\n')
                    chunk = []
                if txn_id % 1_000_000 == 0:
                    print(f'  ... 已生成 {txn_id:,} 条')
        if chunk:
            f.write('\n'.join(chunk) + '\n')

    size_mb = os.path.getsize(args.out) / 1024 / 1024
    print(f'完成：{txn_id:,} 条，文件大小 {size_mb:.1f} MB')

    print('\n各商户条数（前 10）：')
    for mid, cnt in list(zip(merchant_ids, counts))[:10]:
        print(f'  {mid}: {cnt:,}')
    print('  ...')
    for mid, cnt in list(zip(merchant_ids, counts))[-5:]:
        print(f'  {mid}: {cnt:,}')
    ok = 'OK' if sum(counts) == args.total else 'FAIL'
    print(f'\n校验：合计 {sum(counts):,} == 目标 {args.total:,} -> {ok}')


if __name__ == '__main__':
    main()
