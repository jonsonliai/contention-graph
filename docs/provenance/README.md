# Provenance

This directory holds the rules under which results in this repository are published, and the
tooling that enforces them.

It is separate from the rest of the documentation because it is not about the method. A reader
who wants to understand the work should read [`../WHITEPAPER.md`](../WHITEPAPER.md) and the
top-level README; this directory answers a different question, which is **how to tell whether
what is published here can be relied upon.**

| File | What it is |
|---|---|
| [`VERSIONING.md`](VERSIONING.md) | What the version numbers mean, and the rule that results are never revised in place |
| [`PUBLICATION_CHECKLIST.md`](PUBLICATION_CHECKLIST.md) | What must be true before a result is published: provenance recorded, negative results included, nothing in the repository that should not be there |
| [`release.py`](release.py) | Cuts a version, writes the changelog, and emits the identifiers a citation needs |

## Why these rules are stricter than a research repository usually needs

Results published here are cited as evidence in a United States immigration petition
(EB-2 National Interest Waiver, self-petitioned). That is disclosed rather than omitted, for
two reasons.

**It explains the rules.** A citation in that context has to be retrievable years later, by a
reader who was not present, at the exact state that was cited. That is why every release is
tagged and archived with a DOI, why the README records which release was cited, and why a
result is never edited after publication. These are ordinary good practice; the citation makes
them mandatory rather than optional.

**Concealing it would be worse than stating it.** A repository built partly for a filing and
presented as though it were not invites exactly the suspicion the filing can least afford. The
disclosure costs nothing that honest work would want to keep.

**None of it changes what is claimed.** The hypotheses at
[`../METHOD.md`](../METHOD.md) and their falsification conditions were published before any
result was obtained, and the timestamps are third-party verifiable. A hypothesis that is
falsified is published falsified. If the rules in this directory did anything other than
constrain the author, they would not be worth stating.
