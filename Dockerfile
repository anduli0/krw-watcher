# KRW-Watcher production image
# Single-stage, slim. SINGLE WORKER ONLY (in-memory state + APScheduler in lifespan).
FROM python:3.12-slim

# Faster, cleaner Python in containers
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first for better layer caching.
# asyncpg / pandas / numpy ship manylinux wheels for cp312, so no compiler is needed.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code only. Do NOT copy .env or *.db (see .dockerignore / secrets policy).
COPY backend/ ./backend/
COPY setup.py run_once.py ./

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Documented default port; PaaS platforms inject $PORT at runtime.
EXPOSE 8010

# Healthcheck without curl (not present in slim) — use stdlib urllib against /health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request,sys; port=os.environ.get('PORT','8010'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+port+'/health', timeout=4).getcode()==200 else 1)"]

# Single-worker uvicorn, honoring $PORT (default 8010). NEVER scale workers/replicas > 1.
CMD ["sh","-c","uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8010} --workers 1"]
