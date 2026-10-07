import logging
import os
import re
import json
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ---------- SETUP ----------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set!")

TIMEZONE = os.getenv("TIMEZONE", "Africa/Lagos")
TZ = ZoneInfo(TIMEZONE)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- PERSISTENT STORAGE (JSON file) ----------
DATA_FILE = Path("reminders.json")

def load_reminders() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except Exception:
            return {}
    return {}

def save_reminders(data: dict) -> None:
    DATA_FILE.write_text(json.dumps(data, indent=2))

# In-memory cache: {user_id: [ {id, text, when (ISO), job_name}, ... ]}
REMINDERS = load_reminders()

# ---------- KEYBOARD ----------
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📅 Today's Date", "🕒 Current Time"],
        ["⏰ Set Reminder", "📋 My Reminders"],
        ["❌ Delete Reminder", "ℹ️ Help"],
    ],
    resize_keyboard=True,
    input_field_placeholder="Choose an option…",
)

# ---------- HELPERS ----------
def now_local() -> datetime:
    return datetime.now(TZ)

def format_date(dt: datetime) -> str:
    return dt.strftime("%A, %d %B %Y")

def format_time(dt: datetime) -> str:
    return dt.strftime("%I:%M:%S %p")

def parse_when(text: str):
    """
    Parses reminder time. Supported formats:
      - 'in 10 minutes' / 'in 2 hours' / 'in 3 days'
      - '2026-10-10 14:30'
      - '10/10/2026 14:30'
    Returns aware datetime in local TZ, or None.
    """
    text = text.strip().lower()
    now = now_local()

    # Relative: "in 5 minutes", "in 2h", "in 3 days"
    m = re.match(r"^in\s+(\d+)\s*(sec|secs|seconds|min|mins|minutes|hour|hours|hr|hrs|day|days)$", text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("sec"):
            return now + timedelta(seconds=n)
        if unit.startswith("min"):
            return now + timedelta(minutes=n)
        if unit.startswith(("hour", "hr")):
            return now + timedelta(hours=n)
        if unit.startswith("day"):
            return now + timedelta(days=n)

    # Absolute: ISO-like "2026-10-10 14:30"
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
                "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=TZ)
        except ValueError:
            continue
    return None

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    data = job.data
    try:
        await context.bot.send_message(
            chat_id=data["chat_id"],
            text=f"⏰ *Reminder!*\n\n📌 {data['text']}\n\n_Set for: {data['when']}_",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Failed to send reminder: {e}")

# ---------- HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Hello *{user.first_name}*!\n\n"
        f"I'm *DreyCustomerBot* — your date & reminder assistant.\n\n"
        f"I can:\n"
        f"• 📅 Tell you today's date\n"
        f"• 🕒 Show current time\n"
        f"• ⏰ Set reminders for important events\n"
        f"• 📋 List and delete your reminders\n\n"
        f"Pick an option below or use /help to see commands.",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "*📖 Available Commands*\n\n"
        "• /start — Main menu\n"
        "• /date — Today's date\n"
        "• /time — Current time\n"
        "• /remind — Set a reminder (interactive)\n"
        "• /list — List your reminders\n"
        "• /delete — Delete a reminder\n"
        "• /help — This message\n\n"
        "*⏰ Reminder formats:*\n"
        "• `in 10 minutes`\n"
        "• `in 2 hours`\n"
        "• `in 3 days`\n"
        "• `2026-10-10 14:30`\n"
        "• `10/10/2026 14:30`\n\n"
        "*Quick example:*\n"
        "`/remind Buy groceries in 30 minutes`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def date_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now = now_local()
    week = now.isocalendar()
    await update.message.reply_text(
        f"📅 *Today's Date*\n\n"
        f"*{format_date(now)}*\n\n"
        f"• Day of year: {now.timetuple().tm_yday}\n"
        f"• Week number: {week.week}\n"
        f"• Timezone: `{TIMEZONE}`",
        parse_mode="Markdown",
    )

async def time_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now = now_local()
    await update.message.reply_text(
        f"🕒 *Current Time*\n\n"
        f"*{format_time(now)}*\n"
        f"{format_date(now)}\n\n"
        f"Timezone: `{TIMEZONE}`",
        parse_mode="Markdown",
    )

async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /remind <text> <when>
    Example: /remind Team meeting in 2 hours
    """
    if not context.args:
        await update.message.reply_text(
            "⏰ *Set a Reminder*\n\n"
            "Usage: `/remind <text> <when>`\n\n"
            "*Examples:*\n"
            "• `/remind Call mom in 30 minutes`\n"
            "• `/remind Pay rent 2026-10-15 09:00`\n"
            "• `/remind Meeting in 2 hours`",
            parse_mode="Markdown",
        )
        return

    full = " ".join(context.args)

    # Find the trailing time expression
    # Look for the last occurrence of 'in X units' or an absolute date
    relative_match = re.search(
        r"\s(in\s+\d+\s*(?:sec|secs|seconds|min|mins|minutes|hour|hours|hr|hrs|day|days))\s*$",
        full, re.IGNORECASE,
    )
    absolute_match = re.search(
        r"\s(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)\s*$", full
    )
    slash_match = re.search(
        r"\s(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2})\s*$", full
    )

    match = relative_match or absolute_match or slash_match
    if not match:
        await update.message.reply_text(
            "❌ I couldn't find a time in your message.\n\n"
            "Try:\n"
            "• `/remind Call mom in 30 minutes`\n"
            "• `/remind Pay rent 2026-10-15 09:00`",
            parse_mode="Markdown",
        )
        return

    when_str = match.group(1)
    text = full[: match.start()].strip()
    when_dt = parse_when(when_str)

    if not when_dt:
        await update.message.reply_text("❌ Invalid time format. Use /help for examples.")
        return

    if when_dt <= now_local():
        await update.message.reply_text("❌ That time is in the past. Please pick a future time.")
        return

    await schedule_reminder(update, context, text, when_dt)

async def schedule_reminder(update, context, text, when_dt):
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    delay = (when_dt - now_local()).total_seconds()

    job_name = f"rem_{user_id}_{int(when_dt.timestamp())}"
    context.job_queue.run_once(
        send_reminder,
        when=delay,
        name=job_name,
        data={
            "chat_id": chat_id,
            "text": text,
            "when": when_dt.strftime("%Y-%m-%d %H:%M"),
        },
    )

    entry = {
        "id": job_name,
        "text": text,
        "when": when_dt.isoformat(),
        "display_when": when_dt.strftime("%Y-%m-%d %H:%M"),
    }
    REMINDERS.setdefault(user_id, []).append(entry)
    save_reminders(REMINDERS)

    await update.message.reply_text(
        f"✅ *Reminder set!*\n\n"
        f"📌 {text}\n"
        f"🕐 {entry['display_when']}\n\n"
        f"_I'll ping you then._",
        parse_mode="Markdown",
    )

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    items = REMINDERS.get(user_id, [])

    if not items:
        await update.message.reply_text(
            "📋 You have no reminders set.\n\nUse /remind to create one.",
        )
        return

    lines = ["📋 *Your Reminders*\n"]
    for i, r in enumerate(items, 1):
        lines.append(f"{i}. 📌 {r['text']}\n   🕐 {r['display_when']}")
    lines.append("\nUse /delete to remove one.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    items = REMINDERS.get(user_id, [])

    if not items:
        await update.message.reply_text("📋 You have no reminders to delete.")
        return

    keyboard = [
        [InlineKeyboardButton(f"❌ {r['text'][:40]}", callback_data=f"del:{r['id']}")]
        for r in items
    ]
    await update.message.reply_text(
        "🗑️ *Select a reminder to delete:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("del:"):
        return
    job_name = data[4:]
    user_id = str(query.from_user.id)

    # Remove from JobQueue
    for job in context.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()

    # Remove from storage
    items = REMINDERS.get(user_id, [])
    REMINDERS[user_id] = [r for r in items if r["id"] != job_name]
    if not REMINDERS[user_id]:
        REMINDERS.pop(user_id, None)
    save_reminders(REMINDERS)

    await query.edit_message_text("✅ Reminder deleted.")

# ---------- TEXT ROUTER ----------
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text

    if text == "📅 Today's Date":
        await date_cmd(update, context)
    elif text == "🕒 Current Time":
        await time_cmd(update, context)
    elif text == "⏰ Set Reminder":
        await update.message.reply_text(
            "⏰ Use the command format:\n\n"
            "`/remind <text> <when>`\n\n"
            "Examples:\n"
            "• `/remind Call mom in 30 minutes`\n"
            "• `/remind Pay rent 2026-10-15 09:00`",
            parse_mode="Markdown",
        )
    elif text == "📋 My Reminders":
        await list_reminders(update, context)
    elif text == "❌ Delete Reminder":
        await delete_reminder(update, context)
    elif text == "ℹ️ Help":
        await help_command(update, context)
    else:
        await update.message.reply_text(
            "🤔 I didn't understand that.\n\n"
            "Use the menu below or /help to see what I can do.",
            reply_markup=MAIN_KEYBOARD,
        )

# ---------- RESTORE PENDING REMINDERS ON STARTUP ----------
async def post_init(app: Application) -> None:
    """Re-register reminders that are still in the future after a restart."""
    now = now_local()
    restored = 0
    for user_id, items in REMINDERS.items():
        for r in items:
            try:
                when_dt = datetime.fromisoformat(r["when"])
            except Exception:
                continue
            if when_dt <= now:
                continue
            delay = (when_dt - now).total_seconds()
            app.job_queue.run_once(
                send_reminder,
                when=delay,
                name=r["id"],
                data={
                    "chat_id": int(user_id),
                    "text": r["text"],
                    "when": r["display_when"],
                },
            )
            restored += 1
    logger.info(f"♻️ Restored {restored} pending reminders.")

# ---------- ERROR HANDLER ----------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong. Please try again."
            )
        except Exception:
            pass

# ---------- MAIN ----------
def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("date", date_cmd))
    app.add_handler(CommandHandler("time", time_cmd))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("list", list_reminders))
    app.add_handler(CommandHandler("delete", delete_reminder))
    app.add_handler(CallbackQueryHandler(delete_callback, pattern=r"^del:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)

    logger.info("🤖 DreyCustomerBot is starting…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
