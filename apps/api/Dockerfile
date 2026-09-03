FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN sed -i \
    -e 's|http://deb.debian.org/debian-security|http://mirrors.cloud.tencent.com/debian-security|g' \
    -e 's|http://deb.debian.org/debian|http://mirrors.cloud.tencent.com/debian|g' \
    /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::http::Timeout=20 -o Acquire::Retries=5 update \
    && apt-get -o Acquire::http::Timeout=20 -o Acquire::Retries=5 install \
    -y --no-install-recommends build-essential cmake ffmpeg \
    && pip install --no-cache-dir \
    --index-url https://mirrors.cloud.tencent.com/pypi/simple \
    --timeout 120 \
    --retries 10 \
    -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-mcp.txt .
RUN pip install --no-cache-dir \
    --index-url https://mirrors.cloud.tencent.com/pypi/simple \
    --timeout 120 \
    --retries 10 \
    -r requirements-mcp.txt

COPY app ./app
COPY data ./data

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
