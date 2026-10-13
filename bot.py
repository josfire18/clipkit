"""ClipKit Discord bot: /clip <link> [phrase] [start] [end] -> captioned GIF + soundboard-ready MP3.

Put DISCORD_TOKEN=... in a .env file next to this script, then run:  python bot.py
"""
import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

import discord
from discord import app_commands

import clipkit

HERE = Path(__file__).resolve().parent
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=[logging.FileHandler(HERE / "bot.log", encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger("clipkit")


def load_env():
    env_file = HERE / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip('"'))


load_env()
MODEL = os.environ.get("WHISPER_MODEL", "small")
COOKIES_BROWSER = os.environ.get("COOKIES_BROWSER") or None
DEFAULT_UPLOAD_LIMIT = 10 * 1024 * 1024

client = discord.Client(intents=discord.Intents.default())
tree = app_commands.CommandTree(client)
job_lock = asyncio.Lock()  # one clip at a time: whisper + ffmpeg already use every core


def parse_time(text):
    """'12.5', '0:12.5' or '1:02:03' -> seconds."""
    if text is None:
        return None
    try:
        seconds = 0.0
        for part in text.strip().split(":"):
            seconds = seconds * 60 + float(part)
        return seconds
    except ValueError:
        raise clipkit.ClipError(f"Couldn't read \"{text}\" as a time. Use seconds (12.5) or m:ss (0:12.5).")


def fmt_time(seconds):
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def soundboard_name(text):
    name = " ".join(text.split())[:32]
    return name if len(name) >= 2 else "clip"


class SoundboardButton(discord.ui.View):
    def __init__(self, sound_bytes, name):
        super().__init__(timeout=30 * 60)
        self.sound_bytes = sound_bytes
        self.name = name

    @discord.ui.button(label="Add to soundboard", emoji="🔊", style=discord.ButtonStyle.primary)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button):
        perms = interaction.user.guild_permissions
        if not (perms.create_expressions or perms.manage_expressions):
            await interaction.response.send_message(
                "You need the **Create Expressions** permission to add soundboard sounds.", ephemeral=True)
            return
        try:
            await interaction.guild.create_soundboard_sound(
                name=self.name, sound=self.sound_bytes, reason=f"ClipKit, for {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have the **Create Expressions** permission in this server.", ephemeral=True)
            return
        except discord.HTTPException as e:
            # e.g. the server is out of soundboard slots
            await interaction.response.send_message(f"Discord said no: {e.text}", ephemeral=True)
            return
        button.disabled = True
        button.label = f"Added by {interaction.user.display_name}"
        await interaction.response.edit_message(view=self)


@tree.command(name="clip", description="Turn a video link into a captioned GIF + soundboard sound")
@app_commands.guild_only()
@app_commands.describe(
    link="Instagram / TikTok / YouTube / X / Reddit link",
    phrase="Words said in the clip; it cuts around them and uses your spelling as the caption",
    start="Start time, e.g. 12.5 or 0:12.5",
    end="End time, e.g. 15 or 0:15",
    caption="Caption text (default: the phrase, or what's said)",
    no_caption="Make the GIF without a caption",
)
async def clip(interaction: discord.Interaction, link: str, phrase: str = None, start: str = None,
               end: str = None, caption: str = None, no_caption: bool = False):
    await interaction.response.defer(thinking=True)
    if job_lock.locked():
        await interaction.followup.send("Working on another clip first, yours is next...", ephemeral=True)

    out_dir = Path(tempfile.mkdtemp(prefix="clipkit_bot_"))
    try:
        upload_limit = interaction.guild.filesize_limit if interaction.guild else DEFAULT_UPLOAD_LIMIT
        async with job_lock:
            result = await asyncio.to_thread(
                clipkit.make_clip, link, out_dir, phrase=phrase, start=parse_time(start), end=parse_time(end),
                caption=caption, subs=not no_caption, model=MODEL, cookies_browser=COOKIES_BROWSER,
                max_gif_bytes=upload_limit - 256 * 1024,
                range_required_over=clipkit.SOUNDBOARD_MAX_SECONDS, log=log.info)

        label = caption or phrase or "clip"
        lines = [f"**{discord.utils.escape_markdown(label)}** · {fmt_time(result['start'])}–"
                 f"{fmt_time(result['end'])} ({result['end'] - result['start']:.1f}s)"]
        lines += [f"-# {note}" for note in result["notes"]]
        sound_bytes = result["sound"].read_bytes()
        await interaction.followup.send(
            "\n".join(lines),
            files=[discord.File(result["gif"]), discord.File(result["sound"])],
            view=SoundboardButton(sound_bytes, soundboard_name(label)))
    except clipkit.NeedsRange as e:
        listing = "\n".join(f"`{fmt_time(a)}–{fmt_time(b)}` {text}" for a, b, text in e.lines)
        message = (f"That clip is {e.duration:.0f}s and soundboard sounds max out at "
                   f"{clipkit.SOUNDBOARD_MAX_SECONDS}s. Run `/clip` again with `phrase:` (some words from below) "
                   f"or `start:`/`end:`.\n\n{listing or '(no speech detected, use start:/end:)'}")
        await interaction.followup.send(message[:1990])
    except clipkit.ClipError as e:
        await interaction.followup.send(f"⚠️ {e}"[:1990])
    except Exception:
        log.exception("clip failed for %s", link)
        await interaction.followup.send("⚠️ Something broke making that clip. The details are in bot.log.")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


async def sync_commands(guild):
    # Per-server sync shows the command right away; a global sync can take a while to appear.
    tree.copy_global_to(guild=guild)
    await tree.sync(guild=guild)


@client.event
async def on_ready():
    for guild in client.guilds:
        await sync_commands(guild)
    log.info("Logged in as %s, serving %d server(s)", client.user, len(client.guilds))


@client.event
async def on_guild_join(guild):
    await sync_commands(guild)


if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit(f"No DISCORD_TOKEN. Put DISCORD_TOKEN=... in {HERE / '.env'} (see README).")
    client.run(token, log_handler=None)
