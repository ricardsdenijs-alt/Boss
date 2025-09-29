import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime, timedelta
import re
import os
from dotenv import load_dotenv

# -----------------------------
# Load token
# -----------------------------
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN not found in .env")

# -----------------------------
# Bot Setup
# -----------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)
guild = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None

active_timers = []
timer_id_counter = 1


# -----------------------------
# Helper Functions
# -----------------------------
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


async def run_timer(timer):
    user = timer["user"]
    channel = timer["channel"]

    for hop_index in range(timer["hops"]):
        duration = timer["initial_duration"] if hop_index == 0 else 7200
        end_time = datetime.utcnow() + timedelta(seconds=duration)
        timer["next_alert"] = end_time - timedelta(minutes=5)
        timer["hops_left"] = timer["hops"] - hop_index

        await asyncio.sleep(max(0, (timer["next_alert"] - datetime.utcnow()).total_seconds()))

        await channel.send(
            f"{user.mention} ⚠️ **Timer #{timer['id']}** - Bosses in 5 minutes!\n🌍 Region: *{timer['region']}*\n🔗 {timer['link']}"
        )

        await asyncio.sleep(5 * 60)
        await channel.send(
            f"{user.mention} ✅ **Timer #{timer['id']}** completed hop {hop_index + 1}."
        )

    # After all hops
    active_timers.remove(timer)


# -----------------------------
# Events
# -----------------------------
@bot.event
async def on_ready():
    await bot.tree.sync(guild=guild) if guild else await bot.tree.sync()
    print(f"Bot is ready as {bot.user}")


# -----------------------------
# /timer Command
# -----------------------------
@bot.tree.command(name="timer", description="Start a repeating timer with hops.")
@app_commands.describe(
    time="Initial time (e.g. 1h30m)",
    hops="Number of hops (default 1)",
    region="Region name",
    link="Invite link to the server"
)
async def timer(interaction: discord.Interaction, time: str, hops: int = 1, region: str = "Unknown", link: str = ""):
    global timer_id_counter

    try:
        seconds, formatted = parse_time_string(time)
        if seconds <= 0:
            await interaction.response.send_message("❌ Time must be greater than 0.", ephemeral=True)
            return

        # Check duplicate link globally
        for t in active_timers:
            if t["link"] == link:
                await interaction.response.send_message("❌ This link already has a timer.", ephemeral=True)
                return

        timer_data = {
            "id": timer_id_counter,
            "user": interaction.user,
            "channel": interaction.channel,
            "initial_duration": seconds,
            "region": region,
            "link": link,
            "hops": hops,
            "hops_left": hops,
            "next_alert": None
        }

        active_timers.append(timer_data)
        asyncio.create_task(run_timer(timer_data))

        await interaction.response.send_message(f"⏱ Timer #{timer_id_counter} started for {interaction.user.mention} in **{region}** with {hops} hop(s).")
        timer_id_counter += 1

    except ValueError as e:
        await interaction.response.send_message(f"❌ {str(e)}", ephemeral=True)


# -----------------------------
# /timers Command
# -----------------------------
@bot.tree.command(name="timers", description="List all active timers.")
async def timers(interaction: discord.Interaction):
    if not active_timers:
        await interaction.response.send_message("📭 No active timers.", ephemeral=True)
        return

    now = datetime.utcnow()
    msg = "**🕒 Active Timers:**\n\n"

    for t in active_timers:
        seconds_left = int((t["next_alert"] - now).total_seconds())
        minutes_left = max(0, seconds_left // 60)
        msg += (
            f"**Timer #{t['id']}** — Region: `{t['region']}`\n"
            f"  • Time left until alert: **{minutes_left} minutes**\n"
            f"  • Hops remaining: **{t['hops_left']}**\n\n"
        )

    await interaction.response.send_message(msg, ephemeral=True)


# -----------------------------
# /remove Command
# -----------------------------
@bot.tree.command(name="remove", description="Remove a timer by its number.")
@app_commands.describe(timer_number="The timer number to remove.")
async def remove(interaction: discord.Interaction, timer_number: int):
    for t in active_timers:
        if t["id"] == timer_number:
            active_timers.remove(t)
            await interaction.response.send_message(f"🛑 Timer #{timer_number} has been deleted.", ephemeral=True)
            return
    await interaction.response.send_message("❌ No timer found with that number.", ephemeral=True)


# -----------------------------
# /reminder Command
# -----------------------------
@bot.tree.command(name="reminder", description="Set a reminder for boss, raids, or super.")
@app_commands.describe(message="Enter one of: boss, raids, super")
async def reminder(interaction: discord.Interaction, message: str):
    keyword = message.lower()
    if keyword not in ["boss", "super", "raids"]:
        await interaction.response.send_message("❌ Invalid type. Use 'boss', 'super', or 'raids'.", ephemeral=True)
        return

    wait_time = 3600 if keyword in ["boss", "super"] else 7200
    await interaction.response.send_message(f"{interaction.user.mention} your reminder for **{keyword.capitalize()}** is activated.")

    await asyncio.sleep(wait_time)
    await interaction.channel.send(f"{interaction.user.mention}, it's your **{keyword.capitalize()}** reminder!")


# -----------------------------
# Run bot
# -----------------------------
try:
    bot.run(DISCORD_TOKEN)
except discord.LoginFailure:
    raise SystemExit("❌ Login failed: Check your token.")


