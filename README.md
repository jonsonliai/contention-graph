# Contention Graph: demonstrating an attribution gap in LLM inference telemetry

<!-- FILED-VERSION-BLOCK -->
> **Version cited in the immigration petition:** release `v1.0-test`, DOI 10.5281/zenodo.9999999.
> Later releases continue the work; they do not alter that version. To retrieve the
> state that was cited: `git checkout v1.0-test`. The exact commit hash that release
> resolves to is recorded in the GitHub Release and in the Zenodo archive metadata.
<!-- FILED-VERSION-BLOCK -->


A reproducible experiment showing that when one request degrades another through shared
KV-cache pressure, **per-request tracing records the symptom and does not record the cause** —
and a minimal instrumentation that closes the gap.

Runs on a single GPU against an open-source serving runtime and an open-weight model.
No proprietary data, no vendor telemetry, no client environment.

---

![](docs/figures/fig2_timeline.png)

## The claim being tested

Production inference observability is organised around the **request path**. A request enters,
traverses a set of components, and produces a response; instrumentation records what happened
along that path.

A significant class of cause lies *off* that path. When a long-context request evicts a
latency-sensitive request's cache blocks, the victim's time-to-first-token rises. The victim's
span records the elevated latency. It does not record the eviction, because the eviction is not
an event on the victim's path — it happened to the victim, not in it.

The information needed to attribute the degradation **exists somewhere in the system**: the
scheduler knows what it preempted and when. But it is not joined to the victim request. There
is no shared key. Attribution requires a correlation that nothing in the current telemetry
model is built to express.

This repository tests that claim and then tests whether adding one thing — a record of *which
request occupied which bounded resource over which interval* — makes attribution possible.

![](docs/figures/fig1_two_graphs.png)

The full argument, the instrumentation, and the proposed semantic conventions are in
[`docs/WHITEPAPER.md`](docs/WHITEPAPER.md).

### Hypotheses

| | Statement | Falsifiable by |
|---|---|---|
| **H1** | Under cache pressure from a co-resident workload, victim TTFT degrades measurably | No degradation observed at any aggressor intensity |
| **H2** | The victim's OTLP span contains no attribute identifying the aggressor, the eviction, or the cache pressure | Finding such an attribute in the emitted spans |
| **H3** | Runtime-level metrics record that preemption occurred but cannot be joined to the affected request | Finding an existing join key between preemption events and victim requests |
| **H4** | Adding residency records `(request_id, resource, t_start, t_end)` makes the victim→aggressor attribution derivable | Attribution remains ambiguous with residency data present. **Requires an instrumented runtime; reports NOT RUN otherwise** |

**H2 and H3 are the point.** H1 only establishes that there is something to attribute.
If H2 or H3 is falsified, the premise of this work is wrong and that is worth knowing.

**H4 cannot be tested without instrumenting the runtime.** The collector can reconstruct an
approximate contention graph from client-side timings, which is useful for exercising the join
but assumes the quantity H4 asks about. On such input the analysis reports `NOT RUN — INPUT
CANNOT TEST H4` rather than a verdict.

---

## What this is not

- **Not a benchmark.** No claim about the runtime's performance. The aggressor workload is
  constructed to induce pressure, not to represent realistic traffic.
- **Not a product.** The collector in `src/contention_graph.py` is the minimum needed to test
  H4, not an implementation intended for production.
- **Not a claim of novelty in scheduling.** That cache pressure causes interference is well
  known to anyone who operates these systems. The claim concerns what the *telemetry* records
  about it.
- **Not validated at scale.** Single GPU, single node. The generalisation to multi-node,
  multi-tenant production is exactly what is untested here.

---

## Running it

Requirements: one GPU with sufficient memory for the chosen model; Python 3.10+; a serving
runtime with OTLP tracing enabled and a Prometheus metrics endpoint.

```bash
pip install -r requirements.txt

# 1. Start the runtime with tracing and metrics enabled.
#    See docs/RUNTIME_SETUP.md — flags differ between runtimes and versions.

# 0. Validate the harness with synthetic inputs first — no GPU required.
./selftest.sh

# 2. Baseline: victim workload alone.
python -m src.workload --scenario scenarios/baseline.yaml --out runs/baseline

# 3. Contention: victim plus aggressor.
python -m src.workload --scenario scenarios/contention.yaml --out runs/contention

# 3b. Capture the server side for the same window.
python -m src.collect --out runs/contention \
    --spans-from otel-collector-out.jsonl \
    --metrics-url http://localhost:8000/metrics --scrape-seconds 120 \
    --reconstruct          # or --residency-from, if the runtime has been patched

# 4. Analysis: does the victim degrade, and is the cause in the victim's trace?
python -m src.analyze runs/baseline runs/contention
```

`analyze` prints a verdict on each hypothesis and writes `runs/report.md`.

---

## What a result looks like

The output that matters is not the latency numbers. It is the attribute inventory:

```
H2  victim span attributes present under contention:
      gen_ai.request.model, gen_ai.usage.input_tokens, gen_ai.usage.output_tokens,
      gen_ai.response.finish_reasons, ... , duration_ms
    attributes referencing a co-resident request, an eviction, or cache pressure:
      (none)
    -> H2 NOT FALSIFIED
```

That absence is the finding. Everything else in the repository exists to make the absence
demonstrable rather than asserted.

---

## Layout

```
src/workload.py           victim / aggressor generators against an OpenAI-compatible endpoint
src/collect.py            span parsing, metrics scraping, graph build, --mock mode
selftest.sh               zero-GPU validation of the whole pipeline
src/contention_graph.py   the proposed residency record and the attribution join
src/analyze.py            hypothesis evaluation, report generation
scenarios/*.yaml          workload definitions
docs/WHITEPAPER.md        the argument, the definition, the proposed conventions
docs/figures/             figures, as PNG and vector PDF
docs/METHOD.md            experiment design, threats to validity
docs/RUNTIME_SETUP.md     runtime flags, metric names, version notes
```

---

## Status

**Scaffold.** The design and the harness are complete; results are not yet published here.
Results will be added with the runtime version, model, hardware, and commit hash under which
they were produced. Until then this repository states a method and a set of hypotheses, not a
finding.

## Licence

Apache-2.0. The instrumentation in `src/contention_graph.py` is intended for contribution
upstream rather than retention.

## How results here are published

Versioning, the checklist run before any result is published, and the rules on provenance and
on not revising results after publication are in [`docs/provenance/`](docs/provenance/).
That directory also records where this work is cited externally.
