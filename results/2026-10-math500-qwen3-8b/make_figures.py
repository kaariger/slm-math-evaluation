"""Regenerate the figures for the MATH-500 reproduction write-up.

Inputs are the aggregate counts reported in the write-up (no per-item data).
Usage: python make_figures.py  (writes PNGs into ./figures)
"""
from math import sqrt
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT = Path(__file__).with_name("figures")
OUT.mkdir(exist_ok=True)

# Palette: validated categorical slots 1-2 on the light surface.
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE = "#2a78d6", "#eb6834"
CLAIM = 88.80  # MathGPT.ai, June 2025 (hybrid reasoning)


def wilson(k, n, z=1.959963984540054):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * p, 100 * (c + h)


def style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, length=0)
    ax.xaxis.grid(True, color=GRID, linewidth=1)
    ax.set_axisbelow(True)


def fig_test():
    rows = [("Hybrid reasoning (v1)", 451, 460), ("Reasoning off (v1-nt)", 407, 415)]
    fig, ax = plt.subplots(figsize=(8, 3.4), dpi=200, facecolor=SURFACE)
    style(ax)
    for i, (label, kp, ks) in enumerate(rows):
        y = len(rows) - 1 - i
        for k, dy, color, marker, name in (
            (kp, 0.12, BLUE, "o", "Primary grader (PRM800K)"),
            (ks, -0.12, ORANGE, "D", "Secondary grader (math-verify)"),
        ):
            lo, p, hi = wilson(k, 500)
            ax.errorbar(p, y + dy, xerr=[[p - lo], [hi - p]], fmt=marker, color=color, ms=7,
                        elinewidth=2, capsize=4, label=name if i == 0 else None)
            ax.text(hi + 0.4, y + dy, f"{p:.1f}%  [{lo:.1f}, {hi:.1f}]", va="center", fontsize=8, color=INK,
                    bbox=dict(facecolor=SURFACE, edgecolor="none", pad=1.0), zorder=5)
    ax.axvline(CLAIM, color=INK2, linestyle="--", linewidth=1.5)
    ax.text(CLAIM + 0.3, 1.75, "Reported: 88.80% (hybrid reasoning)", ha="left", va="center", fontsize=8, color=INK2)
    ax.set_yticks(range(len(rows)), [r[0] for r in reversed(rows)], color=INK, fontsize=9)
    ax.set_ylim(-0.6, 2.1)
    ax.set_xlim(74, 100)
    ax.set_xlabel("Accuracy (%)", color=INK2, fontsize=9)
    ax.set_title("MATH-500 test set (n = 500): accuracy with 95% Wilson intervals",
                 loc="left", color=INK, fontsize=11)
    ax.legend(loc="upper left", frameon=False, fontsize=8, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(OUT / "fig1-test-accuracy.png", facecolor=SURFACE)


def fig_validation():
    rows = [("v1 · Q4_K_M · hybrid", 186), ("v1-q8 · Q8_0 · hybrid", 182), ("v1-nt · Q4_K_M · reasoning off", 175)]
    fig, ax = plt.subplots(figsize=(8, 2.9), dpi=200, facecolor=SURFACE)
    style(ax)
    for i, (label, k) in enumerate(rows):
        y = len(rows) - 1 - i
        lo, p, hi = wilson(k, 200)
        ax.errorbar(p, y, xerr=[[p - lo], [hi - p]], fmt="o", color=BLUE, ms=7, elinewidth=2, capsize=4)
        ax.text(hi + 0.4, y, f"{p:.1f}%  [{lo:.1f}, {hi:.1f}]", va="center", fontsize=8, color=INK)
    ax.set_yticks(range(len(rows)), [r[0] for r in reversed(rows)], color=INK, fontsize=9)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_xlim(80, 100)
    ax.set_xlabel("Primary-grader accuracy (%)", color=INK2, fontsize=9)
    ax.set_title("Validation tier (n = 200, stratified MATH train): 95% Wilson intervals",
                 loc="left", color=INK, fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "fig2-validation-probes.png", facecolor=SURFACE)


def fig_capped():
    rows = [("Test · v1 hybrid", 30, 500), ("Test · v1-nt reasoning off", 1, 500),
            ("Validation · v1 hybrid", 9, 200), ("Validation · v1-q8 hybrid", 13, 200),
            ("Validation · v1-nt reasoning off", 2, 200)]
    fig, ax = plt.subplots(figsize=(8, 3.2), dpi=200, facecolor=SURFACE)
    style(ax)
    for i, (label, k, n) in enumerate(rows):
        y = len(rows) - 1 - i
        v = 100 * k / n
        ax.barh(y, v, height=0.5, color=BLUE)
        ax.text(v + 0.12, y, f"{k} / {n}  ({v:.1f}%)", va="center", fontsize=8, color=INK)
    ax.set_yticks(range(len(rows)), [r[0] for r in reversed(rows)], color=INK, fontsize=9)
    ax.set_xlim(0, 8.5)
    ax.set_xlabel("Outputs that hit the 14,000-token cap (%)", color=INK2, fontsize=9)
    ax.set_title("Capped outputs (scored as incorrect under the protocol)", loc="left", color=INK, fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "fig3-capped-outputs.png", facecolor=SURFACE)


if __name__ == "__main__":
    fig_test()
    fig_validation()
    fig_capped()
    for k, n in ((451, 500), (460, 500), (407, 500), (415, 500), (186, 200), (182, 200), (175, 200), (444, 500)):
        lo, p, hi = wilson(k, n)
        print(f"{k}/{n}: {p:.1f}% [{lo:.2f}, {hi:.2f}]")
