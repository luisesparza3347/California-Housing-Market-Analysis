import pandas as pd
import statsmodels.api as sm

START = "1990-01-01"
END = "2024-12-31"

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"

SERIES = {
    "HPI": "CASTHPI",            # FHFA all-transactions HPI, California, quarterly
    "Mortgage_30yr": "MORTGAGE30US",  # Freddie Mac PMMS 30-year, weekly
    "Unemployment_Rate": "CAUR",      # California unemployment rate, monthly
    "CPI": "CUUR0400SA0",             # CPI-U, Los Angeles area, monthly
    "Permits": "CABPPRIV",            # CA private housing units authorized, monthly
}


def fetch(series_id):
    """Pull one FRED series into a dated Series. Returns None on failure."""
    try:
        df = pd.read_csv(FRED_CSV.format(series_id))
    except Exception as exc:
        print(f"  could not fetch {series_id}: {exc}")
        return None

    date_col = df.columns[0]
    value_col = df.columns[1]
    df[date_col] = pd.to_datetime(df[date_col])
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    return df.set_index(date_col)[value_col]


# Fetch everything, then average each series up to quarterly
quarterly = {}
for name, series_id in SERIES.items():
    print(f"fetching {name} ({series_id})")
    raw = fetch(series_id)
    if raw is None:
        continue
    quarterly[name] = raw.resample("QE").mean()

df = pd.DataFrame(quarterly).loc[START:END]
df = df.dropna()

print("\nquarterly frame:", df.shape)
print(df.head())
print(df.tail())

# Quarter-over-quarter percent change. Levels of trending series produce
# spurious significance, so the model is estimated on changes.
chg = df.pct_change().dropna() * 100
chg.columns = [f"{c}_chg" for c in chg.columns]

chg['HPI_chg_lag1'] = chg['HPI_chg'].shift(1)
chg = chg.dropna()

print("\nchange frame:", chg.shape)
print(chg.describe())

# Regression on changes, with HAC standard errors for serial correlation
predictors = ["HPI_chg_lag1", "Mortgage_30yr_chg", "Unemployment_Rate_chg", "CPI_chg"]
predictors = [p for p in predictors if p in chg.columns]

X = sm.add_constant(chg[predictors])
y = chg["HPI_chg"]

model = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": 4})
print("\n" + "=" * 60)
print("QOQ CHANGES, HAC STANDARD ERRORS")
print("=" * 60)
print(model.summary())

# Same model on levels, shown only to demonstrate the spurious-trend problem
level_cols = ["Mortgage_30yr", "Unemployment_Rate", "CPI"]
X_lvl = sm.add_constant(df[level_cols])
model_lvl = sm.OLS(df["HPI"], X_lvl).fit()
print("\n" + "=" * 60)
print("LEVELS (for contrast, not the reported model)")
print("=" * 60)
print(f"R-squared: {model_lvl.rsquared:.3f}")
print(f"Durbin-Watson: {sm.stats.durbin_watson(model_lvl.resid):.3f}")