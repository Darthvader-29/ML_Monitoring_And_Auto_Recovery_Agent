# model_a (ACTIVE) — FastAPI inference service (deployment_and_devops.md §2.1).
# Build context: model-services/ (so the shared _common/ package is included).
# The image's site-packages == venva.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# deps first (cached layer), then the shared base + this service's entrypoint/artifact
COPY model_a/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY _common ./_common
COPY model_a/app.py model_a/model.pkl model_a/sample_input.csv ./

EXPOSE 8001
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
