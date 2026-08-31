#!/usr/bin/env python3
"""
contention_run.py — H1 experiment driver for the contention-graph study.

Runs an engine-side metrics sampler and a closed-loop workload generator inside
ONE process so both observation streams share a single time.monotonic_ns()
clock. Alignment between "symptom" (request latency) and "cause" (engine
scheduling state) is the whole point of the experiment, so the clock is not
allowed to be a source of error.

Outputs, both JSONL, one self-contained object per line:

    <outdir>/meta.json       run configuration + clock anchor + engine config
    <outdir>/engine.jsonl    fixed-period engine metric samples
    <outdir>/requests.jsonl  per-request lifecycle records

Design constraints encoded here, each from an observed failure:

  * Request bodies are built with json.dumps(). Never interpolate a payload
    into a shell string: base64 line wrapping silently produced bare newlines,
    every request returned HTTP 400, and the engine metrics stayed at zero for
    an hour while looking perfectly healthy.
  * Every response's HTTP status is recorded and non-2xx responses are counted
    in the run summary. Silently discarding errors makes a broken run look like
    an idle system, which is worse than a run that visibly fails.
  * The sampler records its own scheduling slip. A sampler that quietly drifts
    is indistinguishable from a system that has nothing to report.
  * TTFT requires streaming. A non-streaming client cannot observe time-to-
    first-token at all, only end-to-end latency.

Standard library only. No third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import string
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any

# --------------------------------------------------------------------------
# Engine metrics
# --------------------------------------------------------------------------

# Prometheus text format: NAME{label="v",...} VALUE
_SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?"
    r"[ \t]+(?P<value>[^ \t]+)[ \t]*$"
)
_LABEL_RE = re.compile(r'(?P<k>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<v>(?:[^"\\]|\\.)*)"')

# Metrics the H1 attribution depends on. Names differ across vLLM versions;
# both spellings of the KV gauge are accepted and normalised to kv_cache_usage_perc.
WANTED = {
    "vllm:num_requests_running": "num_requests_running",
    "vllm:num_requests_waiting": "num_requests_waiting",
    "vllm:num_requests_waiting_by_reason": "num_requests_waiting_by_reason",
    "vllm:num_requests_swapped": "num_requests_swapped",
    "vllm:kv_cache_usage_perc": "kv_cache_usage_perc",
    "vllm:gpu_cache_usage_perc": "kv_cache_usage_perc",  # pre-0.28 name
    "vllm:num_preemptions_total": "num_preemptions_total",
    "vllm:prompt_tokens_total": "prompt_tokens_total",
    "vllm:generation_tokens_total": "generation_tokens_total",
}

# Labels that identify the series rather than distinguish it. Everything else
# becomes a key suffix, so waiting_by_reason{reason="capacity"} is stored as
# "num_requests_waiting_by_reason.capacity".
IDENTITY_LABELS = {"engine", "model_name"}


def parse_labels(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    return {m.group("k"): m.group("v") for m in _LABEL_RE.finditer(raw)}


def parse_metrics(text: str) -> tuple[dict[str, float], list[str]]:
    """Return (flattened metrics, names seen but not wanted).

    The second element exists so a renamed metric shows up as a warning at the
    end of the run instead of as a column of zeros in the data.
    """
    out: dict[str, float] = {}
    seen_vllm: list[str] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _SAMPLE_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        if name.startswith("vllm:"):
            seen_vllm.append(name)
        key = WANTED.get(name)
        if key is None:
            continue
        try:
            value = float(m.group("value"))
        except ValueError:
            continue
        labels = parse_labels(m.group("labels"))
        suffix = ".".join(
            v for k, v in sorted(labels.items()) if k not in IDENTITY_LABELS
        )
        out[f"{key}.{suffix}" if suffix else key] = value
    return out, seen_vllm


def http_get(url: str, timeout: float) -> str:
    req = urllib.request.Request(url, headers={"Accept": "text/plain"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# JSONL writer
# --------------------------------------------------------------------------


class JsonlWriter:
    """Line-buffered, lock-guarded JSONL sink.

    Flushes every record: a run that dies mid-experiment should leave behind
    every sample it managed to take, not an empty file with a full OS buffer.
    """

    def __init__(self, path: str):
        self._fh = open(path, "w", encoding="utf-8")
        self._lock = threading.Lock()
        self.count = 0

    def write(self, obj: dict[str, Any]) -> None:
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
            self.count += 1

    def close(self) -> None:
        with self._lock:
            self._fh.close()


# --------------------------------------------------------------------------
# Collector
# --------------------------------------------------------------------------


class Collector(threading.Thread):
    def __init__(self, base_url: str, interval: float, out: JsonlWriter,
                 timeout: float, stop: threading.Event):
        super().__init__(name="collector", daemon=True)
        self.url = base_url.rstrip("/") + "/metrics"
        self.interval = interval
        self.out = out
        self.timeout = timeout
        self.stop = stop
        self.errors = 0
        self.slips = 0
        self.max_slip_ms = 0.0
        self.unknown_vllm_metrics: set[str] = set()
        self.known_hits: set[str] = set()

    def run(self) -> None:
        seq = 0
        next_due = time.monotonic()
        while not self.stop.is_set():
            t_ns = time.monotonic_ns()
            rec: dict[str, Any] = {"type": "engine", "seq": seq, "t_mono_ns": t_ns}
            try:
                metrics, seen = parse_metrics(http_get(self.url, self.timeout))
                rec["ok"] = True
                rec["m"] = metrics
                self.known_hits.update(metrics.keys())
                self.unknown_vllm_metrics.update(
                    n for n in seen if n not in WANTED
                )
            except Exception as exc:  # noqa: BLE001 - any failure must be recorded
                self.errors += 1
                rec["ok"] = False
                rec["error"] = f"{type(exc).__name__}: {exc}"
            self.out.write(rec)

            seq += 1
            next_due += self.interval
            delay = next_due - time.monotonic()
            if delay < 0:
                # Sampling could not keep up. Record it rather than drifting
                # silently, then resynchronise to now.
                slip_ms = -delay * 1000.0
                self.slips += 1
                self.max_slip_ms = max(self.max_slip_ms, slip_ms)
                self.out.write({
                    "type": "sampler_slip",
                    "seq": seq,
                    "t_mono_ns": time.monotonic_ns(),
                    "slip_ms": round(slip_ms, 3),
                })
                next_due = time.monotonic()
            else:
                self.stop.wait(delay)


# --------------------------------------------------------------------------
# Workload
# --------------------------------------------------------------------------

_WORD_ALPHABET = string.ascii_lowercase


def make_prompt(rng: random.Random, n_words: int) -> str:
    """Random lowercase words.

    Deliberately not base64 and not a fixed template: random words tokenise
    predictably, carry no shared prefix (so prefix caching cannot mask the KV
    constraint even if it is left on by accident), and contain no characters
    that need escaping. json.dumps would escape them anyway; this just makes
    the payload readable when a run is inspected by hand.
    """
    words = [
        "".join(rng.choice(_WORD_ALPHABET) for _ in range(rng.randint(3, 9)))
        for _ in range(n_words)
    ]
    return " ".join(words) + " Summarize the passage above:"


class WorkloadWorker(threading.Thread):
    def __init__(self, worker_id: int, cfg: argparse.Namespace, out: JsonlWriter,
                 stop: threading.Event, deadline: float, counters: dict[str, int],
                 counters_lock: threading.Lock):
        super().__init__(name=f"worker-{worker_id}", daemon=True)
        self.worker_id = worker_id
        self.cfg = cfg
        self.out = out
        self.stop = stop
        self.deadline = deadline
        self.counters = counters
        self.lock = counters_lock
        self.rng = random.Random(cfg.seed + worker_id)
        self.url = cfg.base_url.rstrip("/") + "/v1/completions"

    def _bump(self, key: str) -> None:
        with self.lock:
            self.counters[key] = self.counters.get(key, 0) + 1

    def run(self) -> None:
        n = 0
        while not self.stop.is_set() and time.monotonic() < self.deadline:
            self.issue(f"w{self.worker_id}-{n}")
            n += 1

    def issue(self, req_id: str) -> None:
        prompt = make_prompt(self.rng, self.cfg.prompt_words)
        payload = {
            "model": self.cfg.model,
            "prompt": prompt,
            "max_tokens": self.cfg.max_tokens,
            "temperature": 0.0,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # json.dumps, not string interpolation. This is the fix for the class of
        # bug that made every request a silent 400.
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )

        rec: dict[str, Any] = {
            "type": "request",
            "req_id": req_id,
            "worker": self.worker_id,
            "prompt_words": self.cfg.prompt_words,
            "max_tokens": self.cfg.max_tokens,
        }
        t_send = time.monotonic_ns()
        rec["t_send_ns"] = t_send
        t_first: int | None = None
        chunks = 0
        usage: dict[str, Any] | None = None

        try:
            with urllib.request.urlopen(req, timeout=self.cfg.request_timeout) as resp:
                rec["status"] = resp.status
                for raw in resp:
                    if not raw.startswith(b"data:"):
                        continue
                    payload_bytes = raw[5:].strip()
                    if payload_bytes == b"[DONE]":
                        break
                    if t_first is None:
                        t_first = time.monotonic_ns()
                    chunks += 1
                    if b'"usage"' in payload_bytes:
                        try:
                            obj = json.loads(payload_bytes)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(obj, dict) and obj.get("usage"):
                            usage = obj["usage"]
            rec["ok"] = 200 <= rec["status"] < 300
        except urllib.error.HTTPError as exc:
            rec["status"] = exc.code
            rec["ok"] = False
            try:
                rec["error_body"] = exc.read()[:400].decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                rec["error_body"] = None
        except Exception as exc:  # noqa: BLE001 - timeouts, resets, all recorded
            rec["status"] = None
            rec["ok"] = False
            rec["error"] = f"{type(exc).__name__}: {exc}"

        t_done = time.monotonic_ns()
        rec["t_done_ns"] = t_done
        rec["t_first_token_ns"] = t_first
        rec["n_chunks"] = chunks
        rec["e2e_ms"] = round((t_done - t_send) / 1e6, 3)
        rec["ttft_ms"] = round((t_first - t_send) / 1e6, 3) if t_first else None
        if t_first and chunks > 1:
            rec["tpot_ms"] = round((t_done - t_first) / 1e6 / (chunks - 1), 4)
        else:
            rec["tpot_ms"] = None
        if usage:
            rec["prompt_tokens"] = usage.get("prompt_tokens")
            rec["completion_tokens"] = usage.get("completion_tokens")

        self.out.write(rec)
        self._bump("total")
        self._bump("ok" if rec.get("ok") else "failed")
        status_key = f"status_{rec.get('status')}"
        self._bump(status_key)


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def fetch_engine_config(base_url: str, timeout: float) -> dict[str, Any] | None:
    """Capture vllm:cache_config_info, the engine's own account of its config.

    More trustworthy for the exhibit than a hand-written environment note: it
    is emitted by the runtime, not transcribed by the experimenter.
    """
    try:
        text = http_get(base_url.rstrip("/") + "/metrics", timeout)
    except Exception:  # noqa: BLE001
        return None
    for line in text.splitlines():
        if line.startswith("vllm:cache_config_info"):
            m = _SAMPLE_RE.match(line)
            if m:
                return parse_labels(m.group("labels"))
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--model", required=True)
    p.add_argument("--outdir", required=True)
    p.add_argument("--concurrency", type=int, default=32,
                   help="closed-loop: this many requests in flight at all times")
    p.add_argument("--duration", type=float, default=180.0, help="seconds")
    p.add_argument("--sample-interval", type=float, default=0.25, help="seconds")
    p.add_argument("--max-tokens", type=int, default=2048)
    p.add_argument("--prompt-words", type=int, default=400)
    p.add_argument("--request-timeout", type=float, default=600.0)
    p.add_argument("--metrics-timeout", type=float, default=5.0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--note", default="", help="free-text label stored in meta.json")
    cfg = p.parse_args()

    os.makedirs(cfg.outdir, exist_ok=True)
    engine_out = JsonlWriter(os.path.join(cfg.outdir, "engine.jsonl"))
    req_out = JsonlWriter(os.path.join(cfg.outdir, "requests.jsonl"))

    # Single clock anchor: every other timestamp in this run is monotonic_ns,
    # and this pair is the only mapping to wall time.
    anchor = {
        "t_mono_ns": time.monotonic_ns(),
        "t_wall_unix": time.time(),
        "t_wall_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    engine_config = fetch_engine_config(cfg.base_url, cfg.metrics_timeout)

    meta = {
        "schema": "contention-graph/run-meta/1",
        "config": vars(cfg),
        "clock_anchor": anchor,
        "engine_cache_config": engine_config,
        "python": sys.version.split()[0],
    }
    with open(os.path.join(cfg.outdir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)

    if engine_config is None:
        print("WARNING: could not read vllm:cache_config_info; "
              "engine configuration will not be self-documented.", file=sys.stderr)

    stop = threading.Event()
    collector = Collector(cfg.base_url, cfg.sample_interval, engine_out,
                          cfg.metrics_timeout, stop)
    collector.start()

    counters: dict[str, int] = {}
    counters_lock = threading.Lock()
    deadline = time.monotonic() + cfg.duration
    workers = [
        WorkloadWorker(i, cfg, req_out, stop, deadline, counters, counters_lock)
        for i in range(cfg.concurrency)
    ]

    print(f"run: concurrency={cfg.concurrency} duration={cfg.duration}s "
          f"sample_interval={cfg.sample_interval}s -> {cfg.outdir}", file=sys.stderr)
    for w in workers:
        w.start()

    try:
        while any(w.is_alive() for w in workers):
            for w in workers:
                w.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\ninterrupted; draining in-flight requests", file=sys.stderr)
        stop.set()
        for w in workers:
            w.join(timeout=cfg.request_timeout)

    # Let the sampler take a few post-load samples so the recovery edge is
    # captured, then shut it down.
    time.sleep(min(3.0, cfg.sample_interval * 8))
    stop.set()
    collector.join(timeout=10.0)

    engine_out.close()
    req_out.close()

    total = counters.get("total", 0)
    failed = counters.get("failed", 0)
    summary = {
        "requests_total": total,
        "requests_failed": failed,
        "engine_samples": engine_out.count,
        "collector_errors": collector.errors,
        "sampler_slips": collector.slips,
        "sampler_max_slip_ms": round(collector.max_slip_ms, 3),
        "status_counts": {k: v for k, v in sorted(counters.items())
                          if k.startswith("status_")},
        "metrics_captured": sorted(collector.known_hits),
        "vllm_metrics_present_but_unused": sorted(collector.unknown_vllm_metrics)[:40],
    }
    with open(os.path.join(cfg.outdir, "run_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)

    print(json.dumps({k: summary[k] for k in (
        "requests_total", "requests_failed", "engine_samples",
        "collector_errors", "sampler_slips", "status_counts")}, indent=2),
        file=sys.stderr)

    if failed:
        print(f"WARNING: {failed}/{total} requests did not return 2xx. "
              f"See requests.jsonl (error_body field).", file=sys.stderr)
    if not collector.known_hits:
        print("ERROR: no engine metrics were captured at all. Check metric "
              "names against /metrics before trusting this run.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
