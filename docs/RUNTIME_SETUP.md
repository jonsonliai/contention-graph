# Runtime setup

**Verify every flag and metric name below against the documentation for the exact version you
run.** These change between releases and a stale name will make a hypothesis look
INCONCLUSIVE when the data was simply under a different key.

## What the runtime must expose

1. **OTLP trace export** for per-request spans, with the client `X-Request-Id` propagated onto
   the span so client records and server spans can be joined.
2. **A Prometheus metrics endpoint** exposing, at minimum: cache utilisation, preemption or
   eviction counts, queue depth, and running/waiting request counts.
3. **Request-level logging** sufficient to confirm which requests were admitted to which batch,
   if the runtime offers it.

## Checklist before the first real run

- [ ] Confirm the OTLP exporter is emitting: point it at a local collector and check spans arrive
- [ ] Confirm `X-Request-Id` appears on the span; if not, find the runtime's own request-id
      attribute and set `request.id` from it in `src/collect.py`
- [ ] Record: runtime name and exact version, model name and revision, GPU model and memory,
      driver version, and the commit hash of this repository
- [ ] Run the baseline twice and confirm p95 TTFT is stable between the two; if it is not,
      the environment is too noisy for the contention result to mean anything
- [ ] Check the aggressor prompt against `--max-model-len`. Random filler tokenises at
      roughly one token per 2.5-3 characters, so `prompt_words * 3` is a serviceable upper
      estimate. An oversized prompt is rejected with HTTP 400 and applies no pressure, while
      the run completes and the metrics look clean
- [ ] Confirm `src.collect` is running **before** `src.workload` starts and is still running
      when it finishes. A scrape that does not overlap the load window samples an idle
      server and produces flat series
- [ ] Issue one request by hand and read the response body. `--max-model-len`, model name and
      endpoint path are the three things that silently reject an entire run
- [ ] Start the OTLP sink, issue one request, and confirm `/healthz` reports a non-zero span
      count. A run with tracing misconfigured produces H2 NOT RUN after the GPU time is spent
- [ ] After the first collect, read the `joined to client ids` line. Zero on both paths means
      H2 will be INCONCLUSIVE

## Enabling traces

`src/collect.py --spans-from` reads OTLP JSON lines. `tools/otlp_file_sink.py` produces them:
it is an OTLP/HTTP receiver that decodes the protobuf, renders the two identifier fields as
hex, and appends each batch to a file. A full OpenTelemetry Collector does the same job and
can be used instead; the sink exists so that the reproduction path does not require one.

```bash
pip install opentelemetry-sdk opentelemetry-api opentelemetry-exporter-otlp \
            opentelemetry-proto protobuf

# 1. Sink first, so nothing is lost while the runtime starts.
python3 tools/otlp_file_sink.py --out runs/otel-spans.jsonl --port 4318 &
curl -sf http://127.0.0.1:4318/healthz

# 2. Runtime, exporting over HTTP rather than gRPC: one fewer moving part, and the sink
#    speaks it directly.
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_TRACES_INSECURE=true
export OTEL_SERVICE_NAME=vllm-server
vllm serve <model> ... --otlp-traces-endpoint http://127.0.0.1:4318/v1/traces
```

Confirm spans arrive before spending a run on it:

```bash
curl -s localhost:8000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"<model>","messages":[{"role":"user","content":"hi"}],"max_tokens":8}' \
  >/dev/null
sleep 2
curl -s http://127.0.0.1:4318/healthz          # spans should be > 0
```

Zero spans after a successful request means the runtime is not exporting. Check that the
OTel packages are importable *in the runtime's interpreter*, that the flag was accepted, and
that `--otlp-traces-endpoint` points at the sink and not at a gRPC port.

### The request id is the part that breaks

A runtime labels its spans with the id it assigned, typically `chatcmpl-...`. That is not the
client's `X-Request-Id`, so the names match, the values never do, and victim selection
silently returns nothing. H2 then reports INCONCLUSIVE, which reads like a limitation of the
experiment rather than a mapping that was not applied.

`src/workload.py` records the id from the response stream, and `src/collect.py` uses it to
rewrite span ids to the client's. Nothing needs configuring, but the output says which path
was taken and it is worth reading:

```
spans: 400 parsed, 400 carry a request id (attribute: gen_ai.request.id)
  joined to client ids: 0 directly, 400 via the recorded response id
```

`0 directly, 400 via response id` means the runtime does not propagate the header — normal.
`400 directly` means it does. **`0 and 0` means neither worked**, and H2 cannot be evaluated:
the spans are from a different run, or `requests.json` predates `server_request_id`.

## Recording provenance

Every published result must carry runtime version, model, hardware, and this repository's
commit hash. A result without them cannot be reproduced and should not be cited.
