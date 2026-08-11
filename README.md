# Aide v0 — Telegram Personal Assistant

Proactive PA over Telegram: morning brief (calendar + carry-over tasks + phone
telemetry), natural-language task capture, priorities capture, defer autopsy,
quiet hours, and a health-data ingest endpoint for iOS Shortcuts.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install "python-telegram-bot[job-queue]==21.*" anthropic caldav icalendar python-dotenv aiohttp
cp .env.example .env   # fill it in
python -m aide.bot
```

### Telegram setup
1. Message **@BotFather** → `/newbot` → copy the token into `TELEGRAM_BOT_TOKEN`.
2. Message **@userinfobot** → copy your numeric ID into `TELEGRAM_OWNER_ID`.
3. Start a chat with your new bot and send `/start`. All other user IDs are hard-rejected.

### Calendar (read-only, v0)
Any CalDAV server works. iCloud: `CALDAV_URL=https://caldav.icloud.com`, your
Apple ID as user, and an **app-specific password** (appleid.apple.com → Sign-In
& Security). Leave blank to run calendar-less.

### Phone telemetry (FR-9)
The phone pushes; the server never pulls. Set `INGEST_SECRET` to a long random
string, expose port 8787 (Tailscale Funnel or a reverse proxy with TLS), then:

**Health data — iOS Shortcuts:**
1. Shortcuts → Automation → Time of Day (06:30, daily) → New Blank Automation.
2. Add *Find Health Samples* actions (Steps yesterday, Sleep, Workout minutes).
3. Add *Get Contents of URL*: POST `https://your-host:8787/ingest`,
   Header `X-Aide-Secret: <secret>`, JSON body:
   ```json
   {"steps": <Steps>, "sleep_min": <Sleep>, "workout_min": <Workout>}
   ```
   (Or install **Health Auto Export** and point its REST export at the same URL.)

**Screen time:** no clean Shortcuts API. v0 options: a 1-tap evening shortcut
posting `{"screen_min": X, "apps": {"Instagram": Y}}` manually, or wait for the
v3 companion app (DeviceActivity framework). The brief shows whatever arrives
and stays silent about whatever doesn't.

Test the endpoint:
```bash
curl -X POST http://localhost:8787/ingest \
  -H "X-Aide-Secret: $INGEST_SECRET" -H "Content-Type: application/json" \
  -d '{"day":"2026-08-10","steps":8412,"sleep_min":402,"screen_min":221,"apps":{"Instagram":72}}'
```

## Commands
```
/brief   force the morning brief
/plan    today's events + confirmed priorities
/add     add a task            /tasks  list open tasks
/done N  complete task N       /defer N [date]  push it (3+ triggers the autopsy)
/drop N  kill a task           /dnd [hours]     silence
```
Plain messages work too: "remind me to order press plates tomorrow" → task with
due date. "done the VAT thing" → matches and completes. Anything else → chat
with the Aide persona (short, direct, no flattery).

## Design guarantees
- **Owner-locked:** every handler rejects non-owner Telegram IDs.
- **Degrades, never disappears:** no API key / budget blown / LLM down → regex
  intent fallback + deterministic brief still fire.
- **Budget cap:** `DAILY_TOKEN_BUDGET` circuit-breaks LLM calls; Haiku parses,
  Sonnet writes.
- **Quiet hours** (22:00–06:00 default) and `/dnd` suppress scheduled sends.
- **All state local:** one SQLite file in `data/`. Back it up; `/export` lands in v3.

## Deploy
Any small VPS or a Pi. Minimal systemd unit:
```ini
[Unit]
Description=Aide personal assistant
After=network-online.target

[Service]
WorkingDirectory=/opt/aide
EnvironmentFile=/opt/aide/.env
ExecStart=/opt/aide/.venv/bin/python -m aide.bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
Terraform/cloud-init for the VPS is a natural next step — the whole thing is
env-file + one process + one SQLite file, so it slots straight into a GitOps flow.

## v0 → v1
See `pa-spec.md`. v1 adds: intraday check-ins with adaptive silence (needs
calendar busy-detection), time-block planning, calendar write, evening shutdown
with the energy check.
