#!/usr/bin/env python3
"""
join_h1.py — align request-side and engine-side observations from one run.

Reads the JSONL written by contention_run.py and answers the question H1
actually asks: at the moment a request was admitted, what was the engine's
scheduling state, and does that state explain the latency the client observed?

Emits:
    <outdir>/joined.jsonl    one row per request, engine state attached
    stdout                   summary table

The join is nearest-sample on the shared monotonic clock, with the gap recorded
per row. Rows whose nearest engine sample is further away than --max-gap-ms are
marked unjoinable rather than silently matched to a stale sample: a join that
quietly stretches is the same failure mode as a sampler that quietly drifts.

Standard library only.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import statistics
import sys
from typing import Any

CAUSE_KEYS = (
    "num_requests_running",
    "num_requests_waiting",
    "num_requests_waiting_by_reason.capacity",
    "num_requests_waiting_by_reason.deferred",
    "kv_cache_usage_perc",
    "num_preemptions_total",
)


def read_jsonl(path: str) -> list[dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"{path}:{i}: skipping malformed line ({exc})", file=sys.stderr)
    return rows


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[idx]


def fmt(x: float | None, nd: int = 1) -> str:
    return "n/a" if x is None else f"{x:,.{nd}f}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("rundir", help="directory produced by contention_run.py")
    p.add_argument("--max-gap-ms", type=float, default=500.0,
                   help="reject a join whose nearest engine sample is further than this")
    p.add_argument("--buckets", type=int, default=4,
                   help="number of queue-depth buckets in the summary table")
    args = p.parse_args()

    req_path = os.path.join(args.rundir, "requests.jsonl")
    eng_path = os.path.join(args.rundir, "engine.jsonl")
    for path in (req_path, eng_path):
        if not os.path.exists(path):
            print(f"missing {path}", file=sys.stderr)
            return 2

    requests = [r for r in read_jsonl(req_path) if r.get("type") == "request"]
    engine_rows = [e for e in read_jsonl(eng_path)
                   if e.get("type") == "engine" and e.get("ok")]
    slips = [e for e in read_jsonl(eng_path) if e.get("type") == "sampler_slip"]

    if not requests:
        print("no request records", file=sys.stderr)
        return 2
    if not engine_rows:
        print("no usable engine samples", file=sys.stderr)
        return 2

    engine_rows.sort(key=lambda e: e["t_mono_ns"])
    eng_ts = [e["t_mono_ns"] for e in engine_rows]

    joined: list[dict[str, Any]] = []
    unjoinable = 0
    out_path = os.path.join(args.rundir, "joined.jsonl")
    with open(out_path, "w", encoding="utf-8") as out:
        for r in requests:
            t = r["t_send_ns"]
            i = bisect.bisect_left(eng_ts, t)
            cands = [j for j in (i - 1, i) if 0 <= j < len(eng_ts)]
            best = min(cands, key=lambda j: abs(eng_ts[j] - t))
            gap_ms = abs(eng_ts[best] - t) / 1e6
            row = dict(r)
            row["join_gap_ms"] = round(gap_ms, 3)
            if gap_ms > args.max_gap_ms:
                row["joined"] = False
                unjoinable += 1
            else:
                row["joined"] = True
                m = engine_rows[best].get("m", {})
                row["engine_at_send"] = {k: m.get(k) for k in CAUSE_KEYS}
            out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            joined.append(row)

    ok = [r for r in joined if r.get("ok")]
    bad = [r for r in joined if not r.get("ok")]
    ttfts = [r["ttft_ms"] for r in ok if r.get("ttft_ms") is not None]
    e2es = [r["e2e_ms"] for r in ok if r.get("e2e_ms") is not None]

    print("=" * 68)
    print(f"run: {args.rundir}")
    print("=" * 68)
    print(f"requests            {len(joined):>8}   ok {len(ok)}   failed {len(bad)}")
    if bad:
        by_status: dict[Any, int] = {}
        for r in bad:
            by_status[r.get("status")] = by_status.get(r.get("status"), 0) + 1
        print(f"  FAILURE STATUSES  {by_status}")
        sample = next((r.get("error_body") or r.get("error") for r in bad
                       if r.get("error_body") or r.get("error")), None)
        if sample:
            print(f"  first error       {str(sample)[:160]}")
    print(f"engine samples      {len(engine_rows):>8}")
    print(f"sampler slips       {len(slips):>8}"
          + (f"   max {max(s['slip_ms'] for s in slips):.1f} ms" if slips else ""))
    print(f"unjoinable rows     {unjoinable:>8}   (gap > {args.max_gap_ms:.0f} ms)")
    print()

    print("client-observed latency (successful requests)")
    print(f"  TTFT  p50 {fmt(pct(ttfts, .50))} ms   p95 {fmt(pct(ttfts, .95))} ms"
          f"   p99 {fmt(pct(ttfts, .99))} ms   max {fmt(max(ttfts) if ttfts else None)} ms")
    print(f"  E2E   p50 {fmt(pct(e2es, .50))} ms   p95 {fmt(pct(e2es, .95))} ms"
          f"   p99 {fmt(pct(e2es, .99))} ms   max {fmt(max(e2es) if e2es else None)} ms")
    print()

    print("engine state over the run")
    for k in CAUSE_KEYS:
        vals = [e["m"].get(k) for e in engine_rows if e["m"].get(k) is not None]
        if not vals:
            print(f"  {k:<44} NOT PRESENT")
            continue
        nd = 3 if "perc" in k else 1
        print(f"  {k:<44} min {fmt(min(vals), nd)}  "
              f"med {fmt(statistics.median(vals), nd)}  max {fmt(max(vals), nd)}")
    print()

    # The H1 table: bucket requests by the queue depth they were admitted into,
    # then report the latency each bucket experienced. If TTFT rises with queue
    # depth, the symptom the client sees is explained by engine scheduling state
    # rather than by anything visible on the request path.
    rows = [r for r in ok
            if r.get("joined")
            and r.get("ttft_ms") is not None
            and r.get("engine_at_send", {}).get("num_requests_waiting") is not None]
    if len(rows) < args.buckets * 4:
        print(f"too few joined rows ({len(rows)}) for the bucket table")
        return 0

    rows.sort(key=lambda r: r["engine_at_send"]["num_requests_waiting"])
    n = len(rows)
    size = n // args.buckets
    print(f"H1: request latency vs engine queue depth at admission (n={n})")
    print(f"  {'waiting@send':>16} {'n':>6} {'TTFT p50':>11} {'TTFT p95':>11} "
          f"{'KV@send med':>13}")
    for b in range(args.buckets):
        lo = b * size
        hi = n if b == args.buckets - 1 else (b + 1) * size
        chunk = rows[lo:hi]
        if not chunk:
            continue
        waits = [r["engine_at_send"]["num_requests_waiting"] for r in chunk]
        tt = [r["ttft_ms"] for r in chunk]
        kv = [r["engine_at_send"].get("kv_cache_usage_perc") for r in chunk]
        kv = [v for v in kv if v is not None]
        label = f"{waits[0]:.0f}-{waits[-1]:.0f}"
        print(f"  {label:>16} {len(chunk):>6} {fmt(pct(tt, .50)):>11} "
              f"{fmt(pct(tt, .95)):>11} "
              f"{fmt(statistics.median(kv) if kv else None, 3):>13}")
    print()
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
