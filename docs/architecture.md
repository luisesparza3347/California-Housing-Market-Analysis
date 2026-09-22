# Architecture

What's actually running. The API, Deployment, and Service are built and
verified against the kind cluster. Only the Power BI dashboard rework is
still outstanding.

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
                   data/model_output.json,        <-- same PersistentVolumeClaim
                   data/quarterly_changes.parquet
                              |
                              | read-only mount
                              v
                   +----------------------+       +-------------------+
                   |   src/api.py          | <---- |  Power BI Desktop |
                   |   FastAPI, reads the  |       |  Get Data > Web,  |
                   |   volume, computes    |       |  localhost:8080,  |
                   |   nothing             |       |  local-only       |
                   +----------------------+       +-------------------+
```

`quarterly_changes.parquet` is the row-level data the changes model is fit
on (one row per quarter), not the fitted result, written specifically so a
chart can show the actual relationship instead of only the regression's
conclusions about it. It's served at `/data/quarterly-changes`.
`/model/coefficients` still exists too, for a small live callout rather than
a chart. Power BI can also still read `data/*.csv` directly, and still does
for the metro cross-section, which the API doesn't serve.

`src/build_metro_panel.py` runs independently of the fetch-and-model chain
above. It reads seven local CSVs (population, HPI, CPI, mortgage, income,
employment, permits) rather than pulling from FRED directly, and writes
`data/metro_cross_section_2024.csv`, which is what the Power BI dashboard
actually reads. It is data prep for the dashboard, not part of the
statewide regression pipeline, and not a regression dataset itself; see the
README's "Metro cross-section" section for why.

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
  +-- Deployment "housing-api", 1 replica, same image with its command
  |     overridden to `uvicorn src.api:app`, mounting the same PVC
  |     read-only, readiness/liveness probes against /health
  |
  +-- Service "housing-api", type NodePort, exposing that Deployment on
        NodePort 30080, mapped to host port 8080 in kind-config.yaml
```

One image serves both the CronJob and the API Deployment. The Dockerfile's
default command is the fetch-and-model chain; the Deployment overrides that
command rather than needing a second image.

## Why fetch and model run as one chained command, not two CronJobs

The dependency between them is sequential and hard, not two independent
concerns on their own schedules. If they were separate CronJobs, a model run
could fire before a given day's fetch had finished, or after fetch failed
its validation asserts, and would then fit against stale or partial raw
parquet without anything catching it. Chaining them in one pod means a
failed fetch (a failed assert, a network error) stops the command before
model.py ever runs, so `model_output.json` is never rewritten against data
that didn't pass validation.

## Verification

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

- `src/api.py` tested locally with `uvicorn --reload`. `/health` and
  `/model/coefficients` return 200 against a real `model_output.json`;
  removing that file and re-requesting `/model` returns a 503 with a clear
  message, not a fallback computation.
- The image was rebuilt to include `api.py` and its dependencies, reloaded
  into the running kind cluster, and `k8s/deployment.yaml` +
  `k8s/service.yaml` applied against the same PVC the CronJob already
  populated. The pod reached ready via its `/health` probe, and `curl
  http://localhost:8080/health` and `curl
  http://localhost:8080/model/coefficients` from the host both returned
  correctly through the NodePort mapping. Same as the CronJob's PVC
  persistence check, this was run against the live cluster, not assumed
  from the YAML.
- After adding `/data/quarterly-changes`, the image was rebuilt again, the
  Deployment restarted (`kubectl rollout restart`) to pick up the new route,
  and a one-off Job from the CronJob's own spec re-ran `fetch.py` and
  `model.py` to write `quarterly_changes.parquet` onto the PVC. `curl
  http://localhost:8080/data/quarterly-changes` from the host returned 144
  quarterly records, confirming the Deployment's restart and the Job's write
  both landed on the same PVC the API pod already had mounted.
