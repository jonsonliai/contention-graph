#!/usr/bin/env python3
"""
otlp_file_sink.py — receive OTLP/HTTP traces and write them as JSON lines.

`src/collect.py --spans-from` reads OpenTelemetry Collector file-exporter output. Running a
full Collector for that is a hundred megabytes of binary and a YAML config, for a pipeline
whose only job is `otlp -> file`. This does the same thing in one standard-library HTTP
server plus the protobuf definitions that the OTLP exporter already depends on.

Being small matters here for a reason beyond convenience: this file is part of the
reproduction path for a published result, so a reader has to be able to check that it does
not transform what it received. It decodes the protobuf, converts the two identifier fields
to hex, and writes the result. Nothing else.

Usage:
    python3 tools/otlp_file_sink.py --out runs/contention/otel-spans.jsonl --port 4318

Point the runtime at it:
    export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf
    export OTEL_EXPORTER_OTLP_TRACES_INSECURE=true
    export OTEL_SERVICE_NAME=vllm-server
    vllm serve <model> --otlp-traces-endpoint http://127.0.0.1:4318/v1/traces

Requires: opentelemetry-proto, protobuf. Both are already pulled in by
opentelemetry-exporter-otlp, which the runtime needs in order to emit traces at all.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from google.protobuf.json_format import MessageToDict
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest, ExportTraceServiceResponse)
except ImportError:  # pragma: no cover - dependency check, exercised by the operator
    sys.stderr.write(
        "otlp_file_sink needs the OTLP protobuf definitions:\n"
        "    pip install opentelemetry-proto protobuf\n")
    raise SystemExit(2)


STATE = {"requests": 0, "spans": 0, "errors": 0}
LOCK = threading.Lock()
OUT = None          # file handle
VERBOSE = False


def _hexify_ids(d: dict) -> dict:
    """Render traceId/spanId as hex.

    protobuf's JSON mapping base64-encodes `bytes` fields, but the OTLP/JSON specification
    defines these two as lowercase hex. Anything downstream that compares an id against one
    copied out of a trace UI would otherwise silently fail to match.
    """
    for rs in d.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                for key in ("traceId", "spanId", "parentSpanId"):
                    v = sp.get(key)
                    if isinstance(v, str) and v:
                        try:
                            sp[key] = base64.b64decode(v).hex()
                        except Exception:  # noqa: BLE001 - leave malformed ids as received
                            pass
                for link in sp.get("links", []):
                    for key in ("traceId", "spanId"):
                        v = link.get(key)
                        if isinstance(v, str) and v:
                            try:
                                link[key] = base64.b64decode(v).hex()
                            except Exception:  # noqa: BLE001
                                pass
    return d


def _count_spans(d: dict) -> int:
    return sum(len(ss.get("spans", []))
               for rs in d.get("resourceSpans", [])
               for ss in rs.get("scopeSpans", []))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet; the counters are the useful signal
        pass

    def _respond(self, code: int, body: bytes = b"", ctype: str = "application/x-protobuf"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        # A liveness probe, so a runbook can wait for the sink before starting the runtime.
        if self.path in ("/healthz", "/"):
            with LOCK:
                body = json.dumps(dict(STATE)).encode()
            self._respond(200, body, "application/json")
        else:
            self._respond(404)

    def do_POST(self):
        if not self.path.endswith("/v1/traces"):
            self._respond(404)
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n)
            if self.headers.get("Content-Encoding", "").lower() == "gzip":
                raw = gzip.decompress(raw)
            msg = ExportTraceServiceRequest.FromString(raw)
            d = _hexify_ids(MessageToDict(msg))
            n_spans = _count_spans(d)
            line = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
            with LOCK:
                OUT.write(line + "\n")
                OUT.flush()          # a run that dies mid-way keeps what it received
                STATE["requests"] += 1
                STATE["spans"] += n_spans
            if VERBOSE:
                print(f"  +{n_spans} spans (total {STATE['spans']})", file=sys.stderr)
        except Exception as exc:      # noqa: BLE001 - never drop a batch silently
            with LOCK:
                STATE["errors"] += 1
            print(f"otlp_file_sink: failed to decode a batch: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            self._respond(400)
            return
        self._respond(200, ExportTraceServiceResponse().SerializeToString())


def main() -> int:
    global OUT, VERBOSE
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True, help="JSON-lines file to append spans to")
    p.add_argument("--port", type=int, default=4318)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    VERBOSE = args.verbose

    OUT = open(args.out, "a", encoding="utf-8")
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True

    def _bye(*_):
        with LOCK:
            print(f"otlp_file_sink: {STATE['spans']} spans in {STATE['requests']} batches, "
                  f"{STATE['errors']} errors -> {args.out}", file=sys.stderr)
            OUT.flush()
            OUT.close()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)

    print(f"otlp_file_sink listening on http://{args.host}:{args.port}/v1/traces "
          f"-> {args.out}", file=sys.stderr)
    try:
        srv.serve_forever()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        pass
    _bye()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
