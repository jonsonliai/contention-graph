"""
Hypothesis evaluation.

The output that matters is H2: an inventory of what the victim's span actually carries,
and the demonstration that nothing in it names the cause. Everything else is context for
that absence.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

from .contention_graph import ContentionGraph

# Fragments that, if found in any victim attribute or span-event name, falsify H2 — that is,
# that would mean the victim's own record already tells you about the contention.
#
# The list is deliberately over-broad. A narrow list would let H2 survive on a technicality
# when a runtime does carry the information under a name we failed to anticipate. Since a
# falsified H2 is a real and useful finding, the test is biased toward finding one.
CAUSE_FRAGMENTS = (
    "evict", "preempt", "recompute", "co_resident", "coresident", "neighbor", "neighbour",
    "contention", "cache.pressure", "cache_pressure", "batch.id", "batch_id",
    "tenant", "queue.residency", "queue_residency", "resource.id", "resource_id",
)


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


def _load(run: Path) -> dict:
    d = {"requests": json.loads((run / "requests.json").read_text())}
    for name, key in (("spans.json", "spans"), ("metrics.json", "metrics")):
        f = run / name
        d[key] = json.loads(f.read_text()) if f.exists() else None
    g = run / "contention_graph.json"
    d["graph"] = ContentionGraph.load(str(g)) if g.exists() else None
    return d


def _victim_ttft(d: dict) -> list[float]:
    return [r["ttft_ms"] for r in d["requests"]["records"]
            if r["workload"] == "victim" and r["ttft_ms"] is not None]


def h1(base: dict, cont: dict) -> tuple[str, list[str]]:
    """Does the victim degrade when the aggressor is present?

    Judged on the p95 ratio rather than the mean, because contention does not shift the
    distribution uniformly \u2014 it grows a tail. Only requests whose blocks were evicted are
    affected, so the median can look almost unchanged while the tail moves by an order of
    magnitude. A mean-based test would miss exactly the cases the endeavor is about.

    H1 establishes only that there is something to attribute. It is not the finding.
    """
    b, c = _victim_ttft(base), _victim_ttft(cont)
    if not b or not c:
        return "INCONCLUSIVE", ["missing victim TTFT in one or both runs"]
    lines = [
        f"victim TTFT baseline    n={len(b):4d}  p50={_pct(b,50):8.1f}ms  p95={_pct(b,95):8.1f}ms  mean={st.mean(b):8.1f}ms",
        f"victim TTFT contention  n={len(c):4d}  p50={_pct(c,50):8.1f}ms  p95={_pct(c,95):8.1f}ms  mean={st.mean(c):8.1f}ms",
    ]
    ratio = _pct(c, 95) / _pct(b, 95) if _pct(b, 95) else float("nan")
    lines.append(f"p95 ratio contention/baseline = {ratio:.2f}x")
    return ("NOT FALSIFIED" if ratio > 1.2 else "FALSIFIED"), lines


def h2(cont: dict) -> tuple[str, list[str]]:
    """Does the victim's own span name the cause?

    This is the finding, together with H3. The test is deliberately generous to the runtime:
    CAUSE_FRAGMENTS matches loosely and any hit falsifies H2. If a runtime already emits an
    attribute naming the evicting request, the premise of this work is wrong and that is
    worth knowing \u2014 which is why the test is written to find such an attribute rather than
    to avoid finding one.

    Reports INCONCLUSIVE rather than a verdict when no victim span carries a request id,
    because in that case the selection failed and nothing was actually examined.
    """
    spans = cont.get("spans")
    if spans is None:
        return "NOT RUN", ["no spans.json — run src.collect with OTLP capture enabled"]

    victim_spans = [s for s in spans
                    if str(s.get("attributes", {}).get("request.id", "")).startswith("victim")]
    if not victim_spans:
        return "INCONCLUSIVE", ["no victim spans captured; check request-id propagation"]

    keys: set[str] = set()
    for s in victim_spans:
        keys |= set(s.get("attributes", {}).keys())
        for ev in s.get("events", []):
            keys.add("event:" + str(ev.get("name", "")))

    hits = sorted(k for k in keys if any(f in k.lower() for f in CAUSE_FRAGMENTS))
    lines = [
        f"victim spans captured: {len(victim_spans)}",
        "attributes present: " + ", ".join(sorted(keys)) if keys else "attributes present: (none)",
        "attributes referencing a co-resident request, an eviction, or cache pressure: "
        + (", ".join(hits) if hits else "(none)"),
    ]
    return ("FALSIFIED" if hits else "NOT FALSIFIED"), lines


def h3(cont: dict) -> tuple[str, list[str]]:
    """Do the runtime's own metrics let you reach the victim?

    The runtime knows preemption happened; it counted the events. The question is whether
    anything on those series identifies which request suffered. Prometheus series are
    aggregates, and adding a per-request label would make cardinality unbounded \u2014 so the
    absence here is a property of the metrics model rather than an oversight by any
    implementer. That is what makes it structural rather than a bug someone could fix.
    """
    m = cont.get("metrics")
    if m is None:
        return "NOT RUN", ["no metrics.json — run src.collect with the metrics endpoint set"]
    pressure = {k: v for k, v in m.get("series", {}).items()
                if any(f in k.lower() for f in ("preempt", "evict", "cache_usage", "cache.usage"))}
    lines = [
        "runtime series indicating pressure: " + (", ".join(sorted(pressure)) or "(none found)"),
        "",
        "join key between these series and an individual request: "
        + (m.get("per_request_join_key") or "(none)"),
    ]
    if not pressure:
        return "INCONCLUSIVE", lines + ["runtime exposed no preemption/eviction series; see docs/RUNTIME_SETUP.md"]
    return ("FALSIFIED" if m.get("per_request_join_key") else "NOT FALSIFIED"), lines


def h4(cont: dict) -> tuple[str, list[str]]:
    """Evaluate H4, and refuse to return a verdict the input cannot support.

    H4 asks whether residency records make attribution derivable. Only records a runtime
    actually emitted can answer that. A reconstructed graph (see `collect.graph_reconstructed`)
    infers residency from client-side request timings, which assumes the very thing H4 tests —
    so however clean its output looks, it is not evidence.

    The check below is the whole reason `ContentionGraph.provenance` exists. Without it the
    self-test, which runs on synthetic reconstructed data, would print H4 NOT FALSIFIED next
    to three real verdicts, and that line would eventually be quoted somewhere it should not
    be.
    """
    g = cont.get("graph")
    if g is None:
        return "NOT RUN", ["no contention_graph.json — residency capture not enabled"]

    prov = getattr(g, "provenance", "unknown")
    if prov != "runtime":
        return "NOT RUN — INPUT CANNOT TEST H4", [
            "contention graph provenance: %s" % prov,
            "",
            "H4 requires residency records emitted by an instrumented runtime. The graph",
            "supplied was %s, which infers residency from request start and end" % (
                "reconstructed from client timings" if prov == "reconstructed"
                else "of unrecorded origin"),
            "times and proxies occupancy by prompt length. Both are assumptions about the",
            "quantity H4 asks about, so no verdict is returned.",
            "",
            "The join is exercised below to show that it runs and ranks sensibly. This is a",
            "test of the code, not a finding about any runtime.",
        ] + _h4_ranking(cont, g)

    return _h4_verdict(cont, g)


def _h4_ranking(cont: dict, g) -> list[str]:
    """Run the join and format it, without returning a verdict."""
    victims = _victims_by_slowest(cont)
    if not victims:
        return []
    lines = []
    for rid in victims[:5]:
        a = g.attribute(rid)
        classes = a.get("co_resident_classes", [])
        agg = sum(c["share"] for c in classes if c["class"].startswith("aggressor"))
        lines.append("  %s: co-resident=%3d  top class=%-12s  aggressor share=%.2f"
                     % (rid, a.get("co_resident_count", 0),
                        classes[0]["class"] if classes else "(none)", agg))
    return lines


def _victims_by_slowest(cont: dict) -> list[str]:
    v = [r["request_id"] for r in cont["requests"]["records"]
         if r["workload"] == "victim" and r["ttft_ms"] is not None]
    v.sort(key=lambda rid: -next(
        r["ttft_ms"] for r in cont["requests"]["records"] if r["request_id"] == rid))
    return v


def _h4_verdict(cont: dict, g) -> tuple[str, list[str]]:
    victims = _victims_by_slowest(cont)
    if not victims:
        return "INCONCLUSIVE", ["no victim requests to attribute"]

    lines, resolved = [], 0
    n = min(5, len(victims))
    for rid in victims[:n]:
        a = g.attribute(rid)
        classes = a.get("co_resident_classes", [])
        agg_share = sum(c["share"] for c in classes if c["class"].startswith("aggressor"))
        top_c = classes[0]["class"] if classes else "(none)"
        if agg_share >= 0.5:
            resolved += 1
        lines.append(
            f"  {rid}: co-resident={a.get('co_resident_count',0):3d}"
            f"  top class={top_c:<12s}"
            f"  aggressor share={agg_share:.2f}"
            f"  pressure events={len(a.get('pressure_events_during_residency', []))}")
    lines.insert(0, f"slowest {n} victims, attribution from residency records:")
    lines.append("")
    lines.append("Attribution is evaluated at the level of the consumer class, not the")
    lines.append("individual request. Where many similar consumers are co-resident, no single")
    lines.append("one dominates and a per-request answer would be misleading; the actionable")
    lines.append("finding is which class of workload held the resource.")
    lines.append("")
    lines.append(f"aggressor class accounts for the majority for {resolved} of {n}")
    return ("NOT FALSIFIED" if resolved >= 3 else "FALSIFIED"), lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("baseline")
    ap.add_argument("contention")
    ap.add_argument("--report", default="runs/report.md")
    args = ap.parse_args()

    base, cont = _load(Path(args.baseline)), _load(Path(args.contention))

    results = [
        ("H1", "victim degrades under co-resident cache pressure", *h1(base, cont)),
        ("H2", "the victim's span does not name the cause", *h2(cont)),
        ("H3", "runtime metrics record pressure but cannot be joined to the victim", *h3(cont)),
        ("H4", "residency records make the attribution derivable", *h4(cont)),
    ]

    out = ["# Attribution gap experiment — result", ""]
    for tag, claim, verdict, lines in results:
        print(f"\n{tag}  {claim}\n    -> {verdict}")
        for l in lines:
            print("    " + l)
        out += [f"## {tag} — {claim}", "", f"**{verdict}**", "", "```"] + lines + ["```", ""]

    out += [
        "## Reading this",
        "",
        "H1 establishes only that there is something to attribute. **H2 and H3 are the",
        "finding**: the degradation is recorded and the cause is not, and the runtime's own",
        "knowledge of the pressure cannot be joined to the request that suffered it.",
        "",
        "**H4 requires residency records emitted by an instrumented runtime.** Where the",
        "contention graph was reconstructed from client timings instead, no verdict is",
        "returned: the reconstruction assumes the quantity H4 asks about. The ranking shown",
        "under H4 in that case demonstrates that the join runs, and nothing more.",
        "",
        "A falsified H2 or H3 would mean the premise of this work is wrong. That result would",
        "be published here unchanged.",
    ]
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text("\n".join(out))
    print(f"\nwritten: {args.report}")


if __name__ == "__main__":
    main()
