# Changelog

Newest first. Each entry states what changed and what is claimed as a result of the change.
See `VERSIONING.md` for what the version numbers mean.

<!-- Add new entries directly below this line. -->

## v0.1.2-tracing-and-attribution — 2026-08-31

**Retrieve with:** `git checkout v0.1.2-tracing-and-attribution`

Trace capture, and an analysis that declines to attribute what it cannot observe.

**Claimed:** nothing. Two exploratory runs were made against a live runtime and are not
published as results; what they produced was a list of defects in this harness, fixed here.
`v0.2-…` remains reserved for the first result.

**Trace capture, so that H2 can be evaluated at all.**

- `tools/otlp_file_sink.py`: an OTLP/HTTP receiver that decodes the protobuf, renders trace
  and span ids as hex per the OTLP/JSON specification, and appends each batch to a file in
  the format `src/collect.py --spans-from` reads. A full OpenTelemetry Collector does the
  same job; this exists so the reproduction path does not require one.
- **The request id is the part that breaks.** A runtime labels its spans with the id it
  assigned, not with the client's `X-Request-Id`. The attribute names then match and the
  values never do, victim selection returns nothing, and H2 reports INCONCLUSIVE — which
  reads like a limitation of the experiment rather than a mapping that was not applied.
  `src/workload.py` now records the id from the response stream and `src/collect.py` uses it
  to rewrite span ids, reporting which path was taken.
- `selftest.sh` stage 2 now carries a span end to end — protobuf, HTTP, decode, id
  normalisation — and asserts that H2 returns a verdict rather than INCONCLUSIVE.

**Attribution that reports what varied rather than assuming it.**

- `src/align.py` bucketed on queue depth, which assumed pressure would appear as queueing.
  Against a constrained pool it appears as cache occupancy oscillating on a sub-second
  scale while the queue stays empty. It now reports the spread of each candidate variable,
  presents a view per variable, and declines to bucket one that did not move.
- Binning changed from equal-count to **equal-width over the observed range**. The occupancy
  distribution is not continuous — most requests meet a nearly empty pool and a minority
  meet a full one — so quartiles put three of four boundaries inside the baseline and
  produced repeated bucket labels next to differing latencies. That reads as a gradient and
  is an artefact of the binning. Uneven bin populations are reported rather than hidden,
  and bins too thin for a stable p95 are marked.
- Joinability no longer requires a queue gauge specifically; a runtime exposing occupancy
  but no queue depth is still observable.
- A run where no candidate variable both varied and had enough joined requests now says so,
  and says that H1 should be read as unexplained rather than established.

Also: `docs/METHOD.md` gains two threats to validity found by running the thing — sampling
resolution below the oscillation being measured, and victim and aggressor windows that do
not overlap. `docs/RUNTIME_SETUP.md` gains the tracing procedure and the request-id
pitfall.

## v0.1.1-harness-fixes — 2026-08-31

**Retrieve with:** `git checkout v0.1.1-harness-fixes`

Implementation defects found while bringing up a runtime, fixed before any run.

**Claimed:** nothing. No experiment has been run. The pre-registered method and the
falsification conditions in `v0.1-preregistration` are unchanged; `v0.2-…` remains reserved
for the first result.

Four defects, each of which would have produced a plausible-looking run with nothing in it:

- **The documented procedure scraped metrics after the workload had finished.** `src.collect`
  polls a live endpoint, so the sampled series described an idle server. The README now runs
  collection concurrently with the workload, and `src.collect` warns when every captured
  series was constant for the whole window.
- **The aggressor prompt exceeded a typical context window.** 6000 words of random filler is
  roughly 18,000 tokens; on an 8192-token context every aggressor request is rejected with
  HTTP 400 and no pressure is applied. Reduced to 1500 words, with the sizing arithmetic
  recorded in the scenario file. `src.workload` now records the HTTP status per request,
  prints the status distribution, and warns when an entire workload was rejected.
- **Filler text was not deterministic across processes.** The seed was `hash((workload, idx))`,
  which is salted by `PYTHONHASHSEED`, so the baseline and contention runs used different
  prompts — contradicting the control stated in `docs/METHOD.md`. Now seeded with `crc32`.
- **The two observation streams could not be joined.** Request timings were monotonic,
  metric timings were wall-clock, and nothing converted between them. Both now record a
  clock anchor on one monotonic axis; `src/align.py` performs the join and reports victim
  TTFT against engine queue depth at admission.

Also: metric sampling interval reduced from 2s to 0.25s, because cache occupancy and
preemption counters move on a sub-second scale and a 2s interval aliases them flat; sampler
scheduling slip is now recorded rather than absorbed; `selftest.sh` gained a loopback stage
against a local fake runtime (`tests/mock_vllm.py`) covering HTTP, SSE, scraping and
alignment, and a regression stage asserting that a wholly-rejected workload is reported
rather than passed over.

## v0.1-preregistration — 2026-08-31

**DOI:** 10.5281/zenodo.22205182 (this version) · 10.5281/zenodo.22205181 (all versions)  
**Retrieve with:** `git checkout v0.1-preregistration`  
**Also archived at:** Software Heritage swh:1:dir:7402a9556cdef17054e4ed3351fecf128844d7bc

Pre-registration of method and evaluation design

**Claimed:** nothing. No experiment has been run. The hypotheses and the conditions
under which each would be falsified are stated so that they are fixed before any
result exists.
