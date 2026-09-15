"""Pull the five FRED series, validate them, write raw parquet to data/raw/.

Entrypoint for the scheduled fetch job. Writes one parquet file per series at
its native reporting frequency (weekly, monthly, quarterly). Resampling to a
common quarterly frequency is model.py's job, not this one, so the raw data on
disk always matches what FRED actually published.
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

START = "1990-01-01"

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"

# Each series: FRED id, plausible value range, and how stale the latest
# observation is allowed to be given that agency's normal reporting lag.
SERIES = {
    "HPI": {
        "id": "CASTHPI",  # FHFA all-transactions HPI, California, quarterly
        "value_range": (0, 2000),
        "max_staleness_days": 200,  # FHFA can lag a quarter close by ~2-3 months
    },
    "Mortgage_30yr": {
        "id": "MORTGAGE30US",  # Freddie Mac PMMS 30 year, weekly
        "value_range": (0, 25),
        "max_staleness_days": 21,
    },
    "Unemployment_Rate": {
        "id": "CAUR",  # California unemployment rate, monthly
        "value_range": (0, 30),
        "max_staleness_days": 100,
    },
    "CPI": {
        # CPI-U, West region (CUUR0400SA0), monthly. NOT Los Angeles: the LA
        # series CUURA421SA0 was discontinued in the BLS geographic revision
        # of December 2017.
        "id": "CUUR0400SA0",
        "value_range": (0, 1000),
        "max_staleness_days": 100,
    },
    "Permits": {
        "id": "CABPPRIV",  # CA private housing units authorized, monthly
        "value_range": (0, 200_000),
        "max_staleness_days": 100,
    },
}


def fetch(series_id):
    """Pull one FRED series as a dated Series, full history, no slicing."""
    df = pd.read_csv(FRED_CSV.format(series_id))
    date_col = df.columns[0]
    value_col = df.columns[1]
    df[date_col] = pd.to_datetime(df[date_col])
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    return df.set_index(date_col)[value_col].dropna()


def validate(name, series, meta):
    """Assert row count, date coverage, value range, and staleness. Raises on failure."""
    assert len(series) > 0, f"{name}: no rows returned"

    assert series.index.min() <= pd.Timestamp(START), (
        f"{name}: coverage starts at {series.index.min().date()}, "
        f"expected data back to {START}"
    )

    lo, hi = meta["value_range"]
    out_of_range = series[(series < lo) | (series > hi)]
    assert out_of_range.empty, (
        f"{name}: {len(out_of_range)} values outside plausible range "
        f"[{lo}, {hi}], e.g. {out_of_range.iloc[0]}"
    )

    staleness = (datetime.now() - series.index.max()).days
    assert staleness <= meta["max_staleness_days"], (
        f"{name}: latest observation is {series.index.max().date()}, "
        f"{staleness} days old, exceeds the {meta['max_staleness_days']} day limit"
    )


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    for name, meta in SERIES.items():
        print(f"fetching {name} ({meta['id']})")
        series = fetch(meta["id"])
        validate(name, series, meta)

        out_path = RAW_DIR / f"{name}.parquet"
        series.rename(name).to_frame().to_parquet(out_path)
        print(
            f"  ok: {len(series)} rows, "
            f"{series.index.min().date()} to {series.index.max().date()}, "
            f"wrote {out_path}"
        )


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"VALIDATION FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
