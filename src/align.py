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
        # Joined means *some* engine state was attached, not specifically a queue gauge:
        # a runtime that exposes occupancy but no queue depth is still observable.
        if not any(v is not None for v in row["at"].values()):
            unjoined += 1
        rows.append(row)

    joinable = [r for r in rows if any(v is not None for v in r["at"].values())]
    views = {name: _bucket(joinable, name) for name in ("queue_depth", "cache_usage")}
    return {
        "status": "OK" if joinable else "NOT JOINABLE",
        "n_rows": len(rows),
        "n_joined": len(joinable),
        "n_unjoined": unjoined,
        "max_gap_s": max_gap_s,
        "rows": rows,
        "views": views,
        "spread": {name: _spread(joinable, name) for name in views},
        "distinct": {name: _distinct(joinable, name) for name in views},
        # Kept for readers of older run directories.
        "buckets": views["queue_depth"],
    }


def _distinct(rows: list[dict], key: str) -> int:
    """How many different values the variable actually took.

    Spread alone is not enough. A variable observed as 0,0,0,...,1 has a spread of 1 and
    two distinct values; split into four quartiles it yields three identical bucket labels
    and one that differs, which looks like a gradient and is not one.
    """
    return len({r["at"][key] for r in rows if r["at"].get(key) is not None})


def _spread(rows: list[dict], key: str) -> float | None:
    """Range of a candidate explanatory variable across the run.

    A variable that did not move cannot explain a latency difference, however plausible it
    is as a mechanism. Reporting the spread lets the reader see which of the two views is
    worth reading rather than having to infer it from identical bucket labels.
    """
    vals = [r["at"][key] for r in rows if r["at"].get(key) is not None]
    if not vals:
        return None
    return round(max(vals) - min(vals), 4)


def _bucket(rows: list[dict], key: str, n_bins: int = 4) -> list[dict]:
    """Group requests into equal-width bins over the observed range of `key`.

    Equal *width*, not equal count. Under a constrained cache the occupancy distribution is
    not continuous: most requests are admitted against a nearly empty pool and a minority
    against a full one. Quartiles of such a sample put three of the four boundaries inside
    the baseline, producing bucket labels like `0.030-0.030` three times over, next to
    latencies that differ — which reads as a gradient and is an artefact of the binning.

    Equal-width bins separate the baseline from the spike, and the resulting uneven bin
    populations are themselves informative: `n=352` against a near-empty pool and `n=48`
    against a full one is a description of what the run actually did.

    Empty bins are dropped rather than shown, and a bin with too few requests to give a
    stable p95 is marked so the reader does not read one.
    """
    rows = [r for r in rows if r["at"].get(key) is not None]
    if len(rows) < 8:
        return []
    vals_all = [r["at"][key] for r in rows]
    lo_all, hi_all = min(vals_all), max(vals_all)
    if hi_all == lo_all:
        return []
    width = (hi_all - lo_all) / n_bins
    other = "cache_usage" if key == "queue_depth" else "queue_depth"

    out = []
    for b in range(n_bins):
        lo = lo_all + b * width
        hi = hi_all if b == n_bins - 1 else lo_all + (b + 1) * width
        chunk = [r for r in rows
                 if lo <= r["at"][key] <= hi] if b == n_bins - 1 else [
                 r for r in rows if lo <= r["at"][key] < hi]
        if not chunk:
            continue
        vals = sorted(r["at"][key] for r in chunk)
        ttfts = sorted(r["ttft_ms"] for r in chunk)
        companion = [r["at"][other] for r in chunk if r["at"].get(other) is not None]
        out.append({
            "key": key,
            "bin": [round(lo, 4), round(hi, 4)],
            "range": [vals[0], vals[-1]],
            "n": len(chunk),
            "ttft_p50": _pct(ttfts, 50),
            "ttft_p95": _pct(ttfts, 95),
            # A p95 over fewer than 20 samples is one or two observations; naming the
            # threshold here keeps it out of the reader's head.
            "p95_reliable": len(chunk) >= 20,
            "companion": other,
            "companion_median": round(st.median(companion), 3) if companion else None,
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

    spread = a.get("spread", {})
    lines.append("  spread of each candidate variable over the run: "
                 + ", ".join(f"{k}={'n/a' if v is None else v}"
                             for k, v in sorted(spread.items())))

    views = a.get("views") or {"queue_depth": a.get("buckets", [])}
    distinct = a.get("distinct", {})
    labels = {"queue_depth": "engine queue depth", "cache_usage": "KV cache occupancy"}
    shown = False
    for key in ("queue_depth", "cache_usage"):
        buckets = views.get(key) or []
        sp = spread.get(key)
        nd = distinct.get(key, 0)
        if sp is None:
            # The series was not among those the collector captured. Different from a
            # variable that was captured and did not move, and the reader needs to know
            # which: one is a property of the run, the other of the instrumentation.
            lines.append(f"  {labels[key]}: not present in the captured metrics")
            continue
        if sp == 0 or nd < 2:
            lines.append(f"  {labels[key]} did not vary over the run; no view on it")
            continue
        if not buckets:
            lines.append(f"  {labels[key]} varied, but too few joined requests to bin")
            continue
        shown = True
        lines.append(f"  victim TTFT by {labels[key]} at admission:")
        head = "queue depth" if key == "queue_depth" else "KV occupancy"
        comp = "KV med" if key == "queue_depth" else "queue med"
        lines.append(f"  {head:>14} {'n':>5} {'TTFT p50':>10} {'TTFT p95':>10} {comp:>10}")
        thin = False
        for b in buckets:
            lo, hi = b["bin"]
            fmt = "{:g}-{:g}" if key == "queue_depth" else "{:.3f}-{:.3f}"
            cm = ("n/a" if b["companion_median"] is None
                  else f"{b['companion_median']:.3f}")
            mark = "" if b["p95_reliable"] else "  *"
            thin = thin or not b["p95_reliable"]
            lines.append(f"  {fmt.format(lo, hi):>14} {b['n']:>5} {b['ttft_p50']:>10.1f} "
                         f"{b['ttft_p95']:>10.1f} {cm:>10}{mark}")
        if thin:
            lines.append("    * fewer than 20 requests in this bin; its p95 is one or two")
            lines.append("      observations and should not be read as a percentile")
        lines.append(f"    bins are equal width over the observed range, so populations "
                     f"are uneven by design")

    if not shown:
        lines.append("  no candidate variable both varied and had enough joined requests to")
        lines.append("  bucket. The latency difference reported above is not attributable to")
        lines.append("  anything this collector observed; treat H1 as unexplained, not as")
        lines.append("  established, and see docs/METHOD.md on sampling resolution.")
    return lines


def main() -> None:
    import argparse
    import signal
    # This prints a table people pipe into head; the default SIGPIPE handling turns that
    # into a traceback that looks like a failure.
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError):
        pass
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
