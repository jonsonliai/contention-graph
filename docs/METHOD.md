# Experiment design

## Why this design

The claim is about telemetry, not about performance. So the design has to make the
*absence* of an attribute observable, which is harder than making a latency number move.

The structure is a controlled comparison. Two runs differ in one respect — whether an
aggressor workload is present. If victim TTFT rises in the second run, something caused it.
The question the experiment answers is whether anything in the victim's own telemetry lets a
reader determine what.

## Choice of pressure mechanism

KV-cache occupancy under long-context requests was chosen over the alternatives because it is
the cleanest to induce and the hardest to explain away:

- **Not raw concurrency.** Queueing delay under load is well modelled and well instrumented.
  A reviewer would correctly say the existing telemetry already accounts for it.
- **Not GPU compute saturation.** Utilisation metrics exist and are per-device, so a reader
  can at least see the resource is saturated.
- **Cache eviction is different.** The victim's blocks are reclaimed to serve someone else.
  The victim is not slow because the machine is busy; it is slow because a specific other
  request took something from it. That is an attribution question rather than a capacity
  question, and it is the class this work is about.

## Deliberate cache constraint

The runtime is started with `--gpu-memory-utilization` well below its default, in order to
reduce the size of the KV cache and induce, on a single GPU, the pressure that would otherwise
arise only under production multi-tenancy.

**This changes the conditions under which the phenomenon is triggered. It does not change the
phenomenon.** Eviction under cache pressure is the same event whether the cache is small
because it was constrained or small relative to the load it carries. What the constraint buys
is that the event occurs reliably within a run of minutes rather than sporadically over hours.

**What it costs is generality**, and the cost is stated rather than absorbed: a result obtained
under a constrained cache establishes that the attribution gap exists when eviction occurs. It
does not establish how often eviction occurs in production, and no claim to that effect is
made here. The published operator accounts at the introduction address frequency; this
experiment addresses attributability.

The value used is recorded in the provenance block of every result.


## Threats to validity

| Threat | Handling |
|---|---|
| Victim degradation caused by queueing, not eviction | Report runtime queue-depth series alongside; the aggressor rate is deliberately low relative to victim rate so that queue pressure is not the dominant term |
| Degradation is an artefact of client-side connection limits | `max_connections` set well above offered concurrency; client-side arrival timestamps recorded so client queueing is visible |
| Chosen runtime happens to lack instrumentation others have | H2 must be re-run on a second runtime before the result can be stated generally. A single-runtime result is a result about that runtime |
| Prompt content affects results | Filler text is deterministic and seeded with `crc32`, which is stable across processes; identical across runs |
| Cherry-picked intensity | `scenarios/contention_{low,mid,high}.yaml` and `src/sweep.py` report the full curve. The victim workload is identical at every point and in the baseline, so the sweep varies one thing |
| Environment drifted between baseline and contention | Two consecutive baselines are required and `src/sweep.py` compares their p95. Where they disagree it says so and disclaims the ratios rather than letting the quieter one be chosen |
| Aggressor requests rejected rather than served | Prompt length is sized against the runtime's `--max-model-len` before the run; `src/workload.py` records the HTTP status of every request and warns when an entire workload was rejected. A rejected aggressor applies no pressure while the run still completes and the metrics still look clean |
| Metrics sampled outside the load window | Collection runs concurrently with the workload; `src/collect.py` warns when every captured series was constant, which is what an idle-window scrape produces |
| Pressure exists but is invisible at the sampling interval | Cache occupancy under a constrained pool oscillates on a sub-second scale. A sampler slower than the oscillation reports a plausible mean and no variation, and the alignment then attributes nothing. `src/align.py` reports the spread of each candidate variable and refuses to bucket on one that did not move, rather than printing four identical labels |
| A variable correlates with part of the effect and is read as explaining all of it | `src/align.py` reports the within-bin spread of each variable's largest bin. Where that bin's p95 exceeds its p50 by more than 3x, the requests the variable groups as unpressured are not one population, and the table is annotated to say the gradient is not attributable to it. Observed on a real run: queue depth put 389 of 400 requests in one bin spanning 27ms to 348ms, while cache occupancy separated the same requests cleanly |
| Victim and aggressor windows do not overlap | The victim's arrival window must lie inside the aggressor's. A victim delayed until after the aggressors have drained is measured against an empty pool, and the resulting p95 difference — if any — is not attributable to occupancy |

## What a negative result would look like

If the runtime already emits a per-request attribute naming the preempting request, or if the
metrics endpoint exposes preemption events carrying a request id that joins to the victim,
then H2 or H3 is falsified and the premise of this work is substantially weaker. That result
is to be published here in the same form as a positive one. The value of the experiment is
that it can come out either way.

## Scope limits, stated plainly

Single GPU, single node, one runtime, one model, synthetic load. Nothing here establishes that
the gap persists at production scale, across multi-node topologies, or under real traffic
mixes. Those are the questions that require access to production environments, and they are
outside what an experiment on rented capacity can answer.
