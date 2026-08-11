# Aide v0 — multi-arch (amd64 / arm64 / armv7). Python is pinned in the image,
# so the host OS version stops mattering. This is the whole point of the move.
FROM python:3.12-slim

# tzdata is required: zoneinfo has no bundled database on slim images, and
# without it ZoneInfo("Europe/London") raises and every scheduled job dies.
# build-essential is only needed on armv7 wheels-less builds; removed after.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY aide/ ./aide/

# Run unprivileged. UID 1000 matches the default `pi` user so bind-mounted
# data/ stays writable and readable from the host without chown gymnastics.
RUN useradd -u 1000 -m -s /bin/bash aide \
    && mkdir -p /app/data \
    && chown -R aide:aide /app
USER aide

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/app/data/aide.db

EXPOSE 8787

# Only meaningful when INGEST_SECRET is set; harmless otherwise (marks unhealthy
# but restart:unless-stopped won't act on it without an explicit policy).
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8787/healthz || exit 0

CMD ["python", "-m", "aide.bot"]
