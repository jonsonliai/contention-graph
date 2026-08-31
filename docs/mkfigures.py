# -*- coding: utf-8 -*-
"""Figures for the contention graph whitepaper.

Vector PDF for the document, PNG for the repository README and GitHub.
Deliberately plain: no colour beyond a two-tone accent, no gradients, no chrome.
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, FancyArrowPatch, Circle

OUT = "/home/claude/contention-graph/docs/figures"
os.makedirs(OUT, exist_ok=True)

INK, MUTE, ACC, LIGHT = "#15191d", "#6b7684", "#a8320f", "#d6dbe3"
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 8.5,
    "axes.linewidth": 0.7, "figure.dpi": 200,
})


def save(fig, name):
    for ext in ("pdf", "png"):
        fig.savefig(f"{OUT}/{name}.{ext}", bbox_inches="tight",
                    facecolor="white", pad_inches=0.12)
    plt.close(fig)
    print("  wrote", name)


def box(ax, x, y, w, h, label, sub=None, fc="white", ec=INK, lw=0.9, fs=8.5):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.02",
                                fc=fc, ec=ec, lw=lw, zorder=3))
    ax.text(x + w / 2, y + h / 2 + (0.035 if sub else 0), label, ha="center", va="center",
            fontsize=fs, color=INK, zorder=4)
    if sub:
        ax.text(x + w / 2, y + h / 2 - 0.055, sub, ha="center", va="center",
                fontsize=7, color=MUTE, zorder=4)


def arrow(ax, p, q, style="-|>", color=INK, lw=0.9, ls="-", rad=0.0):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=9,
                                 color=color, lw=lw, linestyle=ls, zorder=2,
                                 connectionstyle=f"arc3,rad={rad}",
                                 shrinkA=2, shrinkB=2))


def blank(w, h):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    return fig, ax


# ---------------------------------------------------------------- Fig 1
def fig1_two_graphs():
    fig, ax = blank(7.4, 3.1)
    ax.plot([0.5, 0.5], [0.02, 0.99], color=LIGHT, lw=0.8)

    ax.text(0.005, 0.955, "The call graph", fontsize=9.5, color=INK, weight="bold")
    ax.text(0.005, 0.895, "answers: what did this request do?", fontsize=7.5, color=MUTE)
    box(ax, 0.16, 0.70, 0.18, 0.10, "request  r")
    for i, (lbl, x) in enumerate([("span a", 0.03), ("span b", 0.19), ("span c", 0.35)]):
        box(ax, x, 0.46, 0.13, 0.09, lbl, fs=7.5)
        arrow(ax, (0.25, 0.70), (x + 0.065, 0.55))
    box(ax, 0.19, 0.22, 0.13, 0.09, "span b1", fs=7.5)
    arrow(ax, (0.255, 0.46), (0.255, 0.31))
    ax.text(0.25, 0.10, "a tree, rooted at the request.\nevery vertex belongs to r.",
            fontsize=7.5, color=ACC, ha="center", va="center")

    ax.text(0.525, 0.955, "The contention graph", fontsize=9.5, color=INK, weight="bold")
    ax.text(0.525, 0.895, "answers: what else held the resources it needed, and when?",
            fontsize=7.5, color=MUTE)
    ypos = {"r": 0.74, "r'": 0.58, "r''": 0.42}
    for lbl, y in ypos.items():
        box(ax, 0.55, y, 0.10, 0.085, lbl, fs=8, ec=ACC if lbl == "r" else INK)
    box(ax, 0.83, 0.655, 0.15, 0.135, "q", "KV pool", fs=8)
    box(ax, 0.83, 0.395, 0.15, 0.135, "q'", "batch slots", fs=8)
    # residency edges: r and r' contend on q; r'' holds both
    for lbl in ("r", "r'", "r''"):
        arrow(ax, (0.65, ypos[lbl] + 0.0425), (0.83, 0.7225), style="-", color=MUTE, lw=0.8)
    arrow(ax, (0.65, ypos["r''"] + 0.0425), (0.83, 0.4625), style="-", color=MUTE, lw=0.8)
    ax.text(0.745, 0.255, "edges are residencies, each carrying\nan interval and an occupancy.",
            fontsize=7.5, color=ACC, ha="center", va="center")
    ax.text(0.745, 0.105, "vertices from different requests appear in one graph.",
            fontsize=7.5, color=MUTE, ha="center", va="center")
    save(fig, "fig1_two_graphs")


# ---------------------------------------------------------------- Fig 2
def fig2_timeline():
    fig, ax = plt.subplots(figsize=(7.4, 2.9))
    ax.set_xlim(0, 10); ax.set_ylim(-0.3, 4.6); ax.axis("off")

    lanes = [
        ("aggressor r'",  0.4, 8.6, 3.7, 0.36, LIGHT, "occupancy 900 blocks"),
        ("aggressor r''", 1.6, 9.4, 3.0, 0.36, LIGHT, "occupancy 700 blocks"),
        ("victim  v",     4.3, 5.9, 2.1, 0.30, "#f4d9cf", "occupancy 20 blocks"),
    ]
    for name, x0, x1, y, h, fc, note in lanes:
        ax.add_patch(Rectangle((x0, y), x1 - x0, h, fc=fc,
                               ec=ACC if "victim" in name else MUTE, lw=0.9, zorder=3))
        ax.text(-0.15, y + h / 2, name, ha="right", va="center", fontsize=8, color=INK)
        ax.text(x1 + 0.12, y + h / 2, note, ha="left", va="center", fontsize=7, color=MUTE)

    ax.plot([5.0, 5.0], [1.9, 4.3], color=ACC, lw=1.0, ls=(0, (3, 2)), zorder=5)
    ax.plot(5.0, 2.28, marker="v", color=ACC, ms=6, zorder=6)
    ax.text(5.12, 4.42, "eviction: v's blocks reclaimed to serve r'", fontsize=7.5, color=ACC)

    ax.add_patch(Rectangle((0, 0.55), 10, 0.55, fc="white", ec=MUTE, lw=0.8, zorder=3))
    ax.text(-0.15, 0.825, "v's own span", ha="right", va="center", fontsize=8, color=INK)
    ax.text(5.0, 0.825, "duration 2 100 ms   ·   TTFT 1 240 ms   ·   tokens 60 / 64",
            ha="center", va="center", fontsize=7.5, color=INK, family="DejaVu Sans Mono", zorder=4)
    ax.text(5.0, 0.18, "nothing here refers to the eviction, to r', or to the pool. "
                       "the symptom is recorded; the cause is not.",
            ha="center", va="center", fontsize=7.5, color=ACC)
    ax.annotate("", xy=(5.0, 1.12), xytext=(5.0, 1.88),
                arrowprops=dict(arrowstyle="-|>", color=ACC, lw=0.9, ls=(0, (2, 2))))
    ax.plot([0, 10], [4.55, 4.55], color="white")
    ax.annotate("time", xy=(9.9, -0.1), xytext=(0, -0.1),
                arrowprops=dict(arrowstyle="-|>", color=MUTE, lw=0.8), fontsize=7.5,
                color=MUTE, va="center")
    save(fig, "fig2_timeline")


# ---------------------------------------------------------------- Fig 3
def fig3_who_sees_what():
    fig, ax = blank(7.4, 2.5)
    cols = [
        (0.02, "victim's trace", ["elevated TTFT", "own span tree", "\u2014 no cause"], ACC),
        (0.35, "aggressor's trace", ["ordinary success", "nothing unusual", "\u2014 no cause"], MUTE),
        (0.68, "runtime metrics", ["preemptions +41", "cache usage 94 %", "\u2014 no request id"], MUTE),
    ]
    for x, title, items, c in cols:
        box(ax, x, 0.42, 0.30, 0.46, "", fc="white", ec=c, lw=1.0)
        ax.text(x + 0.15, 0.815, title, ha="center", fontsize=8.5, color=INK, weight="bold")
        for i, it in enumerate(items):
            ax.text(x + 0.15, 0.71 - i * 0.095, it, ha="center", fontsize=7.5,
                    color=ACC if it.startswith("\u2014") else MUTE)
    ax.text(0.5, 0.20,
            "Each party holds a fragment. None holds the pair.\n"
            "The information is not missing from the system \u2014 it is distributed so that\n"
            "no participant's record contains it.",
            ha="center", va="center", fontsize=8.5, color=INK)
    save(fig, "fig3_who_sees_what")


# ---------------------------------------------------------------- Fig 4
def fig4_join():
    fig, ax = blank(7.4, 2.35)
    box(ax, 0.01, 0.50, 0.20, 0.30, "victim v", "nominated on demand", ec=ACC)
    box(ax, 0.26, 0.50, 0.21, 0.30, "residencies E(v)", "resource + interval")
    box(ax, 0.52, 0.50, 0.21, 0.30, "co-residents", "O(log n + k) lookup")
    box(ax, 0.78, 0.50, 0.21, 0.30, "ranked by w", "overlap \u00d7 occupancy")
    for a, b in [(0.21, 0.26), (0.47, 0.52), (0.73, 0.78)]:
        arrow(ax, (a, 0.65), (b, 0.65))
    ax.text(0.50, 0.36, "the quadratic term is never materialised: the join is computed "
                        "only for a nominated victim",
            ha="center", fontsize=7.5, color=MUTE)
    box(ax, 0.26, 0.03, 0.47, 0.22, "aggregate by consumer class",
        "an operator can change a policy for a class, not for request a4f21c", ec=ACC, fs=8)
    arrow(ax, (0.885, 0.50), (0.73, 0.16), style="-|>", color=ACC, rad=-0.25)
    save(fig, "fig4_join")


# ---------------------------------------------------------------- Fig 5
def fig5_emission():
    fig, ax = blank(7.4, 2.6)
    stages = [("arrival", 0.02), ("admission", 0.21), ("batch\nformation", 0.40),
              ("execution", 0.59), ("completion", 0.78)]
    for lbl, x in stages:
        box(ax, x, 0.60, 0.17, 0.20, lbl, fs=8)
    for i in range(len(stages) - 1):
        arrow(ax, (stages[i][1] + 0.17, 0.70), (stages[i + 1][1], 0.70))

    emits = [
        (0.21, "queue residency", "already computed"),
        (0.40, "batch id + members", "already computed"),
        (0.59, "residency open / close", "already computed"),
        (0.59, "pressure event\n+ victim id", "the one addition"),
    ]
    for i, (x, lbl, note) in enumerate(emits):
        y = 0.34 - (0.17 if i == 3 else 0)
        c = ACC if i == 3 else MUTE
        ax.plot([x + 0.085, x + 0.085], [0.60, y + 0.10], color=c, lw=0.8,
                ls=(0, (2, 2)), zorder=1)
        ax.text(x + 0.085, y + 0.045, lbl, ha="center", fontsize=7.5, color=INK)
        ax.text(x + 0.085, y - 0.03, note, ha="center", fontsize=7, color=c)
    ax.text(0.5, 0.03, "the scheduler already knows the victim at the moment it preempts. "
                       "the change is to emit it.",
            ha="center", fontsize=8, color=ACC)
    save(fig, "fig5_emission")


for f in (fig1_two_graphs, fig2_timeline, fig3_who_sees_what, fig4_join, fig5_emission):
    f()
print("figures written to", OUT)
