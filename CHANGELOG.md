# Changelog

Newest first. Each entry states what changed and what is claimed as a result of the change.
See `VERSIONING.md` for what the version numbers mean.

<!-- Add new entries directly below this line. -->

## v0.2.1-citable — 2026-08-31

**DOI:** [pending Zenodo archive]
**Retrieve with:** `git checkout v0.2.1-citable`

Corrections to documentation and to the citation tooling. **This is the version to cite.**

**Claimed:** exactly what `v0.2-first-result` claimed, unchanged. The run data in `runs/` is
byte-identical to that release and the verdicts recompute from it identically. Nothing here
alters a result; `VERSIONING.md` provides for a third component precisely for this.

**Why cite this instead of v0.2.** The README at `v0.2-first-result` gave a command sequence
that does not run: steps 2 and 3 wrote `runs/baseline` and `runs/contention` while steps 4
and 5 read `runs/baseline_a` and `runs/contention_mid`, so a reader following it hits an
error partway through. The checklist's standard is that a result which cannot be reproduced
from what is in the repository is worse than no result, and an exhibit whose reproduction
instructions fail is the case that standard is about. The v0.2 release is not withdrawn — it
remains the first result and its data is unchanged — but the citable state is this one.

- The command sequence is now the one that produced the published runs: two baselines, three
  intensities, sweep before verdicts, and a collector PID to `wait` on. A bare `wait` also
  waits for the OTLP sink and the runtime, neither of which exits.
- "What a result looks like" showed an invented attribute inventory. It is now the actual
  output: fourteen attributes, latency split into queue, prefill and decode, and nothing
  naming what the request waited behind.
- Added the check that matters more than the numbers: the verdicts recompute from the
  committed data with no GPU and no runtime, and `runs/report.md` regenerates byte-for-byte.
  The checksum command was also wrong — paths in `CHECKSUMS.txt` are relative to the
  repository root and the README said to `cd runs` first, which fails on all 23 files.

**Citation tooling.** `docs/provenance/release.py --show` emitted the hash of `HEAD`. Once
anything is committed after a tag that is the wrong commit, and something always is: the DOI
is recorded in a commit that necessarily follows the release it describes. It was reporting
a hash whose checkout shows a changelog still saying the DOI is pending. It now resolves the
tag, and reads the version DOI from `CHANGELOG.md` rather than asking for it again — the
script exists to stop the same four values being typed in several places and disagreeing.

**`docs/provenance/exhibit5.py`** is new. The checklist requires the exhibit to be a snapshot
of the README and the result report as at the cited commit; nothing assembled one. It reads
those documents out of the tagged commit with `git show`, not from the working tree, and
renders one self-contained HTML to print to PDF — no reportlab and no pandoc, because a file
that needs an install to regenerate is one that eventually will not regenerate. Where the
runs were made under an earlier commit than the release, it computes what changed between
the two and says so: two hashes in one exhibit with no explanation reads as though one is a
mistake.

## v0.2-first-result — 2026-08-31

**DOI:** 10.5281/zenodo.22213106 (this version) · 10.5281/zenodo.22205181 (all versions)
**Retrieve with:** `git checkout v0.2-first-result`

First experimental result. Five runs against a live runtime.

**Claimed:** that on vLLM 0.28.0, under co-resident cache pressure from long-context
requests, a victim request's own span records the elevated latency and records nothing that
identifies the cause; and that the runtime's own cache-occupancy and preemption series carry
no key by which they could be joined to the request that suffered. H1 is not falsified and
establishes that there is something to attribute. H4 is NOT RUN and no claim is made about it.

**Not claimed:** anything about how often this occurs in production, about multi-node
topologies, about real traffic mixes, or about runtimes other than the one tested. A single
runtime result is a result about that runtime.

### Provenance

| | |
|---|---|
| Runtime | vLLM 0.28.0 |
| Model | Qwen/Qwen2.5-1.5B-Instruct |
| Torch | 2.13.0+cu130 |
| GPU | NVIDIA RTX PRO 6000 Blackwell Server Edition, **MIG slice GI 3** (46 SM, 24192 MiB), sm_120 |
| Driver / nvcc | 580.126.16 / 12.8.93 |
| Provider | RunPod, pod kvb4pkbaqm5nxy |
| Attention backend | FLASH_ATTN — FlashInfer is unusable on sm_120 under nvcc 12.8 |
| Engine | 512 KV blocks (8,192 tokens), prefix caching off, `--max-model-len 8192` |
| Date | 2026-08-31 |
| Commit | recorded in `runs/provenance.txt` |

The cache constraint is deliberate and its cost is stated in `docs/METHOD.md`: it establishes
that the attribution gap exists when eviction occurs, and says nothing about how often
eviction occurs. The MIG slice is recorded because the phenomenon's onset depends on pool
size and a reader who assumed a full card would size their reproduction wrongly.

### Results

Two consecutive baselines, p95 spread 1.03x against a 1.25x threshold. Three aggressor
intensities, every aggressor request served at every point.

| intensity | aggressors | victim p95 | ratio | KV peak |
|---|---|---|---|---|
| low | 6 @ 0.25/s | 39.7 ms | 1.17x | 0.716 |
| mid | 12 @ 0.5/s | 104.2 ms | 3.06x | 0.718 |
| high | 24 @ 1.0/s | 352.2 ms | 10.34x | 0.761 |

Baseline p95 34.0 ms, n=400 at every point.

**H2** — 2,965 victim spans. Attributes present: `gen_ai.latency.e2e`,
`gen_ai.latency.time_in_model_decode`, `gen_ai.latency.time_in_model_inference`,
`gen_ai.latency.time_in_model_prefill`, `gen_ai.latency.time_in_queue`,
`gen_ai.latency.time_to_first_token`, `gen_ai.request.id`, `gen_ai.request.max_tokens`,
`gen_ai.request.n`, `gen_ai.request.top_p`, `gen_ai.usage.completion_tokens`,
`gen_ai.usage.prompt_tokens`, `request.id`, `request.id.server`. Attributes referencing a
co-resident request, an eviction, or cache pressure: none.

**H3** — pressure series present (`vllm:kv_cache_usage_perc`, `vllm:num_preemptions_total`);
per-request join key: none.

### Two things learned by running it

**The runtime does not propagate the client's request id onto its spans.** All 3,013 joined
spans matched through the response id recorded by `src/workload.py`; none matched the client's
`X-Request-Id` directly. Without that mapping H2 would have reported INCONCLUSIVE, which
reads like a limitation of the experiment rather than a join nobody performed.

**Queue depth explains the degradation at low intensity and stops explaining it at high
intensity.** At the highest point, 389 of 400 requests fall into one queue-depth bin spanning
p50 27 ms to p95 348 ms: a request admitted against a full pool but before a queue has formed
has queue depth zero. Cache occupancy separates the same requests cleanly at every intensity.
A request-path signal proxies for the off-path cause under light load and fails under the load
where attribution is wanted. This was not anticipated in the pre-registered design; it is
reported because it bears directly on the premise rather than despite doing so.

## v0.1.4-residual — 2026-08-31

**Retrieve with:** `git checkout v0.1.4-residual`

A diagnostic for the case where a variable correlates with part of an effect and the table
reads as though it explains all of it.

**Claimed:** nothing. `v0.2-…` remains reserved for the first result.

Five runs were made against a live runtime with the sweep in place. At the highest aggressor
intensity the two views disagreed in a way that could not both be true: bucketing 400 victim
requests by queue depth put 389 of them in one bin with p50 27ms and p95 348ms, while
bucketing the same requests by cache occupancy put 352 in a bin with p50 27ms and p95 40ms.

The queue-depth bin was mixing two populations. Requests admitted against a full pool but
before a queue had formed have queue depth zero, so they land in the same bin as requests
admitted against an empty one, and the bin's p95 is carried by them. The table showed a
monotonic gradient across four bins and the gradient was an artefact.

- `src/align.py` now reports the within-bin spread of each variable's largest bin. Where its
  p95 exceeds its p50 by more than 3x, the bin is annotated: the requests the variable groups
  as unpressured are not one population, so the gradient is not attributable to it.
- Where both views render and only one passes, the output names which variable accounts for
  the difference and which does not, rather than leaving two tables side by side for the
  reader to reconcile.
- `selftest.sh` gains a fifth stage, built from the shape of the run that exposed this, which
  fails if a non-separating variable is presented as a gradient.

The distinction matters more than it looks. Both tables are arithmetically correct. Only one
of them supports the sentence a reader would write after seeing it.

## v0.1.3-sweep — 2026-08-31

**Retrieve with:** `git checkout v0.1.3-sweep`

The two checks the publication checklist requires and the harness could not perform.

**Claimed:** nothing. `v0.2-…` remains reserved for the first result.

`docs/provenance/PUBLICATION_CHECKLIST.md` requires, before any result is published, two
consecutive baseline runs whose p95 agree and more than one aggressor intensity. Three
exploratory runs had been made against a live runtime and neither condition had been met:
each round used one baseline and one intensity. Publishing on that basis would have
contradicted this repository's own stated standard, which is a worse position than having no
result — a reader who checks the checklist against the release finds the gap immediately.

- `scenarios/contention_{low,mid,high}.yaml`: the intensity sweep. The victim workload is
  byte-identical across all three and against `baseline.yaml`, so the sweep varies one thing.
  `contention.yaml` is retained as an alias of the mid point.
- `src/sweep.py`: reports baseline agreement and the degradation curve. Where the two
  baselines disagree by more than 1.25x it says the environment was too noisy for the
  measurement and disclaims the ratios, rather than leaving the quieter baseline available
  to be chosen. It also reports how many aggressor requests were actually served at each
  point, since a point whose aggressors were not served applied less pressure than its label.
- `selftest.sh` gains a fourth stage asserting that refusal. The baseline gate is the one
  most easily satisfied by picking the better of two runs; guarding it means that behaviour
  has to be removed deliberately rather than drifting away under a deadline.

Also: the victim workload across all scenarios is now 400 requests entering 2s after the
aggressors, rather than 200 entering after 15s. In the earlier configuration the aggressors
had drained before the victim arrived, so most victim requests met an empty pool and the
observed p95 difference had nothing in the captured metrics to attribute it to.

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
