# ClipKit

Turn a funny clip into a **Discord soundboard sound** and a **captioned GIF**.

Give it an Instagram, TikTok, YouTube, X or Reddit link (or a video file) and say which part you
want, either by the words said in it or by start/end times. ClipKit downloads the clip, finds the
moment by listening to the speech, and gives you:

- an **MP3** that fits Discord's soundboard rules (5.2 s max, under 512 KB, loudness evened out)
- a **GIF** with your caption burned in

It's a command-line tool and a Python library. The [Rocko](https://github.com/josfire18/rocko)
Discord bot uses it for its `/clip` command.

## Install

You need Python 3.10+ and ffmpeg. On Windows run `winget install Gyan.FFmpeg`; on macOS
`brew install ffmpeg`.

```
pip install git+https://github.com/josfire18/clipkit
```

## Command line

```
clipkit "https://www.instagram.com/p/..." --phrase "bweak yourself"
clipkit clip.mp4 --start 3.2 --end 5.9 --caption "custom text"
```

`--phrase` cuts around the words and uses *your* spelling as the caption, so "bweak" stays "bweak"
even though it hears "break". Files land in `./out`. Run `clipkit --help` for every option.

The first run downloads the speech model (about 500 MB for the default `small`; choose another with `--model`).

## Library

```python
import clipkit

result = clipkit.make_clip(url, "out", phrase="bweak yourself", max_gif_bytes=9_000_000)
result["sound"], result["gif"]      # Paths
```

`make_clip` raises `clipkit.ClipError` for problems the person asking can fix (bad link, phrase not
found). If you pass `range_required_over=5.2` and give no phrase or times, it raises
`clipkit.NeedsRange` for clips over that length. `NeedsRange.lines` holds the transcript with
timestamps so the person can pick.

## Notes

- **Instagram download errors:** Instagram sometimes refuses logged-out downloads. ClipKit retries a
  few times; if that keeps failing, use `--cookies-from-browser firefox` (or `chrome`/`edge`).
- **Speed:** transcription runs on the CPU, about 10–20 s per clip with `small`.
- **Content:** only clip things you're allowed to share.
