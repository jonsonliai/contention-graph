#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
release.py — cut a version and emit the identifiers a citation of it needs.

Run from the repository root:  python3 docs/provenance/release.py <tag> "<message>"

What it does, and why each part exists:

  writes CHANGELOG.md    so that what changed in each version is recorded in the repository
                         rather than only in a release page that can be edited
  tags the commit        so that the version resolves to one immutable object
  refuses on a dirty     because a tag that points at a commit not containing what you are
  working tree           publishing is worse than no tag
  refuses to retag       because results are not revised in place (VERSIONING.md)
  prints identifiers     because the same four values — URL, tag, commit, DOI — have to
                         appear in several places, and typing them repeatedly is how they
                         end up disagreeing

Where this work is cited in the immigration petition described in README.md in this
directory, three locations take those identifiers and must agree verbatim: the petition
letter at Part V.C, the Appendix A exhibit row, and the Exhibit 5 cover sheet. The script
emits each in the form that location needs.

Usage
    python3 release.py v0.2-h1 "First result: H1 evaluated on vLLM"
    python3 release.py v0.2-h1 "..." --doi 10.5281/zenodo.1234567
    python3 release.py --show                 # identifiers of the current HEAD, no changes
    python3 release.py v1.0-filed "..." --filed --doi 10.5281/zenodo.1234567
"""

import argparse
import datetime as dt
import io
import os
import re
import subprocess
import sys

FILED_MARK = "<!-- FILED-VERSION-BLOCK -->"


def git(*a, check=True):
    r = subprocess.run(["git"] + list(a), capture_output=True, text=True)
    if check and r.returncode:
        sys.exit("git %s failed:\n%s" % (" ".join(a), r.stderr.strip()))
    return r.stdout.strip()


def repo_url():
    url = git("config", "--get", "remote.origin.url", check=False)
    if not url:
        return "[repository URL]"
    url = re.sub(r"^git@([^:]+):", r"https://\1/", url)
    return re.sub(r"\.git$", "", url)


def dirty():
    return bool(git("status", "--porcelain", check=False))


def tag_exists(tag):
    return bool(git("tag", "-l", tag, check=False))


def doi_from_changelog(tag):
    """Read the DOI already recorded for `tag` in CHANGELOG.md.

    The DOI is assigned by Zenodo *after* the tag is cut, so it is not in the tagged commit
    and cannot be. It is added by a later commit and read from the working tree here.

    Reading it rather than asking for it again is the point of this script: the four values
    exist in one place and the places that cite them copy from that place. A --show that
    demanded the DOI on the command line would reintroduce the hand-typing it exists to
    prevent, and the two would disagree the first time someone mistyped a digit.
    """
    p = "CHANGELOG.md"
    if not os.path.exists(p):
        return ""
    s = io.open(p, encoding="utf-8").read()
    m = re.search(r"^##\s+" + re.escape(tag) + r"\b.*?$", s, re.M)
    if not m:
        return ""
    rest = s[m.end():]
    nxt = re.search(r"^##\s", rest, re.M)
    section = rest[: nxt.start()] if nxt else rest
    d = re.search(r"\*\*DOI:\*\*\s*(.+)$", section, re.M)
    if not d:
        return ""
    val = d.group(1).strip()
    if val.startswith("["):
        return ""
    # The line records both the version DOI and the concept DOI. A citation names the
    # version: the concept DOI resolves to whatever is newest, so a petition citing it would
    # point at a state written after the petition was filed. The first token on the line is
    # the version DOI by the convention this changelog follows. It also keeps the middle dot
    # out of the identifier, which the Appendix A row uses as its own separator.
    m = re.search(r"10\.\d{4,}/\S+", val)
    return m.group(0).rstrip(".,;") if m else val


def identifiers(tag, doi):
    """Resolve the four values a citation needs, from the tag rather than from HEAD.

    `HEAD` is wrong once anything has been committed after the tag, which is the normal
    case: the DOI is recorded in a commit that necessarily comes after the release it
    describes. Taking the hash from HEAD would emit identifiers naming a commit the citation
    does not point at, and a reader who checked out that hash would find a repository whose
    changelog still says the DOI is pending. The tag is the thing being cited, so the tag is
    what is resolved.
    """
    ref = tag + "^{commit}" if tag_exists(tag) else "HEAD"
    return {
        "url": repo_url(),
        "tag": tag,
        "commit": git("rev-parse", ref),
        "short": git("rev-parse", "--short=12", ref),
        "date": dt.date.today().isoformat(),
        "doi": doi or doi_from_changelog(tag) or "[DOI pending Zenodo archive]",
    }


# ------------------------------------------------------------------ changelog

def prepend_changelog(tag, message, ident):
    p = "CHANGELOG.md"
    if not os.path.exists(p):
        sys.exit("CHANGELOG.md not found.\n"
                 "Run from the repository root:  python3 docs/provenance/release.py ...")
    s = io.open(p, encoding="utf-8").read()
    anchor = "<!-- Add new entries directly below this line. -->"
    if anchor not in s:
        sys.exit("CHANGELOG.md is missing its insertion anchor.")
    entry = (
        "\n\n## %s \u2014 %s\n\n"
        "**DOI:** %s  \n**Retrieve with:** `git checkout %s`\n\n"
        "%s\n\n"
        "**Claimed:** [state what this version claims, and what it does not. If a hypothesis "
        "was falsified, say so here.]\n"
        % (tag, ident["date"], ident["doi"], tag, message)
    )
    io.open(p, "w", encoding="utf-8").write(s.replace(anchor, anchor + entry, 1))
    print("   CHANGELOG.md updated")


# ------------------------------------------------------------------ README filed block

def set_filed_block(ident):
    """Write the immutable block identifying the version cited in the petition."""
    p = "README.md"
    s = io.open(p, encoding="utf-8").read()
    block = (
        "%s\n"
        "> **Version cited in the immigration petition:** release `%s`, DOI %s.\n"
        "> Later releases continue the work; they do not alter that version. To retrieve the\n"
        "> state that was cited: `git checkout %s`. The exact commit hash that release\n"
        "> resolves to is recorded in the GitHub Release and in the Zenodo archive metadata.\n"
        "%s\n" % (FILED_MARK, ident["tag"], ident["doi"], ident["tag"], FILED_MARK)
    )
    if FILED_MARK in s:
        s = re.sub(re.escape(FILED_MARK) + r".*?" + re.escape(FILED_MARK) + r"\n",
                   block, s, flags=re.S)
        print("   README.md filed-version block replaced")
    else:
        lines = s.split("\n")
        i = next((k for k, l in enumerate(lines) if l.startswith("# ")), -1) + 1
        lines.insert(i, "\n" + block)
        s = "\n".join(lines)
        print("   README.md filed-version block inserted")
    io.open(p, "w", encoding="utf-8").write(s)


# ------------------------------------------------------------------ output

def emit(ident, filed):
    line = "%s \u00b7 tag %s \u00b7 commit %s \u00b7 DOI %s" % (
        ident["url"], ident["tag"], ident["short"], ident["doi"])

    print("\n" + "=" * 78)
    print(" IDENTIFIERS FOR THIS RELEASE — all uses must agree verbatim")
    print("=" * 78)
    print("\n--- 0. Short form, for any citation --------------------------------------")
    print("\n%s\n" % line)

    print("--- 1. Petition letter, Part V.C ---------------------------------------")
    print("""
Public technical output. I have published the method described at Part IV.A.5, a
reference implementation, and an experimental evaluation of it, at %s
(release %s, commit %s; archived at DOI %s, %s).
[State what the experiment tested, on what runtime, model and hardware, and what each
hypothesis returned \u2014 INCLUDING ANY THAT WERE FALSIFIED. State what the result does
and does not establish.] Exhibit 5.
""" % (ident["url"], ident["tag"], ident["short"], ident["doi"], ident["date"]))

    print("--- 2. Appendix A, row for Exhibit 5 -----------------------------------")
    print("""
| 5 | Public technical output in the field of the endeavor: %s | Engagement with
the substrate of the endeavor, evidenced by artifacts any reader can inspect and
reproduce \u2014 V.C |
""" % line)

    print("--- 3. Exhibit 5 cover sheet, Contents row 1 ---------------------------")
    print("""
Repository: %s \u2014 release %s \u2014 commit %s \u2014 DOI %s.
The exhibit is a PDF snapshot of the README and the result report as at that commit;
a link alone is not an exhibit.
""" % (ident["url"], ident["tag"], ident["short"], ident["doi"]))

    print("--- 4. Provenance block for runs/report.md -----------------------------")
    print("""
Runtime:        [name and exact version]
Model:          [name and revision]
Hardware:       [GPU model, count, memory, driver version]
Provider:       [provider and instance type]
Commit:         %s
Release:        %s
Date of run:    %s
""" % (ident["commit"], ident["tag"], ident["date"]))

    if not filed:
        print("Note: --filed was not passed, so the README filed-version block was not")
        print("changed. Pass --filed on the version you actually cite in the petition.\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag", nargs="?", help="e.g. v0.2-h1")
    ap.add_argument("message", nargs="?", help="what changed in this version")
    ap.add_argument("--doi", default="", help="Zenodo DOI, once assigned")
    ap.add_argument("--filed", action="store_true",
                    help="mark this as the version cited in the petition")
    ap.add_argument("--show", nargs="?", const="", metavar="TAG",
                    help="print the identifiers for TAG (default: the most recent tag) "
                         "and change nothing")
    a = ap.parse_args()

    if a.show is not None:
        tag = a.show or git("describe", "--tags", "--abbrev=0", check=False) or "[untagged]"
        if a.show and not tag_exists(tag):
            sys.exit("No such tag: %s\nTags: %s"
                     % (tag, ", ".join(git("tag", check=False).split()) or "(none)"))
        emit(identifiers(tag, a.doi), False)
        return

    if not a.tag or not a.message:
        sys.exit("usage: release.py <tag> <message> [--doi DOI] [--filed]")

    if dirty():
        sys.exit("Working tree is not clean. Commit your changes first \u2014 the tag must point\n"
                 "at a commit that contains exactly what you are publishing.")

    if git("tag", "-l", a.tag, check=False):
        sys.exit("Tag %s already exists. Results are not revised in place (VERSIONING.md);\n"
                 "cut a new version instead." % a.tag)

    ident = identifiers(a.tag, a.doi)
    prepend_changelog(a.tag, a.message, ident)
    git("add", "CHANGELOG.md")
    git("commit", "-m", "Release %s: %s" % (a.tag, a.message))

    # The filed block must carry the hash of the commit the tag points at, which does not
    # exist until the commit above has been made. Write it, then amend so that the block and
    # the commit it names are the same object.
    ident["commit"] = git("rev-parse", "HEAD")
    ident["short"] = git("rev-parse", "--short=12", "HEAD")
    if a.filed:
        # The block names the tag and the DOI, not the commit hash, and that is deliberate:
        # a block that named the hash of the commit containing it could never be written,
        # because writing it changes the hash. The tag resolves to the hash through git, the
        # GitHub Release and the Zenodo metadata, which the block says. One write, no amend
        # chase.
        set_filed_block(ident)
        git("add", "README.md")
        git("commit", "--amend", "--no-edit")
        ident["commit"] = git("rev-parse", "HEAD")
        ident["short"] = git("rev-parse", "--short=12", "HEAD")

    git("tag", "-a", a.tag, "-m", a.message)

    print("\n   tagged %s at %s" % (a.tag, ident["short"]))
    print("   next:  git push && git push --tags")
    print("          then create the GitHub Release, which triggers the Zenodo archive")
    print("          then re-run with --doi once Zenodo assigns one")
    emit(ident, a.filed)


if __name__ == "__main__":
    main()
