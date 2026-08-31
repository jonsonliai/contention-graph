"""
The proposed instrumentation, reduced to its minimum.

The call graph answers: what did this request do?
The contention graph answers: what else held the resources this request needed, and when?

These are two different graphs over the same events, and the second one is not currently
recorded by anything. This module records it and performs the join that attribution requires.
"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field, asdict
from typing import Iterable


# --------------------------------------------------------------------------- records

@dataclass(frozen=True)
class Residency:
    """One request's occupancy of one bounded resource over one interval.

    This is the record that does not currently exist. Everything else here is
    bookkeeping over it.

    resource_id identifies the *contended thing*, not the machine: a KV-cache pool,
    a batch slot pool, an accelerator's memory. Two requests with the same resource_id
    and overlapping intervals were in contention, whether or not either observed it.
    """
    request_id: str
    resource_id: str
    t_start: float          # seconds, monotonic clock shared across the collector
    t_end: float
    units: float            # blocks, bytes, slots — resource-defined
    peak_units: float = 0.0

    def overlaps(self, other: "Residency") -> bool:
        return (
            self.resource_id == other.resource_id
            and self.t_start < other.t_end
            and other.t_start < self.t_end
        )

    def overlap_seconds(self, other: "Residency") -> float:
        if not self.overlaps(other):
            return 0.0
        return min(self.t_end, other.t_end) - max(self.t_start, other.t_start)


@dataclass(frozen=True)
class PressureEvent:
    """An eviction, preemption, or recomputation.

    Note what this record does *not* have and why that is the whole problem: the runtime
    knows the victim (it preempted it) but the victim's own span never learns of this event.
    `attributed_to` is left empty by the runtime and filled in by the join below.
    """
    resource_id: str
    t: float
    kind: str               # "evict" | "preempt" | "recompute"
    victim_request_id: str
    units_reclaimed: float
    attributed_to: tuple[str, ...] = ()


# --------------------------------------------------------------------------- the graph

class ContentionGraph:
    """Residency records plus pressure events, with the attribution join over both.

    The `provenance` field is not decoration. A graph built from residency records a runtime
    actually emitted is evidence for H4; a graph reconstructed from client-side request
    timings is not, because the reconstruction assumes what H4 is supposed to test \u2014 that
    occupancy intervals are known. Carrying the distinction on the object means the analysis
    cannot silently report an approximation as a result. See `analyze.h4`.
    """

    #: "runtime"       \u2014 residency emitted by an instrumented runtime. Evidence for H4.
    #: "reconstructed" \u2014 approximated from client timings. NOT evidence for H4.
    #: "unknown"       \u2014 loaded from disk without a recorded provenance.
    def __init__(self, provenance: str = "unknown") -> None:
        self.provenance = provenance
        self._res: dict[str, list[Residency]] = {}
        self._events: list[PressureEvent] = []

    # -- ingest ------------------------------------------------------------

    def add_residency(self, r: Residency) -> None:
        self._res.setdefault(r.resource_id, []).append(r)

    def add_event(self, e: PressureEvent) -> None:
        self._events.append(e)

    def finalize(self) -> None:
        """Sort by start time so interval queries can binary-search."""
        for rs in self._res.values():
            rs.sort(key=lambda r: r.t_start)
        self._events.sort(key=lambda e: e.t)

    # -- queries -----------------------------------------------------------

    def co_resident(self, r: Residency) -> list[Residency]:
        """Every other request that held the same resource while `r` did."""
        rs = self._res.get(r.resource_id, [])
        starts = [x.t_start for x in rs]
        # candidates start before r ends; filter those that also end after r starts
        hi = bisect.bisect_left(starts, r.t_end)
        return [
            x for x in rs[:hi]
            if x.request_id != r.request_id and x.t_end > r.t_start
        ]

    def pressure_during(self, r: Residency) -> list[PressureEvent]:
        return [
            e for e in self._events
            if e.resource_id == r.resource_id and r.t_start <= e.t <= r.t_end
        ]

    # -- the join ----------------------------------------------------------

    def attribute(self, victim_request_id: str, min_share: float = 0.10) -> dict:
        """Attribute a victim's degradation to co-resident consumers.

        Returns the candidates ranked by occupancy-seconds — units held multiplied by
        seconds of overlap — which is the quantity a contention account needs and which
        no per-request trace can produce, because it is a property of a *pair* of requests
        rather than of either one.

        `min_share` suppresses candidates below a share of total contended occupancy.
        Ranking is not causation: this identifies who was there and how much they held.
        Establishing that the holding caused the degradation requires the controlled
        comparison in `analyze.py`, not this function.
        """
        victims = [
            r for rs in self._res.values() for r in rs
            if r.request_id == victim_request_id
        ]
        if not victims:
            return {"request_id": victim_request_id, "error": "no residency recorded"}

        weights: dict[str, float] = {}
        events: list[PressureEvent] = []

        for v in victims:
            for other in self.co_resident(v):
                w = other.overlap_seconds(v) * max(other.units, other.peak_units)
                weights[other.request_id] = weights.get(other.request_id, 0.0) + w
            events.extend(self.pressure_during(v))

        total = sum(weights.values())
        ordered = sorted(weights.items(), key=lambda kv: -kv[1])

        # Report the ranked list regardless of the threshold. A threshold that silently
        # empties the result when many similar consumers are present would hide the very
        # case the method has to handle: diffuse contention with no single dominant party.
        ranked = [
            {
                "request_id": rid,
                "occupancy_seconds": round(w, 4),
                "share": round(w / total, 4) if total else 0.0,
                "above_threshold": bool(total and (w / total) >= min_share),
            }
            for rid, w in ordered[:20]
        ]

        # Contention is frequently collective rather than attributable to one request.
        # Grouping by the prefix of the request id gives a class-level account, which is
        # what an operator can act on when no single consumer dominates.
        by_class: dict[str, float] = {}
        for rid, w in weights.items():
            by_class[rid.rsplit("-", 1)[0]] = by_class.get(rid.rsplit("-", 1)[0], 0.0) + w
        classes = [
            {"class": c, "occupancy_seconds": round(w, 4),
             "share": round(w / total, 4) if total else 0.0}
            for c, w in sorted(by_class.items(), key=lambda kv: -kv[1])
        ]

        return {
            "request_id": victim_request_id,
            "co_resident_candidates": ranked,
            "co_resident_classes": classes,
            "co_resident_count": len(weights),
            "total_occupancy_seconds": round(total, 4),
            "pressure_events_during_residency": [asdict(e) for e in events],
            "resources": sorted({v.resource_id for v in victims}),
        }

    # -- io ----------------------------------------------------------------

    def dump(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump(
                {
                    # Written first so that a human opening the file sees it before the data.
                    "provenance": self.provenance,
                    "residencies": [asdict(r) for rs in self._res.values() for r in rs],
                    "events": [asdict(e) for e in self._events],
                },
                fh,
                indent=2,
            )

    @classmethod
    def load(cls, path: str) -> "ContentionGraph":
        with open(path) as fh:
            d = json.load(fh)
        # Absent provenance is treated as unknown rather than assumed to be runtime-emitted.
        # The conservative default matters: an unlabelled graph must not be able to produce a
        # positive H4 verdict by omission.
        g = cls(provenance=d.get("provenance", "unknown"))
        for r in d.get("residencies", []):
            g.add_residency(Residency(**r))
        for e in d.get("events", []):
            e = dict(e)
            e["attributed_to"] = tuple(e.get("attributed_to", ()))
            g.add_event(PressureEvent(**e))
        g.finalize()
        return g


# --------------------------------------------------------------------------- notes

PROPOSED_ATTRIBUTES = {
    # What an OTEP would propose adding. Names are illustrative; the namespace and
    # the stability level are exactly what a proposal has to argue for.
    "inference.queue.residency_ms": "arrival to batch admission, distinct from TTFT",
    "inference.batch.id": "the batch in which the request executed",
    "inference.batch.co_resident_count": "how many requests shared that batch",
    "inference.resource.id": "the bounded resource occupied",
    "inference.resource.units_held": "occupancy in resource-defined units",
    "inference.resource.residency_ms": "duration of occupancy",
    "inference.pressure.event": "evict | preempt | recompute, as a span event",
    "inference.pressure.units_reclaimed": "how much was reclaimed",
}
