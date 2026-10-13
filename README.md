# ClipKit

Turn a funny clip into a **Discord soundboard sound** and a **captioned GIF**.

Paste an Instagram, TikTok, YouTube, X or Reddit link and say which part you want, either by
the words said in it or by start/end times. ClipKit downloads the clip, finds the moment by
listening to the speech, and gives you:

- an **MP3** that fits Discord's soundboard rules (5.2 s max, under 512 KB, loudness evened out)
- a **GIF** with your caption burned in, sized to fit Discord uploads

It's a project I run for my friends' Discord server, not a hosted service. To use it in your own
server, run your own copy (setup below).

## Using the bot

```
/clip link:https://www.instagram.com/p/... phrase:bweak yourself
```

| Option | What it does |
| --- | --- |
| `link` | The video link (anything [yt-dlp](https://github.com/yt-dlp/yt-dlp) supports) |
| `phrase` | Words said in the clip. ClipKit cuts around them and uses *your* spelling as the caption, so "bweak" stays "bweak" even though it hears "break". |
| `start` / `end` | Pick the part by time instead: `12.5` or `0:12.5` |
| `caption` | Caption text, if you want something other than the phrase |
| `no_caption` | GIF without a caption |

Without `phrase` or `start`/`end`, a clip of 5.2 s or less is used whole. A longer one gets a
reply listing what's said and when, so you can pick.

The reply has an **Add to soundboard** button. Anyone with the *Create Expressions* permission
can use it to put the sound straight onto the server soundboard.

## Running your own

You need Windows, macOS or Linux with Python 3.10+ and ffmpeg. The bot only answers while it's running.

1. **Install ffmpeg.** On Windows run `winget install Gyan.FFmpeg`; on macOS `brew install ffmpeg`.
2. **Install ClipKit:**
   ```
   git clone https://github.com/josfire18/clipkit
   cd clipkit
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt     (macOS/Linux: .venv/bin/pip ...)
   ```
3. **Make a Discord bot** at <https://discord.com/developers/applications>:
   *New Application*, then go to **Bot**, click **Reset Token** and copy the token.
   No privileged intents are needed.
4. **Add the token.** Copy `.env.example` to `.env` and paste the token after `DISCORD_TOKEN=`.
   Keep `.env` private; anyone with the token controls the bot.
5. **Invite the bot.** Open this link, swapping in your app's *Application ID* from the **General Information** page:
   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot+applications.commands&permissions=8796093074432
   ```
   That grants View Channels, Send Messages, Embed Links, Attach Files and Create Expressions (for the soundboard button).
6. **Run it** with `.venv\Scripts\python bot.py`. On Windows, `start-bot.bat` runs it in the
   background with no window. To start it with Windows, put a shortcut to `start-bot.bat` in
   `shell:startup`.

The first clip downloads the speech model (about 500 MB for `small`). Pick a smaller one with
`WHISPER_MODEL` in `.env`.

## Command line

The same tool works without Discord:

```
python clipkit.py "https://www.instagram.com/p/..." --phrase "bweak yourself"
python clipkit.py clip.mp4 --start 3.2 --end 5.9 --caption "custom text"
```

Files land in `./out`. Run `python clipkit.py --help` for every option.

## Notes

- **Instagram download errors:** Instagram sometimes refuses logged-out downloads. ClipKit retries a
  few times. If it keeps failing, set `COOKIES_BROWSER=firefox` (or `chrome`/`edge`) in `.env`
  (or pass `--cookies-from-browser` on the command line) to use a logged-in browser's session.
- **Speed:** transcription runs on the CPU, about 10–20 s per clip with the `small` model.
- **Content:** only clip things you're allowed to share.
