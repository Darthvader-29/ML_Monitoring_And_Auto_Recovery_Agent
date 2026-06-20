# model_b (BACKUP) — FastAPI inference service (deployment_and_devops.md §2.2).
# Identical to model_a except the port. Build context: model-services/ (so the
# shared _common/ package is included). The image's site-packages == venvb.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY model_b/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY _common ./_common
COPY model_b/app.py model_b/model.pkl model_b/sample_input.csv ./

EXPOSE 8002
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]
