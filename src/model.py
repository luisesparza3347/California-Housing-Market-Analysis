"""Changes model with HAC(4) standard errors, writes model_output.json.

Reads the quarterly-resampled HPI, Mortgage_30yr, Unemployment_Rate, and CPI
series from data/raw/. Permits is fetched and validated by fetch.py but is
deliberately left out of this regression frame: it was previously fetched,
resampled, and never used in the model, yet still constrained the sample
through dropna(). Bringing it into the model wasn't warranted, so it is
dropped here instead, explicitly, rather than silently shrinking the sample.

The end of the sample is not pinned to a fixed date. START stays at 1990Q1,
but END rolls forward to whatever the latest complete quarter in the raw data
is, so a fresh cron run picks up new quarters automatically. The 1990Q1-2024Q4
result in CLAUDE.md and the README is the finding as reported at that date,
not a ceiling this script re-imposes on every future run.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import statsmodels.api as sm

START = "1990-01-01"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
OUT_PATH = DATA_DIR / "model_output.json"

# HPI, Mortgage_30yr, Unemployment_Rate, CPI. Permits is intentionally absent,
# see module docstring.
REGRESSION_SERIES = ["HPI", "Mortgage_30yr", "Unemployment_Rate", "CPI"]


def load_quarterly(name):
    series = pd.read_parquet(RAW_DIR / f"{name}.parquet")[name]
    return series.resample("QE").mean()


def build_quarterly_frame():
    quarterly = pd.DataFrame({name: load_quarterly(name) for name in REGRESSION_SERIES})
    return quarterly.loc[START:].dropna()


def build_change_frame(quarterly):
    chg = quarterly.pct_change().dropna() * 100
    chg.columns = [f"{c}_chg" for c in chg.columns]
    chg["HPI_chg_lag1"] = chg["HPI_chg"].shift(1)
    return chg.dropna()


def fit_changes_model(chg):
    predictors = ["HPI_chg_lag1", "Mortgage_30yr_chg", "Unemployment_Rate_chg", "CPI_chg"]
    X = sm.add_constant(chg[predictors])
    y = chg["HPI_chg"]
    return sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": 4})


def fit_levels_model(quarterly):
    """Same relationship on levels, kept only to show the spurious-trend problem the changes model avoids."""
    level_cols = ["Mortgage_30yr", "Unemployment_Rate", "CPI"]
    X = sm.add_constant(quarterly[level_cols])
    y = quarterly["HPI"]
    return sm.OLS(y, X).fit()


def to_result(quarterly, chg, changes_model, levels_model):
    coefficients = {
        term: {
            "coef": float(changes_model.params[term]),
            "std_err": float(changes_model.bse[term]),
            "p": float(changes_model.pvalues[term]),
        }
        for term in changes_model.params.index
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample": {
            "start": quarterly.index.min().date().isoformat(),
            "end": quarterly.index.max().date().isoformat(),
            "n_quarters": len(quarterly),
            "n_changes_obs": len(chg),
        },
        "changes_model": {
            "formula": "HPI_chg ~ HPI_chg_lag1 + Mortgage_30yr_chg + Unemployment_Rate_chg + CPI_chg",
            "cov_type": "HAC",
            "hac_maxlags": 4,
            "coefficients": coefficients,
            "r_squared": float(changes_model.rsquared),
            "durbin_watson": float(sm.stats.durbin_watson(changes_model.resid)),
        },
        "levels_model_contrast": {
            "formula": "HPI ~ Mortgage_30yr + Unemployment_Rate + CPI",
            "r_squared": float(levels_model.rsquared),
            "durbin_watson": float(sm.stats.durbin_watson(levels_model.resid)),
        },
        "notes": {
            "permits": (
                "Fetched and validated by fetch.py but excluded from this "
                "regression frame. It was previously fetched, resampled, and "
                "never used, while still constraining the sample through "
                "dropna(). Dropping it here removes that silent constraint."
            )
        },
    }


def main():
    quarterly = build_quarterly_frame()
    chg = build_change_frame(quarterly)

    changes_model = fit_changes_model(chg)
    levels_model = fit_levels_model(quarterly)

    result = to_result(quarterly, chg, changes_model, levels_model)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(result, f, indent=2)

    print(f"sample: {result['sample']}")
    print(f"changes model R^2 {result['changes_model']['r_squared']:.3f}, "
          f"DW {result['changes_model']['durbin_watson']:.3f}")
    print(f"levels model R^2 {result['levels_model_contrast']['r_squared']:.3f}, "
          f"DW {result['levels_model_contrast']['durbin_watson']:.3f}")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
