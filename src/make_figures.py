"""Generate the two figures in figures/ from data/raw and data/model_output.json.

Not part of the containerized pipeline (fetch.py / model.py / api.py) and not
in requirements.txt: matplotlib is a local, one-off plotting dependency, run
by hand after model.py, not something the CronJob or API image needs.

    pip install matplotlib==3.11.2
    python src/make_figures.py
"""

import json

import matplotlib.pyplot as plt
import statsmodels.api as sm

from model import CHANGES_PREDICTORS, DATA_DIR, OUT_PATH, build_change_frame, build_quarterly_frame

FIGURES_DIR = DATA_DIR.parent / "figures"

BLUE = "#2a78d6"
GRAY = "#898781"
INK = "#0b0b0b"
SECONDARY_INK = "#52514e"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Segoe UI", "DejaVu Sans"],
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "axes.edgecolor": BASELINE,
    "axes.labelcolor": SECONDARY_INK,
    "text.color": INK,
    "xtick.color": SECONDARY_INK,
    "ytick.color": SECONDARY_INK,
})

LABELS = {
    "HPI_chg_lag1": "HPI growth\n(prior quarter)",
    "Mortgage_30yr_chg": "Mortgage rate\nchange",
    "Unemployment_Rate_chg": "Unemployment\nrate change",
    "CPI_chg": "CPI change",
}


def plot_coefficients(result):
    coefs = result["changes_model"]["coefficients"]
    terms = list(reversed(CHANGES_PREDICTORS))

    fig, ax = plt.subplots(figsize=(7, 3.5), dpi=150)

    for i, term in enumerate(terms):
        c = coefs[term]
        coef, se, p = c["coef"], c["std_err"], c["p"]
        ci = 1.96 * se
        color = BLUE if term == "HPI_chg_lag1" else GRAY
        ax.errorbar(coef, i, xerr=ci, fmt="o", color=color, ecolor=color,
                     elinewidth=2, capsize=4, markersize=7, zorder=3)
        label = f"{coef:+.3f}  (p {'< 0.001' if p < 0.001 else f'= {p:.2f}'})"
        ax.annotate(label, (coef, i), xytext=(0, 12), textcoords="offset points",
                     ha="center", fontsize=9, color=SECONDARY_INK)

    ax.axvline(0, color=BASELINE, linewidth=1, zorder=1)
    ax.set_yticks(range(len(terms)))
    ax.set_yticklabels([LABELS[t] for t in terms], fontsize=9)
    ax.set_xlabel("Effect on quarterly HPI growth (HAC(4) 95% CI)")
    ax.set_title(
        "Once last quarter's HPI growth is in the model, nothing else moves it",
        fontsize=11, color=INK, loc="left", pad=14,
    )
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(left=False)
    ax.grid(axis="x", color=GRIDLINE, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)

    fig.tight_layout()
    out = FIGURES_DIR / "coefficients.png"
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)
    print(f"wrote {out}")


def plot_momentum(chg):
    x = chg["HPI_chg_lag1"]
    y = chg["HPI_chg"]
    X = sm.add_constant(x)
    fit = sm.OLS(y, X).fit()
    xs = [x.min(), x.max()]
    ys = [fit.params["const"] + fit.params["HPI_chg_lag1"] * v for v in xs]

    fig, ax = plt.subplots(figsize=(6, 5), dpi=150)
    ax.scatter(x, y, s=22, color=GRAY, alpha=0.6, edgecolor="none", zorder=2)
    ax.plot(xs, ys, color=BLUE, linewidth=2, zorder=3)

    ax.set_xlabel("HPI growth, prior quarter (%)")
    ax.set_ylabel("HPI growth, this quarter (%)")
    ax.set_title(
        f"HPI growth predicts next quarter's growth on its own (R² = {fit.rsquared:.2f})",
        fontsize=11, color=INK, loc="left", pad=14,
    )
    ax.axhline(0, color=GRIDLINE, linewidth=0.8, zorder=1)
    ax.axvline(0, color=GRIDLINE, linewidth=0.8, zorder=1)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(BASELINE)

    fig.tight_layout()
    out = FIGURES_DIR / "momentum_scatter.png"
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)
    print(f"wrote {out}")


def main():
    FIGURES_DIR.mkdir(exist_ok=True)
    with open(OUT_PATH) as f:
        result = json.load(f)

    quarterly = build_quarterly_frame()
    chg = build_change_frame(quarterly)

    plot_coefficients(result)
    plot_momentum(chg)


if __name__ == "__main__":
    main()
