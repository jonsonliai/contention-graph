
# **The Contention Graph**

## **Attributing degradation to co-resident causes in LLM inference serving**

| | |
|--------------------|------------------------------------------------------------------|
| **Author** | LI CHENG (Jonson Li) |
| **Version** | 0.1 — draft for technical review |
| **Date** | [DATE] |
| **Repository** | [URL] · tag [TAG] · commit [HASH] |
| **Licence** | Text CC BY 4.0; reference implementation Apache-2.0 |

---

### ***Abstract***

Distributed tracing models an execution as a tree of spans rooted at a request, and attributes
elapsed time to the work performed within that tree. The model presumes that a request's
latency is a function of its own path. In a multi-tenant, continuously batched inference
service that presumption is false: a request's latency is substantially determined by which
*other* requests held the accelerator's memory, the key-value cache, and the batch slots over
the interval in which it executed. Those requests are not on its path and appear nowhere in
its trace.

This paper argues that the missing information is structurally missing rather than
insufficiently sampled, and that recovering it requires a second graph maintained alongside
the call graph: a **contention graph** recording which request occupied which bounded resource
over which interval. It gives a definition, a join procedure over the two graphs, the
instrumentation the runtime must emit, an evaluation design with stated falsification
conditions, and the limits of what the approach can establish.

**What this paper does not claim.** It does not report that the approach has been validated at
production scale; it has not. It does not claim that overlap-weighted occupancy establishes
causation; it does not, and Section 5.3 says why. The contribution offered here is a
formulation and a testable evaluation, not a result.

**Figures.** Figure 2 is the shortest statement of the problem and Figure 1 of the proposal; a
reader with little time should look at those two.

---

# **1. Detection and attribution are different problems**

Modern telemetry detects that an inference service has degraded within seconds. Establishing
*what* produced the degradation, when the symptom is separated from the cause by several
layers of abstraction, is a different problem and is not solved.

The gap has an operational cost that the field has published on itself. Analysis of
high-severity production inference incidents at a hyperscale operator reports a mean time to
mitigation on the order of forty-nine hours. A substantial proportion of those incidents are
closed by restart, rebalancing, or added capacity — that is, service is restored and no cause
is established. In the adjacent training domain, operators of platforms exceeding 200,000
accelerators have published the decision to prioritise rapid isolation over precise
localisation, over-evicting suspect machines rather than pursuing exact root causes, on the
stated ground that exact fault pinpointing at that scale leaves large numbers of accelerators
idle.

Read carefully, that is not a statement about hardware reliability. It is a statement about the
**cost of diagnosis** exceeding the cost of over-provisioning. When that inequality holds, the
rational engineering choice is to stop diagnosing — and the field has documented itself making
that choice.

## **1.1 Scope**

This paper concerns inference serving, not training, and within inference it concerns causes
that arise from **contention for bounded resources shared between concurrent requests**:

- key-value cache block residency and eviction under multi-tenant pressure
- batch tenancy interference: composition of a continuous batch affecting its members
- accelerator memory pressure from a co-resident workload
- scheduler admission and preemption decisions
- interconnect contention where the serving topology spans devices

It does not concern model architecture, training dynamics, quantisation, or numerical methods.
The failure classes above are properties of how inference is *served*, not of what is being
served.

---

# **2. Why per-request tracing cannot express contention**

This section states the argument the rest of the paper depends on. If it is wrong, the rest
does not follow.

## **2.1 The model tracing implements**

Distributed tracing in the lineage of Dapper models an execution as a directed acyclic graph of
spans. A span records an interval and a causal relation to a parent. The elapsed time of a
request is decomposed into the spans beneath it, and the analytical operations the model
supports — critical path analysis, span-level breakdowns, latency attribution by service — all
operate within that tree.

Write it as a function. For a request `r` with span tree `S(r)`, the model presumes

```
latency(r) = f( work(s) : s in S(r) )
```

Latency is a function of the work performed on this request's own path. Everything a trace
records is a term in that function.

## **2.2 What contention does to it**

Under contention for a bounded resource `q` of capacity `C(q)`, the elapsed time of a span is
not determined by its own work alone. Let `occ(q, I)` be the occupancy of `q` over interval `I`
by all consumers. Then

```
latency(r) = f( work(s), occ(q, I(s)) : s in S(r) )
                         ^^^^^^^^^^^^
                         ranges over { r' in R : r' != r }
                         — requests outside S(r) entirely
```

**The second argument has no representation in the span tree.** This is the whole of the
problem, and three consequences follow.

**Finer decomposition does not help.** Splitting a span into ten sub-spans redistributes the
elapsed time within `S(r)`; it does not introduce a term for `r'`. A perfectly instrumented
request path still records that the request waited, not what it waited behind.

**The victim is the one instrumented, and the victim is the one that cannot see.** An eviction
happens *to* a request, not *in* it. The evicting party performs an ordinary successful
operation and its own trace shows nothing unusual. The information is distributed such that no
participant's trace contains it.

![](docs/figures/fig2_timeline.png)

**Figure 2.** The same event from two vantage points. Above the line, what happened. Below it,
what the victim's span records. The eviction is the cause of the elevated time to first token
and appears nowhere in the record of the request it degraded.

![](docs/figures/fig3_who_sees_what.png)

**Figure 3.** Why no single source answers the question. The runtime knows preemption occurred
but not for whom, in a form that can be joined; the victim knows it was slow but not why; the
aggressor's record is unremarkable. The information exists and is fragmented.

**Timestamp correlation is not sufficient.** One might join concurrent requests by wall-clock
overlap. That fails on three counts:

| | Why overlap alone is not co-residency |
|---|---|
| **Identity** | Two requests concurrent in time may share nothing — different devices, different cache pools, different replicas. Without resource identity, overlap is not contention |
| **Magnitude** | A request holding four cache blocks and one holding four thousand are not equivalent co-residents. Presence is not occupancy |
| **Granularity** | Residency changes during a request's lifetime as blocks are allocated and reclaimed. Request-level start and end times are too coarse to express it |

## **2.3 The assumption is old and it is not specific to inference**

The independence assumption in per-request instrumentation is not an artefact of GenAI
tooling. It is inherited. The same structure appears wherever a bounded resource is shared:

| Layer | Bounded resource | What the victim's record shows | What it omits |
|---|---|---|---|
| Storage metadata service | Lock and lease state at a master | The operation was slow | Which clients held what while it waited |
| Virtual machine host | Host memory, last-level cache | The guest was slow | Which co-tenant caused the pressure |
| Database | Row and page locks | The statement waited | The blocking transaction's identity, unless separately captured |
| Inference serving | KV-cache blocks, accelerator memory, batch slots | Time to first token rose | Which sequences were resident |

Databases are the instructive exception: mature systems *do* capture blocking-session identity,
precisely because the problem was recognised there decades ago and instrumented for
specifically. Nothing equivalent exists in inference serving, and that absence is what this
paper addresses.

---

# **3. The contention graph**

## **3.1 Definition**

Let `R` be requests and `Q` bounded resources. The contention graph is a bipartite temporal
graph over `R` and `Q` whose edges are **residencies**:

```
e = (r, q, t_start, t_end, u, u_peak)
```

where `u` is occupancy in units defined by the resource — cache blocks, bytes, batch slots.

Two residencies **contend** when they share a resource and overlap in time:

```
contend(e1, e2)  <=>  q1 == q2  and  t1_start < t2_end  and  t2_start < t1_end
```

**Pressure events** record reclamation:

```
p = (q, t, kind, r_victim, u_reclaimed)      kind in { evict, preempt, recompute }
```

The runtime knows the victim of a pressure event — it selected it. The victim's own trace never
learns of it. Recording `r_victim` on the event is the single cheapest change
proposed in this paper and closes a large part of the gap on its own.

## **3.2 Resource identity**

`q` must identify the *contended thing*, not the machine. A cache pool, a batch-slot pool, one
accelerator's memory. Two requests carrying the same `q` with overlapping intervals were in
contention whether or not either observed it; two requests on the same host with different `q`
were not. Resource identity is what timestamp correlation lacks and is the reason it must be
emitted rather than inferred.

## **3.3 Relation to the call graph**

The two graphs are over the same events and answer different questions:

| | Call graph | Contention graph |
|---|---|---|
| Question | What did this request do? | What else held the resources it needed, and when? |
| Structure | Tree, rooted at the request | Bipartite temporal graph over requests and resources |
| Vertex of interest | Span | Residency interval |
| Sufficient for | Causes on the request path | Causes off it |

They are not competing representations and the second does not subsume the first. Attribution
requires reasoning over both: the call graph localises *where* in the request the time was
spent, the contention graph identifies *what else* was present when it was spent there.

![](docs/figures/fig1_two_graphs.png)

**Figure 1.** The two graphs. Every vertex of the call graph belongs to one request; the
contention graph places vertices from different requests in one structure, which is what makes
the co-residency relation expressible at all.

---

# **4. The join**

Given a victim `v` and its residencies `E(v)`, candidate contribution is weighted by
overlap-seconds times occupancy:

```
w(r')  =   sum over e in E(v)
             sum over e' in E(r') where contend(e, e')
               |I(e) INTERSECT I(e')|  *  max(u_e', u_peak_e')

         \_____________________/    \________________________/
            overlap in seconds          occupancy while overlapping
```

Candidates are ranked by `w`, and pressure events falling within `I(e)` are attached.

**Occupancy is a property of the pair `(r, r')` and of the interval.** No per-request trace can
carry it, because it is not a property of either request alone. That is the formal reason the
second graph is required rather than merely convenient.

## **4.1 Class-level aggregation is usually the useful answer**

Where many similar consumers are co-resident, no single one dominates and a per-request answer
is misleading. Ranking forty co-resident requests by a 2.5 percent share each conveys nothing
an operator can act on.

The join therefore aggregates by consumer class — workload label, tenant, model, or endpoint —
and reports class-level shares alongside the individual ranking. **The actionable finding is
usually which class of workload held the resource, not which request.** An operator can change
an admission policy for a class; it cannot do anything about request `a4f21c`.

This is a design conclusion rather than a presentational one, and it emerged from the
evaluation harness: the first implementation applied a minimum-share threshold to individual
candidates and returned nothing at all under diffuse contention — silently, and precisely in
the case the method exists to handle.

![](docs/figures/fig4_join.png)

**Figure 4.** The join, computed on demand. Ranking answers *who was present and how much they
held*; the class-level aggregate is what an operator can act on.

## **4.2 Cost**

Residency emission is `O(1)` per allocation change and `O(n)` in requests. The join is computed
**on demand for a nominated victim**, not continuously for all pairs, so the quadratic term is
never materialised. With residencies indexed by resource and sorted by start time, retrieving
co-residents is `O(log n + k)` for `k` overlapping intervals.

Emission volume is the real constraint, not compute. Three reductions, in increasing order of
information loss:

1. Emit residency only for requests exceeding a latency percentile — attribution is only ever
   requested for outliers
2. Aggregate residency into fixed windows, trading interval precision for volume
3. Emit occupancy summaries per class rather than per request, which preserves Section 4.1's
   answer while discarding the individual ranking

## **4.3 What ranking establishes, and what it does not**

`w(r')` identifies **who was present and how much they held**. It does not establish that the
holding caused the degradation. Correlation between occupancy and victim latency is expected
under the hypothesis and is also expected under several alternatives — a load surge raising
both, a scheduling policy that admits large requests when the system is already slow, or an
external common cause.

Establishing causation requires either a **controlled comparison** — the same victim workload
with and without the co-resident class, which is what the evaluation in Section 6 does — or a
**counterfactual** the system can produce, such as admission control that withholds the class
and measures the difference in production. The join is a hypothesis generator that reduces a
space of thousands of concurrent requests to a ranked handful. It is not a verdict, and
presenting it as one would repeat the error this paper attributes to existing tooling.

---

# **5. Instrumentation**

## **5.1 What the runtime must emit**

| Signal | Emitted when | Fields |
|---|---|---|
| Residency open | A request is allocated units of a bounded resource | request id, resource id, timestamp, units |
| Residency close | Units are released | request id, resource id, timestamp, peak units |
| Pressure event | Eviction, preemption, or recomputation | resource id, timestamp, kind, **victim request id**, units reclaimed |
| Batch composition | A batch is formed | batch id, member request ids |

Only the third requires the runtime to record something it does not already compute. The
scheduler necessarily knows the victim of a preemption at the moment it preempts; the change
is to emit it.

![](docs/figures/fig5_emission.png)

**Figure 5.** Emission points along the serving path. Three of the four signals are quantities
the runtime already computes and discards. Only the victim identity on a pressure event is an
addition, and it is available at the moment the decision is made.

## **5.2 Proposed semantic conventions**

Telemetry for inference is converging on OpenTelemetry, where conventions for generative AI
workloads are developed by the Generative AI Special Interest Group under the Semantic
Conventions SIG and carried in the `gen_ai.*` namespace. Those conventions describe the
generative AI *call*: model, request and response parameters, token counts, outcome. Every
attribute is a property of the request. **Nothing expresses co-residency**, which is the same
independence assumption of Section 2 carried into the vocabulary itself.

Proposed additions, in four groups:

| Group | Attribute | Type | Notes |
|---|---|---|---|
| Admission | `inference.queue.residency_ms` | int | Arrival to batch admission, distinct from TTFT |
| Batch | `inference.batch.id` | string | The batch in which the request executed |
| | `inference.batch.co_resident_count` | int | Members of that batch |
| Residency | `inference.resource.id` | string | The bounded resource occupied |
| | `inference.resource.units_held` | int | Occupancy, resource-defined units |
| | `inference.resource.residency_ms` | int | Duration of occupancy |
| Pressure | `inference.pressure.event` | span event | `evict` \| `preempt` \| `recompute` |
| | `inference.pressure.units_reclaimed` | int | Reclaimed quantity |

The namespace, the stability level, and whether residency belongs on the span or in a separate
signal are exactly the questions a proposal must argue rather than assume. They are open and
are listed as such in Section 8.

---

# **6. Evaluation design**

The design is stated with its falsification conditions before any result, so that the result
cannot be selected after the fact. The harness is at the repository cited above.

## **6.1 Setup**

Open-source serving runtime, open-weight model, rented accelerator capacity. Two workload
classes: a **victim** of short latency-sensitive requests, and an **aggressor** of long-context
high-footprint requests constructed to occupy cache blocks for extended intervals. Two runs
differing in one respect — whether the aggressor is present.

## **6.2 Hypotheses**

| | Statement | Falsified by |
|---|---|---|
| **H1** | Victim TTFT degrades under co-resident cache pressure | No degradation at any aggressor intensity |
| **H2** | The victim's span carries no attribute identifying the aggressor, the eviction, or the pressure | Finding such an attribute |
| **H3** | Runtime metrics record that pressure occurred but carry no key joining it to the victim | Finding such a join key |
| **H4** | Residency records make the attribution derivable | Attribution remains ambiguous with residency present |

**H2 and H3 are the finding.** H1 establishes only that there is something to attribute. A
falsified H2 or H3 would mean existing tooling already resolves this and the premise of the
paper is wrong; that result would be published unchanged.

## **6.3 Threats to validity**

Degradation caused by queueing rather than eviction — mitigated by holding aggressor rate low
relative to victim rate and reporting queue-depth series alongside. Client-side artefacts —
mitigated by recording client arrival timestamps and setting connection limits above offered
concurrency. Runtime specificity — **H2 cannot be stated generally from one runtime**; the
sweep is repeated on a second. Selection of a favourable intensity — the aggressor rate is
swept and the full curve reported.

## **6.4 What the evaluation cannot establish**

Single node, single runtime, synthetic load. Nothing here establishes that the gap persists at
production scale, across multi-node topologies, or under real traffic mixes. Those questions
require production environments and are outside what an experiment on rented capacity can
answer. They are the reason validation against operator data is a separate stage of the work
rather than an extension of this one.

---

# **7. Related work**

Distributed tracing in the Dapper lineage established the span-tree model and the analyses
built on it. Continuous batching and paged key-value cache management in modern serving
systems — vLLM's PagedAttention among them — are what make the contention described here both
possible and invisible: they are the mechanisms that allocate the contended resource. Work on
performance interference between co-located datacenter workloads addresses the same phenomenon
at the level of the host and the last-level cache, and is the closest antecedent; the
contribution proposed here is to bring it inside the request-level telemetry model rather than
treating it as a separate offline analysis. Published operational accounts from large training
platforms document the choice to over-evict rather than localise, which is the economic
consequence this paper's introduction rests on.

**[TO BE COMPLETED BEFORE PUBLICATION: this section must be replaced with a properly cited
survey. Every claim above needs a specific reference with authors, venue, and year, and the
interference and queueing literature in particular deserves fuller treatment than a paragraph.
Publishing a systems paper with a gestural related-work section invites the reviewer to assume
the work has not been situated — and in the case of the database blocking-session analogy at
Section 2.3, prior art almost certainly exists that should be credited rather than
rediscovered.]**

---

# **8. Open questions**

Stated because a proposal that presents itself as complete is less useful to a reviewer than
one that says where it is uncertain.

1. **Does the join generalise across architectures?** Prefill-decode disaggregation, speculative
   decoding, and multi-node tensor parallelism change what the contended resource *is*. The
   representation may need extension per architecture, which would weaken the claim
   considerably.
2. **What is the right resource granularity?** Too coarse and everything contends with
   everything; too fine and residency emission becomes unaffordable.
3. **Should residency be a span attribute, a span event, or a separate signal?** Attributes are
   fixed at span end; residency changes during execution. This may argue for a metric with
   exemplars, or a distinct signal type.
4. **Can the runtime attribute pressure without the join?** The scheduler knows both parties at
   the moment of eviction. Emitting the pair directly would be cheaper than reconstructing it —
   but only covers evictions, not the diffuse pressure of Section 4.1.
5. **What is the acceptable emission overhead?** If instrumentation costs more than a few
   percent, operators will disable it and the question is moot.
6. **Is class-level attribution sufficient in practice?** Section 4.1 argues it is usually the
   actionable answer. Whether operators agree is an empirical question about operator
   behaviour, not about systems.

---

# **9. Summary**

Detection is solved and attribution is not, and the reason is structural rather than a matter
of sampling density. Per-request tracing implements a model in which latency is a function of
the request's own path; under contention for a bounded resource, latency is a function of a set
of concurrent requests that the model has no term for.

The proposal is a second graph, recording which request held which bounded resource over which
interval, joined to the call graph on demand for a nominated victim. Residency emission is
linear; the join is computed only when asked; class-level aggregation gives the answer an
operator can act on.

The approach is unvalidated at production scale, the join identifies presence rather than
causation, and whether it generalises across serving architectures is open. The evaluation
design in Section 6 states the conditions under which it would be shown wrong, and those
conditions are the reason it is worth running.

---

*Comments, corrections, and disagreement are welcome, and disagreement is more useful than
agreement. If the premise at Section 2 is wrong I would rather find out now.*

*LI CHENG (Jonson Li) — [contact] — [repository URL]*
