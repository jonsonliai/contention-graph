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
echo "Pipeline OK. runs/_selftest_report.md written."
echo "NOTE: these are synthetic inputs and a fake runtime. The verdicts above are a test"
echo "      of the harness, not a finding about any runtime."
