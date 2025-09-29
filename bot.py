import os
import re
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any

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
# Bot & state
# -------------------------
intents = discord.Intents.default()
intents.message_content = True  # enable if you need message content; adjust in dev portal if required

bot = commands.Bot(command_prefix="/", intents=intents)
active_timers: List[Dict[str, Any]] = []
_timer_id_counter = 1
_timer_lock = asyncio.Lock()  # protect counter increments


# -------------------------
# Utilities
# -------------------------
def parse_time_string(time_str: str) -> int:
    """
    Accepts formats like '1h30m', '45m', '2h' (whitespace allowed).
    Returns seconds (int). Raises ValueError for invalid input.
    """
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


async def execute_timer(timer_data: Dict[str, Any]):
    """
    Worker coroutine which handles alerting for a timer.
    If the timer is cancelled via timer_data['task'].cancel(), CancelledError will be raised here.
    """
    timer_id = timer_data["id"]
    user = timer_data["user"]
    channel = timer_data["channel"]
    hops = timer_data["hops"]
    initial_duration = timer_data["initial_duration"]
    region = timer_data["region"]
    link = timer_data["link"]

    try:
        for hop_index in range(hops):
            current_hop = hop_index + 1
            # First hop uses the user-provided duration, subsequent hops use 2 hours (7200s)
            duration = initial_duration if hop_index == 0 else 7200
            timer_data["remaining_hops"] = hops - current_hop

            # Update the next "alert_time" to reflect when the 5-minute warning (or the event)
            now = datetime.now(timezone.utc)
            if duration > 300:
                # next alert is the 5-minute warning moment
                timer_data["alert_time"] = now + timedelta(seconds=duration - 300)
            else:
                # next alert is the event time itself (no 5-minute warning)
                timer_data["alert_time"] = now + timedelta(seconds=duration)

            logger.info(f"[Timer #{timer_id}] Hop {current_hop}/{hops}: waiting {duration}s")

            # If >5 minutes, alert 5 minutes before the event
            if duration > 300:
                await asyncio.sleep(duration - 300)

                # If timer was removed from active_timers meanwhile, stop
                if timer_data not in active_timers:
                    logger.info(f"[Timer #{timer_id}] Removed before 5-minute alert; aborting.")
                    return

                # Post 5-minute warning
                if channel:
                    try:
                        await channel.send(
                            f"{user.mention} ⚠️ **Timer #{timer_id}** - bosses in 5 minutes!\n"
                            f"🌍 Region: *{region}*\n🔗 {link or 'No link provided'}"
                        )
                    except Exception as exc:
                        logger.exception(f"[Timer #{timer_id}] Failed to send 5-minute alert: {exc}")

                # wait the remaining 5 minutes
                await asyncio.sleep(300)
            else:
                # If duration <= 5min, just sleep the whole duration
                await asyncio.sleep(duration)

            # After waiting the hop duration (i.e., the event time), optionally notify it's happening now
            if timer_data in active_timers and channel:
                try:
                    await channel.send(
                        f"{user.mention} 🔔 **Timer #{timer_id}** - event happening now!\n"
                        f"🌍 Region: *{region}*\n🔗 {link or 'No link provided'}"
                    )
                except Exception as exc:
                    logger.exception(f"[Timer #{timer_id}] Failed to send 'happening now' message: {exc}")

            # loop will continue to next hop (which will set its own duration)
            # no extra sleep here — the next iteration's 'duration' controls the wait.

    except asyncio.CancelledError:
        # Task was cancelled (e.g., via remove command)
        logger.info(f"[Timer #{timer_id}] Cancelled.")
        # Let it bubble up so that higher-level cancel semantics remain intact
        raise
    except Exception as exc:
        logger.exception(f"[Timer #{timer_id}] Unexpected error: {exc}")
    finally:
        # Ensure timer cleaned from active_timers
        if timer_data in active_timers:
            active_timers.remove(timer_data)
            logger.info(f"[Timer #{timer_id}] Cleaned up from active_timers.")


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
    except Exception as exc:
        logger.exception(f"Command sync failed: {exc}")


@bot.tree.command(name="timer", description="Start a repeating timer with hops.")
@app_commands.describe(
    time="Initial time (e.g. 1h30m)",
    hops="Number of hops (default 1)",
    region="Region name",
    link="Invite link to the server"
)
async def timer_command(
    interaction: Interaction,
    time: str,
    hops: int = 1,
    region: str = "Unknown",
    link: str = ""
):
    global _timer_id_counter

    # Some linters / static analyzers don't realize interaction.response has send_message.
    # Give it a neutral runtime typing to avoid spurious "cannot find send_message" warnings.
    response: Any = interaction.response

    # Parse time first (so we can respond quickly and only once)
    try:
        seconds = parse_time_string(time)
    except ValueError as ve:
        await response.send_message(f"❌ {ve}", ephemeral=True)
        return

    if hops < 1:
        await response.send_message("❌ Hops must be at least 1.", ephemeral=True)
        return

    if link:
        if any(t["link"] == link for t in active_timers):
            await response.send_message("❌ A timer with that link already exists.", ephemeral=True)
            return

    # Reserve an ID safely
    async with _timer_lock:
        timer_id = _timer_id_counter
        _timer_id_counter += 1

    now = datetime.now(timezone.utc)
    # initial alert_time reflects the 5-minute warning if applicable
    if seconds > 300:
        alert_time = now + timedelta(seconds=seconds - 300)
    else:
        alert_time = now + timedelta(seconds=seconds)

    # store channel as a Messageable (could be None in some contexts)
    timer_data: Dict[str, Any] = {
        "id": timer_id,
        "user": interaction.user,
        "channel": interaction.channel,  # where to send alerts
        "initial_duration": seconds,
        "region": region,
        "link": link,
        "hops": hops,
        "remaining_hops": hops,
        "start_time": now,
        "alert_time": alert_time,
        "task": None,  # will be set below
    }

    # Start the timer task and keep reference for cancellation
    task = asyncio.create_task(execute_timer(timer_data))
    timer_data["task"] = task
    active_timers.append(timer_data)

    await response.send_message(
        f"⏱ **Timer #{timer_id}** activated for `{time}` (hops: {hops}) in region **{region}**.",
        ephemeral=True
    )
    logger.info(f"[Timer #{timer_id}] Created by {interaction.user} (link={bool(link)})")


@bot.tree.command(name="timers", description="List all active timers.")
async def timers_command(interaction: Interaction):
    response: Any = interaction.response

    if not active_timers:
        await response.send_message("📭 No active timers.", ephemeral=True)
        return

    lines = ["**🕒 Active Timers:**\n"]
    now = datetime.now(timezone.utc)
    for t in active_timers:
        remaining = t["alert_time"] - now
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

        lines.append(
            f"**Timer #{t['id']}** — Region: **{t['region']}** — Hops left: **{t['remaining_hops']}** — Next: **{time_display}**\n"
        )

    await response.send_message("".join(lines), ephemeral=True)


@bot.tree.command(name="remove", description="Remove a timer by its number.")
@app_commands.describe(timer_number="The timer number to remove.")
async def remove_command(interaction: Interaction, timer_number: int):
    response: Any = interaction.response

    for t in list(active_timers):  # iterate a copy
        if t["id"] == timer_number:
            # Cancel the running task if present
            task = t.get("task")
            if task and not task.done():
                task.cancel()
            if t in active_timers:
                active_timers.remove(t)
            await response.send_message(f"🛑 Timer #{timer_number} has been deleted.", ephemeral=True)
            logger.info(f"[Timer #{timer_number}] Removed by {interaction.user}")
            return

    await response.send_message("❌ No timer found with that number.", ephemeral=True)


@bot.tree.command(name="reminder", description="Set a reminder for boss, raids, or super.")
@app_commands.describe(message="Enter one of: boss, raids, super")
async def reminder_command(interaction: Interaction, message: str):
    response: Any = interaction.response

    keyword = message.lower().strip()
    if keyword not in {"boss", "super", "raids"}:
        await response.send_message("❌ Use exactly one of: boss, super, raids", ephemeral=True)
        return

    wait_time_seconds = 3600 if keyword in {"boss", "raids"} else 7200

    # Send immediate confirmation
    await response.send_message(f"⏰ Reminder set for **{keyword}** in {wait_time_seconds // 60} minutes!", ephemeral=True)

    # Schedule the actual reminder to the same channel
    async def reminder_worker(target_channel, author, kw, seconds):
        try:
            await asyncio.sleep(seconds)
            if target_channel:
                await target_channel.send(f"{author.mention} ⏰ Reminder: **{kw}** is happening now!")
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.exception(f"Reminder failed: {exc}")

    asyncio.create_task(reminder_worker(interaction.channel, interaction.user, keyword, wait_time_seconds))


# -------------------------
# Tiny webserver (for Render / healthcheck)
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


# -------------------------
# Entrypoint
# -------------------------
async def _main():
    await start_webserver()
    # Start the bot (this will block until bot stops)
    try:
        await bot.start(DISCORD_TOKEN)
    finally:
        # Attempt graceful shutdown
        logger.info("Bot stopped, cancelling remaining active timer tasks...")
        for t in list(active_timers):
            task = t.get("task")
            if task and not task.done():
                task.cancel()
        # allow short time for tasks to cancel
        await asyncio.sleep(0.1)


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user, exiting.")
