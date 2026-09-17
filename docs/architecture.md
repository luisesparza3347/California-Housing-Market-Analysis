# Architecture

What's actually running, as of the end of Day 2 of this project's build.
The API, Deployment, and Service in the diagram below are Day 3 and not yet
built; they're included because the image and the volume are already
designed around them existing.

## Data flow

```
                    FRED API (fred.stlouisfed.org)
                              |
                              | CSV over HTTPS, full history per series
                              v
                   +----------------------+
                   |   src/fetch.py       |
                   |   pull + validate    |
                   +----------------------+
                              |
                              | writes native-frequency parquet
                              v
                   data/raw/{HPI,Mortgage_30yr,        <-- PersistentVolumeClaim
                             Unemployment_Rate,             "housing-data" in k8s
                             CPI,Permits}.parquet
                              |
                              | read + resample to quarterly
                              v
                   +----------------------+
                   |   src/model.py       |
                   |   HAC(4) regression  |
                   |   + diagnostics      |
                   +----------------------+
                              |
                              | writes
                              v
                   data/model_output.json  <-- same PersistentVolumeClaim
                              |
                              | (Day 3, not yet built)
                              v
                   +----------------------+       +-------------------+
                   |   src/api.py          | <---- |  Power BI reads   |
                   |   FastAPI, reads the  |       |  data/*.csv       |
                   |   volume, computes    |       |  directly, not    |
                   |   nothing             |       |  through the API  |
                   +----------------------+       +-------------------+
```

`src/build_metro_panel.py` runs independently of the fetch-and-model chain
above. It reads seven local CSVs (population, HPI, CPI, mortgage, income,
employment, permits) rather than pulling from FRED directly, and writes
`data/metro_cross_section_2024.csv`, which is what the Power BI dashboard
actually reads. It is data prep for the dashboard, not part of the
statewide regression pipeline, and not a regression dataset itself; see the
README's "Locked decisions" section for why.

## What's deployed in the kind cluster

```
kind cluster "ca-housing"
  |
  +-- PersistentVolumeClaim "housing-data" (1Gi, default "standard" storage class)
  |     mounted at /app/data by every pod below
  |
  +-- CronJob "fetch-and-model"
  |     schedule: daily at 06:00 UTC
  |     image: ca-housing-pipeline:dev (loaded locally via `kind load docker-image`,
  |            not pulled from a registry)
  |     command: python -m src.fetch && python -m src.model
  |
  +-- (Day 3) Deployment running the same image with its command overridden
  |     to `uvicorn src.api:app`, mounting the same PVC read-only
  |
  +-- (Day 3) Service exposing that Deployment, NodePort 30080,
        mapped to host port 8080 in kind-config.yaml
```

One image serves both the CronJob and the future API Deployment. The
Dockerfile's default command is the fetch-and-model chain; the Deployment
will override that command rather than needing a second image.

## Why fetch and model run as one chained command, not two CronJobs

The dependency between them is sequential and hard, not two independent
concerns on their own schedules. If they were separate CronJobs, a model run
could fire before a given day's fetch had finished, or after fetch failed
its validation asserts, and would then fit against stale or partial raw
parquet without anything catching it. Chaining them in one pod means a
failed fetch (a failed assert, a network error) stops the command before
model.py ever runs, so `model_output.json` is never rewritten against data
that didn't pass validation.

## Verified during the Day 2 build

- Building the image and running it locally against a bind-mounted `data/`
  directory produces the same output as running `fetch.py` and `model.py`
  directly in the venv.
- Loading that image into the kind cluster and running it as a one-off Job
  created from the CronJob's own spec (`kubectl create job
  --from=cronjob/fetch-and-model`) completes successfully and writes to the
  PVC.
- Deleting that Job's pod entirely, then mounting the same PVC from a
  brand-new pod, shows every parquet file and `model_output.json` still
  present. That's the actual guarantee a PVC is for, checked directly rather
  than assumed from Kubernetes documentation.
