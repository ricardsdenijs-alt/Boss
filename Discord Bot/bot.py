import os
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime, timedelta
import re
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv("MTQyMTU0NzgxNjQ0MTg3MjQyNw.GwBJvY.EjfYKEmHL8GppZ6oI0PSFqIuovVUG6QbCMhMKo")

# Logging
handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="/", intents=intents)

# -----------------------------
# Events
# -----------------------------
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Bot is ready as {bot.user}. Slash commands synced.")

# -----------------------------
# Timer Commands
# -----------------------------
active_timers = {}
timer_id_counters = {}

def parse_time_string(time_str):
    time_str = time_str.replace(" ", "").lower()
    pattern = r'(?:(\d+)h)?(?:(\d+)m)?'
    match = re.fullmatch(pattern, time_str)

    if not match:
        raise ValueError("Invalid time format. Use like '1h30m', '45m', or '2h'.")

    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0

    total_seconds = hours * 3600 + minutes * 60
    return total_seconds, f"{hours}h {minutes}m"


async def run_hop(interaction, user_id, timer_id, hop_num, duration, region, link_md):
    try:
        end_time = datetime.utcnow() + timedelta(seconds=duration)
        for t in active_timers[user_id]:
            if t["id"] == timer_id and t["hop"] == hop_num:
                t["end_time"] = end_time

        if duration > 300:
            await asyncio.sleep(duration - 300)
            remaining_spawns = sum(1 for t in active_timers[user_id] if t["id"] == timer_id and t["hop"] > hop_num)
            await interaction.followup.send(
                f"⚠️ **Timer #{timer_id}** - Bosses in 5 minutes, Region: *{region}*, SPAWNS LEFT: {remaining_spawns}\n🔗 {link_md}"
            )
            await asyncio.sleep(300)
        else:
            await asyncio.sleep(duration)

        if hop_num == max(t["hop"] for t in active_timers[user_id] if t["id"] == timer_id):
            active_timers[user_id] = [t for t in active_timers[user_id] if t["id"] != timer_id]

    except asyncio.CancelledError:
        await interaction.followup.send(f"❌ **Timer #{timer_id}** was cancelled.")
        active_timers[user_id] = [t for t in active_timers[user_id] if t["id"] != timer_id]


@bot.tree.command(name="timer", description="Start a repeating timer with hops.")
@app_commands.describe(
    time="Initial time (e.g. 1h30m)",
    hops="Number of hops (default 1)",
    region="Region (default 'Unknown')",
    link="Invite link (optional)"
)
async def timer(interaction: discord.Interaction, time: str, hops: int = 1, region: str = "Unknown", link: str = ""):
    try:
        total_seconds, duration_str = parse_time_string(time)
        if total_seconds <= 0:
            await interaction.response.send_message("❌ Time must be greater than 0.", ephemeral=True)
            return

        if hops < 1:
            hops = 1

        user_id = interaction.user.id
        link_md = f"[Join Server]({link})" if link else ""

        timer_id_counters[user_id] = timer_id_counters.get(user_id, 0) + 1
        timer_id = timer_id_counters[user_id]

        await interaction.response.send_message(f"timer #{timer_id} has been activated\nregion: {region}")

        if user_id not in active_timers:
            active_timers[user_id] = []

        for hop in range(hops):
            hop_num = hop + 1
            duration = total_seconds if hop == 0 else 2 * 3600
            end_time = datetime.utcnow() + timedelta(seconds=duration)

            task = asyncio.create_task(run_hop(interaction, user_id, timer_id, hop_num, duration, region, link_md))

            active_timers[user_id].append({
                "id": timer_id,
                "hop": hop_num,
                "task": task,
                "end_time": end_time,
                "region": region,
                "link_md": link_md,
                "duration": duration
            })

    except ValueError as ve:
        await interaction.response.send_message(f"❌ {str(ve)}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Unexpected error: {str(e)}", ephemeral=True)


@bot.tree.command(name="timers", description="List your active timers.")
async def timers(interaction: discord.Interaction):
    user_id = interaction.user.id

    if user_id not in active_timers or not active_timers[user_id]:
        await interaction.response.send_message("📭 You have no active timers.", ephemeral=True)
        return

    now = datetime.utcnow()
    msg = "⏱ **Your Active Timers:**\n\n"

    timers_grouped = {}
    for t in active_timers[user_id]:
        timers_grouped.setdefault(t["id"], []).append(t)

    for tid, timer_hops in timers_grouped.items():
        timer_hops.sort(key=lambda x: x["hop"])
        msg += f"**Timer #{tid}** with {len(timer_hops)} hop(s):\n"
        for t in timer_hops:
            remaining = int((t["end_time"] - now).total_seconds())
            if remaining <= 0:
                continue
            mins, secs = divmod(remaining, 60)
            hrs, mins = divmod(mins, 60)
            time_left = f"{hrs}h {mins}m"
            msg += (
                f"  • Hop {t['hop']} in `{t['region']}` — **{time_left} left**\n"
                f"    🔗 {t['link_md']}\n"
            )
        msg += "\n"

    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="remove", description="Cancel a timer by its number.")
@app_commands.describe(timer_number="Number of the timer to cancel")
async def remove(interaction: discord.Interaction, timer_number: int):
    user_id = interaction.user.id

    if user_id not in active_timers or not active_timers[user_id]:
        await interaction.response.send_message("❌ You have no active timers.", ephemeral=True)
        return

    timers_grouped = {}
    for t in active_timers[user_id]:
        timers_grouped.setdefault(t["id"], []).append(t)

    if timer_number not in timers_grouped:
        await interaction.response.send_message(f"❌ No timer with number {timer_number} found.", ephemeral=True)
        return

    for t in timers_grouped[timer_number]:
        t["task"].cancel()

    await interaction.response.send_message(f"🛑 Timer #{timer_number} cancelled successfully.", ephemeral=True)

# -----------------------------
# Reminder Command
# -----------------------------
@bot.tree.command(name="reminder", description="Set a keyword-based reminder.")
@app_commands.describe(message="One of: Super, Boss, Raids")
async def reminder(interaction: discord.Interaction, message: str):
    keyword = message.lower()

    if keyword == "super":
        wait_time = 60 * 60
    elif keyword == "boss":
        wait_time = 60 * 60
    elif keyword == "raids":
        wait_time = 120 * 60
    else:
        await interaction.response.send_message("❌ Invalid reminder type. Use: Super, Boss, or Raids.", ephemeral=True)
        return

    await interaction.response.send_message(f"{interaction.user.mention} your reminder for **{message.capitalize()}** has been activated")