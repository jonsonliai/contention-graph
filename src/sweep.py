"""
Sweep reporting: baseline stability, and the curve across aggressor intensities.

`docs/METHOD.md` lists cherry-picked intensity as a threat to validity and requires the full
curve rather than the point with the largest effect. A single contention run cannot separate
"pressure causes this" from "this intensity happens to produce a number worth reporting", and
the reader has no way to tell which they are looking at.

`docs/provenance/PUBLICATION_CHECKLIST.md` requires two further things before a result is
published, and both are checks on the environment rather than on the hypothesis:

  * two consecutive baseline runs whose p95 agree. If they do not, the machine is too noisy
    for any contention result obtained on it to mean anything, and the correct action is to
    stop rather than to pick the quieter baseline.
  * more than one aggressor intensity.

This module reports both, and states plainly when the baselines disagree.

Usage:
    python -m src.sweep --baselines runs/baseline_a runs/baseline_b \\
                        --points runs/contention_low runs/contention_mid runs/contention_high
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

# Two baselines whose p95 differ by more than this are not measuring the same machine.
# Stated as a constant so the reader can see the bar rather than infer it, and so that
# moving it is a visible edit rather than a judgement made at reading time.
BASELINE_AGREEMENT_RATIO = 1.25


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


def _victim_ttft(run: Path) -> list[float]:
    d = json.loads((run / "requests.json").read_text())
    return [r["ttft_ms"] for r in d["records"]
            if r["workload"] == "victim" and r["ttft_ms"] is not None]


def _aggressor_spec(run: Path) -> dict:
    d = json.loads((run / "requests.json").read_text())
    spec = d.get("scenario", {}).get("workloads", {}).get("aggressor", {})
    served = [r for r in d["records"]
              if r["workload"] == "aggressor" and r.get("ttft_ms") is not None]
    asked = [r for r in d["records"] if r["workload"] == "aggressor"]
    return {"count": spec.get("count", 0), "rate": spec.get("rate_per_s", 0),
            "max_tokens": spec.get("max_tokens", 0),
            "served": len(served), "asked": len(asked)}


def _cache_peak(run: Path) -> float | None:
    p = run / "metrics.json"
    if not p.exists():
        return None
    m = json.loads(p.read_text())
    vals = [v for name, pts in m.get("series", {}).items()
            if "cache_usage" in name.lower() for _, v in pts]
    return round(max(vals), 3) if vals else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baselines", nargs="+", required=True)
    ap.add_argument("--points", nargs="+", required=True,
                    help="contention run directories, in increasing intensity")
    ap.add_argument("--out", default="runs/sweep.md")
    args = ap.parse_args()

    lines: list[str] = []

    def say(s: str = "") -> None:
        print(s)
        lines.append(s)

    # ---------------------------------------------------------------- baselines
    say("## Baseline stability")
    say()
    bases = []
    for b in args.baselines:
        t = _victim_ttft(Path(b))
        bases.append((b, t))
        say(f"  {b:<28} n={len(t):4d}  p50={_pct(t,50):8.1f}ms  "
            f"p95={_pct(t,95):8.1f}ms  mean={st.mean(t):8.1f}ms")
    say()

    stable = True
    if len(bases) < 2:
        say("  Only one baseline. The checklist requires two consecutive runs, because a")
        say("  contention result obtained on a machine that was not measured twice cannot be")
        say("  distinguished from one obtained on a machine that drifted.")
        stable = False
    else:
        p95s = [_pct(t, 95) for _, t in bases]
        ratio = max(p95s) / min(p95s) if min(p95s) else float("inf")
        say(f"  p95 spread across baselines: {ratio:.2f}x "
            f"(threshold {BASELINE_AGREEMENT_RATIO:.2f}x)")
        if ratio > BASELINE_AGREEMENT_RATIO:
            stable = False
            say()
            say("  **The baselines disagree.** The environment is too noisy for a contention")
            say("  result measured against it to mean anything. Do not proceed by choosing")
            say("  the quieter baseline; that selects the comparison that flatters the")
            say("  result. Find the source of the variance, or report that the environment")
            say("  did not permit the measurement.")
        else:
            say("  Baselines agree; the environment held still across the two runs.")
    say()

    # ---------------------------------------------------------------- the curve
    ref = _pct(bases[0][1], 95)
    say("## Victim degradation vs aggressor intensity")
    say()
    say(f"  {'run':<26} {'aggr':>10} {'served':>7} {'p50':>9} {'p95':>10} "
        f"{'p95 ratio':>10} {'KV peak':>8}")
    for pt in args.points:
        run = Path(pt)
        t = _victim_ttft(run)
        a = _aggressor_spec(run)
        peak = _cache_peak(run)
        ratio = _pct(t, 95) / ref if ref else float("nan")
        spec = f"{a['count']}@{a['rate']}/s"
        served = f"{a['served']}/{a['asked']}"
        say(f"  {pt:<26} {spec:>10} {served:>7} {_pct(t,50):>9.1f} "
            f"{_pct(t,95):>10.1f} {ratio:>9.2f}x {('n/a' if peak is None else peak):>8}")
        if a["asked"] and a["served"] < a["asked"]:
            say(f"    NOTE: {a['asked'] - a['served']} aggressor requests were not served at "
                f"this point; the applied pressure is lower than the specification")
    say()
    say("  p95 ratio is against the first baseline. `served` shows how many aggressor")
    say("  requests actually completed: a point where they did not applied less pressure")
    say("  than its label suggests.")
    say()

    if not stable:
        say("## Reading this")
        say()
        say("  The baseline check did not pass, so the ratios above are not evidence about")
        say("  contention. They are reported so the run is not silently discarded, and so")
        say("  that a reader can see what was measured.")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines) + "\n")
    print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
