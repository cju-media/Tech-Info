"""Cut the sermon out of the livestream's audio and render it over a still image.

The Sunday livestream is downloaded audio-only, the sermon span is read from
the service's archived timings.txt (written by Youtube Processing/osc_server.js,
whose timing offset already lines the elapsed times up with the broadcast), and
ffmpeg turns that span plus the thumbnail into an mp4 for migrate_videos.py to
upload.
"""
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time

import requests

# Seconds trimmed off / added to the span read from timings.txt. The timings
# are placed by hand as each section starts, so they sit a beat late; widen
# these if a sermon is clipped, tighten them if the neighbours leak in.
START_PAD_SECONDS = 0
END_PAD_SECONDS = 0

# Degraded-but-published conditions from the latest cut_sermon call, worded to
# follow "the video uploaded, but ..." in the text migrate_videos.py sends.
NOTES = []

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

# The next timing is placed when the next item starts, after a transition, so
# the span ends in a minute or so of organ music. Gemini lists where human
# speech occurs in the last SPEECH_WINDOW_SECONDS of the span; if the final
# NO_SPEECH_SECONDS hold none, the sermon is cut SPEECH_TAIL_PAD_SECONDS after
# the last word. Speech after that gap (up to MAX_SPEECH_AFTER_GAP_SECONDS in all)
# is the next item starting right at the timing, e.g. the prayer leader's first
# words, not more sermon. Without GEMINI_API_KEY (or if the call fails) the span is left
# as the timings give it.
GEMINI_MODEL = 'gemini-3.5-flash'
SPEECH_WINDOW_SECONDS = 5 * 60
NO_SPEECH_SECONDS = 10
SPEECH_TAIL_PAD_SECONDS = 2
MAX_SPEECH_AFTER_GAP_SECONDS = 30

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


DOWNLOAD_TIMEOUT_SECONDS = 30 * 60


def download_stream_audio(url, out_dir, cookies_path=None):
    """Download only the audio of a YouTube video. Returns the file path.

    YT_DLP names the yt-dlp executable (the workflow's standalone build); without
    it, yt-dlp runs as a module of the current interpreter. YouTube often
    bot-checks datacenter IPs such as GitHub's hosted runners; a cookies.txt
    export (cookies_path) gets past that. Progress goes straight to the log
    (stderr is captured so a failure can be reported), and the download is
    abandoned after DOWNLOAD_TIMEOUT_SECONDS rather than hanging the job.
    """
    template = os.path.join(out_dir, 'stream-audio.%(ext)s')
    ytdlp = [os.environ['YT_DLP']] if os.environ.get('YT_DLP') else [sys.executable, '-m', 'yt_dlp']
    cmd = ytdlp + ['-f', 'bestaudio', '--no-playlist', '--newline',
                   '--concurrent-fragments', '8', '--socket-timeout', '30', '--retries', '10',
                   '-o', template]
    if cookies_path:
        cmd += ['--cookies', cookies_path]
    cmd.append(url)
    try:
        subprocess.run(cmd, check=True, stderr=subprocess.PIPE, text=True,
                       timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as e:
        tail = (e.stderr or '').strip().splitlines()[-1:] if isinstance(e.stderr, str) else []
        raise RuntimeError(f"yt-dlp took over {DOWNLOAD_TIMEOUT_SECONDS // 60} minutes"
                           + (f" ({tail[0]})" if tail else "")) from e

    downloaded = sorted(f for f in os.listdir(out_dir) if f.startswith('stream-audio.'))
    if not downloaded:
        raise RuntimeError("yt-dlp finished but wrote no audio file")
    return os.path.join(out_dir, downloaded[0])


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


SPEECH_PROMPT = """This audio is the final minutes of a church sermon. The speaker talks, \
then there is a transition and organ or choir music.

List every stretch of human speech (a person talking, including "Amen" or a closing \
prayer) with start and end times measured from the beginning of this clip, written as \
"MM:SS" strings (for example "04:10" is four minutes ten seconds in). Singing, \
humming, choir and instruments are NOT speech. Merge pauses between sentences shorter \
than 3 seconds into one stretch. If there is no speech at all, return an empty list.

Respond with ONLY compact JSON, no markdown fences:
{"speech": [{"start": "00:00", "end": "02:03"}]}"""


def _seconds(value):
    """Gemini's 12.5, "12.5" or "1:02.5" as seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    seconds = 0.0
    for part in str(value).strip().split(':'):
        seconds = seconds * 60 + float(part)
    return seconds


def parse_speech_segments(text):
    """[(start, end), ...] from Gemini's reply; raises ValueError if unusable."""
    cleaned = re.sub(r'^```(?:json)?|```$', '', text.strip(), flags=re.M).strip()
    try:
        rows = json.loads(cleaned)['speech']
        segments = [(_seconds(row['start']), _seconds(row['end'])) for row in rows]
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"Could not read speech segments from {text[:200]!r}") from e
    return sorted(seg for seg in segments if seg[1] > seg[0])


def ask_gemini_for_speech(clip_path, api_key, retries=3):
    """Speech segments (seconds into the clip) as Gemini hears them."""
    with open(clip_path, 'rb') as f:
        audio = base64.b64encode(f.read()).decode()
    body = {
        'contents': [{'parts': [
            {'text': SPEECH_PROMPT},
            {'inline_data': {'mime_type': 'audio/mp3', 'data': audio}},
        ]}],
        'generationConfig': {'temperature': 0},
    }
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent'
    for attempt in range(1, retries + 1):
        response = requests.post(url, headers={'x-goog-api-key': api_key}, json=body, timeout=300)
        if response.status_code == 429 or response.status_code >= 500:
            if attempt < retries:
                time.sleep(5 * attempt)
                continue
        response.raise_for_status()
        break
    parts = response.json()['candidates'][0]['content']['parts']
    return parse_speech_segments(''.join(part.get('text', '') for part in parts))


def speech_end_before_music(segments, clip_length):
    """When the sermon's speech ends: the start of the last gap of at least
    NO_SPEECH_SECONDS with no speech, provided no more than
    MAX_SPEECH_AFTER_GAP_SECONDS of speech follows it. None if there is no such gap."""
    segments = [(a, min(b, clip_length)) for a, b in segments if a < clip_length]
    found = None
    for i, (_, end) in enumerate(segments):
        next_start = segments[i + 1][0] if i + 1 < len(segments) else clip_length
        speech_after = sum(b - a for a, b in segments[i + 1:])
        if next_start - end >= NO_SPEECH_SECONDS and speech_after <= MAX_SPEECH_AFTER_GAP_SECONDS:
            found = end
    return found


def trim_after_speech(audio_path, start, end):
    """end moved back to just after the last spoken word, if music runs on after it."""
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("GEMINI_API_KEY not set; not looking for where the speech ends.")
        NOTES.append("GEMINI_API_KEY is not set, so the music after the sermon was not trimmed.")
        return end

    window_start = max(start, end - SPEECH_WINDOW_SECONDS)
    clip_length = end - window_start
    with tempfile.TemporaryDirectory() as work_dir:
        clip_path = os.path.join(work_dir, 'tail.mp3')
        try:
            subprocess.run(
                ['ffmpeg', '-y', '-loglevel', 'error', '-ss', str(window_start), '-t', str(clip_length),
                 '-i', audio_path, '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame', '-b:a', '48k', clip_path],
                check=True)
            segments = ask_gemini_for_speech(clip_path, api_key)
        except (subprocess.CalledProcessError, requests.RequestException, ValueError, KeyError, IndexError) as e:
            print(f"Could not find where the speech ends ({e}); keeping the timed end.")
            NOTES.append("the speech-end check failed, so it may end with about a minute of organ music.")
            return end

    print("Speech in the last {:.0f}s of the span (seconds into that window): {}".format(
        clip_length, ", ".join(f"{a:.0f}-{b:.0f}" for a, b in segments) or "none"))
    # Times past the end of the clip mean the model misread the timeline (it once
    # wrote 4:10 as 410); don't guess at them.
    if any(end > clip_length + 5 for _, end in segments):
        print("Speech times run past the end of the clip; keeping the timed end.")
        NOTES.append("the speech-end check gave unusable times, so it may end with about a minute of organ music.")
        return end
    speech_end = speech_end_before_music(segments, clip_length)
    if speech_end is None:
        print("No 10s+ gap in the speech near the end of the span; keeping the timed end.")
        return end

    new_end = min(window_start + speech_end + SPEECH_TAIL_PAD_SECONDS, end)
    if new_end - start < MIN_SERMON_SECONDS:
        print(f"Ignoring speech end at {speech_end:.0f}s: trimming would leave "
              f"under {MIN_SERMON_SECONDS // 60} minutes.")
        return end
    print(f"Trimmed {end - new_end:.1f}s of music after the last spoken word.")
    return new_end


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
    NOTES.clear()
    span = find_sermon_span(timings_text)
    if span is None:
        raise ValueError("No Sermon line in timings.txt")
    start, end, _item = span
    if end is None:
        end = int(probe_duration(audio_path))
    start, end = apply_pads(start, end)
    check_span(start, end)
    end = trim_after_speech(audio_path, start, end)
    end = trim_trailing_silence(audio_path, start, end)
    return render_sermon_video(audio_path, image_path, start, end, out_path)
