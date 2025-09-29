import asyncio
import os
import re
from datetime import datetime, timedelta
from typing import List, Dict

import discord
from discord import app_commands, Interaction
from discord.ext import commands
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

if not DISCORD_TOKEN:
    raise ValueError("❌ DISCORD_TOKEN not found in .env")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)
guild_object = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None

# Timer tracking
active_timers: List[Dict] = []
timer_id_counter = 1


def parse_time_string(time_str: str) -> int:
    time_str = time_str.replace(" ", "").lower()
    pattern = r'(?:(\d+)h)?(?:(\d+)m)?'
    match = re.fullmatch(pattern, time_str)

    if not match:
        raise ValueError("Invalid time format. Use '1h30m', '45m', etc.")

    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0

    total_seconds = hours * 3600 + minutes * 60
    if total_seconds <= 0:
        raise ValueError("Time must be greater than 0.")

    return total_seconds


async def execute_timer(timer_data: Dict):
    user = timer_data["user"]
    channel = timer_data["channel"]
    timer_id = timer_data["id"]

    try:
        for hop_index in range(timer_data["hops"]):
            duration = timer_data["initial_duration"] if hop_index == 0 else 7200
            current_hop = hop_index + 1
            region = timer_data["region"]
            link = timer_data["link"]

            print(f"⏰ Timer #{timer_id} Hop {current_hop}: Duration = {duration}s")

            if duration > 300:
                await asyncio.sleep(duration - 300)
                if timer_data in active_timers:
                    await channel.send(
                        f"{user.mention} ⚠️ **Timer #{timer_id}** - Bosses in 5 minutes!\n"
                        f"🌍 Region: *{region}*\n🔗 {link}"
                    )
            else:
                await asyncio.sleep(duration)

            timer_data["remaining_hops"] = timer_data["hops"] - current_hop

            if current_hop < timer_data["hops"]:
                await asyncio.sleep(7200)  # Wait 2 hours

        if timer_data in active_timers:
            active_timers.remove(timer_data)
            print(f"🗑️ Timer #{timer_id} removed after completion.")

    except Exception as err1:
        print(f"❌ Error in Timer #{timer_id}: {err1}")
        if timer_data in active_timers:
            active_timers.remove(timer_data)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        if guild_object:
            synced = await bot.tree.sync(guild=guild_object)
            print(f"✅ Synced {len(synced)} commands to guild {GUILD_ID}")
        else:
            synced = await bot.tree.sync()
            print(f"✅ Synced {len(synced)} commands globally")
    except Exception as err2:
        print(f"❌ Sync Error: {err2}")


@bot.tree.command(name="timer", description="Start a repeating timer with hops.")
@app_commands.describe(
    time="Initial time (e.g. 1h30m)",
    hops="Number of hops (default 1)",
    region="Region name",
    link="Invite link to the server"
)
async def timer_command(interaction: Interaction, time: str, hops: int = 1, region: str = "Unknown", link: str = ""):
    global timer_id_counter

    try:
        seconds = parse_time_string(time)

        if hops < 1:
            await interaction.followup.send("❌ Hops must be at least 1.", ephemeral=True)
            return

        if link:
            if any(t["link"] == link for t in active_timers):
                await interaction.followup.send("❌ This link already has a timer.", ephemeral=True)
                return

        timer_data = {
            "id": timer_id_counter,
            "user": interaction.user,
            "channel": interaction.channel,
            "initial_duration": seconds,
            "region": region,
            "link": link,
            "hops": hops,
            "remaining_hops": hops,
            "start_time": datetime.now(),
            "alert_time": datetime.now() + timedelta(seconds=max(0, seconds - 300)),
        }

        active_timers.append(timer_data)
        asyncio.create_task(execute_timer(timer_data))

        await interaction.followup.send(
            f"⏱ **Timer #{timer_id_counter}** activated!\n🌍 Region: {region}",
            ephemeral=True
        )

        timer_id_counter += 1

    except ValueError as err3:
        await interaction.followup.send(f"❌ {err3}", ephemeral=True)

    except Exception as err4:
        print(f"❌ Timer Command Error: {err4}")
        await interaction.followup.send(f"❌ Unexpected error: {err4}", ephemeral=True)


@bot.tree.command(name="timers", description="List all active timers.")
async def timers_command(interaction: Interaction):
    if not active_timers:
        await interaction.followup.send("📭 No active timers.", ephemeral=True)
        return

    lines = ["**🕒 Active Timers:**\n"]
    for timer in active_timers:
        remaining = timer["alert_time"] - datetime.now()
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
            f"**Timer #{timer['id']}**\n"
            f"• Time until alert: **{time_display}**\n"
            f"• Region: **{timer['region']}**\n"
            f"• Hops remaining: **{timer['remaining_hops']}**\n\n"
        )

    await interaction.followup.send("".join(lines), ephemeral=True)


@bot.tree.command(name="remove", description="Remove a timer by its number.")
@app_commands.describe(timer_number="The timer number to remove.")
async def remove_command(interaction: Interaction, timer_number: int):
    for timer in active_timers:
        if timer["id"] == timer_number:
            active_timers.remove(timer)
            await interaction.followup.send(f"🛑 Timer #{timer_number} has been deleted.", ephemeral=True)
            return
    await interaction.followup.send("❌ No timer found with that number.", ephemeral=True)


@bot.tree.command(name="reminder", description="Set a reminder for boss, raids, or super.")
@app_commands.describe(message="Enter one of: boss, raids, super")
async def reminder_command(interaction: Interaction, message: str):
    keyword = message.lower()
    if keyword not in ["boss", "super", "raids"]:
        await interaction.followup.send("❌ Use 'boss', 'super', or 'raids'.", ephemeral=True)
        return

    # Define reminder times (example: boss/raids = 1h, super = 2h)
    wait_time = 3600 if keyword in ["boss", "raids"] else 7200

    await interaction.followup.send(
        f"⏰ Reminder set for **{keyword}** in {wait_time // 60} minutes!",
        ephemeral=True
    )

    async def send_reminder():
        await asyncio.sleep(wait_time)
        try:
            await interaction.followup.send(f"🔔 Reminder: Time for **{keyword}**!", ephemeral=True)
        except Exception as e:
            print(f"❌ Could not send reminder followup: {e}")

    asyncio.create_task(send_reminder())


# Run bot
bot.run(DISCORD_TOKEN)
