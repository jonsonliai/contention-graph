"""
Temporal alignment of the two observation streams.

`src/workload.py` records what the client saw. `src/collect.py` records what the runtime
reported. Both write `time.monotonic()` timestamps, and each writes a clock anchor, so the
two can be placed on one axis and joined.

The join answers a question H1 alone does not: not merely *whether* victim latency rose, but
whether it rose **when the pool was under pressure**. That distinction matters to the argument.
A latency increase on its own is consistent with the machine simply being busy. A latency
increase that tracks queue depth and cache occupancy at the moment of admission is consistent
with contention — and it is still not visible anywhere in the victim's own span, which is the
point H2 goes on to make.

What this module does *not* do: attribute a victim to an aggressor. That requires residency
records and is H4's job (`src/contention_graph.py`). This is correlation over aggregates,
which is exactly the weaker thing the current telemetry model permits. Its weakness is part
of the finding rather than a defect in the implementation.
"""

from __future__ import annotations

import bisect
import json
import statistics as st
from pathlib import Path

# Series whose names carry the state we want at a request's admission. Substring matched,
# case-insensitively, for the same reason as in `src/collect.py`: a rename between runtime
# versions should not silently produce an empty column.
CAUSE_SERIES = {
    "queue_depth": ("num_requests_waiting", "num_waiting", "pending"),
    "running": ("num_requests_running",),
    "cache_usage": ("kv_cache_usage", "gpu_cache_usage", "cache_usage"),
    "preemptions": ("num_preemptions", "preempt"),
}

# A sample further from the request than this is not evidence about that request's admission.
DEFAULT_MAX_GAP_S = 1.0


def _series_matching(series: dict, fragments: tuple[str, ...]) -> list[tuple[float, float]]:
    """Merge every series whose name contains any fragment, sorted by time."""
    pts: list[tuple[float, float]] = []
    for name, samples in series.items():
        low = name.lower()
        if any(f in low for f in fragments):
            pts.extend((float(t), float(v)) for t, v in samples)
    pts.sort()
    return pts


def _nearest(pts: list[tuple[float, float]], t: float) -> tuple[float, float] | None:
    """Nearest sample by time, or None if the series is empty."""
    if not pts:
        return None
    ts = [p[0] for p in pts]
    i = bisect.bisect_left(ts, t)
    cands = [j for j in (i - 1, i) if 0 <= j < len(pts)]
    j = min(cands, key=lambda k: abs(ts[k] - t))
    return pts[j][1], abs(ts[j] - t)


def align(run: Path, max_gap_s: float = DEFAULT_MAX_GAP_S) -> dict:
    """Attach engine state at admission to each victim request.

    Returns a dict with the per-request rows and a bucketed summary. Rows whose nearest
    metric sample falls outside `max_gap_s` are marked unjoined rather than matched to a
    stale sample: a join that silently stretches misreports the state a request met.
    """
    req = json.loads((run / "requests.json").read_text())
    mpath = run / "metrics.json"
    if not mpath.exists():
        return {"status": "NO METRICS", "rows": [], "buckets": []}
    met = json.loads(mpath.read_text())

    anchor = req.get("clock_anchor")
    if not anchor or "t0_mono" not in anchor:
        return {"status": "NO CLOCK ANCHOR",
                "detail": "requests.json predates the anchor; re-run src.workload",
                "rows": [], "buckets": []}
    if "clock_anchor" not in met:
        return {"status": "NO CLOCK ANCHOR",
                "detail": "metrics.json predates the anchor; re-run src.collect",
                "rows": [], "buckets": []}

    t0 = float(anchor["t0_mono"])
    series = met.get("series", {})
    channels = {k: _series_matching(series, frags) for k, frags in CAUSE_SERIES.items()}

    rows, unjoined = [], 0
    for r in req["records"]:
        if r.get("workload") != "victim" or r.get("ttft_ms") is None:
            continue
        t_abs = t0 + float(r["t_arrival"])
        row = {"request_id": r["request_id"], "ttft_ms": r["ttft_ms"], "at": {}}
        gaps = []
        for name, pts in channels.items():
            got = _nearest(pts, t_abs)
            if got is None:
                row["at"][name] = None
                continue
            value, gap = got
            gaps.append(gap)
            row["at"][name] = value if gap <= max_gap_s else None
        row["max_gap_s"] = round(max(gaps), 3) if gaps else None
        if row["at"].get("queue_depth") is None:
            unjoined += 1
        rows.append(row)

    joinable = [r for r in rows if r["at"].get("queue_depth") is not None]
    return {
        "status": "OK" if joinable else "NOT JOINABLE",
        "n_rows": len(rows),
        "n_joined": len(joinable),
        "n_unjoined": unjoined,
        "max_gap_s": max_gap_s,
        "rows": rows,
        "buckets": _bucket(joinable),
    }


def _bucket(rows: list[dict], n_buckets: int = 4) -> list[dict]:
    """Group requests by queue depth at admission and report the latency each group met."""
    if len(rows) < n_buckets * 4:
        return []
    rows = sorted(rows, key=lambda r: r["at"]["queue_depth"])
    n, size = len(rows), len(rows) // n_buckets
    out = []
    for b in range(n_buckets):
        lo, hi = b * size, (n if b == n_buckets - 1 else (b + 1) * size)
        chunk = rows[lo:hi]
        if not chunk:
            continue
        depths = [r["at"]["queue_depth"] for r in chunk]
        ttfts = sorted(r["ttft_ms"] for r in chunk)
        cache = [r["at"]["cache_usage"] for r in chunk if r["at"].get("cache_usage") is not None]
        out.append({
            "queue_depth_range": [depths[0], depths[-1]],
            "n": len(chunk),
            "ttft_p50": _pct(ttfts, 50),
            "ttft_p95": _pct(ttfts, 95),
            "cache_usage_median": round(st.median(cache), 3) if cache else None,
        })
    return out


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return round(xs[k], 1)


def format_lines(a: dict) -> list[str]:
    """Render for the analysis report."""
    if a["status"] == "NO METRICS":
        return ["alignment: no metrics.json — engine state at admission is unknown"]
    if a["status"] == "NO CLOCK ANCHOR":
        return ["alignment: " + a.get("detail", "no clock anchor")]
    if a["status"] == "NOT JOINABLE":
        return [f"alignment: {a['n_rows']} victim requests, none within "
                f"{a['max_gap_s']}s of a metric sample",
                "  the two streams do not overlap in time; was collect running during the "
                "workload?"]

    lines = [f"alignment: {a['n_joined']}/{a['n_rows']} victim requests joined to a metric "
             f"sample within {a['max_gap_s']}s"]
    if not a["buckets"]:
        lines.append("  too few joined requests to bucket")
        return lines
    lines.append("  victim TTFT by engine queue depth at admission:")
    lines.append(f"  {'queue depth':>14} {'n':>5} {'TTFT p50':>10} {'TTFT p95':>10} "
                 f"{'cache med':>10}")
    for b in a["buckets"]:
        lo, hi = b["queue_depth_range"]
        cache = "n/a" if b["cache_usage_median"] is None else f"{b['cache_usage_median']:.3f}"
        lines.append(f"  {f'{lo:g}-{hi:g}':>14} {b['n']:>5} {b['ttft_p50']:>10.1f} "
                     f"{b['ttft_p95']:>10.1f} {cache:>10}")
    return lines


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run")
    ap.add_argument("--max-gap-s", type=float, default=DEFAULT_MAX_GAP_S)
    ap.add_argument("--dump", help="write the per-request rows here as JSON")
    args = ap.parse_args()

    a = align(Path(args.run), args.max_gap_s)
    for line in format_lines(a):
        print(line)
    if args.dump:
        Path(args.dump).write_text(json.dumps(a, indent=2))
        print(f"written: {args.dump}")


if __name__ == "__main__":
    main()
