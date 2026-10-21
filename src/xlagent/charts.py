"""README charts (matplotlib, dev dependency). Colours: fixed status palette.

Outcome is a *state*, so it uses status colours (good / warning / serious /
critical) and every cell also carries a text label - colour never carries the
meaning alone.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
STATUS = {
    "passed": ("#0ca30c", "passed"),
    "passed_unverified": ("#0ca30c", "ran (no expected file)"),
    "safety_rejected": ("#fab219", "rejected by safety check"),
    "mismatch": ("#ec835a", "output mismatch"),
    "no_output": ("#ec835a", "no output"),
    "crashed": ("#d03b3b", "crashed"),
    "timed_out": ("#d03b3b", "timed out"),
    "no_code": ("#d03b3b", "no code"),
}


def attempts_chart(rows, dest: Path) -> Path:
    """One row per example, one cell per attempt, labelled with its outcome."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    max_attempts = max((r.attempts for r in rows), default=1)
    fig, ax = plt.subplots(figsize=(9, 0.9 + 0.75 * len(rows)), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for y, row in enumerate(reversed(rows)):
        for x, outcome in enumerate(row.outcomes):
            colour, label = STATUS.get(outcome, ("#999999", outcome))
            ax.add_patch(plt.Rectangle((x + 0.02, y + 0.12), 0.96, 0.76, color=colour, linewidth=0))
            ax.text(
                x + 0.5,
                y + 0.5,
                f"{x + 1}: {label}",
                ha="center",
                va="center",
                fontsize=8.5,
                color=TEXT,
            )
    ax.set_xlim(0, max(max_attempts, 4))
    ax.set_ylim(0, len(rows))
    ax.set_yticks([i + 0.5 for i in range(len(rows))])
    ax.set_yticklabels([r.example for r in reversed(rows)], color=TEXT, fontsize=9)
    ax.set_xticks([i + 0.5 for i in range(max(max_attempts, 4))])
    ax.set_xticklabels(
        [f"attempt {i + 1}" for i in range(max(max_attempts, 4))], color=TEXT_2, fontsize=8
    )
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0)
    ax.set_title(
        "Repair loop on the three synthetic examples (replay mode, max 4 attempts)",
        color=TEXT,
        fontsize=10,
        loc="left",
    )
    fig.tight_layout()
    fig.savefig(dest, facecolor=SURFACE)
    plt.close(fig)
    return dest


def masking_chart(real: pd.DataFrame, masked: pd.DataFrame, dest: Path) -> Path:
    """Side-by-side table: what stays on the machine vs what the model sees."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 1, figsize=(9, 3.6), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    for ax, frame, title in (
        (axes[0], real, "Your file (stays on your machine)"),
        (axes[1], masked, "What the model sees by default (masked: same types and shapes)"),
    ):
        ax.axis("off")
        cells = frame.astype(str).replace({"NaT": "", "nan": "", "None": ""}).to_numpy()
        table = ax.table(cellText=cells, colLabels=list(frame.columns), loc="center")
        table.auto_set_font_size(False)
        table.set_fontsize(7.5)
        table.scale(1, 1.25)
        for (r, _c), cell in table.get_celld().items():
            cell.set_edgecolor("#d9d8d4")
            cell.set_facecolor("#efeeea" if r == 0 else SURFACE)
            cell.get_text().set_color(TEXT)
        ax.set_title(title, color=TEXT, fontsize=9.5, loc="left")
    fig.tight_layout()
    fig.savefig(dest, facecolor=SURFACE)
    plt.close(fig)
    return dest
