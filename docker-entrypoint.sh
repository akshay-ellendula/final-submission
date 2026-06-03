#!/usr/bin/env bash
set -euo pipefail

# Normalise DATABASE_URL: cloud platforms (Railway, Render, Heroku) provide
# postgresql:// or postgres://, but we use asyncpg so need postgresql+asyncpg://.
if [[ "${DATABASE_URL:-}" == postgres://* ]]; then
    export DATABASE_URL="postgresql+asyncpg://${DATABASE_URL#postgres://}"
    echo "[entrypoint] rewritten postgres:// → postgresql+asyncpg://"
elif [[ "${DATABASE_URL:-}" == postgresql://* ]]; then
    export DATABASE_URL="postgresql+asyncpg://${DATABASE_URL#postgresql://}"
    echo "[entrypoint] rewritten postgresql:// → postgresql+asyncpg://"
fi

# Wait for Postgres if configured. compose's depends_on: service_healthy
# already gates us, this is belt-and-braces for `docker run` usage.
if [[ "${DATABASE_URL:-}" == *"postgresql+asyncpg://"* ]]; then
    echo "[entrypoint] waiting for postgres..."
    for i in $(seq 1 30); do
        if python - <<'PY' 2>/dev/null
import asyncio, os, asyncpg
url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
async def main():
    conn = await asyncpg.connect(url)
    await conn.close()
asyncio.run(main())
PY
        then
            echo "[entrypoint] postgres reachable"
            break
        fi
        sleep 2
    done
fi

# Run alembic upgrade. create_all in app.main.lifespan covers the fallback
# for the SQLite smoke-test path where alembic is overkill.
if ! alembic -c /app/config/alembic.ini upgrade head; then
    echo "[entrypoint] alembic upgrade failed — create_all fallback will run at app startup" >&2
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
