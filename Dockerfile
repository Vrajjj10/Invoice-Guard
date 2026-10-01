FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    ENVIRONMENT=production

# libgl/glib: runtime libs opencv (pulled in by rapidocr) needs on slim images
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts/train_anomaly.py ./scripts/train_anomaly.py
COPY data/vendors.json ./data/vendors.json

# Anomaly model is trained on synthetic data at build time (artifact is git-ignored)
RUN mkdir -p models data/uploads \
    && python scripts/train_anomaly.py \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request as u; u.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)"

# Secrets (GEMINI_API_KEY, DEMO_API_KEY, SLACK_WEBHOOK_URL) come from the environment at runtime
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
