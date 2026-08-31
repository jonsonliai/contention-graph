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
import json
import os
import random
import string
import time
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
    error: str | None = None


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
    prompt = _filler(spec["prompt_words"], seed=hash((wl, idx)) & 0xFFFF)

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


async def _drive(cfg: dict, out: list[RequestRecord]) -> None:
    t0 = time.monotonic()
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.scenario).read_text())
    cfg.setdefault("endpoint", os.environ.get(
        "INFERENCE_ENDPOINT", "http://localhost:8000/v1/chat/completions"))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[RequestRecord] = []
    t_run_start = time.time()
    asyncio.run(_drive(cfg, records))
    t_run_end = time.time()

    (out_dir / "requests.json").write_text(json.dumps(
        {
            "scenario": cfg,
            "t_run_start_epoch": t_run_start,
            "t_run_end_epoch": t_run_end,
            "records": [asdict(r) for r in records],
        }, indent=2))

    ok = [r for r in records if r.ttft_ms is not None]
    print(f"{len(records)} requests, {len(ok)} with a first token, "
          f"{len(records) - len(ok)} failed -> {out_dir}/requests.json")
    print("Now capture the server side for the same window: "
          "python -m src.collect --out %s --since %.0f --until %.0f"
          % (args.out, t_run_start, t_run_end))


if __name__ == "__main__":
    main()
