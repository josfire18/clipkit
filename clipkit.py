"""ClipKit: turn a video clip (Instagram, TikTok, YouTube, X, a local file...) into
a Discord-soundboard-ready sound and a captioned GIF.

  python clipkit.py URL --phrase "bweak yourself"
  python clipkit.py URL --start 3.2 --end 5.9 --caption "custom text"
  python clipkit.py clip.mp4

--phrase finds the words in the clip's speech and cuts around them.
Other programs (e.g. the Rocko Discord bot) call make_clip() directly.
"""
import argparse
import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Discord soundboard limits: MP3/OGG, at most 5.2 s and 512 KB.
SOUNDBOARD_MAX_SECONDS = 5.2
SOUNDBOARD_MAX_BYTES = 512 * 1024


class ClipError(Exception):
    """Something the person asking can fix (bad link, phrase not found, ...)."""


class NeedsRange(ClipError):
    """The clip is too long to use whole; carries the transcript so a range can be picked."""

    def __init__(self, duration, lines):
        super().__init__(f"Clip is {duration:.1f}s; pick a phrase or start/end.")
        self.duration = duration
        self.lines = lines  # [(start, end, text), ...]


def find_tool(name):
    path = shutil.which(name)
    if path:
        return path
    # A shell opened before `winget install` won't have ffmpeg on PATH yet; look where winget puts it.
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet"
    candidates = [winget / "Links" / f"{name}.exe", *winget.glob(f"Packages/Gyan.FFmpeg*/*/bin/{name}.exe")]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise RuntimeError(f"Can't find {name}. Install ffmpeg (Windows: winget install Gyan.FFmpeg).")


FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")


def run(cmd, cwd=None):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(map(str, cmd))}\n{result.stderr[-2000:]}")
    return result.stdout


def duration_of(path):
    out = run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


def fetch(source, workdir, cookies_browser=None):
    if Path(source).exists():
        return Path(source), Path(source).stem
    import yt_dlp

    opts = {
        "outtmpl": str(workdir / "source.%(ext)s"),
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "ffmpeg_location": str(Path(FFMPEG).parent),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    info = None
    for attempt in range(3):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(source, download=True)
            break
        except yt_dlp.utils.DownloadError as e:
            # Instagram intermittently sends empty responses to logged-out requests.
            if attempt == 2:
                raise ClipError(f"Couldn't download that link ({e}). If the site wants a login, "
                                f"use cookies from a logged-in browser.") from e
            time.sleep(3)
    files = list(workdir.glob("source.*"))
    if not files:
        raise ClipError("Download produced no file.")
    return files[0], info.get("title") or info.get("id") or "clip"


_models = {}


def transcribe(video, model_name, log=print):
    import numpy as np
    from faster_whisper import WhisperModel

    log(f"Transcribing (whisper '{model_name}')...")
    # Decode with our ffmpeg rather than faster-whisper's PyAV path (version-fragile).
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    audio = np.frombuffer(raw, dtype=np.float32)
    if model_name not in _models:  # loading takes seconds; the bot reuses it across requests
        _models[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = _models[model_name].transcribe(audio, word_timestamps=True, vad_filter=False)
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({"text": w.word.strip(), "start": w.start, "end": w.end})
    return words


def norm(text):
    return re.sub(r"[^a-z0-9' ]", "", text.lower()).strip()


def find_phrase(words, phrase):
    """Best fuzzy match of phrase against runs of transcribed words.

    Fuzzy because the joke spelling ("bweak") rarely matches what whisper hears ("break").
    """
    target = norm(phrase)
    n = len(target.split())
    best = (0.0, None)
    for size in range(max(1, n - 1), n + 2):
        for i in range(0, len(words) - size + 1):
            run_words = words[i:i + size]
            candidate = norm(" ".join(w["text"] for w in run_words))
            score = difflib.SequenceMatcher(None, target, candidate).ratio()
            if score > best[0]:
                best = (score, run_words)
    return best


def chunk_captions(words, start, end, max_words=4, max_gap=0.6):
    """Group the words inside [start, end] into short caption lines (times relative to start)."""
    inside = [w for w in words if w["end"] > start and w["start"] < end]
    cues, current = [], []
    for w in inside:
        if current and (len(current) >= max_words or w["start"] - current[-1]["end"] > max_gap):
            cues.append(current)
            current = []
        current.append(w)
    if current:
        cues.append(current)
    clip_len = end - start
    return [
        (max(0.0, c[0]["start"] - start), min(clip_len, c[-1]["end"] - start + 0.25),
         " ".join(w["text"] for w in c))
        for c in cues
    ]


def write_srt(cues, path):
    def ts(t):
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

    lines = []
    for i, (a, b, text) in enumerate(cues, 1):
        lines += [str(i), f"{ts(a)} --> {ts(b)}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def make_sound(video, start, end, out_path):
    """Returns a note if the sound had to be trimmed to fit the soundboard."""
    note = None
    length = end - start
    if length > SOUNDBOARD_MAX_SECONDS:
        note = f"{length:.2f}s is over Discord's {SOUNDBOARD_MAX_SECONDS}s soundboard limit; sound trimmed to fit."
        length = SOUNDBOARD_MAX_SECONDS
    fade = min(0.03, length / 4)
    afilter = (f"loudnorm=I=-14:TP=-1.5:LRA=11,"
               f"afade=t=in:d={fade},afade=t=out:st={length - fade:.3f}:d={fade}")
    for bitrate in ("128k", "96k", "64k"):
        run([FFMPEG, "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(video),
             "-vn", "-af", afilter, "-ar", "44100", "-ac", "2", "-b:a", bitrate, str(out_path)])
        if out_path.stat().st_size <= SOUNDBOARD_MAX_BYTES:
            break
    return note


def make_gif(video, start, end, srt_name, workdir, out_path, width, fps):
    filters = [f"fps={fps}", f"scale={width}:-2:flags=lanczos"]
    if srt_name:
        # Run from workdir and pass a bare filename: Windows drive colons break the subtitles filter.
        style = ("FontName=Arial,Bold=1,FontSize=22,PrimaryColour=&H00FFFFFF,"
                 "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,MarginV=18")
        filters.append(f"subtitles={srt_name}:force_style='{style}'")
    vf = ",".join(filters) + ",split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4"
    run([FFMPEG, "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(Path(video).resolve()),
         "-filter_complex", vf, "-loop", "0", str(out_path.resolve())], cwd=workdir)


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "clip"


def make_clip(source, out_dir, phrase=None, start=None, end=None, pad=0.25, caption=None, subs=True,
              name=None, width=480, fps=15, model="small", cookies_browser=None,
              max_gif_bytes=None, range_required_over=None, log=print):
    """Download/cut a clip into <name>.mp3 + <name>.gif in out_dir.

    range_required_over: if no phrase/start/end is given and the clip is longer than this,
    raise NeedsRange (with the transcript) instead of using the whole clip.
    max_gif_bytes: shrink the GIF (size, then frame rate) until it fits, e.g. an upload limit.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    notes = []
    heard = None

    with tempfile.TemporaryDirectory(prefix="clipkit_") as tmp:
        workdir = Path(tmp)
        log("Fetching clip...")
        video, title = fetch(source, workdir, cookies_browser)
        total = duration_of(video)

        no_range = phrase is None and start is None and end is None
        must_pick = range_required_over is not None and no_range and total > range_required_over
        need_words = phrase or must_pick or (subs and not caption)
        words = transcribe(video, model, log) if need_words else []

        if must_pick:
            raise NeedsRange(total, chunk_captions(words, 0.0, total, max_words=8, max_gap=0.8))

        clip_start, clip_end = 0.0, total
        if phrase:
            score, matched = find_phrase(words, phrase)
            if not matched or score < 0.5:
                said = " ".join(w["text"] for w in words) or "(no speech detected)"
                raise ClipError(f"Couldn't find \"{phrase}\" in the speech. Heard: {said}")
            heard = " ".join(w["text"] for w in matched)
            clip_start = max(0.0, matched[0]["start"] - pad)
            clip_end = min(total, matched[-1]["end"] + pad)
            log(f"Found '{heard}' at {matched[0]['start']:.2f}-{matched[-1]['end']:.2f}s (match {score:.0%})")
        if start is not None:
            clip_start = start
        if end is not None:
            clip_end = min(end, total)
        if clip_end <= clip_start:
            raise ClipError(f"Empty clip: start {clip_start:.2f}s is not before end {clip_end:.2f}s "
                            f"(the video is {total:.1f}s long).")

        srt_name = None
        if subs:
            if caption or phrase:
                # Your spelling wins over whisper's ("bweak", not "break").
                cues = [(0.0, clip_end - clip_start, caption or phrase)]
            else:
                cues = chunk_captions(words, clip_start, clip_end)
            if cues:
                srt_name = "subs.srt"
                write_srt(cues, workdir / srt_name)

        name = name or slugify(phrase or title)
        sound_path = out_dir / f"{name}.mp3"
        gif_path = out_dir / f"{name}.gif"

        log(f"Cutting {clip_start:.2f}-{clip_end:.2f}s ({clip_end - clip_start:.2f}s)")
        note = make_sound(video, clip_start, clip_end, sound_path)
        if note:
            notes.append(note)
        for scale, gif_fps in ((1.0, fps), (0.8, fps), (0.65, min(fps, 12)), (0.5, min(fps, 10))):
            gif_width = int(width * scale) // 2 * 2
            make_gif(video, clip_start, clip_end, srt_name, workdir, gif_path, gif_width, gif_fps)
            if not max_gif_bytes or gif_path.stat().st_size <= max_gif_bytes:
                break
        else:
            notes.append("GIF is still over the upload limit at its smallest size.")

    return {"sound": sound_path, "gif": gif_path, "start": clip_start, "end": clip_end,
            "heard": heard, "notes": notes}


def main():
    p = argparse.ArgumentParser(description="Make a Discord soundboard sound + captioned GIF from a clip.")
    p.add_argument("source", help="URL (Instagram, TikTok, YouTube, X, ...) or a local video file")
    p.add_argument("--phrase", help="cut around where this is said (fuzzy-matched against the speech)")
    p.add_argument("--start", type=float, help="clip start in seconds")
    p.add_argument("--end", type=float, help="clip end in seconds")
    p.add_argument("--pad", type=float, default=0.25, help="seconds of padding around --phrase (default 0.25)")
    p.add_argument("--caption", help="caption text to use instead of the transcript")
    p.add_argument("--no-subs", action="store_true", help="GIF without captions")
    p.add_argument("--name", help="output file name (default: from phrase/title)")
    p.add_argument("--out", default="out", help="output folder (default: ./out)")
    p.add_argument("--width", type=int, default=480, help="GIF width in px (default 480)")
    p.add_argument("--fps", type=int, default=15, help="GIF frame rate (default 15)")
    p.add_argument("--model", default="small", help="whisper model: tiny/base/small/medium (default small)")
    p.add_argument("--cookies-from-browser", metavar="BROWSER",
                   help="use your browser's login (chrome/firefox/edge) if the site needs it")
    args = p.parse_args()

    try:
        result = make_clip(args.source, args.out, phrase=args.phrase, start=args.start, end=args.end,
                           pad=args.pad, caption=args.caption, subs=not args.no_subs, name=args.name,
                           width=args.width, fps=args.fps, model=args.model,
                           cookies_browser=args.cookies_from_browser)
    except ClipError as e:
        sys.exit(str(e))

    for note in result["notes"]:
        print(f"  ! {note}")
    sound, gif = result["sound"], result["gif"]
    print(f"\nSound: {sound.resolve()}  ({sound.stat().st_size / 1024:.0f} KB, {duration_of(sound):.2f}s)")
    print(f"GIF:   {gif.resolve()}  ({gif.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
