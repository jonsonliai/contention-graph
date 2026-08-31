"""
Victim and aggressor workload generation against an OpenAI-compatible endpoint.

Victim:    short prompts, short outputs, latency-sensitive. The class whose TTFT we watch.
Aggressor: long context, long outputs. Constructed to occupy KV-cache blocks for a long time,
           which is the pressure the victim is meant to feel.

Every request is tagged with a client-side request id and its wall-clock arrival, first-token,
and completion times are recorded. Those three timestamps are the only thing the client can
see; the point of the experiment is what they cannot tell you.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import random
import string
import time
import zlib
from dataclasses import dataclass, asdict
from pathlib import Path

import yaml  # pyyaml
import httpx


@dataclass
class RequestRecord:
    request_id: str
    workload: str            # "victim" | "aggressor"
    t_arrival: float
    t_first_token: float | None
    t_complete: float | None
    prompt_tokens_approx: int
    max_tokens: int
    ttft_ms: float | None
    total_ms: float | None
    status: int | None = None
    error: str | None = None
    error_body: str | None = None


def _seed_for(wl: str, idx: int) -> int:
    """Stable across processes.

    `hash()` on a tuple containing a string is salted by PYTHONHASHSEED and therefore differs
    between interpreter invocations. Using it here meant the baseline run and the contention
    run were driven by different prompt text, which contradicts the control stated in
    `docs/METHOD.md` ("filler text is deterministic and seeded; identical across runs").
    crc32 is stable by definition.
    """
    return zlib.crc32(f"{wl}-{idx}".encode()) & 0xFFFF


def _filler(n_words: int, seed: int) -> str:
    """Deterministic pseudo-text, seeded per (workload, index).

    Determinism matters for a reason beyond reproducibility: prefix caching. If prompts
    shared a common prefix, the runtime could serve them from cached blocks and the cache
    pressure the experiment is trying to induce would not materialise. Random vocabulary per
    request keeps prompts distinct while holding token count constant across runs.
    """
    rng = random.Random(seed)
    vocab = ["".join(rng.choices(string.ascii_lowercase, k=rng.randint(3, 9)))
             for _ in range(400)]
    return " ".join(rng.choice(vocab) for _ in range(n_words))


async def _one(client: httpx.AsyncClient, cfg: dict, wl: str, idx: int,
               t0: float, out: list[RequestRecord]) -> None:
    """Issue one request and record the three timestamps a client can observe.

    Streaming is required, not a preference: without it there is no first-token event and
    TTFT cannot be separated from total latency. That separation is the whole measurement \u2014
    cache eviction shows up in time to first token, not in throughput after generation has
    begun.

    Failures are recorded on the record rather than raised, so that a run which loses some
    requests still produces a usable distribution and the loss is visible in the output.
    """
    spec = cfg["workloads"][wl]
    rid = f"{wl}-{idx:05d}"
    prompt = _filler(spec["prompt_words"], seed=_seed_for(wl, idx))

    body = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": spec["max_tokens"],
        "temperature": 0.0,
        "stream": True,
    }
    # Propagate the client-side id so server-side records can be joined to it.
    headers = {"X-Request-Id": rid}

    rec = RequestRecord(
        request_id=rid, workload=wl, t_arrival=time.monotonic() - t0,
        t_first_token=None, t_complete=None,
        prompt_tokens_approx=spec["prompt_words"],
        max_tokens=spec["max_tokens"], ttft_ms=None, total_ms=None,
    )
    try:
        async with client.stream("POST", cfg["endpoint"], json=body,
                                 headers=headers, timeout=cfg.get("timeout_s", 300)) as r:
            rec.status = r.status_code
            if r.status_code >= 400:
                # Read the body before raising. A rejected request says why it was
                # rejected, and that sentence is usually the whole diagnosis; without it
                # a run of uniformly failing requests looks indistinguishable from an
                # idle server.
                raw = await r.aread()
                rec.error_body = raw[:400].decode("utf-8", "replace")
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data: ") or line.endswith("[DONE]"):
                    continue
                if rec.t_first_token is None:
                    rec.t_first_token = time.monotonic() - t0
                    rec.ttft_ms = (rec.t_first_token - rec.t_arrival) * 1000
        rec.t_complete = time.monotonic() - t0
        rec.total_ms = (rec.t_complete - rec.t_arrival) * 1000
    except Exception as exc:                      # noqa: BLE001 - recorded, not raised
        rec.error = f"{type(exc).__name__}: {exc}"
    out.append(rec)


async def _drive(cfg: dict, out: list[RequestRecord]) -> dict:
    """Returns the clock anchor: `t_arrival` and friends are offsets from `t0`.

    Both clocks are read at the same instant so that request timings can be placed on the
    same monotonic axis as the metric samples in `src/collect.py`. Without a shared axis the
    two observation streams cannot be joined, and the join is what turns "the victim was slow"
    into "the victim was slow while the pool was full".
    """
    t0 = time.monotonic()
    anchor = {"t0_mono": t0, "t0_wall": time.time()}
    limits = httpx.Limits(max_connections=cfg.get("max_connections", 128))
    async with httpx.AsyncClient(limits=limits) as client:
        tasks: list[asyncio.Task] = []
        for wl, spec in cfg["workloads"].items():
            if spec.get("count", 0) <= 0:
                continue

            async def run(wl=wl, spec=spec) -> None:
                await asyncio.sleep(spec.get("start_delay_s", 0.0))
                gap = 1.0 / spec["rate_per_s"] if spec.get("rate_per_s") else 0.0
                for i in range(spec["count"]):
                    tasks.append(asyncio.create_task(_one(client, cfg, wl, i, t0, out)))
                    if gap:
                        await asyncio.sleep(gap)

            tasks.append(asyncio.create_task(run()))
        # settle: wait until no new tasks are being created and all have finished
        while True:
            pending = [t for t in tasks if not t.done()]
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)
    return anchor


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.scenario).read_text())
    cfg.setdefault("endpoint", os.environ.get(
        "INFERENCE_ENDPOINT", "http://localhost:8000/v1/chat/completions"))
    # Env overrides so the same scenario file can be pointed at whatever is actually
    # serving, without editing the file and thereby changing what a result was produced
    # from. The effective values are recorded in requests.json under "scenario".
    if os.environ.get("INFERENCE_ENDPOINT"):
        cfg["endpoint"] = os.environ["INFERENCE_ENDPOINT"]
    if os.environ.get("INFERENCE_MODEL"):
        cfg["model"] = os.environ["INFERENCE_MODEL"]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[RequestRecord] = []
    t_run_start = time.time()
    anchor = asyncio.run(_drive(cfg, records))
    t_run_end = time.time()

    (out_dir / "requests.json").write_text(json.dumps(
        {
            "scenario": cfg,
            "t_run_start_epoch": t_run_start,
            "t_run_end_epoch": t_run_end,
            "clock_anchor": anchor,
            "records": [asdict(r) for r in records],
        }, indent=2))

    ok = [r for r in records if r.ttft_ms is not None]
    failed = [r for r in records if r.ttft_ms is None]
    print(f"{len(records)} requests, {len(ok)} with a first token, "
          f"{len(failed)} failed -> {out_dir}/requests.json")

    # Status distribution, always. A run in which every request was rejected produces
    # perfectly clean metrics and an empty finding; the only way to notice is to look at
    # what the server actually returned.
    if failed:
        by_status = collections.Counter(r.status for r in failed)
        print("  FAILED BY STATUS: " + ", ".join(
            f"{k if k is not None else 'no-response'}={v}" for k, v in sorted(
                by_status.items(), key=lambda kv: (kv[0] is None, kv[0]))))
        first = next((r for r in failed if r.error or r.error_body), None)
        if first is not None:
            # The exception text carries the status reason phrase, which is usually the
            # actionable part; the body is shown too but is often boilerplate.
            print("  first failure: " + " ".join(str(first.error or "").split())[:220])
            if first.error_body and "<html" not in first.error_body.lower():
                print("    body: " + " ".join(first.error_body.split())[:220])
        by_wl = collections.Counter(r.workload for r in failed)
        for wl, n in sorted(by_wl.items()):
            total = sum(1 for r in records if r.workload == wl)
            if n == total:
                print(f"  WARNING: every '{wl}' request failed. That workload applied no "
                      f"pressure; any contention result from this run is void.")

    print("Capture the server side DURING the run, not after: see README. "
          "A metrics scrape started once the workload has finished samples an idle server.")


if __name__ == "__main__":
    main()
