# The whole platform in one image: the web app built here, served by the API, with the background worker
# alongside (services/api/novara/serve.py). Hosts that build from a Dockerfile at the repository root (Railway,
# Render...) use it as is; it needs a PostgreSQL database. Steps: docs/deploy-railway.md.

# ---------------------------------------------------------------- the web app
FROM node:22.12.0-bookworm-slim AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
RUN npm run build

# ---------------------------------------------------------------- the API, the worker and the built app
FROM python:3.12.8-slim-bookworm
# libcairo2: PNG export of logos (cairosvg). Everything else comes as wheels.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libcairo2 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY services/api/novara ./novara
COPY services/api/migrations ./migrations
COPY --from=web /web/dist ./web

# Uploaded files go under /data: attach a volume there to keep them across deploys.
ENV NOVARA_ENV=production \
    NOVARA_WEB_DIST=/app/web \
    NOVARA_STORAGE_ROOT=/data/storage \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000
EXPOSE 8000
CMD ["python", "-m", "novara.serve"]
