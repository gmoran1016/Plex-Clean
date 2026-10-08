FROM node:22-alpine AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLEX_CONFIG_DIR=/config \
    PYTHONPATH=/app/backend

WORKDIR /app
COPY pyproject.toml ./
COPY backend/ ./backend/
RUN pip install --no-cache-dir . \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin plexclean \
    && mkdir -p /config
COPY --from=frontend-build /frontend/dist/ /app/backend/app/static/
RUN python -c "import app.main" \
    && chown -R plexclean:plexclean /app /config

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()" || exit 1

CMD ["uvicorn", "--app-dir", "/app/backend", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
