#!/usr/bin/env bash
# Validates the pipeline end to end without a GPU. Run this before booking accelerator time.
#
# Stage 1 exercises the analysis on synthetic files.
# Stage 2 exercises the parts stage 1 cannot reach — real HTTP, real SSE parsing, a real
#         Prometheus scrape, and the alignment between the two streams — against a local
#         fake runtime. Those paths are where a harness fails in ways that look, from the
#         analysis output alone, exactly like a system with nothing to report.
set -e
cd "$(dirname "$0")"

echo "=== stage 1: analysis on synthetic inputs ==="
python3 -m src.collect --out runs/_selftest_baseline  --mock
python3 -m src.collect --out runs/_selftest_contention --mock
python3 -m src.analyze runs/_selftest_baseline runs/_selftest_contention \
        --report runs/_selftest_report.md

echo
echo "=== stage 2: live loopback against tests/mock_vllm.py ==="
PORT=${SELFTEST_PORT:-8123}
OTLP_PORT=${SELFTEST_OTLP_PORT:-4399}
SPANS=runs/_selftest_live_spans.jsonl
rm -f "$SPANS"

# The span path is exercised end to end -- protobuf encode, HTTP, decode, id normalisation
# -- because that is where H2 fails in ways the analysis output cannot distinguish from a
# real finding. A missing victim span and a runtime that emits no cause attribute both
# produce a report with nothing in it.
HAVE_OTLP=0
if python3 -c "import opentelemetry.proto, google.protobuf" 2>/dev/null; then
  HAVE_OTLP=1
  python3 tools/otlp_file_sink.py --out "$SPANS" --port "$OTLP_PORT" \
          > runs/_selftest_sink.log 2>&1 &
  SINK_PID=$!
  for _ in $(seq 1 40); do
    curl -sf "http://127.0.0.1:$OTLP_PORT/healthz" >/dev/null && break
    sleep 0.25
  done
  export MOCK_OTLP_ENDPOINT="http://127.0.0.1:$OTLP_PORT/v1/traces"
else
  echo "  opentelemetry-proto not installed; skipping the span path (stage 2 will still"
  echo "  cover HTTP, SSE, scraping and alignment). pip install -r requirements.txt"
  SINK_PID=""
fi

python3 tests/mock_vllm.py --port "$PORT" --slots 2 --token-ms 2 \
        --max-tokens-cap 24 --max-model-len 2000 --model mock/model \
        --otlp-endpoint "${MOCK_OTLP_ENDPOINT:-}" \
        > runs/_selftest_mock.log 2>&1 &
MOCK_PID=$!
trap 'kill $MOCK_PID $SINK_PID 2>/dev/null || true' EXIT

for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:$PORT/metrics" >/dev/null && break
  sleep 0.25
done

OUT=runs/_selftest_live
rm -rf "$OUT"; mkdir -p "$OUT"

# Metrics collection runs DURING the workload, not after it. Started afterwards it samples
# an idle server and every series is flat, which is indistinguishable from a real negative.
python3 -m src.collect --out "$OUT" \
        --metrics-url "http://127.0.0.1:$PORT/metrics" \
        --scrape-seconds 12 --scrape-interval 0.25 &
COLLECT_PID=$!

INFERENCE_ENDPOINT="http://127.0.0.1:$PORT/v1/chat/completions" \
INFERENCE_MODEL="mock/model" \
python3 -m src.workload --scenario tests/selftest_live.yaml --out "$OUT"

wait $COLLECT_PID

if [ "$HAVE_OTLP" = "1" ]; then
  sleep 1
  kill $SINK_PID 2>/dev/null || true
  wait $SINK_PID 2>/dev/null || true
  python3 -m src.collect --out "$OUT" --spans-from "$SPANS"
  python3 - "$OUT" <<'PY'
import json, sys, pathlib
out = pathlib.Path(sys.argv[1])
rep = json.loads((out / "span_id_report.json").read_text())
if rep["spans"] == 0:
    sys.exit("SELFTEST FAILED: the sink received no spans")
if rep["mapped_via_response_id"] == 0:
    sys.exit("SELFTEST FAILED: no span was joined to a client request via the response id. "
             "That mapping is what lets H2 select victim spans when the runtime labels "
             "spans with its own id.")
spans = json.loads((out / "spans.json").read_text())
victims = [s for s in spans
           if str(s["attributes"].get("request.id", "")).startswith("victim")]
if not victims:
    sys.exit("SELFTEST FAILED: no victim spans after normalisation")

# Run the H2 test itself. That victim spans exist is a precondition; that the test returns
# a verdict rather than INCONCLUSIVE is the property worth guarding, because INCONCLUSIVE
# and a real finding are indistinguishable in the report.
sys.path.insert(0, ".")
from src.analyze import _load, h2                                   # noqa: E402
verdict, _ = h2(_load(out))
if verdict != "NOT FALSIFIED":
    sys.exit(f"SELFTEST FAILED: H2 returned {verdict!r}. The fixture emits no cause "
             f"attribute, so the only correct verdict is NOT FALSIFIED; anything else "
             f"means selection or matching is broken.")
print(f"  ok: {rep['spans']} spans, {rep['mapped_via_response_id']} mapped to client ids, "
      f"{len(victims)} victim spans, H2 -> {verdict}")
PY
fi

python3 -m src.align "$OUT"

echo
echo "=== stage 3: an oversized prompt must fail loudly, not quietly ==="
# Guards the failure mode that costs the most: a workload whose requests are all rejected
# still completes, and the resulting metrics are clean because no load was ever applied.
OUT3=runs/_selftest_oversize
rm -rf "$OUT3"
cat > runs/_selftest_oversize.yaml <<YAML
model: mock/model
endpoint: http://127.0.0.1:$PORT/v1/chat/completions
timeout_s: 30
max_connections: 32
workloads:
  victim:
    count: 6
    rate_per_s: 20
    prompt_words: 20
    max_tokens: 8
    start_delay_s: 0
  aggressor:
    count: 4
    rate_per_s: 10
    prompt_words: 6000
    max_tokens: 8
    start_delay_s: 0
YAML
python3 -m src.workload --scenario runs/_selftest_oversize.yaml --out "$OUT3" \
  2>&1 | tee runs/_selftest_oversize.out
grep -q "every 'aggressor' request failed" runs/_selftest_oversize.out \
  || { echo "SELFTEST FAILED: an entirely-rejected workload was not reported"; exit 1; }
grep -q "FAILED BY STATUS: 400=" runs/_selftest_oversize.out \
  || { echo "SELFTEST FAILED: HTTP status was not recorded"; exit 1; }
echo "  ok: rejection was reported with status and a warning"

kill $MOCK_PID 2>/dev/null || true

echo
echo "=== stage 4: the sweep must refuse an unstable baseline ==="
# The checklist's baseline-stability gate is the one most easily satisfied by picking the
# quieter of two runs. Guarding it here means that behaviour has to be removed deliberately
# rather than drifting away under a deadline.
python3 - <<'PY'
import json, pathlib, random, subprocess, sys, tempfile, time

def mk(d, extra_ms, seed):
    rng = random.Random(seed)
    p = pathlib.Path(d); p.mkdir(parents=True, exist_ok=True)
    recs = [{"request_id": f"victim-{i:05d}", "workload": "victim", "t_arrival": 2 + i * .25,
             "t_first_token": None, "t_complete": None, "prompt_tokens_approx": 60,
             "max_tokens": 64, "total_ms": 300, "status": 200, "error": None,
             "server_request_id": f"s{i}",
             "ttft_ms": max(5.0, rng.gauss(28, 3)
                            + (rng.expovariate(1 / extra_ms)
                               if extra_ms and rng.random() < .25 else 0))}
            for i in range(120)]
    json.dump({"scenario": {"workloads": {"aggressor": {"count": 0, "rate_per_s": 0}}},
               "t_run_start_epoch": time.time(), "t_run_end_epoch": time.time() + 60,
               "clock_anchor": {"t0_mono": 1000.0, "t0_wall": time.time()},
               "records": recs}, open(p / "requests.json", "w"))

tmp = tempfile.mkdtemp()
mk(f"{tmp}/quiet", 0, 1)
mk(f"{tmp}/noisy", 400, 2)
mk(f"{tmp}/point", 200, 3)
out = subprocess.run([sys.executable, "-m", "src.sweep",
                      "--baselines", f"{tmp}/quiet", f"{tmp}/noisy",
                      "--points", f"{tmp}/point", "--out", f"{tmp}/sweep.md"],
                     capture_output=True, text=True).stdout
if "The baselines disagree" not in out:
    sys.exit("SELFTEST FAILED: sweep accepted two baselines that do not agree")
if "not evidence about" not in out:
    sys.exit("SELFTEST FAILED: sweep did not disclaim its ratios after a failed baseline check")
print("  ok: unstable baselines were refused and the ratios disclaimed")
PY

echo
echo "=== stage 5: a variable that does not separate the population must be called out ==="
# Observed on a real run: bucketing by queue depth put 389 of 400 requests in one bin with
# p50 27ms and p95 348ms, while the same requests bucketed by cache occupancy gave p50 27ms
# and p95 40ms. The first table reads as a gradient and is not one.
python3 - <<'PY'
import json, pathlib, random, subprocess, sys, tempfile, time

rng = random.Random(7)
tmp = pathlib.Path(tempfile.mkdtemp()) / "run"
tmp.mkdir(parents=True)
t0 = 1000.0
recs, kv, q = [], [], []
for i in range(400):
    hot = i >= 352
    ttft = rng.gauss(26.6, 3) if not hot else rng.gauss(120, 60) + rng.expovariate(1 / 400)
    recs.append({"request_id": f"victim-{i:05d}", "workload": "victim",
                 "t_arrival": 2.0 + i * .25, "t_first_token": None, "t_complete": None,
                 "prompt_tokens_approx": 60, "max_tokens": 64, "total_ms": 300,
                 "ttft_ms": max(5.0, ttft), "server_request_id": f"c{i}",
                 "status": 200, "error": None})
for k in range(3200):
    tm = t0 + k * .1
    idx = int((tm - t0 - 2.0) / .25)
    hot = 352 <= idx < 400
    kv.append([tm, round(rng.uniform(.57, .76) if hot else rng.uniform(0, .19), 4)])
    q.append([tm, float(rng.choice([1, 2, 3]))
              if (not hot and rng.random() < .03) else 0.0])
lab = 'engine="0",model_name="M"'
json.dump({"scenario": {}, "t_run_start_epoch": time.time(),
           "t_run_end_epoch": time.time() + 320,
           "clock_anchor": {"t0_mono": t0, "t0_wall": time.time()}, "records": recs},
          open(tmp / "requests.json", "w"))
json.dump({"endpoint": "sim", "clock_anchor": {"t_mono": t0, "t_wall": time.time()},
           "interval_s": 0.1, "samples": 3200, "scrape_errors": 0, "sampler_slips": [],
           "series": {f"vllm:kv_cache_usage_perc{{{lab}}}": kv,
                      f"vllm:num_requests_waiting{{{lab}}}": q},
           "per_request_join_key": None}, open(tmp / "metrics.json", "w"))

out = subprocess.run([sys.executable, "-m", "src.align", str(tmp)],
                     capture_output=True, text=True).stdout
if "are not one population" not in out:
    sys.exit("SELFTEST FAILED: a variable whose largest bin mixes fast and slow requests "
             "was presented as a gradient")
if "accounts for the difference and" not in out:
    sys.exit("SELFTEST FAILED: the two views were not compared")
print("  ok: the non-separating variable was called out and the views compared")
PY

echo
echo "Pipeline OK. runs/_selftest_report.md written."
echo "NOTE: these are synthetic inputs and a fake runtime. The verdicts above are a test"
echo "      of the harness, not a finding about any runtime."
