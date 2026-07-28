FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir \
    --index-url https://mirrors.cloud.tencent.com/pypi/simple \
    --timeout 120 \
    --retries 10 \
    -r requirements.txt

COPY requirements-mcp.txt .
RUN pip install --no-cache-dir \
    --index-url https://mirrors.cloud.tencent.com/pypi/simple \
    --timeout 120 \
    --retries 10 \
    -r requirements-mcp.txt

COPY app ./app
COPY data ./data
COPY scripts/migrate_intent_taxonomy_v2.py ./scripts/migrate_intent_taxonomy_v2.py
COPY scripts/migrate_intent_taxonomy_v21.py ./scripts/migrate_intent_taxonomy_v21.py

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
