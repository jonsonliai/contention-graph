# Changelog

Newest first. Each entry states what changed and what is claimed as a result of the change.
See `VERSIONING.md` for what the version numbers mean.

<!-- Add new entries directly below this line. -->

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
