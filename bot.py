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
from aiohttp import web

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

        # Finished all hops
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
        return await interaction.response.send_message(f"❌ {e}", ephemeral=True)

    if hops < 1:
        return await interaction.response.send_message("❌ Hops must be at least 1.", ephemeral=True)

    link = (link or "").strip()
    if link and any(t.link == link for t in active_timers):
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
    await interaction.response.send_message(
        f"⏱ **Timer #{timer_id}** activated for {time} (hops: {hops}) in region **{region}**.",
        ephemeral=True
    )

@bot.tree.command(name="timers", description="List all active timers.")
async def timers_command(interaction: Interaction):
    if not active_timers:
        return await interaction.response.send_message("📭 No active timers.", ephemeral=True)

    lines = ["**🕒 Active Timers:**\n"]
    now = datetime.now(timezone.utc)
    for t in active_timers:
        remaining = max(0, int((t.alert_time - now).total_seconds())) if t.alert_time else 0
        hours, minutes = divmod(remaining // 60, 60)
        display = f"{hours}h{minutes}m" if hours else f"{minutes}m"
        lines.append(f"**#{t.id}** — Region: **{t.region}**, Hops left: **{t.remaining_hops}**, Next: **{display}**\n")

    await interaction.response.send_message("".join(lines), ephemeral=True)

@bot.tree.command(name="remove", description="Remove a timer by its number.")
async def remove_command(interaction: Interaction, timer_number: int):
    for t in list(active_timers):
        if t.id == timer_number:
            if t.task and not t.task.done():
                t.task.cancel()
            if t in active_timers:
                active_timers.remove(t)
            return await interaction.response.send_message(f"🛑 Timer #{timer_number} deleted.", ephemeral=True)
    await interaction.response.send_message("❌ No timer found with that number.", ephemeral=True)

# ------------------------------------------------------
# Reminder Commands
# ------------------------------------------------------
@bot.tree.command(name="reminder", description="Set a reminder for boss, raids, or super.")
async def reminder_command(interaction: Interaction, message: str):
    keyword = message.lower().strip()
    if keyword not in {"boss", "super", "raids"}:
        return await interaction.response.send_message("❌ Use exactly one of: boss, super, raids", ephemeral=True)

    wait_time = 3600 if keyword in {"boss", "super"} else 7200
    reminder = ReminderData(keyword=keyword, start_time=datetime.now(timezone.utc), duration=wait_time)
    reminder.task = asyncio.create_task(reminder_worker(interaction.channel, interaction.user, reminder))
    active_reminders.setdefault(interaction.user.id, []).append(reminder)
    await interaction.response.send_message(f"⏰ Reminder set for **{keyword}** in {wait_time//60} minutes!", ephemeral=True)

async def reminder_worker(channel, author, data: ReminderData):
    try:
        await asyncio.sleep(data.duration)
        await safe_send(channel, f"{author.mention} ⏰ Reminder: **{data.keyword}** is happening now!")
    except asyncio.CancelledError:
        return
    finally:
        if author.id in active_reminders and data in active_reminders[author.id]:
            active_reminders[author.id].remove(data)

@bot.tree.command(name="reminders", description="List your active reminders.")
async def reminders_command(interaction: Interaction):
    reminders = active_reminders.get(interaction.user.id, [])
    if not reminders:
        return await interaction.response.send_message("📭 You have no active reminders.", ephemeral=True)
    now = datetime.now(timezone.utc)
    lines = ["**⏰ Your Active Reminders:**\n"]
    for i, r in enumerate(reminders, 1):
        end_time = r.start_time + timedelta(seconds=r.duration)
        remaining = max(0, int((end_time - now).total_seconds()))
        m, s = divmod(remaining, 60)
        lines.append(f"{i}. **{r.keyword}** — {m}m{s}s remaining\n")
    await interaction.response.send_message("".join(lines), ephemeral=True)

# ------------------------------------------------------
# Web Server for Render/UptimeRobot
# ------------------------------------------------------
async def handle_root(_):
    return web.Response(text="✅ Bot is running and healthy")

async def handle_status(_):
    return web.json_response({
        "status": "ok",
        "timers": len(active_timers),
        "reminders": sum(len(v) for v in active_reminders.values())
    })

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/status", handle_status)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"🌐 Webserver started on port {PORT}")
    return runner

# ------------------------------------------------------
# Entrypoint
# ------------------------------------------------------
async def main():
    runner = await start_webserver()

    bot_task = asyncio.create_task(bot.start(DISCORD_TOKEN))
    logger.info("🚀 Bot task started.")

    try:
        await bot_task
    except asyncio.CancelledError:
        logger.info("Bot cancelled.")
    except Exception as e:
        logger.exception(f"Bot crashed: {e}")
    finally:
        logger.info("Cleaning up...")
        for t in list(active_timers):
            if t.task and not t.task.done():
                t.task.cancel()
        for u, rems in active_reminders.items():
            for r in rems:
                if r.task and not r.task.done():
                    r.task.cancel()
        await asyncio.gather(*(t.task for t in active_timers if t.task), return_exceptions=True)
        await runner.cleanup()
        logger.info("Shutdown complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Exited by user.")
