#!/usr/bin/env python3
import os
import re
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
import discord
from discord import Interaction, app_commands
from discord.ext import commands
from aiohttp import web  # used for keepalive

# ------------------------------------------------------
# Configuration
# ------------------------------------------------------
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
PORT = int(os.getenv("PORT", 8080))

if not DISCORD_TOKEN:
    raise RuntimeError("❌ DISCORD_TOKEN not found in environment")

try:
    GUILD_OBJECT: Optional[discord.Object] = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None
except ValueError:
    GUILD_OBJECT = None

# ------------------------------------------------------
# Logging setup
# ------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("timer-bot")

# ------------------------------------------------------
# Data Models
# ------------------------------------------------------
@dataclass
class TimerData:
    id: int
    user: Any
    channel: Optional[Any]
    initial_duration: int
    region: str = "Unknown"
    link: str = ""
    hops: int = 1
    remaining_hops: int = 1
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    alert_time: Optional[datetime] = None
    task: Optional[asyncio.Task] = None


@dataclass
class ReminderData:
    keyword: str
    start_time: datetime
    duration: int
    task: Optional[asyncio.Task] = None

# ------------------------------------------------------
# Discord Bot Setup
# ------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

active_timers: List[TimerData] = []
active_reminders: Dict[int, List[ReminderData]] = {}
_timer_id_counter = 1
_timer_lock = asyncio.Lock()

# ------------------------------------------------------
# Utility Functions
# ------------------------------------------------------
def parse_time_string(time_str: str) -> int:
    cleaned = time_str.replace(" ", "").lower()
    pattern = r'(?:(\d+)h)?(?:(\d+)m)?$'
    match = re.fullmatch(pattern, cleaned)
    if not match:
        raise ValueError("Invalid time format. Use examples: '1h30m', '45m', '2h'")
    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    total_seconds = hours * 3600 + minutes * 60
    if total_seconds <= 0:
        raise ValueError("Time must be greater than 0.")
    return total_seconds

def humanize_seconds(seconds: int) -> str:
    minutes_total = seconds // 60
    hours, minutes = divmod(minutes_total, 60)
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"

# ------------------------------------------------------
# Timer Logic
# ------------------------------------------------------
async def execute_timer(timer: TimerData):
    timer_id = timer.id
    try:
        for hop in range(timer.hops):
            duration = timer.initial_duration if hop == 0 else 7200
            timer.remaining_hops = timer.hops - hop
            now = datetime.now(timezone.utc)
            timer.alert_time = now + timedelta(seconds=duration)

            logger.info(f"[Timer #{timer_id}] Hop {hop+1}/{timer.hops} -> waiting {duration}s")

            if duration > 300:
                await asyncio.sleep(duration - 300)
                if timer not in active_timers:
                    return
                if timer.channel:
                    await safe_send(timer.channel,
                        f"@here ⚠️ **Timer #{timer_id}** - bosses in 5 minutes!\n"
                        f"🌍 Region: *{timer.region}*\n🔗 {timer.link or 'No link provided'}"
                    )
                await asyncio.sleep(300)
            else:
                await asyncio.sleep(duration)

        logger.info(f"[Timer #{timer_id}] Completed all hops.")
    except asyncio.CancelledError:
        logger.info(f"[Timer #{timer_id}] Cancelled.")
        raise
    finally:
        if timer in active_timers:
            active_timers.remove(timer)
            logger.info(f"[Timer #{timer_id}] Cleaned up.")

async def safe_send(channel, message: str):
    try:
        if hasattr(channel, "send"):
            await channel.send(message)
    except discord.DiscordException as exc:
        logger.warning(f"Failed to send message: {exc}")

# ------------------------------------------------------
# Reminder Logic
# ------------------------------------------------------
async def _run_reminder(reminder: ReminderData, channel, user):
    try:
        await asyncio.sleep(reminder.duration)
        if channel and hasattr(channel, "send"):
            try:
                await channel.send(f"🔔 {user.mention} — Reminder: **{reminder.keyword}**")
            except discord.DiscordException as exc:
                logger.warning(f"Failed to send reminder to channel: {exc}")
    except asyncio.CancelledError:
        logger.info(f"Reminder for {reminder.keyword} cancelled for user {getattr(user, 'id', 'unknown')}")
        raise
    finally:
        uid = getattr(user, "id", None)
        if uid is not None:
            lst = active_reminders.get(uid)
            if lst and reminder in lst:
                lst.remove(reminder)
                if not lst:
                    active_reminders.pop(uid, None)

def schedule_reminder(keyword: str, duration: int, channel, user) -> ReminderData:
    reminder = ReminderData(keyword=keyword, start_time=datetime.now(timezone.utc), duration=duration)
    task = asyncio.create_task(_run_reminder(reminder, channel, user))
    reminder.task = task
    uid = getattr(user, "id", None)
    if uid is not None:
        active_reminders.setdefault(uid, []).append(reminder)
    return reminder

# ------------------------------------------------------
# Keepalive Web Server (for UptimeRobot)
# ------------------------------------------------------
async def handle_root(request):
    return web.Response(text="✅ Bot is alive!", content_type="text/plain")

async def run_keepalive():
    app = web.Application()
    app.router.add_get("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"🌐 Keepalive server running on port {PORT}")
    # don't block here; site will keep serving in the event loop

# ------------------------------------------------------
# Discord Commands
# ------------------------------------------------------
@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user} (id: {bot.user.id})")
    try:
        if GUILD_OBJECT:
            synced = await bot.tree.sync(guild=GUILD_OBJECT)
            logger.info(f"Synced {len(synced)} commands to guild {GUILD_OBJECT.id}")
        else:
            synced = await bot.tree.sync()
            logger.info(f"Synced {len(synced)} global commands")
    except Exception as exc:
        logger.exception(f"Command sync failed: {exc}")

@bot.tree.command(name="timer", description="Start a repeating timer with hops.")
@app_commands.describe(time="Initial time (e.g. 1h30m)", hops="Number of hops (default 1)", region="Region name", link="Invite link to the server")
async def timer_command(interaction: Interaction, time: str, hops: int = 1, region: str = "Unknown", link: str = ""):
    global _timer_id_counter
    try:
        seconds = parse_time_string(time)
    except ValueError as e:
        # noinspection PyUnresolvedReferences
        return await interaction.response.send_message(f"❌ {e}", ephemeral=True)

    if hops < 1:
        # noinspection PyUnresolvedReferences
        return await interaction.response.send_message("❌ Hops must be at least 1.", ephemeral=True)

    link = (link or "").strip()
    if link and any(t.link == link for t in active_timers):
        # noinspection PyUnresolvedReferences
        return await interaction.response.send_message("❌ A timer with that link already exists.", ephemeral=True)

    async with _timer_lock:
        timer_id = _timer_id_counter
        _timer_id_counter += 1

    now = datetime.now(timezone.utc)
    timer = TimerData(
        id=timer_id,
        user=interaction.user,
        channel=interaction.channel,
        initial_duration=seconds,
        region=region,
        link=link,
        hops=hops,
        remaining_hops=hops,
        start_time=now,
        alert_time=now + timedelta(seconds=seconds)
    )
    timer.task = asyncio.create_task(execute_timer(timer))
    active_timers.append(timer)

    # noinspection PyUnresolvedReferences
    return await interaction.response.send_message(
        f"⏱ **Timer #{timer_id}** activated for {time} (hops: {hops}) in region **{region}**.",
        ephemeral=True
    )

@bot.tree.command(name="timers", description="List all active timers.")
async def timers_command(interaction: Interaction):
    if not active_timers:
        # noinspection PyUnresolvedReferences
        return await interaction.response.send_message("📭 No active timers.", ephemeral=True)

    lines = ["**🕒 Active Timers:**\n"]
    now = datetime.now(timezone.utc)
    for t in active_timers:
        remaining = max(0, int((t.alert_time - now).total_seconds())) if t.alert_time else 0
        display = humanize_seconds(remaining)
        lines.append(f"**#{t.id}** — Region: **{t.region}**, Hops left: **{t.remaining_hops}**, Next: **{display}**\n")

    # noinspection PyUnresolvedReferences
    return await interaction.response.send_message("".join(lines), ephemeral=True)

@bot.tree.command(name="remove", description="Remove a timer by its number.")
async def remove_command(interaction: Interaction, timer_number: int):
    for t in list(active_timers):
        if t.id == timer_number:
            if t.task and not t.task.done():
                t.task.cancel()
            if t in active_timers:
                active_timers.remove(t)
            # noinspection PyUnresolvedReferences
            return await interaction.response.send_message(f"🛑 Timer #{timer_number} deleted.", ephemeral=True)

    # noinspection PyUnresolvedReferences
    return await interaction.response.send_message("❌ No timer found with that number.", ephemeral=True)

@bot.tree.command(name="reminder", description="Set a reminder for boss, raids, or super. Usage: <keyword> [time], e.g. 'boss 30m'")
async def reminder_command(interaction: Interaction, message: str):
    """
    Expected `message` formats:
      - "boss"
      - "boss 30m"
      - "raids 1h"
      - "super 45m"
    Default duration if not given: 1h
    """
    parts = message.strip().split()
    if not parts:
        # noinspection PyUnresolvedReferences
        return await interaction.response.send_message("❌ Please provide a keyword (boss, super, raids).", ephemeral=True)

    keyword = parts[0].lower()
    allowed = {"boss", "super", "raids"}
    if keyword not in allowed:
        # noinspection PyUnresolvedReferences
        return await interaction.response.send_message(f"❌ Unknown keyword. Allowed: {', '.join(sorted(allowed))}.", ephemeral=True)

    # parse optional time
    duration_seconds = 3600  # default 1 hour
    if len(parts) > 1:
        time_part = "".join(parts[1:])  # join in case user typed "1h 30m"
        try:
            duration_seconds = parse_time_string(time_part)
        except ValueError as e:
            # noinspection PyUnresolvedReferences
            return await interaction.response.send_message(f"❌ {e}", ephemeral=True)

    # Schedule the reminder (no local unused variable)
    schedule_reminder(keyword, duration_seconds, interaction.channel, interaction.user)
    human_time = humanize_seconds(duration_seconds)
    # noinspection PyUnresolvedReferences
    return await interaction.response.send_message(f"🔔 Reminder for **{keyword}** set for {human_time}.", ephemeral=True)

@bot.tree.command(name="reminders", description="List or cancel your active reminders.")
@app_commands.describe(action="Optional: 'list' (default) or 'cancel <keyword>'")
async def reminders_command(interaction: Interaction, action: Optional[str] = "list"):
    """
    /reminders — shows your active reminders
    /reminders cancel boss — cancels your reminder for 'boss'
    """
    uid = getattr(interaction.user, "id", None)
    if uid is None:
        # noinspection PyUnresolvedReferences
        return await interaction.response.send_message("❌ Unable to identify your user ID.", ephemeral=True)

    user_reminders = active_reminders.get(uid, [])
    parts = (action or "").split()

    # Handle cancel request
    if parts and parts[0].lower() == "cancel":
        if len(parts) < 2:
            # noinspection PyUnresolvedReferences
            return await interaction.response.send_message("❌ Usage: `/reminders cancel <keyword>`", ephemeral=True)
        keyword = parts[1].lower()
        for rem in list(user_reminders):
            if rem.keyword == keyword:
                if rem.task and not rem.task.done():
                    rem.task.cancel()
                user_reminders.remove(rem)
                if not user_reminders:
                    active_reminders.pop(uid, None)
                # noinspection PyUnresolvedReferences
                return await interaction.response.send_message(f"🗑 Reminder for **{keyword}** cancelled.", ephemeral=True)
        # noinspection PyUnresolvedReferences
        return await interaction.response.send_message(f"❌ No active reminder found for '{keyword}'.", ephemeral=True)

    # Default: list all reminders
    if not user_reminders:
        # noinspection PyUnresolvedReferences
        return await interaction.response.send_message("📭 You have no active reminders.", ephemeral=True)

    now = datetime.now(timezone.utc)
    lines = ["**🔔 Your Active Reminders:**\n"]
    for rem in user_reminders:
        remaining = max(0, int((rem.start_time + timedelta(seconds=rem.duration) - now).total_seconds()))
        lines.append(f"• **{rem.keyword}** — triggers in {humanize_seconds(remaining)}\n")

    # noinspection PyUnresolvedReferences
    return await interaction.response.send_message("".join(lines), ephemeral=True)


# ------------------------------------------------------
# Run bot + keepalive
# ------------------------------------------------------
async def main():
    # start keepalive first (returns once site started)
    await run_keepalive()
    # start the bot (this call blocks until the bot stops)
    try:
        await bot.start(DISCORD_TOKEN)
    finally:
        # ensure cleanup
        await bot.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down by user request.")

