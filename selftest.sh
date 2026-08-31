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
python3 tests/mock_vllm.py --port "$PORT" --slots 2 --token-ms 2 \
        --max-tokens-cap 24 --max-model-len 2000 --model mock/model \
        > runs/_selftest_mock.log 2>&1 &
MOCK_PID=$!
trap 'kill $MOCK_PID 2>/dev/null || true' EXIT

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
