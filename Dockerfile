FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/

# Default command runs the fetch job followed by the model, which is what the
# CronJob needs on every scheduled tick: fresh raw series, then a model refit
# against them. The Deployment overrides this command to run the API server
# instead (uvicorn src.api:app), so the same image serves both entrypoints.
CMD ["sh", "-c", "python -m src.fetch && python -m src.model"]
