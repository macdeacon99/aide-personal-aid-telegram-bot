from __future__ import annotations
"""Aide v0 — Telegram gateway + scheduler. Entry point: python -m aide.bot"""
import asyncio
import logging
import re
from datetime import datetime, time as dtime

from telegram import Update
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from .brief import build_brief
from .calendar_client import todays_events
from .config import CFG
from .db import DB
from .ingest import start_ingest
from .llm import LLM

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("aide")

db = DB(CFG.db_path)
llm = LLM(db)


# ---------- guards ----------
def owner_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id != CFG.owner_id:
            log.warning("Rejected user %s", update.effective_user and update.effective_user.id)
            return
        return await func(update, ctx)
    return wrapper


def in_quiet_hours(now: datetime | None = None) -> bool:
    h = (now or datetime.now(CFG.tz)).hour
    if CFG.quiet_start > CFG.quiet_end:      # wraps midnight, e.g. 22 → 6
        return h >= CFG.quiet_start or h < CFG.quiet_end
    return CFG.quiet_start <= h < CFG.quiet_end


async def send(ctx: ContextTypes.DEFAULT_TYPE, text: str, intent: str = ""):
    db.log_msg("out", text, intent)
    await ctx.bot.send_message(chat_id=CFG.owner_id, text=text)


# ---------- commands ----------
@owner_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Aide online. /brief for the morning brief, /add <task>, /plan, /tasks, "
        "/done <id>, /defer <id>, /dnd <hours>. Or just talk to me.")


@owner_only
async def cmd_brief(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send(ctx, build_brief(db, llm), "brief")


@owner_only
async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args or [])
    if not text:
        await update.message.reply_text("Usage: /add Pay VAT bill by Friday")
        return
    parsed = llm.parse_intent(f"add {text}")
    title = parsed.get("title") or text
    due = parsed.get("due")
    tid = db.add_task(title, due=due, priority=int(parsed.get("priority") or 3))
    await update.message.reply_text(f"✅ #{tid} {title}" + (f" [due {due}]" if due else ""))


@owner_only
async def cmd_tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tasks = db.open_tasks()
    if not tasks:
        await update.message.reply_text("Nothing open. Rare sight.")
        return
    lines = []
    for t in tasks[:20]:
        due = f" [due {t['due']}]" if t["due"] else ""
        dc = f" (x{t['defer_count']})" if t["defer_count"] else ""
        lines.append(f"#{t['id']} P{t['priority']} {t['title']}{due}{dc}")
    await update.message.reply_text("\n".join(lines))


@owner_only
async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        tid = int(ctx.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /done <task id>")
        return
    ok = db.complete_task(tid)
    await update.message.reply_text(f"✅ #{tid} done." if ok else f"No open task #{tid}.")


@owner_only
async def cmd_defer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        tid = int(ctx.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /defer <task id> [YYYY-MM-DD]")
        return
    new_due = ctx.args[1] if len(ctx.args) > 1 and re.match(r"\d{4}-\d{2}-\d{2}", ctx.args[1]) else None
    count = db.defer_task(tid, new_due)
    if count is None:
        await update.message.reply_text(f"No open task #{tid}.")
    elif count >= 3:
        await update.message.reply_text(
            f"⏭ #{tid} deferred — that's {count} times now. Do it, schedule it "
            "immovably, delegate it, or /drop it. Which?")
    else:
        await update.message.reply_text(f"⏭ #{tid} deferred (x{count}).")


@owner_only
async def cmd_drop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        tid = int(ctx.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /drop <task id>")
        return
    ok = db.drop_task(tid)
    await update.message.reply_text(f"❌ #{tid} dropped. One less thing." if ok else f"No open task #{tid}.")


@owner_only
async def cmd_plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from datetime import date
    prios = db.priorities_for(date.today().isoformat())
    events = todays_events()
    lines = ["Today:"]
    lines += [e.line() for e in events] or ["(no calendar events)"]
    if prios:
        lines.append("\nPriorities:")
        lines += [f"{i + 1}. {p}" for i, p in enumerate(prios)]
    else:
        lines.append("\nNo priorities set yet — tell me what matters today.")
    await update.message.reply_text("\n".join(lines))


@owner_only
async def cmd_dnd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        hours = float(ctx.args[0])
    except (IndexError, ValueError):
        hours = 2.0
    db.set_dnd(hours)
    await update.message.reply_text(f"🔕 Quiet for {hours:g}h. I'll hold anything non-urgent.")


# ---------- natural language ----------
@owner_only
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    db.log_msg("in", text)

    await ctx.bot.send_chat_action(chat_id=CFG.owner_id, action="typing")
    reply = llm.agent_turn(text)

    if reply is None:
        # No API key or budget blown — fall back to bare task capture so the
        # bot still does something useful rather than nothing.
        low = text.lower().strip()
        m = re.match(r"^(add|remind me to|remember to|need to)\s+(.+)$", low)
        if m:
            title = text[m.start(2):]
            tid = db.add_task(title)
            reply = f"Added #{tid}: {title}\n(No API key set — running in basic mode.)"
        else:
            reply = ("No API key configured, so I can't hold a conversation right now. "
                     "Commands still work: /tasks /add /done /brief")
    await send(ctx, reply, "agent")


# ---------- scheduled jobs ----------
async def job_nightly_prune(ctx: ContextTypes.DEFAULT_TYPE):
    """Keep the messages table small — meta rows are only needed for today's
    budget arithmetic, and old dialogue past 60 days is dead weight."""
    db.prune_messages()


async def job_reminders(ctx: ContextTypes.DEFAULT_TYPE):
    """Fire any due reminders. Runs every minute; quiet hours still apply."""
    if in_quiet_hours():
        return
    for r in db.due_reminders():
        await send(ctx, f"\u23f0 {r['message']}", "reminder")
        db.mark_reminder_sent(r["id"])


async def job_morning_brief(ctx: ContextTypes.DEFAULT_TYPE):
    if in_quiet_hours():
        return
    dnd = db.dnd_until()
    if dnd and dnd > datetime.now():
        return
    await send(ctx, build_brief(db, llm), "brief")


def main():
    problems = CFG.validate()
    for p in problems:
        log.warning("CONFIG: %s", p)
    if not CFG.bot_token or not CFG.owner_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_ID are required.")

    app = Application.builder().token(CFG.bot_token).build()

    for name, fn in [("start", cmd_start), ("brief", cmd_brief), ("add", cmd_add),
                     ("tasks", cmd_tasks), ("done", cmd_done), ("defer", cmd_defer),
                     ("drop", cmd_drop), ("plan", cmd_plan), ("dnd", cmd_dnd)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    # Weekday + weekend briefs
    wh, wm = CFG.brief_weekday
    app.job_queue.run_daily(job_morning_brief, dtime(wh, wm, tzinfo=CFG.tz),
                            days=(1, 2, 3, 4, 5), name="brief_weekday")
    eh, em = CFG.brief_weekend
    app.job_queue.run_daily(job_morning_brief, dtime(eh, em, tzinfo=CFG.tz),
                            days=(0, 6), name="brief_weekend")

    # Nightly housekeeping
    app.job_queue.run_daily(job_nightly_prune, dtime(4, 15, tzinfo=CFG.tz), name="prune")

    # Reminders — checked every minute
    app.job_queue.run_repeating(job_reminders, interval=60, first=30, name="reminders")

    # Telemetry ingest server alongside the bot
    if CFG.ingest_secret:
        async def _post_init(application: Application):
            application.bot_data["ingest_runner"] = await start_ingest(db)
            log.info("Telemetry ingest on :%d", CFG.ingest_port)
        app.post_init = _post_init

    log.info("Aide v0 up. Owner=%s TZ=%s", CFG.owner_id, CFG.tz)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
