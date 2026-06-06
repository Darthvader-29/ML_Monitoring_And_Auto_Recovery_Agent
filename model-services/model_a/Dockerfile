# model_a (ACTIVE) — FastAPI inference service (deployment_and_devops.md §2.1).
# Build context: model-services/model_a/. The image's site-packages == venva.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# deps first (cached layer), then code + artifact + sample
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py metrics.py model.pkl sample_input.csv ./

EXPOSE 8001
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
