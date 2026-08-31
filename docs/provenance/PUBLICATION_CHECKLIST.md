# Before a result is published

This checklist is run before any release that claims a result. It exists because a published
result is a claim on other people's time: someone may build on it, or try to reproduce it, or
cite it. **A result that cannot be reproduced from what is in the repository is worse than no
result**, because it costs the reader the effort of finding that out.

The checklist is also run before any external citation of this work, including the citation in
an immigration petition described in [`README.md`](README.md) in this directory. A repository
containing a single commit, no results, and no history supports nothing, and citing one would
be evidence against the claim rather than for it.

---

## Gate 1 — the work is actually yours

Relevant because the repository is offered elsewhere as evidence of the author's own
engagement with the problem.

- [ ] You have read every file and can explain each design decision, including the ones you
      would now make differently
- [ ] You have run the experiment yourself and the numbers in `runs/` are yours
- [ ] You have changed what needed changing. The scaffold was a starting point; a repository
      whose entire content is unmodified starter code is weak evidence of engagement
- [ ] The commit history shows the work being done over time, not a single import commit

## Gate 2 — the result exists

- [ ] `selftest.sh` passes
- [ ] Baseline run completed and p95 TTFT is stable across two consecutive baseline runs
- [ ] Contention run completed at more than one aggressor intensity
- [ ] `runs/report.md` contains real verdicts for H1, H2 and H3
- [ ] H4 is either tested against a patched runtime, or reported as NOT RUN — a reconstructed
      graph is labelled an approximation and is not offered as evidence for H4
- [ ] If any hypothesis is falsified, the result is published unchanged. A negative result
      published is a contribution; a negative result withheld is a credibility problem the
      moment anyone re-runs the experiment

## Gate 3 — provenance is recorded

Every published result carries, in the report and in the README:

- [ ] Runtime name and exact version
- [ ] Model name and revision
- [ ] GPU model, memory, count, driver version
- [ ] Provider and instance type
- [ ] Repository commit hash under which the run was produced
- [ ] Date of the run

A result without these cannot be reproduced and should not be cited.

## Gate 4 — nothing that should not be there

- [ ] No client name, client data, or telemetry from any production environment
- [ ] No content from any employer's systems, and nothing that any employment agreement or
      confidentiality obligation covers
- [ ] Only open-weight models and public infrastructure
- [ ] No API keys, endpoints, or credentials in the history — check every commit, not the
      current tree
- [ ] `LICENSE` present (Apache-2.0), replacing `LICENSE.note`

## Gate 5 — citable form

- [ ] Public repository under your own account
- [ ] A tagged release, so the citation is stable against later commits
- [ ] The exact commit hash recorded, since a branch reference is not a permanent link
- [ ] Optionally a DOI via Zenodo, which archives the release and gives a citation that
      survives the repository being moved or deleted

---

## What then goes into an external citation

Once the gates are cleared, `release.py` emits the identifiers. Where the work is cited in the
immigration petition, three places take the same identifiers and they must agree verbatim:

| Location | What to write |
|---|---|
| Petition letter, Part V.C | What was published, where, on what date, and what it demonstrates — including any falsified hypothesis |
| Appendix A, Exhibit 5 row | Repository URL, tag, commit hash |
| Exhibit 5 cover sheet | The same URL, tag, and hash, plus the provenance block from Gate 3 |

And submit as the exhibit itself: a PDF snapshot of the README and `runs/report.md` as at the
cited commit. **A link alone is not an exhibit** — links rot, and the adjudicator may be
reading offline.

---

## If the gates are not cleared by the date a citation is needed

Remove Exhibit 5 and every reference to it. The petition letter's working notes record what
must be reverted: the paragraph at Part V.C, the Appendix A row, the cover sheet, the
numbering note, and the pointer at Part II.C.

That is a worse outcome than having the exhibit. It is a much better outcome than citing a
repository that does not support what it is cited for.
