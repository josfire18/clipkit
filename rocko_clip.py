"""Rocko add-on: /clip, a video link -> captioned GIF + soundboard-ready MP3.

Install into Rocko's venv with  pip install "clipkit[rocko] @ git+https://github.com/josfire18/clipkit@v0.2.0"
and Rocko finds it through the "rocko.addons" entry point in pyproject.toml. Settings (in Rocko's .env):
WHISPER_MODEL (tiny/base/small/medium) and COOKIES_BROWSER (chrome/firefox/edge).
"""
import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

import clipkit
import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("rocko.clip")
DEFAULT_UPLOAD_LIMIT = 10 * 1024 * 1024


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
                name=self.name, sound=self.sound_bytes, reason=f"Rocko /clip, for {interaction.user}")
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


class Clip(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.model = os.environ.get("WHISPER_MODEL", "small")
        self.cookies_browser = os.environ.get("COOKIES_BROWSER") or None
        self.job_lock = asyncio.Lock()  # one clip at a time: whisper + ffmpeg already use every core

    @app_commands.command(name="clip", description="Turn a video link into a captioned GIF + soundboard sound")
    @app_commands.guild_only()
    @app_commands.describe(
        link="Instagram / TikTok / YouTube / X / Reddit link",
        phrase="Words said in the clip; it cuts around them and uses your spelling as the caption",
        start="Start time, e.g. 12.5 or 0:12.5",
        end="End time, e.g. 15 or 0:15",
        caption="Caption text (default: the phrase, or what's said)",
        no_caption="Make the GIF without a caption",
    )
    async def clip(self, interaction: discord.Interaction, link: str, phrase: str = None, start: str = None,
                   end: str = None, caption: str = None, no_caption: bool = False):
        await interaction.response.defer(thinking=True)
        if self.job_lock.locked():
            await interaction.followup.send("Working on another clip first, yours is next...", ephemeral=True)

        out_dir = Path(tempfile.mkdtemp(prefix="rocko_clip_"))
        try:
            upload_limit = interaction.guild.filesize_limit if interaction.guild else DEFAULT_UPLOAD_LIMIT
            async with self.job_lock:
                result = await asyncio.to_thread(
                    clipkit.make_clip, link, out_dir, phrase=phrase, start=parse_time(start),
                    end=parse_time(end), caption=caption, subs=not no_caption, model=self.model,
                    cookies_browser=self.cookies_browser, max_gif_bytes=upload_limit - 256 * 1024,
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
                       f"{clipkit.SOUNDBOARD_MAX_SECONDS}s. Run `/clip` again with `phrase:` (some words from "
                       f"below) or `start:`/`end:`.\n\n{listing or '(no speech detected, use start:/end:)'}")
            await interaction.followup.send(message[:1990])
        except clipkit.ClipError as e:
            await interaction.followup.send(f"⚠️ {e}"[:1990])
        except Exception:
            log.exception("clip failed for %s", link)
            await interaction.followup.send("⚠️ Something broke making that clip. The details are in bot.log.")
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)


async def setup(bot):
    await bot.add_cog(Clip(bot))
