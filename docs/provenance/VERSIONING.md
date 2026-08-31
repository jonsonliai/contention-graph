# Versioning

Versions here identify **what was known when**, not what is compatible with what.

The requirement the scheme has to meet is this: **a reader arriving months or years later must
be able to tell which state of the repository a given claim was made from, and to retrieve
exactly that state.** Results are cited elsewhere, including in an immigration petition (see
[`README.md`](README.md) in this directory), and a citation that cannot be resolved to a
specific state is not worth making.

## The scheme

Versions communicate **evidentiary state**, not API compatibility.

| Version | Means |
|---|---|
| `v0.1-preregistration` | Method, reference implementation, and evaluation design with falsification conditions. **No result claimed.** Published before any experiment was run |
| `v0.2-…` | First experimental result. The suffix names what was added, e.g. `v0.2-h1` |
| `v0.3-…`, `v0.4-…` | Further hypotheses evaluated, or results extended to a second runtime |
| `v1.0-filed` | **The state cited externally.** Frozen. Later work continues on higher versions and does not alter this one |

**The ordering carries the argument.** `v0.1-preregistration` exists to demonstrate that the
hypotheses and the conditions under which they would be falsified were fixed and published
before any result was obtained. If that release did not precede the result releases in time,
the claim would be worth much less \u2014 the author could have chosen which hypothesis to state
after seeing which way the data fell. The Zenodo timestamps make the order verifiable by a
third party, which is the point: it is not a promise, it is a record.

## Every release carries

- A git tag, annotated, on a specific commit
- A GitHub Release with notes stating what changed and what is now claimed
- A Zenodo archive and DOI for that version
- An entry in `CHANGELOG.md`
- Provenance for any result: runtime and version, model and revision, GPU model and driver,
  provider and instance type, commit hash, date

## Which version was cited

The top-level README carries a fixed block naming the release that was cited externally.
**That block is not removed or edited when later versions are released.** A reader arriving
after further work has been done must still be able to identify and retrieve the state that
was actually relied upon.

## Results are not revised in place

If a later run contradicts an earlier published result, the earlier release is **not** amended
or withdrawn. The correction appears in a new version, and the changelog records both. A
result that can be quietly changed after publication is not evidence of anything.
