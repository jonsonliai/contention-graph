# Attribution gap experiment — result

## H1 — victim degrades under co-resident cache pressure

**NOT FALSIFIED**

```
victim TTFT baseline    n= 400  p50=    26.2ms  p95=    34.0ms  mean=    27.1ms
victim TTFT contention  n= 400  p50=    26.7ms  p95=   104.2ms  mean=    39.1ms
p95 ratio contention/baseline = 3.06x

alignment: 400/400 victim requests joined to a metric sample within 1.0s
  spread of each candidate variable over the run: cache_usage=0.7182, queue_depth=0.0
  engine queue depth did not vary over the run; no view on it
  victim TTFT by KV cache occupancy at admission:
    KV occupancy     n   TTFT p50   TTFT p95  queue med
     0.000-0.179   371       26.6       40.0      0.000
     0.359-0.539     1      155.3      155.3      0.000  *
     0.539-0.718    28       41.4      148.2      0.000
    * fewer than 20 requests in this bin; its p95 is one or two
      observations and should not be read as a percentile
    bins are equal width over the observed range, so populations are uneven by design
```

## H2 — the victim's span does not name the cause

**NOT FALSIFIED**

```
victim spans captured: 2965
attributes present: gen_ai.latency.e2e, gen_ai.latency.time_in_model_decode, gen_ai.latency.time_in_model_inference, gen_ai.latency.time_in_model_prefill, gen_ai.latency.time_in_queue, gen_ai.latency.time_to_first_token, gen_ai.request.id, gen_ai.request.max_tokens, gen_ai.request.n, gen_ai.request.top_p, gen_ai.usage.completion_tokens, gen_ai.usage.prompt_tokens, request.id, request.id.server
attributes referencing a co-resident request, an eviction, or cache pressure: (none)
```

## H3 — runtime metrics record pressure but cannot be joined to the victim

**NOT FALSIFIED**

```
runtime series indicating pressure: vllm:kv_cache_usage_perc{engine="0",model_name="Qwen/Qwen2.5-1.5B-Instruct"}, vllm:num_preemptions_created{engine="0",model_name="Qwen/Qwen2.5-1.5B-Instruct"}, vllm:num_preemptions_total{engine="0",model_name="Qwen/Qwen2.5-1.5B-Instruct"}

join key between these series and an individual request: (none)
```

## H4 — residency records make the attribution derivable

**NOT RUN — INPUT CANNOT TEST H4**

```
contention graph provenance: reconstructed

H4 requires residency records emitted by an instrumented runtime. The graph
supplied was reconstructed from client timings, which infers residency from request start and end
times and proxies occupancy by prompt length. Both are assumptions about the
quantity H4 asks about, so no verdict is returned.

The join is exercised below to show that it runs and ranks sensibly. This is a
test of the code, not a finding about any runtime.
  victim-00024: co-resident=  3  top class=aggressor     aggressor share=0.98
  victim-00008: co-resident=  3  top class=aggressor     aggressor share=0.98
  victim-00016: co-resident=  2  top class=aggressor     aggressor share=0.98
  victim-00040: co-resident=  2  top class=aggressor     aggressor share=0.98
  victim-00032: co-resident=  2  top class=aggressor     aggressor share=0.98
```

## Reading this

H1 establishes only that there is something to attribute. **H2 and H3 are the
finding**: the degradation is recorded and the cause is not, and the runtime's own
knowledge of the pressure cannot be joined to the request that suffered it.

**H4 requires residency records emitted by an instrumented runtime.** Where the
contention graph was reconstructed from client timings instead, no verdict is
returned: the reconstruction assumes the quantity H4 asks about. The ranking shown
under H4 in that case demonstrates that the join runs, and nothing more.

A falsified H2 or H3 would mean the premise of this work is wrong. That result would
be published here unchanged.