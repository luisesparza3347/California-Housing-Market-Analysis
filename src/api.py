"""FastAPI service that reads model_output.json off the volume. Computes nothing.

Entrypoint for the Deployment. The CronJob is the only thing that ever
writes data/model_output.json; this process only ever reads it. If the file
isn't there, the API says so and returns an error, it does not fetch or fit
anything itself.
"""

import json
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MODEL_OUTPUT_PATH = DATA_DIR / "model_output.json"
QUARTERLY_CHANGES_PATH = DATA_DIR / "quarterly_changes.parquet"

app = FastAPI(title="ca-housing-pipeline API")


def load_model_output():
    """Read model_output.json from the volume. Raises FileNotFoundError if the CronJob hasn't run yet."""
    with open(MODEL_OUTPUT_PATH) as f:
        return json.load(f)


@app.get("/health")
def health():
    """Liveness/readiness probe target. Does not require model_output.json to exist yet."""
    return {"status": "ok"}


@app.get("/model")
def model():
    """The full contents of the CronJob's most recent model_output.json, unmodified."""
    try:
        return load_model_output()
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{MODEL_OUTPUT_PATH.name} not found on the volume. "
                "The fetch-and-model CronJob hasn't completed a run yet."
            ),
        )
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"{MODEL_OUTPUT_PATH.name} is present but not valid JSON: {exc}",
        )


@app.get("/model/coefficients")
def coefficients():
    """Just the changes-model coefficient table, the part a dashboard tile is most likely to want."""
    try:
        result = load_model_output()
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{MODEL_OUTPUT_PATH.name} not found on the volume. "
                "The fetch-and-model CronJob hasn't completed a run yet."
            ),
        )
    return JSONResponse(result["changes_model"]["coefficients"])


@app.get("/data/quarterly-changes")
def quarterly_changes():
    """The row-level quarterly data model.py fit the changes model on, one row per quarter.

    Not a fitted result, the actual observations, so a chart of this can show
    the relationship itself rather than the regression's conclusions about it.
    """
    try:
        df = pd.read_parquet(QUARTERLY_CHANGES_PATH)
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{QUARTERLY_CHANGES_PATH.name} not found on the volume. "
                "The fetch-and-model CronJob hasn't completed a run yet."
            ),
        )
    df = df.reset_index()
    df.columns = ["date"] + list(df.columns[1:])
    df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)
    return JSONResponse(df.to_dict(orient="records"))
