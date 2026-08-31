#!/usr/bin/env python3
"""
mock_vllm.py — a fake vLLM endpoint for testing contention_run.py off-GPU.

Serves just enough to exercise the collector end to end:
  GET  /metrics          Prometheus text, including vllm:cache_config_info
  POST /v1/completions   SSE stream, with an admission queue that saturates

It simulates a fixed KV budget: only `--slots` requests generate at a time,
the rest wait. That makes TTFT rise with queue depth, so the H1 bucket table in
join_h1.py has something real to find. It is a test fixture, not a simulator —
never use its numbers as data.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {
    "running": 0,
    "waiting": 0,
    "preemptions": 0.0,
    "kv": 0.0,
    "prompt_tokens": 0.0,
    "gen_tokens": 0.0,
}
LOCK = threading.Lock()
SLOTS: threading.Semaphore
CFG: argparse.Namespace
MODEL = "mock/model"


def bump(key: str, delta: float) -> None:
    with LOCK:
        STATE[key] += delta


def metrics_text() -> str:
    with LOCK:
        s = dict(STATE)
    lab = f'engine="0",model_name="{MODEL}"'
    lines = [
        "# HELP vllm:num_requests_running Number of requests currently running.",
        "# TYPE vllm:num_requests_running gauge",
        f"vllm:num_requests_running{{{lab}}} {s['running']:.1f}",
        "# HELP vllm:num_requests_waiting Number of requests waiting.",
        "# TYPE vllm:num_requests_waiting gauge",
        f"vllm:num_requests_waiting{{{lab}}} {s['waiting']:.1f}",
        "# TYPE vllm:num_requests_waiting_by_reason gauge",
        f'vllm:num_requests_waiting_by_reason{{{lab},reason="capacity"}} {s["waiting"]:.1f}',
        f'vllm:num_requests_waiting_by_reason{{{lab},reason="deferred"}} 0.0',
        "# TYPE vllm:kv_cache_usage_perc gauge",
        f"vllm:kv_cache_usage_perc{{{lab}}} {s['kv']:.6f}",
        "# TYPE vllm:num_preemptions_total counter",
        f"vllm:num_preemptions_total{{{lab}}} {s['preemptions']:.1f}",
        f"vllm:num_preemptions_created{{{lab}}} 1.788184174081528e+09",
        "# TYPE vllm:prompt_tokens_total counter",
        f"vllm:prompt_tokens_total{{{lab}}} {s['prompt_tokens']:.1f}",
        "# TYPE vllm:generation_tokens_total counter",
        f"vllm:generation_tokens_total{{{lab}}} {s['gen_tokens']:.1f}",
        "# TYPE vllm:cache_config_info gauge",
        f'vllm:cache_config_info{{block_size="16",enable_prefix_caching="False",'
        f'gpu_memory_utilization="0.35",kv_cache_size_tokens="8192",'
        f'num_gpu_blocks="512",num_gpu_blocks_override="512"}} 1.0',
        "# TYPE vllm:some_metric_we_do_not_use gauge",
        f"vllm:some_metric_we_do_not_use{{{lab}}} 42.0",
    ]
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        body = metrics_text().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in ("/v1/completions", "/v1/chat/completions"):
            self.send_error(404)
            return
        chat = self.path.endswith("/chat/completions")
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.send_error(400, f"JSON decode error: {exc}")
            return

        n_tokens = min(int(req.get("max_tokens", 16)), CFG.max_tokens_cap)
        if chat:
            text = " ".join(m.get("content", "") for m in req.get("messages", []))
        else:
            text = req.get("prompt", "")
        prompt_len = len(text) // 4

        # Reject prompts that would not fit the simulated context window, the way a real
        # runtime does. The scenarios have to be sized against this, and a self-test that
        # accepted anything would not catch an oversized prompt before GPU time was spent.
        if prompt_len > CFG.max_model_len:
            self.send_error(400, f"prompt is too long: {prompt_len} tokens > "
                                 f"max_model_len {CFG.max_model_len}")
            return

        bump("waiting", 1)
        acquired = SLOTS.acquire(timeout=CFG.admission_timeout)
        bump("waiting", -1)
        if not acquired:
            self.send_error(503, "no capacity")
            return
        bump("running", 1)
        with LOCK:
            STATE["kv"] = min(1.0, STATE["running"] / CFG.slots * random.uniform(.75, .98))
            if STATE["waiting"] > CFG.slots:
                STATE["preemptions"] += random.choice([0, 0, 1])

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            for i in range(n_tokens):
                time.sleep(CFG.token_ms / 1000.0)
                if chat:
                    chunk = {
                        "id": "chatcmpl-mock", "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {"content": f" t{i}"},
                                     "finish_reason": None}],
                    }
                else:
                    chunk = {
                        "id": "cmpl-mock", "object": "text_completion",
                        "choices": [{"index": 0, "text": f" t{i}", "finish_reason": None}],
                    }
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                self.wfile.flush()
            usage = {
                "id": "cmpl-mock", "object": "text_completion", "choices": [],
                "usage": {"prompt_tokens": prompt_len,
                          "completion_tokens": n_tokens,
                          "total_tokens": prompt_len + n_tokens},
            }
            self.wfile.write(b"data: " + json.dumps(usage).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            bump("prompt_tokens", prompt_len)
            bump("gen_tokens", n_tokens)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            bump("running", -1)
            with LOCK:
                STATE["kv"] = min(1.0, max(0.0, STATE["running"] / CFG.slots * 0.9))
            SLOTS.release()


def main() -> int:
    global SLOTS, CFG, MODEL
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--slots", type=int, default=3,
                   help="concurrent generations allowed; the simulated KV budget")
    p.add_argument("--token-ms", type=float, default=4.0)
    p.add_argument("--max-tokens-cap", type=int, default=64)
    p.add_argument("--admission-timeout", type=float, default=120.0)
    p.add_argument("--max-model-len", type=int, default=8192,
                   help="reject prompts longer than this, as a real runtime would")
    p.add_argument("--model", default="mock/model")
    CFG = p.parse_args()
    MODEL = CFG.model
    SLOTS = threading.Semaphore(CFG.slots)
    srv = ThreadingHTTPServer(("127.0.0.1", CFG.port), Handler)
    srv.daemon_threads = True
    print(f"mock vllm on :{CFG.port} slots={CFG.slots}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
