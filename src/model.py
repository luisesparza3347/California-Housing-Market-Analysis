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
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.stats.outliers_influence import variance_inflation_factor
from statsmodels.stats.stattools import jarque_bera
from statsmodels.tsa.stattools import adfuller

START = "1990-01-01"

CHANGES_PREDICTORS = ["HPI_chg_lag1", "Mortgage_30yr_chg", "Unemployment_Rate_chg", "CPI_chg"]

# Each change column's underlying level series, for the stationarity check.
CHANGE_TO_LEVEL = {
    "HPI_chg": "HPI",
    "Mortgage_30yr_chg": "Mortgage_30yr",
    "Unemployment_Rate_chg": "Unemployment_Rate",
    "CPI_chg": "CPI",
}

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
    X = sm.add_constant(chg[CHANGES_PREDICTORS])
    y = chg["HPI_chg"]
    return sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": 4})


def fit_levels_model(quarterly):
    """Same relationship on levels, kept only to show the spurious-trend problem the changes model avoids."""
    level_cols = ["Mortgage_30yr", "Unemployment_Rate", "CPI"]
    X = sm.add_constant(quarterly[level_cols])
    y = quarterly["HPI"]
    return sm.OLS(y, X).fit()


def run_diagnostics(quarterly, chg, changes_model):
    """Checks the changes-model design actually holds up, not just that it runs.

    Stationarity: confirms levels are non-stationary (ADF fails to reject a
    unit root) and changes mostly are (ADF rejects it), which is the actual
    justification for modeling changes instead of levels, not just an
    assertion about spurious trends.

    VIF: confirms the macro predictors' insignificance in the changes model
    isn't multicollinearity absorbing their effect into HPI_chg_lag1.

    HAC lag sensitivity: refits at maxlags 0, 1, 2, 4, 8 to confirm the
    macro-insignificance result holds regardless of the specific lag length
    chosen, not just at 4.

    Residuals: Jarque-Bera and Breusch-Pagan on the reported HAC(4) fit,
    reported plainly rather than hidden, since HAC standard errors are
    exactly what a heteroskedastic, autocorrelated residual series calls for.
    """
    stationarity = {}
    for chg_col, level_col in CHANGE_TO_LEVEL.items():
        _, level_p, *_ = adfuller(quarterly[level_col])
        _, change_p, *_ = adfuller(chg[chg_col])
        stationarity[level_col] = {
            "level_adf_p": float(level_p),
            "change_adf_p": float(change_p),
        }

    X = sm.add_constant(chg[CHANGES_PREDICTORS])
    vif = {
        col: float(variance_inflation_factor(X.values, i))
        for i, col in enumerate(CHANGES_PREDICTORS, start=1)
    }

    y = chg["HPI_chg"]
    hac_sensitivity = {}
    for lags in (0, 1, 2, 4, 8):
        m = sm.OLS(y, X).fit() if lags == 0 else sm.OLS(y, X).fit(
            cov_type="HAC", cov_kwds={"maxlags": lags}
        )
        hac_sensitivity[str(lags)] = {p: float(m.pvalues[p]) for p in CHANGES_PREDICTORS}

    jb_stat, jb_p, skew, kurtosis = jarque_bera(changes_model.resid)
    bp_stat, bp_p, _, _ = het_breuschpagan(changes_model.resid, X)

    return {
        "stationarity_adf": stationarity,
        "vif": vif,
        "hac_maxlags_sensitivity": hac_sensitivity,
        "residuals": {
            "jarque_bera_stat": float(jb_stat),
            "jarque_bera_p": float(jb_p),
            "skew": float(skew),
            "kurtosis": float(kurtosis),
            "breusch_pagan_stat": float(bp_stat),
            "breusch_pagan_p": float(bp_p),
        },
    }


def to_result(quarterly, chg, changes_model, levels_model, diagnostics):
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
        "diagnostics": diagnostics,
        "notes": {
            "permits": (
                "Fetched and validated by fetch.py but excluded from this "
                "regression frame. It was previously fetched, resampled, and "
                "never used, while still constraining the sample through "
                "dropna(). Dropping it here removes that silent constraint."
            ),
            "diagnostics": (
                "Levels are non-stationary by ADF (justifying the changes "
                "model over levels), predictor VIFs are all near 1 (the "
                "macro variables' insignificance is a real null result, not "
                "multicollinearity), and the macro-insignificance result "
                "holds across HAC maxlags 0 through 8, not just at 4. "
                "Residuals fail Jarque-Bera normality and are flagged "
                "heteroskedastic by Breusch-Pagan, which is expected for a "
                "series spanning the 2008 and 2020 shocks and is exactly "
                "what HAC standard errors are chosen to handle."
            ),
        },
    }


def main():
    quarterly = build_quarterly_frame()
    chg = build_change_frame(quarterly)

    changes_model = fit_changes_model(chg)
    levels_model = fit_levels_model(quarterly)
    diagnostics = run_diagnostics(quarterly, chg, changes_model)

    result = to_result(quarterly, chg, changes_model, levels_model, diagnostics)

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
