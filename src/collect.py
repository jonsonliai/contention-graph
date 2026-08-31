"""
Server-side capture: spans, runtime metrics, and the contention graph.

Three sources, three different levels of confidence, and the difference matters:

  spans     What the runtime already tells you about each request.   -> tests H2
  metrics   What the runtime knows but does not attach to a request. -> tests H3
  graph     Residency records. These do not exist yet in any runtime.-> tests H4

For H4 there are two paths. If you have patched the runtime to emit residency records, pass
--residency-from and the graph is built from real data. If you have not, --reconstruct builds
an *approximation* from client-side request intervals. The approximation is clearly labelled
in the output and does not prove H4; it shows the shape of the join and lets you check the
harness before you spend time patching a runtime.

--mock generates synthetic inputs so the whole pipeline can be exercised without a GPU.
"""

from __future__ import annotations

import argparse
import json
import random
import time
import urllib.request
from pathlib import Path

from .contention_graph import ContentionGraph, Residency, PressureEvent

# Runtime metric names differ between projects and versions. Matched case-insensitively
# as substrings so that a rename does not silently produce an empty result.
PRESSURE_HINTS = ("preempt", "evict", "recompute", "cache_usage", "cache.usage",
                  "gpu_cache", "kv_cache", "swap")
QUEUE_HINTS = ("num_waiting", "waiting", "queue", "pending", "num_running", "running")


# ----------------------------------------------------------------- spans

def parse_spans(path: Path) -> list[dict]:
    """Parse an OpenTelemetry Collector file-exporter output (JSON per line).

    Configure the collector with a `file` exporter rather than trying to receive OTLP
    directly; it is the least version-sensitive path and avoids a protobuf dependency here.
    """
    out: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        for rs in rec.get("resourceSpans", []):
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    out.append({
                        "name": sp.get("name"),
                        "trace_id": sp.get("traceId"),
                        "span_id": sp.get("spanId"),
                        "start_unix_nano": int(sp.get("startTimeUnixNano", 0)),
                        "end_unix_nano": int(sp.get("endTimeUnixNano", 0)),
                        "attributes": _flatten_attrs(sp.get("attributes", [])),
                        "events": [
                            {"name": e.get("name"),
                             "time_unix_nano": int(e.get("timeUnixNano", 0)),
                             "attributes": _flatten_attrs(e.get("attributes", []))}
                            for e in sp.get("events", [])
                        ],
                    })
    return out


def _flatten_attrs(attrs: list[dict]) -> dict:
    """OTLP attributes are [{key, value:{stringValue|intValue|...}}]."""
    out: dict = {}
    for a in attrs:
        k = a.get("key")
        v = a.get("value", {})
        for kind in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if kind in v:
                out[k] = v[kind]
                break
        else:
            out[k] = json.dumps(v)
    return out


def normalize_request_id(spans: list[dict], extra_keys: tuple[str, ...] = ()) -> None:
    """Ensure every span carries `request.id`.

    If the runtime does not propagate X-Request-Id onto the span, find its own request-id
    attribute and add its name to --request-id-key. Without this the H2 test cannot select
    victim spans and will report INCONCLUSIVE rather than a result.
    """
    candidates = ("request.id", "http.request.header.x_request_id", "x-request-id",
                  "gen_ai.request.id", "vllm.request_id") + extra_keys
    for s in spans:
        a = s["attributes"]
        if "request.id" in a:
            continue
        for k in candidates:
            if k in a:
                a["request.id"] = a[k]
                break


# ----------------------------------------------------------------- metrics

def scrape(url: str, duration_s: float, interval_s: float = 2.0) -> dict:
    """Poll a Prometheus endpoint and keep the series whose names look relevant."""
    series: dict[str, list[tuple[float, float]]] = {}
    t_end = time.monotonic() + duration_s
    while time.monotonic() < t_end:
        t = time.time()
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                body = r.read().decode("utf-8", "ignore")
        except Exception as exc:                      # noqa: BLE001
            print(f"  scrape failed: {exc}")
            time.sleep(interval_s)
            continue
        for line in body.splitlines():
            if line.startswith("#") or " " not in line:
                continue
            name, _, val = line.rpartition(" ")
            low = name.lower()
            if not any(h in low for h in PRESSURE_HINTS + QUEUE_HINTS):
                continue
            try:
                series.setdefault(name, []).append((t, float(val)))
            except ValueError:
                pass
        time.sleep(interval_s)

    return {
        "endpoint": url,
        "series": series,
        # The finding for H3: is there any key on these series that identifies an
        # individual request? Prometheus series are aggregates; if a runtime did expose a
        # per-request label it would show up as a label on a pressure series.
        "per_request_join_key": _find_join_key(series),
    }


def _find_join_key(series: dict) -> str | None:
    for name in series:
        if "{" not in name:
            continue
        labels = name.split("{", 1)[1].rstrip("}")
        for part in labels.split(","):
            k = part.split("=", 1)[0].strip()
            if k.lower() in ("request_id", "request.id", "req_id", "seq_id", "sequence_id"):
                return k
    return None


# ----------------------------------------------------------------- graph

def graph_from_residency(path: Path) -> ContentionGraph:
    """Build from residency records a runtime actually emitted (JSON lines).

    This is the only input that can support H4. The runtime knows when it allocated and
    reclaimed cache blocks; these records are that knowledge, written down. Obtaining them
    requires instrumenting the runtime, which is why H4 is expected to report NOT RUN until
    that work is done.
    """
    g = ContentionGraph(provenance="runtime")
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("type") == "residency":
            g.add_residency(Residency(**{k: d[k] for k in
                                         ("request_id", "resource_id", "t_start", "t_end",
                                          "units") if k in d},
                                      peak_units=d.get("peak_units", 0.0)))
        elif d.get("type") == "pressure":
            g.add_event(PressureEvent(
                resource_id=d["resource_id"], t=d["t"], kind=d["kind"],
                victim_request_id=d["victim_request_id"],
                units_reclaimed=d.get("units_reclaimed", 0.0)))
    g.finalize()
    return g


def graph_reconstructed(requests_json: Path, resource_id: str = "kv_pool_0") -> ContentionGraph:
    """APPROXIMATION \u2014 exercises the join, does not test H4.

    Two substitutions are made, and both are assumptions rather than observations:

      residency interval  <-  the request's whole lifetime.  In reality a request acquires
                              and releases blocks throughout its life, and may hold none for
                              part of it.
      units held          <-  prompt length plus max_tokens.  In reality occupancy depends on
                              the block allocator, on prefix sharing with other requests, and
                              on how much of max_tokens was actually generated.

    Both substitutions assume the thing H4 asks about: that occupancy intervals are known.
    A graph built this way can therefore demonstrate that the join produces a sensible
    ranking, and can demonstrate nothing at all about whether real residency records would
    make attribution derivable. The provenance tag carries that distinction forward so the
    analysis reports it rather than the reader having to remember it.
    """
    d = json.loads(requests_json.read_text())
    g = ContentionGraph(provenance="reconstructed")
    for r in d["records"]:
        if r["t_complete"] is None:
            continue
        g.add_residency(Residency(
            request_id=r["request_id"], resource_id=resource_id,
            t_start=r["t_arrival"], t_end=r["t_complete"],
            units=float(r["prompt_tokens_approx"] + r["max_tokens"])))
    g.finalize()
    return g


# ----------------------------------------------------------------- mock

def write_mock(out: Path) -> None:
    """Synthetic inputs matching the shape of the real ones, for pipeline validation.

    The mock deliberately reproduces the expected finding: victim spans carry latency and
    nothing about the cause; metrics show preemption with no per-request label.
    """
    rng = random.Random(7)
    out.mkdir(parents=True, exist_ok=True)
    contention = "contention" in out.name

    records, spans = [], []
    t = 0.0
    for i in range(120):
        t += 0.25
        ttft = rng.gauss(180, 25) if not contention else rng.gauss(180, 25) + rng.expovariate(1 / 900)
        ttft = max(40.0, ttft)
        rid = f"victim-{i:05d}"
        records.append({
            "request_id": rid, "workload": "victim", "t_arrival": t,
            "t_first_token": t + ttft / 1000, "t_complete": t + ttft / 1000 + 1.1,
            "prompt_tokens_approx": 60, "max_tokens": 64,
            "ttft_ms": ttft, "total_ms": ttft + 1100, "error": None})
        spans.append({
            "name": "chat.completions", "trace_id": f"{i:032x}", "span_id": f"{i:016x}",
            "start_unix_nano": int(t * 1e9), "end_unix_nano": int((t + 1.2) * 1e9),
            # Exactly the attribute set a compliant runtime emits today.
            "attributes": {
                "request.id": rid, "gen_ai.request.model": "mock-8b",
                "gen_ai.usage.input_tokens": 60, "gen_ai.usage.output_tokens": 64,
                "gen_ai.response.finish_reasons": "stop", "duration_ms": ttft + 1100},
            "events": []})

    if contention:
        for j in range(14):
            ta = j * 1.25
            rid = f"aggressor-{j:05d}"
            records.append({
                "request_id": rid, "workload": "aggressor", "t_arrival": ta,
                "t_first_token": ta + 0.9, "t_complete": ta + 26.0,
                "prompt_tokens_approx": 6000, "max_tokens": 1024,
                "ttft_ms": 900.0, "total_ms": 26000.0, "error": None})

    (out / "requests.json").write_text(json.dumps(
        {"scenario": {"mock": True}, "t_run_start_epoch": time.time(),
         "t_run_end_epoch": time.time() + 60, "records": records}, indent=2))
    (out / "spans.json").write_text(json.dumps(spans, indent=2))
    (out / "metrics.json").write_text(json.dumps({
        "endpoint": "mock",
        "series": {
            'runtime:num_preemptions_total': [[time.time(), 41.0 if contention else 0.0]],
            'runtime:gpu_cache_usage_perc': [[time.time(), 0.94 if contention else 0.21]],
            'runtime:num_requests_waiting': [[time.time(), 6.0 if contention else 0.0]]},
        # The point of H3: aggregates with no request identity on them.
        "per_request_join_key": None}, indent=2))
    # Inherits provenance="reconstructed", so the self-test cannot report a positive H4.
    graph_reconstructed(out / "requests.json").dump(str(out / "contention_graph.json"))
    print(f"mock inputs written to {out}")


# ----------------------------------------------------------------- cli

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--mock", action="store_true", help="generate synthetic inputs, no GPU needed")
    ap.add_argument("--spans-from", help="OTel Collector file-exporter output (JSON lines)")
    ap.add_argument("--request-id-key", default="", help="extra attribute holding the request id")
    ap.add_argument("--metrics-url", help="runtime Prometheus endpoint")
    ap.add_argument("--scrape-seconds", type=float, default=0.0)
    ap.add_argument("--residency-from", help="residency records from a patched runtime")
    ap.add_argument("--reconstruct", action="store_true",
                    help="approximate the contention graph from client timings (not evidence for H4)")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.mock:
        write_mock(out)
        return

    if args.spans_from:
        spans = parse_spans(Path(args.spans_from))
        normalize_request_id(spans, tuple(k for k in [args.request_id_key] if k))
        (out / "spans.json").write_text(json.dumps(spans, indent=2))
        n_id = sum(1 for s in spans if "request.id" in s["attributes"])
        print(f"spans: {len(spans)} parsed, {n_id} carry request.id")
        if spans and not n_id:
            print("  WARNING: no span carries a request id. H2 cannot be evaluated.")
            print("  Find the runtime's own request-id attribute and pass --request-id-key.")

    if args.metrics_url and args.scrape_seconds > 0:
        m = scrape(args.metrics_url, args.scrape_seconds)
        (out / "metrics.json").write_text(json.dumps(m, indent=2))
        print(f"metrics: {len(m['series'])} series; "
              f"per-request join key: {m['per_request_join_key'] or '(none)'}")

    g = None
    if args.residency_from:
        g = graph_from_residency(Path(args.residency_from))
        print("contention graph built from runtime residency records")
    elif args.reconstruct:
        g = graph_reconstructed(out / "requests.json")
        print("contention graph RECONSTRUCTED from client timings — approximation, not evidence for H4")
    if g is not None:
        g.dump(str(out / "contention_graph.json"))


if __name__ == "__main__":
    main()
