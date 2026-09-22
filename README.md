# California Housing Market Analysis

A scheduled data pipeline that pulls five California housing-related series
from FRED, fits a statewide house price model, and builds a metro-level
cross-section for a Power BI dashboard. Originally a one-off analysis script,
rebuilt here as a containerized job running on a schedule against a
persistent volume.

## The finding

California house price growth is dominated by its own momentum. Once a one
quarter lag of HPI growth is in the model, quarter-over-quarter changes in
mortgage rates, unemployment, and inflation add nothing.

![Coefficient plot showing HPI growth lag dominates, while the three macro terms sit at zero](figures/coefficients.png)

![Scatter of HPI growth against its own prior-quarter value, with a fitted line](figures/momentum_scatter.png)

Statewide changes model, HAC(4) standard errors, 1990Q1 through 2024Q4, 140
quarters, 138 observations in the changes regression.

| Term | Coefficient | p |
|------|------------|---|
| HPI_chg_lag1 | 0.8245 | <0.001 |
| Mortgage_30yr_chg | -0.0136 | 0.556 |
| Unemployment_Rate_chg | -0.0042 | 0.306 |
| CPI_chg | -0.1159 | 0.762 |

R-squared 0.673, Durbin-Watson 2.012. A levels version of the same
relationship, kept only for contrast, gets R-squared 0.903 with a
Durbin-Watson of 0.080, which is the spurious-trend result the changes model
exists to avoid, not a competing finding.

Because this is now a scheduled job, `model.py` does not pin its sample end
date to 2024Q4. START stays fixed at 1990-01-01 and END rolls forward to
whatever the latest complete quarter in the raw data is, so every cron run
picks up new quarters automatically. The table above is the finding as
reported against that window. Running the pipeline today reproduces the same
story against a longer, rolling sample (currently 1990Q1 through 2026Q2, 146
quarters), with HPI_chg_lag1 still dominant at p on the order of 1e-33 and
the three macro terms still statistically indistinguishable from zero.

## Model diagnostics

`model.py` now runs four checks against its own design, and writes the
results into `model_output.json` alongside the coefficients, rather than
leaving the model's assumptions untested.

**Stationarity.** An Augmented Dickey-Fuller test on each series in levels
fails to reject a unit root for all four (p from 0.08 to 0.999), which is
the actual justification for modeling quarter-over-quarter changes instead
of levels. After differencing, HPI, mortgage rates, and unemployment are all
clearly stationary (p below 0.03); CPI is borderline (p around 0.06) but
close.

**Multicollinearity.** Variance inflation factors across the four predictors
run from 1.06 to 1.22, nowhere near a level that would suggest one
predictor's effect is being absorbed by another. The macro variables'
insignificance is a real null result, not multicollinearity in disguise.

**HAC lag sensitivity.** Refitting at HAC maxlags of 0, 1, 2, 4, and 8 leaves
the story unchanged at every setting. HPI_chg_lag1 stays significant at
p near zero throughout, and all three macro terms stay solidly
insignificant, with p-values between 0.3 and 0.8 regardless of lag length.
The result isn't an artifact of picking exactly 4 lags.

**Residuals.** The reported HAC(4) model's residuals fail a Jarque-Bera
normality test (fat-tailed, kurtosis around 5.6) and are flagged
heteroskedastic by a Breusch-Pagan test (p around 0.03). That's expected for
a housing series spanning the 2008 crash and the 2020 shock, and it's the
actual reason HAC standard errors, not plain OLS, are used for inference
here.

## Robustness checks

Three follow-up checks, prompted by comparing this project's results against
two specific published sources, also live in `model.py` and
`model_output.json`. Both sources are named directly rather than paraphrased
as "the literature," since a claim like this is only as good as the citation
behind it.

- Adam Guren, "House Price Momentum and Strategic Complementarity,"
  *Journal of Political Economy* 126(3), 2018, pp. 1172-1218.
- William D. Larson, "Effects of Mortgage Interest Rates on House Price
  Appreciation, The Role of Payment Constraints," FHFA Working Paper 22-04,
  2022.

**Does momentum extend past one lag, as Guren's research would predict.**
Guren documents house price serial correlation persisting 8 to 14 quarters,
well past the single lag the core model uses. Refitting HPI_chg on its own
lags 1 through 8 shows real information beyond lag 1, R-squared climbs from
0.669 at one lag to 0.716 at four, and BIC prefers 4 lags over 8, meaning
there's more here than the core model captures, but not an unbounded amount
more. The coefficients at higher lags alternate in sign (lag 2 negative, lag
3 positive), which turned out to have a specific explanation, see below.

**Does a delayed effect rescue any of the three macro variables, per
Larson's transmission mechanism.** Larson finds mortgage rate changes affect
house prices partly through payment constraints on borrowers, a channel that
plausibly takes more than one quarter to show up, and the core model here
only ever tests mortgage rate, unemployment, and CPI changes in the same
quarter as HPI growth. Testing each one at lags 0 through 3 instead
(controlling for HPI_chg_lag1) checks whether that kind of delayed effect is
present in this state-level, quarterly data. It isn't. No lag of any of the
three variables reaches significance, so a transmission delay isn't hiding
an effect the contemporaneous model missed, at least not one this
specification can detect.

**Is the core result an artifact of unremoved seasonality.** CASTHPI, the
exact FRED series this project pulls, is officially Not Seasonally Adjusted,
and there is no seasonally-adjusted state-level alternative published on
FRED. Mean HPI_chg does differ by calendar quarter across the sample
(roughly 0.84 percent in Q1 versus 1.33 percent in Q3, 1990 to present),
which is a real, decades-long pattern, not noise. Adding calendar-quarter
dummies to the core model leaves HPI_chg_lag1 essentially unchanged (0.824
becomes 0.842, still significant at p near zero), so the headline finding
survives. Two supporting numbers move, though, and reporting the original
figures without this check would have overstated how settled they are.
Unemployment_Rate_chg shifts from clearly insignificant (p 0.31) to
borderline (p 0.07), and CPI_chg's point estimate roughly triples in size
(-0.12 to -0.43) while staying insignificant either way. Separately, adding
the same calendar-quarter dummies to the 4-lag HPI-only model from the
momentum check above barely changes its alternating-sign pattern, and the
dummies themselves stop being significant once 4 HPI lags are already
included. That means the AR(4) model's own lag structure is already
absorbing most of the seasonal signal on its own, rather than the
alternating signs being a separate, unexplained problem.

## FRED series

| Name | ID | Notes |
|------|-----|-------|
| HPI | CASTHPI | FHFA all-transactions, California, quarterly |
| Mortgage_30yr | MORTGAGE30US | Freddie Mac PMMS 30 year, weekly |
| Unemployment_Rate | CAUR | California, monthly |
| CPI | CUUR0400SA0 | CPI-U West region, monthly |
| Permits | CABPPRIV | CA private housing units authorized, monthly |

The CPI series is CPI-U for the West region, not Los Angeles. An earlier
version of this project's code had a comment claiming the CPI series was Los
Angeles specific. It wasn't ever pulling a Los Angeles-only series; the
comment was simply wrong. The Los Angeles series, CUURA421SA0, was
discontinued in December 2017 as part of a BLS geographic revision, which is
presumably why an LA-specific series was never a live option for a pipeline
meant to run today. The comment is now fixed in `fetch.py`.

Permits is fetched, validated, and written to disk like the other four
series, but deliberately left out of the regression below. It was never
part of the model, yet was still shrinking the merged sample through a
`dropna()` call whenever it happened to be missing, a silent constraint
from a column nobody was using. It's excluded from the regression frame
explicitly now instead.

All series are resampled to quarterly means before modeling.

## Metro cross-section

`src/build_metro_panel.py` builds a separate, wide CBSA-by-month grid (4
CBSAs, 6 months of 2024) that feeds the Power BI dashboard. It isn't a
regression dataset. HPI, mortgage rates, and unemployment vary by date
only, carrying no cross-metro information at all, and only CPI genuinely
varies by metro, with a null coefficient at p = 0.97 when tested directly.
The 24 rows are effectively 6 time points repeated four times, not 24
independent observations. It stays in the pipeline as data prep for the
dashboard, which is what the wide format was always actually for, not as a
second regression.

## Data cleaning and exploration

`notebooks/eda.ipynb` checks the five raw series for missing values and
reporting gaps before they reach `model.py`, and shows exactly what the
quarterly resampling and merge do to the sample. Two findings there are
worth knowing without opening it.

CPI and Unemployment_Rate are both missing an October 2025 observation, a
real BLS data gap from the 2025 government shutdown (October 1 to
November 12), not a fetch bug. BLS cancelled the October CPI print outright
and has said the October 2025 unemployment rate will never be collected. It
thins one quarter's average, it does not create a missing quarter.

Exactly one quarter gets dropped from the regression frame, the current,
still-incomplete trailing quarter, since HPI hasn't been published for it
yet. That is the mechanism behind this README's earlier claim that
`model.py` rolls its sample end date forward to the latest complete
quarter, confirmed directly in the notebook rather than assumed.

Not part of the containerized pipeline, matplotlib and Jupyter are
local-only, deliberately left out of `requirements.txt`.

## Pipeline and API

`fetch.py` writes each raw series to `data/raw/` as parquet at its native
reporting frequency, and asserts on row count, date coverage, value ranges,
and staleness before writing anything, so a bad pull fails the job loudly
instead of landing partial data. `model.py` reads that parquet, resamples
to quarterly, and writes `model_output.json` and `quarterly_changes.parquet`
(the row-level data the regression is actually fit on, not just the fitted
summary).

`src/api.py` reads whatever that job most recently wrote off the volume,
mounted read-only, and nothing else, it does not fetch or fit anything
itself. If the data isn't there yet, every route that needs it returns a
503 with a clear message instead of silently falling back, verified
directly by removing `model_output.json` and confirming the error. Routes
are listed under "Running it" below. Full data flow diagram in
`docs/architecture.md`.

## Repository layout

```
src/
  fetch.py               # pull + validate five FRED series, write parquet
  model.py               # changes model with HAC(4) and diagnostics, writes JSON
  api.py                 # FastAPI, reads volume, computes nothing
  make_figures.py        # local-only, generates the two PNGs in figures/
  build_metro_panel.py   # Power BI data prep, see "Metro cross-section" above
k8s/
  kind-config.yaml
  pvc.yaml
  cronjob.yaml
  deployment.yaml         # runs the API, verified against the kind cluster
  service.yaml            # NodePort 30080, verified against the kind cluster
dashboard/               # .pbix + screenshots (not yet built)
docs/
  architecture.md
notebooks/
  eda.ipynb               # local-only, data cleaning and exploration
data/                    # gitignored, local only
figures/                 # coefficient plot + momentum scatter
Dockerfile
README.md
requirements.txt
```

One image, two entrypoints. The Dockerfile's default command chains
`fetch.py` then `model.py`, which is what the CronJob runs. The Deployment
overrides that same image's command to run `uvicorn src.api:app` instead, so
nothing about the image changes between the batch job and the API.

## Running it

PowerShell, from the repo root, with the venv activated.

```powershell
.venv\Scripts\Activate.ps1
python src\fetch.py             # pulls and validates the 5 FRED series, writes data\raw\*.parquet
python src\model.py             # fits the model, writes data\model_output.json
python src\build_metro_panel.py # builds the Power BI cross-section
python src\make_figures.py      # regenerates figures\*.png from the above (needs matplotlib)
uvicorn src.api:app --reload    # serves model_output.json at localhost:8000
```

### In Docker

```powershell
docker build -t ca-housing-pipeline:dev .
docker run --rm -v "${PWD}\data:/app/data" ca-housing-pipeline:dev
```

This runs the same chained fetch-then-model command the CronJob uses,
against a host-mounted `data/` directory instead of a PVC.

### On a kind cluster

```powershell
kind create cluster --name ca-housing --config k8s\kind-config.yaml
kind load docker-image ca-housing-pipeline:dev --name ca-housing
kubectl apply -f k8s\pvc.yaml
kubectl apply -f k8s\cronjob.yaml
kubectl apply -f k8s\deployment.yaml
kubectl apply -f k8s\service.yaml
```

Once the Deployment's pod is ready, the API is reachable from the host at
`localhost:8080` (the NodePort mapping reserved in `kind-config.yaml`),
independent of whether the CronJob has run yet.

```powershell
curl http://localhost:8080/health
curl http://localhost:8080/model                    # full model_output.json
curl http://localhost:8080/model/coefficients        # just the coefficient table
curl http://localhost:8080/data/quarterly-changes    # row-level data behind it
```

The CronJob is scheduled daily at 06:00 UTC. To trigger a run immediately
instead of waiting for the schedule, run a one-off Job from the same spec.

```powershell
kubectl create job --from=cronjob/fetch-and-model manual-test
kubectl logs job/manual-test
```

This was tested end to end during the build. A Job created this way ran
successfully against the PVC, and after deleting that Job's pod entirely, a
fresh pod mounted the same PVC and could still read every parquet file and
`model_output.json` it had written, which is the actual point of using a
PVC instead of the container's own filesystem.

## Next steps

The Power BI dashboard is the one piece not yet reworked to match the
statewide model. It can pull `/model/coefficients` and
`/data/quarterly-changes` live from the API, via Power BI Desktop's Get
Data > Web connector against a locally running kind cluster, instead of
only reading static CSVs. Screenshots will land in `dashboard/` once it's
done.
