"""Aide v0 — configuration. Everything env-driven, nothing hardcoded."""
import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _time_tuple(s: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        h, m = s.split(":")
        return int(h), int(m)
    except Exception:
        return default


@dataclass(frozen=True)
class Config:
    # Telegram
    bot_token: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    owner_id: int = int(os.environ.get("TELEGRAM_OWNER_ID", "0"))

    # Anthropic
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    model_fast: str = os.environ.get("MODEL_FAST", "claude-haiku-4-5-20251001")
    model_smart: str = os.environ.get("MODEL_SMART", "claude-sonnet-4-6")
    daily_token_budget: int = int(os.environ.get("DAILY_TOKEN_BUDGET", "300000"))

    # Calendar (CalDAV — iCloud, Fastmail, Nextcloud, Google via bridge)
    caldav_url: str = os.environ.get("CALDAV_URL", "")
    caldav_user: str = os.environ.get("CALDAV_USER", "")
    caldav_pass: str = os.environ.get("CALDAV_PASS", "")

    # Telemetry ingest
    ingest_port: int = int(os.environ.get("INGEST_PORT", "8787"))
    ingest_secret: str = os.environ.get("INGEST_SECRET", "")

    # Schedule
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo(os.environ.get("TZ", "Europe/London")))
    brief_weekday: tuple[int, int] = field(
        default_factory=lambda: _time_tuple(os.environ.get("BRIEF_WEEKDAY", "07:00"), (7, 0)))
    brief_weekend: tuple[int, int] = field(
        default_factory=lambda: _time_tuple(os.environ.get("BRIEF_WEEKEND", "08:30"), (8, 30)))
    quiet_start: int = int(os.environ.get("QUIET_START_HOUR", "22"))   # 22:00
    quiet_end: int = int(os.environ.get("QUIET_END_HOUR", "6"))        # 06:30 → hour 6 conservative

    # Email (IMAP read / SMTP send). App-specific passwords, never your
    # account password. Leave blank to disable email entirely.
    imap_host: str = os.environ.get("IMAP_HOST", "")
    imap_port: int = int(os.environ.get("IMAP_PORT", "993"))
    imap_user: str = os.environ.get("IMAP_USER", "")
    imap_pass: str = os.environ.get("IMAP_PASS", "")
    smtp_host: str = os.environ.get("SMTP_HOST", "")
    smtp_port: int = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user: str = os.environ.get("SMTP_USER", "")
    smtp_pass: str = os.environ.get("SMTP_PASS", "")
    smtp_from: str = os.environ.get("SMTP_FROM", "")
    vip_senders: str = os.environ.get("VIP_SENDERS", "")

    db_path: str = os.environ.get("DB_PATH", "data/aide.db")

    def validate(self) -> list[str]:
        problems = []
        if not self.bot_token:
            problems.append("TELEGRAM_BOT_TOKEN missing")
        if not self.owner_id:
            problems.append("TELEGRAM_OWNER_ID missing")
        if not self.anthropic_api_key:
            problems.append("ANTHROPIC_API_KEY missing (bot will run with deterministic fallbacks only)")
        if not self.ingest_secret:
            problems.append("INGEST_SECRET missing (telemetry endpoint disabled)")
        return problems


CFG = Config()
