# model_b (BACKUP) — FastAPI inference service (deployment_and_devops.md §2.2).
# Identical to model_a except the port. The image's site-packages == venvb.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py metrics.py model.pkl sample_input.csv ./

EXPOSE 8002
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]
