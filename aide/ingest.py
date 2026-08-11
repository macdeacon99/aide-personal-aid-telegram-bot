"""Aide v0 — telemetry ingest endpoint (FR-9).

The phone POSTs here via iOS Shortcuts / Health Auto Export:

  POST /ingest
  X-Aide-Secret: <shared secret>
  {"day": "2026-08-10", "steps": 8412, "sleep_min": 402, "workout_min": 35,
   "screen_min": 221, "apps": {"Instagram": 72, "Reddit": 40}}

Every field optional except day (defaults to yesterday). Payload cap 64KB.
"""
import hmac
import json
from datetime import date, timedelta

from aiohttp import web

from .config import CFG
from .db import DB

MAX_BODY = 64 * 1024
NUMERIC_KINDS = ("steps", "sleep_min", "workout_min", "screen_min")


def make_app(db: DB) -> web.Application:
    async def ingest(request: web.Request) -> web.Response:
        secret = request.headers.get("X-Aide-Secret", "")
        if not CFG.ingest_secret or not hmac.compare_digest(secret, CFG.ingest_secret):
            return web.Response(status=403, text="forbidden")
        body = await request.read()
        if len(body) > MAX_BODY:
            return web.Response(status=413, text="too large")
        try:
            payload = json.loads(body)
            assert isinstance(payload, dict)
        except Exception:
            return web.Response(status=400, text="bad json")

        day = payload.get("day") or (date.today() - timedelta(days=1)).isoformat()
        stored = 0
        for kind in NUMERIC_KINDS:
            v = payload.get(kind)
            if isinstance(v, (int, float)):
                db.ingest_telemetry(day, kind, float(v))
                stored += 1
        apps = payload.get("apps")
        if isinstance(apps, dict):
            for app_name, minutes in list(apps.items())[:20]:
                if isinstance(minutes, (int, float)):
                    db.ingest_telemetry(day, "app_screen", float(minutes), str(app_name)[:60])
                    stored += 1
        return web.json_response({"ok": True, "day": day, "stored": stored})

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application(client_max_size=MAX_BODY)
    app.router.add_post("/ingest", ingest)
    app.router.add_get("/healthz", health)
    return app


async def start_ingest(db: DB) -> web.AppRunner:
    runner = web.AppRunner(make_app(db))
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", CFG.ingest_port)
    await site.start()
    return runner
