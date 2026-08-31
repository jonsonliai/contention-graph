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

## Recording provenance

Every published result must carry runtime version, model, hardware, and this repository's
commit hash. A result without them cannot be reproduced and should not be cited.
