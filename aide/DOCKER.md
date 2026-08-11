# Aide v0 — Docker Setup & Migration

Moving Aide into a container pins Python 3.12 inside the image, so the host OS
version stops mattering. Your Bullseye Pi keeps serving Plex untouched, and the
whole thing lifts to a homelab box later with one `docker compose up`.

---

## 1. Install Docker on the Pi (~5 min)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker            # or log out and back in
docker run --rm hello-world
```

Docker's convenience script handles Bullseye and armv7 fine. Plex is unaffected —
it stays a native systemd service.

## 2. Stop the old systemd service

```bash
sudo systemctl disable --now aide
sudo rm /etc/systemd/system/aide.service
sudo systemctl daemon-reload
```

Keep your existing `data/aide.db` — the compose file bind-mounts it back in, so
your tasks survive the move.

## 3. Build and run

```bash
cd /opt/aide
mkdir -p data
docker compose up -d --build
docker compose logs -f
```

First build on a Pi takes 5–15 minutes (some wheels compile from source on ARM).
Subsequent builds are cached and near-instant.

Expect:
```
Aide v0 up. Owner=123456789 TZ=Europe/London
Telemetry ingest on :8787
```

## 4. Verify

```bash
curl -s http://localhost:8787/healthz          # -> ok
docker compose ps                              # -> running, healthy
```
Then `/brief` in Telegram.

---

## Daily operations

```bash
docker compose logs -f --tail=100     # follow logs
docker compose restart                # restart after .env change
docker compose up -d --build          # rebuild after code change
docker compose down                   # stop (data survives in ./data)
docker compose exec aide sh           # shell inside
```

Inspect the database from the host without entering the container:
```bash
sqlite3 data/aide.db "SELECT id,title,status,defer_count FROM tasks;"
```

---

## Migrating to the homelab later

The entire application state is two things:

| What | Where |
|---|---|
| Config + secrets | `.env` |
| All data | `data/aide.db` |

To move hosts:
```bash
docker compose down
rsync -avz /opt/aide/ user@newhost:/opt/aide/
# on the new host:
cd /opt/aide && docker compose up -d --build
```

That's the migration. No OS dependencies, no Python version to match, no venv
to rebuild. Same command on a Pi, an x86 mini-PC, or a Proxmox VM.

---

## Homelab notes (for when you get there)

**Registry instead of rebuilding.** Once you have more than one host, build once
and pull everywhere:
```bash
docker buildx build --platform linux/arm64,linux/amd64 \
  -t ghcr.io/<you>/aide:v0 --push .
```
Then swap `build: .` for `image: ghcr.io/<you>/aide:v0` in the compose file.

**Secrets.** `.env` is fine for one host. On a real homelab, move to Docker
secrets or SOPS-encrypted files committed alongside the compose — which fits the
GitOps flow you already use at work. Keep `.env` in `.dockerignore` and
`.gitignore` regardless; it holds your bot token.

**Backups.** SQLite in WAL mode shouldn't be copied with `cp` while running.
Use the online backup API:
```bash
docker compose exec aide python -c \
  "import sqlite3;s=sqlite3.connect('/app/data/aide.db');d=sqlite3.connect('/app/data/backup.db');s.backup(d);d.close()"
```
Worth a nightly cron once you care about the history.

**Reverse proxy.** When you add Traefik or Caddy, drop the published port and
put the ingest endpoint behind TLS on a real hostname. The Shortcuts automation
then points at `https://aide.yourdomain/ingest` instead of a Tailscale IP —
tidier and survives IP changes.

**Watchtower / Renovate.** Pin the base image digest rather than `python:3.12-slim`
if you want reproducible rebuilds. Bare tags drift.

---

## Gotchas already handled

- **tzdata is installed in the image.** Slim Python images ship no timezone
  database, and without it `ZoneInfo("Europe/London")` throws and every
  scheduled brief dies silently. This bit is not optional.
- **Runs as UID 1000**, matching the default `pi` user, so `data/` stays
  readable and writable from the host with no chown dance.
- **Log rotation capped** at 3 × 10MB — an unbounded json-file driver will
  quietly eat an SD card over months.
- **`.env` excluded from the build context**, so your bot token never gets
  baked into an image layer.
- **`from __future__ import annotations`** is now redundant on 3.12 but left in
  place — it costs nothing and keeps the code runnable outside the container.
