FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -r apex && useradd -r -g apex apex

COPY requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt

COPY app /app/app
COPY config /app/config
COPY dashboard /app/dashboard
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh && chown -R apex:apex /app

USER apex

# DATABASE_URL is supplied by docker-compose (or `docker run -e`).
# A credential-free default is used only so the image is runnable without
# compose for `--help` / smoke tests; production must override.
ENV DATABASE_URL=sqlite+aiosqlite:////app/data/apex.db

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=6 CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
