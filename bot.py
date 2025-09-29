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

# -------------------------
# Configuration
# -------------------------
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
PORT = int(os.getenv("PORT", 8080))

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN not found in environment")

try:
    GUILD_OBJECT: Optional[discord.Object] = (
        discord.Object(id=int(GUILD_ID)) if GUILD_ID else None
    )
except ValueError:
    GUILD_OBJECT = None

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("timer-bot")

# -------------------------
# Small typed models
# -------------------------
@dataclass
class TimerData:
    id: int
    user: Any
    channel: Optional[Any]  # text channel / messageable
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


# -------------------------
# Bot & state
# -------------------------
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="/", intents=intents)
active_timers: List[TimerData] = []
active_reminders: Dict[int, List[ReminderData]] = {}  # user_id -> list of reminders
_timer_id_counter = 1
_timer_lock = asyncio.Lock()

# -------------------------
# Utilities
# -------------------------
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


async def execute_timer(timer: TimerData):
    timer_id = timer.id
    channel = timer.channel
    hops = timer.hops
    initial_duration = timer.initial_duration
    region = timer.region
    link = timer.link

    try:
        for hop_index in range(hops):
            current_hop = hop_index + 1
            duration = initial_duration if hop_index == 0 else 7200
            timer.remaining_hops = hops - current_hop

            now = datetime.now(timezone.utc)
            if duration > 300:
                timer.alert_time = now + timedelta(seconds=duration - 300)
            else:
                timer.alert_time = now + timedelta(seconds=duration)

            logger.info(f"[Timer #{timer_id}] Hop {current_hop}/{hops}: waiting {duration}s")

            if duration > 300:
                await asyncio.sleep(duration - 300)
                # check whether timer was removed while sleeping
                if timer not in active_timers:
                    return
                if channel and hasattr(channel, "send"):
                    try:
                        await channel.send(
                            f"@here ⚠️ **Timer #{timer_id}** - bosses in 5 minutes!\n"
                            f"🌍 Region: *{region}*\n🔗 {link or 'No link provided'}"
                        )
                    except discord.DiscordException as exc:
                        logger.exception(f"[Timer #{timer_id}] Failed 5-min alert: {exc}")
                await asyncio.sleep(300)
            else:
                await asyncio.sleep(duration)



    except asyncio.CancelledError:
        logger.info(f"[Timer #{timer_id}] Cancelled.")
        raise
    # noinspection PyBroadException

    finally:
        if timer in active_timers:
            try:
                active_timers.remove(timer)
            except ValueError:
                pass
        logger.info(f"[Timer #{timer_id}] Cleaned up.")


# -------------------------
# Commands
# -------------------------
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (id: {bot.user.id})")
    try:
        if GUILD_OBJECT:
            synced = await bot.tree.sync(guild=GUILD_OBJECT)
            logger.info(f"Synced {len(synced)} commands to guild {GUILD_OBJECT.id}")
        else:
            synced = await bot.tree.sync()
            logger.info(f"Synced {len(synced)} global commands")
    # noinspection PyBroadException
    except Exception as exc:
        logger.exception(f"Command sync failed: {exc}")


@bot.tree.command(name="timer", description="Start a repeating timer with hops.")
@app_commands.describe(
    time="Initial time (e.g. 1h30m)",
    hops="Number of hops (default 1)",
    region="Region name",
    link="Invite link to the server"
)
async def timer_command(interaction: Interaction, time: str, hops: int = 1,
                        region: str = "Unknown", link: str = ""):
    global _timer_id_counter
    response: Any = interaction.response  # type: ignore
    # annotate as Any so PyCharm won't warn about InteractionResponse.send_message

    try:
        seconds = parse_time_string(time)
    except ValueError as ve:
        await response.send_message(f"❌ {ve}", ephemeral=True)
        return

    if hops < 1:
        await response.send_message("❌ Hops must be at least 1.", ephemeral=True)
        return

    link = (link or "").strip()
    if link and any(t.link == link for t in active_timers):
        await response.send_message("❌ A timer with that link already exists.", ephemeral=True)
        return

    async with _timer_lock:
        timer_id = _timer_id_counter
        _timer_id_counter += 1

    now = datetime.now(timezone.utc)
    alert_time = now + timedelta(seconds=seconds - 300 if seconds > 300 else seconds)

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
        alert_time=alert_time,
        task=None
    )

    task = asyncio.create_task(execute_timer(timer))
    timer.task = task
    active_timers.append(timer)

    await response.send_message(
        f"⏱ **Timer #{timer_id}** activated for `{time}` (hops: {hops}) in region **{region}**.",
        ephemeral=True
    )


@bot.tree.command(name="timers", description="List all active timers.")
async def timers_command(interaction: Interaction):
    response: Any = interaction.response  # type: ignore
    if not active_timers:
        await response.send_message("📭 No active timers.", ephemeral=True)
        return

    lines = ["**🕒 Active Timers:**\n"]
    now = datetime.now(timezone.utc)
    for t in active_timers:
        alert_time = t.alert_time
        if isinstance(alert_time, datetime):
            remaining = alert_time - now
            total_seconds = max(0, int(remaining.total_seconds()))
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            if total_seconds == 0:
                time_display = "Alert pending"
            elif hours > 0:
                time_display = f"{hours}h{minutes}m"
            elif minutes > 0:
                time_display = f"{minutes}m"
            else:
                time_display = "Less than 1m"
        else:
            time_display = "Unknown"

        lines.append(
            f"**Timer #{t.id}** — Region: **{t.region or 'Unknown'}** — "
            f"Hops left: **{t.remaining_hops}** — Next: **{time_display}**\n"
        )

    await response.send_message("".join(lines), ephemeral=True)


@bot.tree.command(name="remove", description="Remove a timer by its number.")
@app_commands.describe(timer_number="The timer number to remove.")
async def remove_command(interaction: Interaction, timer_number: int):
    response: Any = interaction.response  # type: ignore

    for t in list(active_timers):
        if t.id == timer_number:
            task = t.task
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                # noinspection PyBroadException
                except Exception as exc:
                    logger.exception(f"[Timer #{timer_number}] Cancel await error: {exc}")

            if t in active_timers:
                try:
                    active_timers.remove(t)
                except ValueError:
                    pass

            await response.send_message(f"🛑 Timer #{timer_number} deleted.", ephemeral=True)
            return

    await response.send_message("❌ No timer found with that number.", ephemeral=True)


@bot.tree.command(name="reminder", description="Set a reminder for boss, raids, or super.")
@app_commands.describe(message="Enter one of: boss, raids, super")
async def reminder_command(interaction: Interaction, message: str):
    response: Any = interaction.response  # type: ignore
    keyword = message.lower().strip()
    if keyword not in {"boss", "super", "raids"}:
        await response.send_message("❌ Use exactly one of: boss, super, raids", ephemeral=True)
        return

    wait_time_seconds = 3600 if keyword in {"boss", "super"} else 7200
    minutes = wait_time_seconds // 60
    await response.send_message(f"⏰ Reminder set for **{keyword}** in {minutes} minutes!", ephemeral=True)

    reminder = ReminderData(
        keyword=keyword,
        start_time=datetime.now(timezone.utc),
        duration=wait_time_seconds,
        task=None
    )

    async def reminder_worker(target_channel, author, data: ReminderData):
        try:
            await asyncio.sleep(data.duration)
            if target_channel and hasattr(target_channel, "send"):
                try:
                    await target_channel.send(f"{author.mention} ⏰ Reminder: **{data.keyword}** is happening now!")
                except discord.DiscordException as exc:
                    logger.exception(f"Reminder send failed: {exc}")
        except asyncio.CancelledError:
            return
        # noinspection PyBroadException
        except Exception as exc:
            logger.exception(f"Reminder failed: {exc}")
        finally:
            if author.id in active_reminders and data in active_reminders[author.id]:
                active_reminders[author.id].remove(data)

    task = asyncio.create_task(reminder_worker(interaction.channel, interaction.user, reminder))
    reminder.task = task
    active_reminders.setdefault(interaction.user.id, []).append(reminder)


@bot.tree.command(name="reminders", description="List your active reminders.")
async def reminders_command(interaction: Interaction):
    response: Any = interaction.response  # type: ignore
    user_id = interaction.user.id

    reminders = active_reminders.get(user_id, [])
    if not reminders:
        await response.send_message("📭 You have no active reminders.", ephemeral=True)
        return

    lines = ["**⏰ Your Active Reminders:**\n"]
    now = datetime.now(timezone.utc)
    for idx, r in enumerate(reminders, 1):
        end_time = r.start_time + timedelta(seconds=r.duration)
        remaining = max(0, int((end_time - now).total_seconds()))
        minutes = remaining // 60
        seconds = remaining % 60
        lines.append(f"{idx}. **{r.keyword}** — {minutes}m{seconds}s remaining\n")

    await response.send_message("".join(lines), ephemeral=True)

# -------------------------
# Tiny webserver
# -------------------------
async def _handle_root(_request):
    return web.Response(text="✅ Bot is running")


async def start_webserver():
    app = web.Application()
    app.router.add_get("/", _handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Webserver started on port {PORT}")
    return runner

# -------------------------
# Entrypoint
# -------------------------
async def _main():
    runner = await start_webserver()
    try:
        await bot.start(DISCORD_TOKEN)
    finally:
        logger.info("Bot stopping, cancelling timers/reminders...")
        tasks_to_wait: List[asyncio.Task] = []
        for t in list(active_timers):
            task = t.task
            if task and not task.done():
                task.cancel()
                tasks_to_wait.append(task)
        for user_id, reminders in list(active_reminders.items()):
            for r in reminders:
                task = r.task
                if task and not task.done():
                    task.cancel()
                    tasks_to_wait.append(task)
            active_reminders[user_id] = []

        if tasks_to_wait:
            await asyncio.gather(*tasks_to_wait, return_exceptions=True)
        try:
            await runner.cleanup()
        # noinspection PyBroadException
        except Exception:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user, exiting.")
