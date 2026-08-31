#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exhibit5.py — assemble the exhibit itself, from the commit that is cited.

`PUBLICATION_CHECKLIST.md` requires that the exhibit be a snapshot of the README and the
result report as at the cited commit, and states why: "A link alone is not an exhibit —
links rot, and the adjudicator may be reading offline."

This reads those documents out of the tagged commit with `git show`, not out of the working
tree. The distinction is the whole point. The working tree contains later commits — the DOI
was recorded in one — and a snapshot taken from it would show a state the citation does not
name. An exhibit that does not match the commit it cites is worse than no exhibit, because
the discrepancy is discoverable by anyone who checks.

Output is one self-contained HTML file, printed to PDF from a browser. Not reportlab, and
not pandoc:

  * it must still work in several years, on a machine that is not this one, with whatever is
    installed then. A file that needs a pip install to regenerate is a file that will
    eventually not regenerate.
  * printing to PDF is available in every browser, and the HTML is itself inspectable — a
    reader who doubts the snapshot can diff it against `git show`.

Usage:
    python3 docs/provenance/exhibit5.py v0.2-first-result
    python3 docs/provenance/exhibit5.py v0.2-first-result --out exhibit5.html

Then open the file and print to PDF. Print backgrounds on; the identifier block is meant to
be visible on paper.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import re
import subprocess
import sys

# The documents that constitute the exhibit, in the order an adjudicator should meet them.
# report.md before sweep.md because the verdicts are the finding and the sweep is the
# evidence that no single intensity was chosen to flatter them.
DOCUMENTS = [
    ("README.md", "Repository README"),
    ("runs/report.md", "Hypothesis verdicts"),
    ("runs/sweep.md", "Baseline stability and the intensity sweep"),
    ("runs/provenance.txt", "Provenance of the run"),
]


def git(*a, check=True):
    r = subprocess.run(["git"] + list(a), capture_output=True, text=True)
    if check and r.returncode:
        sys.exit("git %s failed:\n%s" % (" ".join(a), r.stderr.strip()))
    return r.stdout


def show_at(ref, path):
    """The file's content at `ref`, or None if it is not in that commit."""
    r = subprocess.run(["git", "show", "%s:%s" % (ref, path)],
                       capture_output=True, text=True)
    return None if r.returncode else r.stdout


def repo_url():
    url = git("config", "--get", "remote.origin.url", check=False).strip()
    if not url:
        return "[repository URL]"
    url = re.sub(r"^git@([^:]+):", r"https://\1/", url)
    return re.sub(r"\.git$", "", url)


def doi_from_changelog(tag, text):
    """Version DOI for `tag`, read from the CHANGELOG in the working tree.

    From the working tree rather than from the tagged commit, because Zenodo assigns the DOI
    after the tag is cut; it cannot be inside the thing it identifies.
    """
    m = re.search(r"^##\s+" + re.escape(tag) + r"\b.*?$", text, re.M)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^##\s", rest, re.M)
    section = rest[: nxt.start()] if nxt else rest
    d = re.search(r"\*\*DOI:\*\*\s*(.+)$", section, re.M)
    if not d or d.group(1).strip().startswith("["):
        return ""
    v = re.search(r"10\.\d{4,}/\S+", d.group(1))
    return v.group(0).rstrip(".,;") if v else ""


# --------------------------------------------------------------------------- rendering

def md_to_html(src: str) -> str:
    """Enough Markdown for these documents, and no more.

    A dependency-free renderer is a deliberate limitation, not an oversight: it handles the
    constructs these four files actually use — headings, tables, fenced code, lists, inline
    code, bold — and passes anything else through as escaped text. Escaped, so that an
    unhandled construct appears verbatim rather than disappearing. A snapshot that silently
    drops content it could not render would be the worst possible failure for this file.
    """
    out, i = [], 0
    lines = src.split("\n")

    def inline(t):
        t = html.escape(t)
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)",
                   r'<a href="\2">\1</a>', t)
        return t

    while i < len(lines):
        ln = lines[i]

        if ln.startswith("```"):                                   # fenced code
            j = i + 1
            body = []
            while j < len(lines) and not lines[j].startswith("```"):
                body.append(lines[j]); j += 1
            out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(body)))
            i = j + 1
            continue

        if re.match(r"^\s*\|.*\|\s*$", ln) and i + 1 < len(lines) \
                and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
            j = i + 2
            rows = []
            while j < len(lines) and re.match(r"^\s*\|.*\|\s*$", lines[j]):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            out.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (
                "".join("<th>%s</th>" % inline(c) for c in head),
                "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % inline(c) for c in r)
                        for r in rows)))
            i = j
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            lv = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lv, inline(m.group(2)), lv))
            i += 1
            continue

        if re.match(r"^\s*[-*]\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append("<li>%s</li>" % inline(re.sub(r"^\s*[-*]\s+", "", lines[i])))
                i += 1
            out.append("<ul>%s</ul>" % "".join(items))
            continue

        if not ln.strip():
            i += 1
            continue

        para = []
        while i < len(lines) and lines[i].strip() \
                and not lines[i].startswith(("#", "```", "|")) \
                and not re.match(r"^\s*[-*]\s+", lines[i]):
            para.append(lines[i]); i += 1
        out.append("<p>%s</p>" % inline(" ".join(para)))

    return "\n".join(out)


CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font: 10.5pt/1.5 "Helvetica Neue", Helvetica, Arial, sans-serif;
       color: #111; max-width: 190mm; margin: 0 auto; }
h1 { font-size: 17pt; margin: 0 0 .3em; }
h2 { font-size: 13pt; margin: 1.6em 0 .4em; border-bottom: 1px solid #ccc;
     padding-bottom: .2em; }
h3 { font-size: 11.5pt; margin: 1.2em 0 .3em; }
p, li { margin: .45em 0; }
code { font: 9.5pt/1.4 "SF Mono", Menlo, Consolas, monospace;
       background: #f2f2f2; padding: .5px 3px; border-radius: 2px; }
pre { background: #f7f7f7; border: 1px solid #e0e0e0; border-radius: 3px;
      padding: 8px 10px; overflow-x: auto; page-break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 8.5pt; line-height: 1.35; }
table { border-collapse: collapse; margin: .7em 0; font-size: 9.5pt; width: 100%; }
th, td { border: 1px solid #ccc; padding: 4px 7px; text-align: left;
         vertical-align: top; }
th { background: #f2f2f2; }
.ident { border: 2px solid #111; padding: 12px 14px; margin: 1.2em 0;
         background: #fafafa; page-break-inside: avoid; }
.ident dt { font-weight: 600; float: left; width: 92px; clear: left; }
.ident dd { margin: 0 0 .35em 100px;
            font: 9.5pt/1.5 "SF Mono", Menlo, Consolas, monospace; word-break: break-all; }
.note { font-size: 9pt; color: #444; border-left: 3px solid #bbb;
        padding-left: 10px; margin: 1em 0; }
.doc { page-break-before: always; }
.doc:first-of-type { page-break-before: avoid; }
.missing { border: 1px solid #b00; background: #fff4f4; padding: 8px 10px;
           color: #900; }
footer { margin-top: 2.5em; padding-top: .6em; border-top: 1px solid #ccc;
         font-size: 8.5pt; color: #555; }
@media print { a { color: inherit; text-decoration: none; } }
"""


def build(tag, out_path):
    if not git("tag", "-l", tag, check=False).strip():
        sys.exit("No such tag: %s\nTags: %s" % (tag, " ".join(git("tag").split())))

    ref = tag + "^{commit}"
    commit = git("rev-parse", ref).strip()
    short = git("rev-parse", "--short=12", ref).strip()
    when = git("show", "-s", "--format=%cI", ref).strip()
    url = repo_url()
    changelog = ""
    if os.path.exists("CHANGELOG.md"):
        changelog = open("CHANGELOG.md", encoding="utf-8").read()
    doi = doi_from_changelog(tag, changelog) or "[DOI pending Zenodo archive]"

    parts = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        "<title>Exhibit 5 — %s — %s</title>" % (html.escape(url), html.escape(tag)),
        "<style>%s</style></head><body>" % CSS,
        "<h1>Exhibit 5 — public technical output</h1>",
        "<div class='ident'><dl>",
        "<dt>Repository</dt><dd>%s</dd>" % html.escape(url),
        "<dt>Release</dt><dd>%s</dd>" % html.escape(tag),
        "<dt>Commit</dt><dd>%s</dd>" % html.escape(commit),
        "<dt>DOI</dt><dd>%s</dd>" % html.escape(doi),
        "<dt>Committed</dt><dd>%s</dd>" % html.escape(when),
        "<dt>Snapshot</dt><dd>%s</dd>" % dt.datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "</dl></div>",
        "<div class='note'>The documents below were extracted from commit "
        "<code>%s</code> with <code>git show</code>, not from a working tree. "
        "They can be checked against the repository at that commit, or against the "
        "Zenodo archive of the release. Later commits to this repository do not alter "
        "the state cited here.</div>" % html.escape(short),
    ]

    # Two commit hashes appear in this exhibit and they are not the same one. The runs were
    # produced under the code as it stood when they were made; the release commit adds the
    # results and the documents describing them. Both are true and the relation between them
    # has to be stated, because an exhibit containing two hashes and no explanation reads as
    # though one of them is a mistake.
    run_commit = ""
    prov = show_at(ref, "runs/provenance.txt") or ""
    m = re.search(r"^commit:\s*([0-9a-f]{7,40})", prov, re.M)
    if m:
        run_commit = m.group(1)
    if run_commit and not commit.startswith(run_commit):
        # What changed between the two is computed, not asserted. An exhibit that claimed
        # "the release did not change the code" would be making a statement a reader can
        # check with one command, and on this repository that statement would have been
        # false: the release also corrected the scenario files' default model to the one the
        # runs actually used. Stating the wrong thing about a verifiable fact is worse than
        # stating nothing, so the tool reports what the diff contains.
        changed = git("diff", "--name-only", run_commit, commit, "--",
                      "src/", "scenarios/", "tests/", "tools/", check=False).split()
        if changed:
            what = ("Between them the following changed: %s. A reader should inspect that "
                    "diff before relying on the runs: code that changed after a run was "
                    "made was not the code that produced it."
                    % ", ".join("<code>%s</code>" % html.escape(c) for c in changed))
        else:
            what = ("No file under <code>src/</code>, <code>scenarios/</code>, "
                    "<code>tests/</code> or <code>tools/</code> differs between them, so "
                    "the release adds results and documentation only.")
        parts.append(
            "<div class='note'><strong>On the two commit hashes in this exhibit.</strong> "
            "The runs recorded below were produced under commit <code>%s</code>, the state "
            "of the harness when they were made. The release cited above, <code>%s</code>, "
            "is a later commit that adds those results and the documents describing them. "
            "%s Verify with <code>git diff %s %s -- src/ scenarios/ tests/ tools/</code>."
            "</div>"
            % (html.escape(run_commit), html.escape(short), what,
               html.escape(run_commit), html.escape(short)))

    missing = []
    for path, title in DOCUMENTS:
        content = show_at(ref, path)
        parts.append("<div class='doc'><h2>%s <code>%s</code></h2>"
                     % (html.escape(title), html.escape(path)))
        if content is None:
            missing.append(path)
            parts.append("<div class='missing'>Not present in commit %s. This is recorded "
                         "rather than omitted: a reader must be able to see that the "
                         "document was absent, not infer it from a gap.</div>"
                         % html.escape(short))
        elif path.endswith(".md"):
            parts.append(md_to_html(content))
        else:
            parts.append("<pre><code>%s</code></pre>" % html.escape(content))
        parts.append("</div>")

    parts.append(
        "<footer>Generated by <code>docs/provenance/exhibit5.py</code> from "
        "<code>%s</code> at commit <code>%s</code>. Print to PDF with backgrounds "
        "enabled.</footer></body></html>" % (html.escape(tag), html.escape(short)))

    open(out_path, "w", encoding="utf-8").write("\n".join(parts))

    print("written: %s" % out_path)
    print("  repository %s" % url)
    print("  release    %s" % tag)
    print("  commit     %s" % commit)
    print("  DOI        %s" % doi)
    if missing:
        print("\n  WARNING: not in the cited commit: %s" % ", ".join(missing))
        print("  The exhibit records the absence. If these should have been in the")
        print("  release, the release is the thing to fix, not this file.")
    if doi.startswith("["):
        print("\n  WARNING: no DOI recorded for %s in CHANGELOG.md. Archive the release" % tag)
        print("  on Zenodo and record the DOI before this exhibit is filed.")
    print("\n  Open it and print to PDF (backgrounds on). The identifiers above must")
    print("  match those emitted by release.py --show %s verbatim." % tag)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tag", help="the release being cited, e.g. v0.2-first-result")
    ap.add_argument("--out", default="", help="output HTML (default: exhibit5-<tag>.html)")
    a = ap.parse_args()
    build(a.tag, a.out or "exhibit5-%s.html" % a.tag)


if __name__ == "__main__":
    main()
