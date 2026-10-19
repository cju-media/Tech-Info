"""Cut the sermon out of the livestream's audio and render it over a still image.

The Sunday livestream is downloaded audio-only, the sermon span is read from
the service's archived timings.txt (written by Youtube Processing/osc_server.js,
whose timing offset already lines the elapsed times up with the broadcast), and
ffmpeg turns that span plus the thumbnail into an mp4 for migrate_videos.py to
upload.
"""
import os
import re
import subprocess
import sys

# Seconds trimmed off / added to the span read from timings.txt. The timings
# are placed by hand as each section starts, so they sit a beat late; widen
# these if a sermon is clipped, tighten them if the neighbours leak in.
START_PAD_SECONDS = 0
END_PAD_SECONDS = 0

FADE_IN_SECONDS = 1
FADE_OUT_SECONDS = 2

# The next timing lands a little after the sermon actually ends, leaving dead
# air at the tail. Silence of at least SILENCE_MIN_SECONDS that runs to the end
# of the span is cut, keeping SILENCE_TAIL_PAD_SECONDS of it so the last word
# can ring out. SILENCE_NOISE_DB is the level counted as silent: raise it
# (e.g. -35) if room noise keeps the tail from being detected, lower it if
# quiet speech gets clipped.
SILENCE_NOISE_DB = -40
SILENCE_MIN_SECONDS = 10
SILENCE_TAIL_PAD_SECONDS = 1.5

# A span outside this range means the timings or the offset are off, and
# publishing the cut would put a wrong video on a public channel.
MIN_SERMON_SECONDS = 3 * 60
MAX_SERMON_SECONDS = 75 * 60

_TIMING_LINE = re.compile(r'^\s*((?:\d+:)?\d{1,2}:\d{2})\s+(.*\S)\s*$')
_SERMON_ITEM = re.compile(r'^Sermon\b', re.IGNORECASE)


def parse_timestamp(text):
    """'56:01' or '1:09:25' as seconds."""
    seconds = 0
    for part in text.split(':'):
        seconds = seconds * 60 + int(part)
    return seconds


def find_sermon_span(timings_text):
    """(start, end, item) in seconds for the Sermon line of a timings.txt.

    end is the next timing's start, or None when the sermon is the last line
    (the caller then runs to the end of the audio). Returns None if there is
    no Sermon line.
    """
    entries = []
    for line in timings_text.splitlines():
        match = _TIMING_LINE.match(line)
        if match:
            entries.append((parse_timestamp(match.group(1)), match.group(2)))

    for index, (start, item) in enumerate(entries):
        if _SERMON_ITEM.match(item):
            end = entries[index + 1][0] if index + 1 < len(entries) else None
            return start, end, item
    return None


def apply_pads(start, end):
    start = start + START_PAD_SECONDS
    return start, (end + END_PAD_SECONDS if end is not None else None)


def check_span(start, end):
    """Raise ValueError unless the span looks like a sermon."""
    if end is None:
        raise ValueError("Sermon span has no end")
    length = end - start
    if not (MIN_SERMON_SECONDS <= length <= MAX_SERMON_SECONDS):
        raise ValueError(
            f"Sermon span {start}s-{end}s is {length / 60:.1f} minutes, outside the "
            f"expected {MIN_SERMON_SECONDS // 60}-{MAX_SERMON_SECONDS // 60} minute range"
        )


def download_stream_audio(url, out_dir, cookies_path=None):
    """Download only the audio of a YouTube video. Returns the file path.

    YT_DLP names the yt-dlp executable (the workflow's standalone build); without
    it, yt-dlp runs as a module of the current interpreter. YouTube often
    bot-checks datacenter IPs such as GitHub's hosted runners; a cookies.txt
    export (cookies_path) gets past that.
    """
    template = os.path.join(out_dir, 'stream-audio.%(ext)s')
    ytdlp = [os.environ['YT_DLP']] if os.environ.get('YT_DLP') else [sys.executable, '-m', 'yt_dlp']
    cmd = ytdlp + ['-f', 'bestaudio', '--no-playlist', '--no-progress',
                   '-o', template, '--print', 'after_move:filepath']
    if cookies_path:
        cmd += ['--cookies', cookies_path]
    cmd.append(url)
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    path = result.stdout.strip().splitlines()[-1]
    if not os.path.exists(path):
        raise RuntimeError(f"yt-dlp reported {path!r} but it does not exist")
    return path


def probe_duration(path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=nokey=1:noprint_wrappers=1', path],
        check=True, capture_output=True, text=True)
    return float(result.stdout.strip())


def find_trailing_silence(audio_path, start, end):
    """Seconds into audio_path[start:end] where a silence that runs to the end begins.

    None if the span doesn't end in at least SILENCE_MIN_SECONDS of silence.
    """
    length = end - start
    result = subprocess.run(
        ['ffmpeg', '-hide_banner', '-nostats', '-ss', str(start), '-t', str(length),
         '-i', audio_path,
         '-af', f'silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_SECONDS}',
         '-f', 'null', '-'],
        check=True, capture_output=True, text=True)

    last_start = last_end = None
    for line in result.stderr.splitlines():
        found = re.search(r'silence_start: (-?[\d.]+)', line)
        if found:
            last_start, last_end = max(float(found.group(1)), 0.0), None
            continue
        found = re.search(r'silence_end: ([\d.]+)', line)
        if found:
            last_end = float(found.group(1))

    if last_start is None:
        return None
    # Older ffmpegs print no silence_end for a silence still going at EOF.
    if last_end is not None and last_end < length - 0.5:
        return None
    return last_start


def trim_trailing_silence(audio_path, start, end):
    """end moved back to just after the last speech, if the span ends in dead air."""
    silence_start = find_trailing_silence(audio_path, start, end)
    if silence_start is None:
        return end
    new_end = min(start + silence_start + SILENCE_TAIL_PAD_SECONDS, end)
    if new_end - start < MIN_SERMON_SECONDS:
        print(f"Ignoring trailing silence at {silence_start:.0f}s: trimming would leave "
              f"under {MIN_SERMON_SECONDS // 60} minutes.")
        return end
    print(f"Trimmed {end - new_end:.1f}s of trailing silence from the sermon.")
    return new_end


def render_sermon_video(audio_path, image_path, start, end, out_path):
    """Write an mp4 of audio_path[start:end] over image_path."""
    duration = end - start
    fade_out_start = max(duration - FADE_OUT_SECONDS, 0)
    audio_filter = (f"afade=t=in:d={FADE_IN_SECONDS},"
                    f"afade=t=out:st={fade_out_start}:d={FADE_OUT_SECONDS},"
                    "loudnorm=I=-16:TP=-1.5:LRA=11")
    # Fill the frame and crop the overflow (the podcast graphic is 1920x1082,
    # so padding would leave black slivers at the sides).
    video_filter = ("scale=1920:1080:force_original_aspect_ratio=increase,"
                    "crop=1920:1080,format=yuv420p")
    subprocess.run([
        'ffmpeg', '-y', '-loglevel', 'error',
        '-loop', '1', '-framerate', '5', '-i', image_path,
        '-ss', str(start), '-t', str(duration), '-i', audio_path,
        '-vf', video_filter, '-af', audio_filter,
        '-c:v', 'libx264', '-tune', 'stillimage',
        '-c:a', 'aac', '-b:a', '192k',
        # An explicit output -t, not -shortest: with a looped still, -shortest
        # lets the video run ~12s past the audio.
        '-t', str(duration), '-movflags', '+faststart', out_path,
    ], check=True)
    return out_path


def cut_sermon(timings_text, audio_path, image_path, out_path):
    """Find the sermon in timings_text, cut it from audio_path, render to out_path.

    Raises ValueError if there is no Sermon line or the span is implausible.
    """
    span = find_sermon_span(timings_text)
    if span is None:
        raise ValueError("No Sermon line in timings.txt")
    start, end, _item = span
    if end is None:
        end = int(probe_duration(audio_path))
    start, end = apply_pads(start, end)
    check_span(start, end)
    end = trim_trailing_silence(audio_path, start, end)
    return render_sermon_video(audio_path, image_path, start, end, out_path)
