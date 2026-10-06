# syntax=docker/dockerfile:1

# ---- Frontend build ----
FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Backend runtime ----
FROM python:3.12-slim AS app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev
COPY --from=frontend /frontend/dist /app/frontend_dist
COPY curriculum/ /app/curriculum/

ENV LLMLL_FRONTEND_DIST=/app/frontend_dist \
    LLMLL_DATABASE_URL=sqlite:////data/llmll.db
VOLUME /data
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python -m app.cli import-curriculum --path /app/curriculum/de && uvicorn --factory app.main:create_app --host 0.0.0.0 --port ${PORT:-8000}"]
