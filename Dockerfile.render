FROM node:22-alpine AS admin-build

WORKDIR /admin-web

RUN corepack enable && corepack prepare pnpm@11.5.2 --activate

COPY admin-web/package.json admin-web/pnpm-lock.yaml admin-web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY admin-web/ ./
RUN pnpm build:prod


FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY wechat_rag_bot/requirements-render.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY wechat_rag_bot/app/__init__.py ./app/__init__.py
COPY wechat_rag_bot/app/schemas ./app/schemas
COPY wechat_rag_bot/app/render_gateway.py ./app/render_gateway.py
COPY --from=admin-build /admin-web/dist-prod ./admin-web

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.render_gateway:app --host 0.0.0.0 --port ${PORT:-8000}"]
