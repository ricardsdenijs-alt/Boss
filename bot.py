import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime, timedelta
import re
import os
from dotenv import load_dotenv

# Load token
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

if not DISCORD_TOKEN:
    raise ValueError("❌ DISCORD_TOKEN not found in .env")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)
guild = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None

active_timers = []
timer_id_counter = 1


def parse_time_string(time_str):
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


async def run_timer(timer):
    user = timer["user"]
    channel = timer["channel"]
    timer_id = timer["id"]

    try:
        for hop_index in range(timer["hops"]):
            duration = timer["initial_duration"] if hop_index == 0 else 7200
            link = timer["link"]
            region = timer["region"]

            print(f"⏰ Timer #{timer_id} started: {duration}s for hop {hop_index + 1}")

            # 5-minute alert logic
            if duration > 300:
                # Wait until 5 minutes before end
                wait_time_before_alert = duration - 300
                print(f"⏰ Timer #{timer_id}: Waiting {wait_time_before_alert}s for 5-min alert")
                await asyncio.sleep(wait_time_before_alert)
                
                # Send 5-minute warning
                if timer in active_timers:  # Check if timer still exists
                    await channel.send(
                        f"{user.mention} ⚠️ **Timer #{timer_id}** - Bosses in 5 minutes!\n"
                        f"🌍 Region: *{region}*\n🔗 {link}"
                    )
                    print(f"✅ 5-min alert sent for Timer #{timer_id}")
                
                # Wait the remaining 5 minutes
                await asyncio.sleep(300)
            else:
                # If duration is 5 minutes or less, just wait the full time
                await asyncio.sleep(duration)

            # Send completion message for this hop
            if timer in active_timers:
                await channel.send(
                    f"{user.mention} 🎯 **Timer #{timer_id}** - Hop {hop_index + 1} completed!\n"
                    f"🌍 Region: *{region}*\n🔗 {link}"
                )
                print(f"✅ Timer #{timer_id} hop {hop_index + 1} completed")

            # If there are more hops, continue after 2 hours
            if hop_index < timer["hops"] - 1:
                print(f"⏰ Timer #{timer_id}: Waiting 2h for next hop")
                await asyncio.sleep(7200)  # 2 hours between hops

    except Exception as e:
        print(f"❌ Error in timer #{timer_id}: {e}")
        try:
            await channel.send(f"❌ Timer #{timer_id} encountered an error: {e}")
        except:
            pass

    # Cleanup
    if timer in active_timers:
        active_timers.remove(timer)
        print(f"🗑️ Timer #{timer_id} removed from active timers")


@bot.event
async def on_ready():
    try:
        if guild:
            await bot.tree.sync(guild=guild)
            print(f"✅ Bot is ready as {bot.user} in guild {GUILD_ID}")
        else:
            await bot.tree.sync()
            print(f"✅ Bot is ready as {bot.user} (global)")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")


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
        seconds = parse_time_string(time)
        
        # Validate hops
        if hops < 1:
            await interaction.response.send_message("❌ Hops must be at least 1.", ephemeral=True)
            return

        # Check duplicate link if link is provided
        if link:
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
        }

        active_timers.append(timer_data)
        asyncio.create_task(run_timer(timer_data))

        # Format time display
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        
        time_display = ""
        if hours > 0:
            time_display += f"{hours}h"
        if minutes > 0:
            time_display += f"{minutes}m"
        
        await interaction.response.send_message(
            f"⏱ **Timer #{timer_id_counter}** started by {interaction.user.mention}\n"
            f"• Initial time: **{time_display}**\n"
            f"• Region: **{region}**\n"
            f"• Hops: **{hops}**\n"
            f"• Link: {link if link else 'Not provided'}"
        )

        timer_id_counter += 1

    except ValueError as e:
        await interaction.response.send_message(f"❌ {str(e)}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ An unexpected error occurred: {str(e)}", ephemeral=True)


@bot.tree.command(name="timers", description="List all active timers.")
async def timers(interaction: discord.Interaction):
    if not active_timers:
        await interaction.response.send_message("📭 No active timers.", ephemeral=True)
        return

    msg = "**🕒 Active Timers:**\n\n"
    for t in active_timers:
        # Calculate time display
        seconds = t["initial_duration"]
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        time_display = ""
        if hours > 0:
            time_display += f"{hours}h"
        if minutes > 0:
            time_display += f"{minutes}m"
            
        msg += (
            f"**Timer #{t['id']}** — Region: `{t['region']}`\n"
            f"• Initial duration: **{time_display}**\n"
            f"• Hops remaining: **{t['hops']}**\n"
            f"• User: {t['user'].mention}\n"
            f"• Invite: {t['link'] if t['link'] else 'Not provided'}\n\n"
        )

    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="remove", description="Remove a timer by its number.")
@app_commands.describe(timer_number="The timer number to remove.")
async def remove(interaction: discord.Interaction, timer_number: int):
    for t in active_timers:
        if t["id"] == timer_number:
            active_timers.remove(t)
            await interaction.response.send_message(f"🛑 Timer #{timer_number} has been deleted.", ephemeral=True)
            return
    await interaction.response.send_message("❌ No timer found with that number.", ephemeral=True)


@bot.tree.command(name="reminder", description="Set a reminder for boss, raids, or super.")
@app_commands.describe(message="Enter one of: boss, raids, super")
async def reminder(interaction: discord.Interaction, message: str):
    keyword = message.lower()
    if keyword not in ["boss", "super", "raids"]:
        await interaction.response.send_message("❌ Use 'boss', 'super', or 'raids'.", ephemeral=True)
        return

    # Determine wait time based on keyword
    if keyword == "boss":
        wait_time = 3600  # 1 hour
        display_name = "Boss"
    elif keyword == "super":
        wait_time = 3600  # 1 hour
        display_name = "Super"
    else:  # raids
        wait_time = 7200  # 2 hours
        display_name = "Raids"

    await interaction.response.send_message(
        f"⏰ {interaction.user.mention} your reminder for **{display_name}** is activated. "
        f"I'll remind you in {wait_time//3600} hour{'s' if wait_time > 3600 else ''}."
    )
    
    # Use asyncio.create_task to run the reminder in background
    asyncio.create_task(send_reminder(interaction, wait_time, display_name))


async def send_reminder(interaction, wait_time, display_name):
    try:
        print(f"⏰ Reminder set for {display_name}, waiting {wait_time}s")
        await asyncio.sleep(wait_time)
        await interaction.channel.send(
            f"{interaction.user.mention} 🔔 it's your **{display_name}** reminder!"
        )
        print(f"✅ Reminder sent for {display_name}")
    except Exception as e:
        print(f"❌ Error in reminder: {e}")


# Optional web server for uptime (Render, Replit)
try:
    from flask import Flask
    from threading import Thread

    app = Flask(__name__)

    @app.route('/')
    def home():
        return "✅ Bot is running."

    def run_flask():
        port = int(os.environ.get("PORT", 3000))
        app.run(host='0.0.0.0', port=port)

    Thread(target=run_flask, daemon=True).start()
    print("🌐 Flask server started")
except ImportError:
    print("⚠️ Flask not available, web server disabled")

# Run bot
if __name__ == "__main__":
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        print(f"❌ Bot crashed: {e}")
