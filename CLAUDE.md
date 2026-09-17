# ca-housing-pipeline

California house price analysis, rebuilt as a scheduled pipeline. A containerized
job refreshes five FRED series on a cron schedule into a persistent volume, an API
serves the model results off that same volume, and a Power BI dashboard reads the
metro cross-section built alongside it.

This file holds decisions that are already made. Do not relitigate them. If
something here turns out to be wrong, say so and stop rather than working around it.

## Working agreement

- Windows 11, PowerShell, PyCharm. Give PowerShell syntax, not bash.
- The author is an Economics undergrad with a CS minor. Strong on pandas,
  statsmodels, SQL. New to Docker and Kubernetes as of this project. Explain
  infra concepts when they first come up; do not explain pandas.
- Commit as you go, one logical change per commit, real messages. The commit
  history is part of the deliverable.
- No em dashes and no colons in prose written for the README or docs.
- When a decision below conflicts with something you would normally do, follow
  the decision and flag the disagreement in chat.

## The finding this project reports

California house price growth is dominated by its own momentum. Once a one quarter
lag of HPI growth is in the model, quarterly changes in mortgage rates,
unemployment, and inflation add nothing.

Final statewide model, 1990Q1 to 2024Q4, 140 quarters, 138 in the changes model,
HAC(4) standard errors:

| Term | Coefficient | p |
|------|------------|---|
| HPI_chg_lag1 | 0.8245 | <0.001 |
| Mortgage_30yr_chg | -0.0136 | 0.556 |
| Unemployment_Rate_chg | -0.0042 | 0.306 |
| CPI_chg | -0.1159 | 0.762 |

R^2 0.673, Durbin-Watson 2.012. Levels version for contrast: R^2 0.903,
Durbin-Watson 0.080, which is the spurious-trend result the changes model exists
to avoid.

**Correction, made during the Day 2 build.** The claim above, that unemployment
looked significant at -0.265 (p = 0.003) before the lag was added, does not
reproduce. It was checked against every specification tried (pct_change vs
point-diff, HAC vs plain OLS, QoQ vs YoY) and against the full git history of
the original `fred_loader.py`, which never computed a no-lag model at all. The
number isn't in this project's code history anywhere; it most likely came from
the excluded Word write-up and was carried into this file without being
re-derived. Per this file's own rule, this turned out to be wrong, so it is
being said plainly rather than worked around. It is deliberately not in the
README. Full investigation notes are in `docs/session-log.md` under Day 2.

## FRED series

| Name | ID | Notes |
|------|-----|-------|
| HPI | CASTHPI | FHFA all-transactions, California, quarterly |
| Mortgage_30yr | MORTGAGE30US | Freddie Mac PMMS 30 year, weekly |
| Unemployment_Rate | CAUR | California, monthly |
| CPI | CUUR0400SA0 | CPI-U **West region**, monthly |
| Permits | CABPPRIV | CA private housing units authorized, monthly |

CPI is the West region series, NOT Los Angeles. The LA series CUURA421SA0 was
discontinued in December 2017 in the BLS geographic revision. The existing code
comment says Los Angeles and is wrong. Fix the comment and explain the
substitution in the README, because the reason shows the source was checked.

All series are resampled to quarterly means before modeling.

## Locked decisions

1. **Permits is currently fetched, resampled, and never used in the model, but it
   still constrains the sample through dropna().** Resolve this explicitly. Either
   bring it into the model or drop it from the regression frame. A silent sample
   constraint is not acceptable. Whichever way it goes, say so in the README.

2. **Everything writes to disk.** The current loader prints to stdout and saves
   nothing. A scheduled job that leaves no artifact is pointless. Raw series go to
   the volume as parquet, model output goes as JSON.

3. **The API computes nothing.** It reads what the job wrote. If the volume is
   empty it returns a clear error, it does not fall back to fetching.

4. **Validation is in scope, not optional.** This pulls from a live API on a
   schedule, so it can fail in ways a static dataset cannot. The fetch job asserts
   on row counts, date coverage, value ranges, and staleness of the most recent
   observation. A failed assert fails the job loudly.

5. **The metro cross-section stays, reframed.** The 24 row build was diagnosed as
   effectively 6 time points: HPI and both macro series vary by date only, and
   only CPI has genuine cross-metro variation, with a null coefficient at p = 0.97.
   It is not a regression dataset. It is the data prep for the Power BI dashboard,
   which is what the wide format was always actually for. The README says this
   plainly. Auditing your own earlier work is a feature, not something to hide.

6. **Power BI stays** as a cleaned .pbix plus PNG screenshots in dashboard/. It
   needs rework to match the statewide model. It is not served from the cluster,
   which is not possible, and the README should not imply otherwise.

7. **Cut Test.py.** It imports the main module, which re-runs the whole pipeline
   as a side effect just to print column names.

8. **The old Word write-up does not go in the repo.** Appendices A and B have 2023
   figures mislabeled as 2024 and appendices C to E are visibly AI generated. The
   README replaces it.

## Target structure

```
src/
  fetch.py               # pull + validate five FRED series, write parquet
  model.py               # changes model with HAC(4), writes JSON
  api.py                 # FastAPI, reads volume, computes nothing
  build_metro_panel.py   # reframed cross-section, feeds Power BI
k8s/
  kind-config.yaml
  pvc.yaml
  cronjob.yaml
  deployment.yaml
  service.yaml
dashboard/               # .pbix + PNG screenshots
docs/
  architecture.md        # diagram + data flow
notebooks/
  eda.ipynb              # not in the original plan, added post-Day-3
data/                    # gitignored, local only
figures/
Dockerfile
README.md
requirements.txt
```

One image, two entrypoints: the fetch job and the API server.

## Build order

Front-load the thinking-heavy work. Infra last, because it is the part that can
eat a day if something goes sideways.

**Day 1, done.** Restructure into folders, git init, gitignore before the first add.
Rework fred_loader into src/fetch.py and src/model.py with real outputs and the
validation asserts. Resolve the Permits question. Fix the CPI comment. Reframe
the metro script. Verify the model reproduces the coefficients above.

**Day 2, done.** Dockerfile, build, run the fetch job in a container against a local
volume mount. Then kind cluster, PVC, CronJob. Confirm the CronJob writes and the
data survives a pod restart, which is the whole point of the PVC.

**Day 3, API and k8s done, Power BI not.** FastAPI, Deployment, Service, and the
README and architecture diagram were finished (a day ahead of schedule, during
Day 2). A `/data/quarterly-changes` route and its `quarterly_changes.parquet`
artifact were added afterward, for the Power BI rework to connect to. Power BI
rework and screenshots are still outstanding, that is the only remaining item.
Repo cleanup and push, still outstanding, held until the dashboard is in.

## The gate

If the Kubernetes pieces are not working by the end of day 2, cut the FastAPI
service and let the CronJob write results that Power BI reads directly. Docker,
the CronJob, and the PVC stay in all cases, because those are what the resume
bullet claims. Do not cut the README to save time.

**The gate never triggered.** Docker, the CronJob, the PVC, the API, and its
Deployment and Service all work as built, verified against the live kind
cluster, not just written and assumed correct.

## Resume bullet

There is a live bullet on the resume describing this infrastructure. The work did
not exist when the bullet was written. This build is what makes the bullet true.
Write the final bullet to match what actually got built, not the other way around.

## Repo state at project start (historical, kept for context)

This section describes what was on disk before Day 1, before any of the
locked decisions above were applied. It is no longer current, all of it has
since been restructured, and is kept only so the "why" behind decisions 1-4
and 7 above has the original mess to point back to.

- `fred_loader.py` was the script that became `src/fetch.py` and
  `src/model.py`. It printed to stdout and wrote nothing, still had the Los
  Angeles CPI comment, and had no validation asserts.
- `Housing_and_Inflation_analysis.py` was the metro cross-section build,
  hardcoded to four CBSAs and 2024 only, now `src/build_metro_panel.py`.
  `Test.py` imported it as a module, rerunning the whole build as a side
  effect just to print column names, now cut.
- Seven input CSVs lived in the repo root by relative path, not under
  `data/`. A stray `master.csv`, written by neither script, sat alongside
  them and was deleted.
- No `requirements.txt` existed.

**For what is actually true right now, README.md's "Repository layout",
"Locked decisions", and "What's left" sections are the current source of
truth, not this file.** This file is the plan and the decisions behind it,
not a live status tracker, and will drift out of date if treated as one.

## Commands

PowerShell, from the repo root, with the venv activated. See README.md's
"Running it" section for the current, complete set, this is not repeated
here to avoid two copies drifting apart.

## Notes on the metro cross-section script

Worth knowing before touching `src/build_metro_panel.py` (formerly
`Housing_and_Inflation_analysis.py`, relocated and reframed per decision 5,
its analytical logic below is otherwise unchanged from the original).

- It is hardcoded to exactly four CBSAs (LA, San Diego, San Francisco, Riverside)
  and the six CPI-reporting months of 2024 (`DATES_2024`). Extending to more
  metros or years means redoing `RELEVANT_CBSAS`, `CBSA_COUNTIES`, and
  `COUNTY_CITIES`, not just widening a date range.
- City-to-CBSA assignment goes through county membership via a hand-built
  `COUNTY_CITIES` dict, because the population file has city names but CBSAs are
  defined as whole counties.
- The San Diego and Riverside CPI columns are shifted forward one row
  (`.shift(1)`) to correct for those two metros reporting on the off month from
  the other two.
- The asserts check exact row counts (4 CBSAs, 24 final rows), not just
  null-checks, so a future data source swap will fail loudly if the grid shape
  changes rather than silently dropping rows.
