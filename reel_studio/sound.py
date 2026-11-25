"""The page's own sound: recorded beside the screen, and cut into the film on
the beats a director chooses.

A film of a game was silent apart from its narrator. The julkalender's
Tomten speaks when a door opens (an mp3 per day), Ljudkvartetten reads its
words aloud, Lillstaden's oven pings: a director could let the page be heard
and comment on it, as a commentator does (Magnus, 2026-11-25).

The browser plays into a PulseAudio null sink of its own, and ffmpeg records
that sink's monitor while the screen is recorded. Without PulseAudio in the
image nothing changes: no sink, no recording, a silent page as before.

A beat's ``sound``:
- "under": the page is heard under the line, lower than the voice.
- "first": the page is heard first, as loud as the voice; the line comes
  after it has gone quiet, a comment on what was heard.
Either way the page goes down under each line and back up after it (duck).
"""
from __future__ import annotations

import array
import collections
import json
import math
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Sequence

SOUND_MODES = ("under", "first")
SOUND_FILE = "page-sound.json"
CAPTURE_FILE = "page-sound.mka"
# Where the browser's sound goes: one sink per session, so two browsers are
# never heard in each other's films.
SINK_PREFIX = "take_"
# How loud the page's sound is when no line is spoken: as loud as the
# narrator, measured as a listener hears it (EBU R128 loudness, LUFS), the
# band an interview clip sits in beside a presenter. Set by its peak, a
# short beep and a long greeting came out at different loudness, and the
# greeting played 4.5 LU over the voice with its peak at 0 dBFS (2026-11-25).
PAGE_BELOW_VOICE_LU = 0.0
# The narration's loudness when a film has none to measure (Edge's voices
# measure -20.3 LUFS).
VOICE_LUFS = -20.0
# No page sound peaks over this, however quiet it sounds.
PEAK_CEILING_DB = -1.5
# A clip too short or quiet to measure as loudness is set by its peak.
FULL_PEAK_DB = -3.0
# The film's sound is mono: a stereo page is averaged into one channel. The
# mixer's own downmix put it 3 dB louder than it was measured.
MONO = "pan=mono|c0=0.5*c0+0.5*c1"
# Under a line the page goes down and comes back up when the voice stops,
# as a TV presenter brings an interview clip up and under: DUCK_DB down
# over DUCK_LEAD, reached as the line starts; DUCK_AFTER past its end, back
# up over DUCK_RELEASE; and a pause shorter than DUCK_HOLD keeps it down so
# it does not pump between two lines. From Audacity's Auto Duck (-12 dB,
# 0.5 s fades, 1 s max pause), Avid's newsroom duck (12 dB), and a deeper
# dip for speech under speech (15-18 dB), as the page's sound often is: the
# research report "Ljud upp och under rösten" (2026-11-25).
DUCK_DB = 15.0
DUCK_LEAD = 0.3
DUCK_AFTER = 0.2
DUCK_RELEASE = 0.6
DUCK_HOLD = 2.0
# Quieter than this, a clip is silence and is left out.
SILENT_DB = -55.0
FADE_SECONDS = 0.04
# Where the page's sound ends with nothing after it, it is faded out, not cut.
TAIL_FADE = 0.5
# What is left between the screen recording's start and the sound's first
# sample as its clock tells it (CaptureClock), the browser's buffer counted
# in: measured with a beep and a white flash set off by one press, -0.59 to
# -0.53 s in three sessions, -0.87 to -0.66 s in four on a host with every
# core taken (2026-11-25). Off by 0.1 s or less on a host as it usually is.
SCREEN_START_LAG = -0.43
# The buffer the browser plays into the sink with, in ms (PULSE_LATENCY_MSEC).
# 30 ms cut a two-second tone in up to 36 places on a busy host; with 200 ms,
# and Web Audio asked for its playback buffer, it came through whole.
PULSE_LATENCY_MSEC = "200"
# A beat whose sound comes first waits at most this long for the page to go
# quiet, and counts this long in the plan's estimate.
QUIET_WAIT_MAX_MS = 20000
QUIET_SECONDS = 0.5
# The ear: the sink's level, read as it plays, so a line can come in where
# the page pauses, as a person waits for the other to finish a sentence
# before speaking (Magnus, 2026-11-25: "agenten behöver en form av gehör").
# 50 ms windows at 8 kHz mono are enough to hear speech start and stop.
EAR_RATE = 8000
EAR_WINDOW = 0.05
# A pause: PAUSE_SECONDS in a row PAUSE_BELOW_DB under what the page has
# been heard at (its loud windows), or silent. A breath between two
# sentences is 0.2 to 0.5 s; a word's own gaps are shorter.
PAUSE_SECONDS = 0.3
PAUSE_BELOW_DB = 18.0
EAR_SILENT_DB = -55.0
# With listen, how long past it the ear waits for a pause before the line
# comes in all the same.
PAUSE_SEARCH_MS = 6000
# Without listen, a turn ends where a sound the page played ends and the
# ear hears a pause: Tomten's greeting is one mp3 and the day's voice the
# next, so the line comes in between them, as a person answers the one
# who spoke and not what is said after. A sound shorter than this (a
# press's click) is not a turn.
TURN_MIN_SECONDS = 1.0
FIRST_ESTIMATE_SECONDS = 3.0

# What the page plays, kept in the page itself: media elements, Web Audio
# sources and speech. A beat waits on it ("first"), and probe and a rehearsal
# report it, so a director knows what there is to comment on.
SOUND_JS = r"""(() => {
  if (window.__takeSound) return;
  const heard = [], live = new Set(), t0 = performance.now();
  const at = () => Math.round(performance.now() - t0) / 1000;
  const begin = (entry) => { entry.at = at(); heard.push(entry); if (heard.length > 60) heard.shift(); live.add(entry); return entry; };
  const end = (entry) => { if (live.delete(entry)) entry.seconds = Math.round((at() - entry.at) * 10) / 10; };
  const name = (src) => { try { const u = new URL(src, location.href); return u.pathname.split("/").pop() || u.host; } catch (e) { return String(src).slice(0, 80); } };
  const tracked = new WeakMap();
  const media = (element) => {
    const known = tracked.get(element);
    if (known && live.has(known)) return known;
    const entry = begin({kind: element.tagName === "VIDEO" ? "video" : "audio", src: name(element.currentSrc || element.src)});
    tracked.set(element, entry);
    return entry;
  };
  // A media element in the page that plays by itself (autoplay).
  document.addEventListener("play", (event) => {
    const element = event.target;
    if (!(element instanceof HTMLMediaElement) || (tracked.get(element) && live.has(tracked.get(element)))) return;
    const entry = media(element);
    const done = () => end(entry);
    element.addEventListener("ended", done, {once: true});
    element.addEventListener("pause", done, {once: true});
  }, true);
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () {
    const entry = media(this);
    const done = () => end(entry);
    this.addEventListener("ended", done, {once: true});
    this.addEventListener("pause", done, {once: true});
    this.addEventListener("error", done, {once: true});
    const result = play.apply(this, arguments);
    if (result && result.catch) result.catch((e) => { entry.blocked = String(e && e.name || e); end(entry); });
    return result;
  };
  // A film is not played live: the largest buffer the page can have, so a
  // busy host does not cut its sound to pieces (2026-11-25).
  for (const name of ["AudioContext", "webkitAudioContext"]) {
    const Native = window[name];
    if (!Native || Native.__take) continue;
    const Wrapped = function (options) { return new Native(Object.assign({}, options, {latencyHint: "playback"})); };
    Wrapped.prototype = Native.prototype;
    Wrapped.__take = true;
    window[name] = Wrapped;
  }
  // Chrome gives AudioBufferSourceNode a start of its own: each kind of
  // source that has one is followed.
  for (const kind of ["AudioScheduledSourceNode", "AudioBufferSourceNode", "OscillatorNode", "ConstantSourceNode"]) {
    const proto = window[kind] && window[kind].prototype;
    if (!proto || !Object.prototype.hasOwnProperty.call(proto, "start")) continue;
    const start = proto.start;
    proto.start = function () {
      if (this.__takeHeard) return start.apply(this, arguments);
      const buffer = this.buffer;
      const entry = this.__takeHeard = begin({kind: "webaudio", src: buffer ? `${Math.round(buffer.duration * 10) / 10} s buffer` : this.constructor.name});
      this.addEventListener("ended", () => end(entry), {once: true});
      return start.apply(this, arguments);
    };
  }
  if (window.speechSynthesis) {
    const speak = speechSynthesis.speak.bind(speechSynthesis);
    speechSynthesis.speak = (utterance) => {
      const entry = begin({kind: "speech", text: String(utterance.text || "").slice(0, 160), voices: speechSynthesis.getVoices().length});
      utterance.addEventListener("end", () => end(entry));
      utterance.addEventListener("error", () => end(entry));
      // No voice at all: nothing is said, and no end may ever come.
      if (!entry.voices) setTimeout(() => end(entry), 200);
      return speak(utterance);
    };
  }
  window.__takeSound = {
    playing: () => { for (const entry of live) if (entry.kind === "speech" ? !speechSynthesis.speaking : false) end(entry); return live.size; },
    heard: (since) => heard.filter((entry) => entry.at >= (since || 0)),
    now: at,
  };
})();"""

READ_SOUNDS_JS = "(since) => window.__takeSound ? window.__takeSound.heard(since) : []"
PLAYING_JS = "() => window.__takeSound ? window.__takeSound.playing() : 0"
NOW_JS = "() => window.__takeSound ? window.__takeSound.now() : 0"


def available() -> bool:
    return bool(shutil.which("pactl") and shutil.which("pulseaudio"))


def _pactl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=10)


def ensure_server() -> bool:
    """A PulseAudio server to play into: running, or started now."""
    if not available():
        return False
    try:
        if _pactl("info").returncode == 0:
            return True
        subprocess.run(["pulseaudio", "--daemonize=yes", "--exit-idle-time=-1", "--disallow-exit=yes"],
                       capture_output=True, timeout=15)
        for _ in range(30):
            if _pactl("info").returncode == 0:
                # An idle sink is suspended and its monitor goes quiet:
                # time would go missing from the recording.
                _pactl("unload-module", "module-suspend-on-idle")
                return True
            time.sleep(0.1)
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def open_sink(session_id: str) -> tuple[str, str] | None:
    """(sink name, module id) of a sink of this session's own, or None."""
    if not ensure_server():
        return None
    sink = SINK_PREFIX + re.sub(r"[^0-9a-zA-Z]", "", session_id)[:24]
    try:
        loaded = _pactl("load-module", "module-null-sink", f"sink_name={sink}",
                        f"sink_properties=device.description={sink}")
    except (OSError, subprocess.SubprocessError):
        return None
    if loaded.returncode != 0 or not loaded.stdout.strip().isdigit():
        return None
    return sink, loaded.stdout.strip()


def close_sink(module: str | None) -> None:
    if not module:
        return
    try:
        _pactl("unload-module", module)
    except (OSError, subprocess.SubprocessError):
        pass


# How long the capture's clock is read: its first seconds are enough.
CLOCK_SECONDS = 15.0


class CaptureClock:
    """When a recording's first sample was heard, on time.monotonic().

    ffmpeg takes 0.5 to 2 s to open the sink on a busy host, never the same
    twice, so the moment it was started says little. Its progress says how
    much it has recorded: now less that is the start, late by however long
    the report took to arrive and never early, so the least of them is it."""

    def __init__(self, stream: Any) -> None:
        self.started: float | None = None
        self._opened = time.monotonic()
        threading.Thread(target=self._read, args=(stream,), daemon=True).start()

    def _read(self, stream: Any) -> None:
        try:
            for line in iter(stream.readline, b""):
                now = time.monotonic()
                if not line.startswith(b"out_time_us=") or now - self._opened > CLOCK_SECONDS:
                    continue
                try:
                    recorded = int(line.split(b"=", 1)[1]) / 1e6
                except ValueError:
                    continue
                if recorded > 0:
                    start = now - recorded
                    self.started = start if self.started is None else min(self.started, start)
        except (OSError, ValueError):
            pass


def start_capture(sink: str, output: Path) -> subprocess.Popen[bytes] | None:
    """Record the sink: Matroska with FLAC, readable even if cut short. The
    process has a ``clock`` (CaptureClock) that says when it started."""
    try:
        process = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-nostats", "-y", "-f", "pulse",
             # Small fragments: what is still in the server when the
             # recording stops is lost, so the end is on time.
             "-fragment_size", "1920",
             # Every sample as the sink gave it. Stamped by the clock and
             # resampled to it, the recording was cut to pieces: Tomten's
             # greeting had 58 gaps of silence and stuttered (2026-11-25).
             # Where it starts is read from its progress (CaptureClock).
             "-i", f"{sink}.monitor", "-progress", "pipe:1", "-stats_period", "0.05",
             "-ac", "2", "-ar", "48000", "-c:a", "flac", str(output)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    process.clock = CaptureClock(process.stdout)  # type: ignore[attr-defined]
    return process


def recorded_seconds(path: Path) -> float | None:
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                                capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def anchored_start(path: Path, stopped_at: float) -> float | None:
    """When the recording's first sample was heard, from where it was
    stopped: its samples run without a gap. Rougher than its clock (a few
    tenths of a second), for a recording whose clock read nothing."""
    seconds = recorded_seconds(path)
    return stopped_at - seconds if seconds else None


def record_span(directory: Path, file: str, video_start: float, shift: float) -> None:
    """Where a sound recording sits on the session's clock. A pickup's comes
    after the session's, at the place its footage was joined in."""
    path = directory / SOUND_FILE
    spans = load_spans(directory)
    spans = [span for span in spans if span["file"] != file]
    spans.append({"file": file, "video_start": round(video_start, 3), "shift": round(shift, 3)})
    path.write_text(json.dumps(sorted(spans, key=lambda span: span["video_start"])))


def load_spans(directory: Path) -> list[dict]:
    path = directory / SOUND_FILE
    try:
        spans = json.loads(path.read_text()) if path.is_file() else []
    except (OSError, ValueError):
        return []
    return [span for span in spans if isinstance(span, dict) and (directory / str(span.get("file", ""))).is_file()]


def locate(spans: Sequence[dict], directory: Path, offset: float) -> tuple[Path, float] | None:
    """(the recording, the time in it) of the session clock's offset."""
    chosen = None
    for span in spans:
        if float(span["video_start"]) <= offset + 1e-6:
            chosen = span
    if chosen is None:
        return None
    return directory / chosen["file"], max(0.0, offset - float(chosen["video_start"]) + float(chosen["shift"]))


def peak_db(path: Path, start: float = 0.0, seconds: float | None = None) -> float:
    command = ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}"]
    if seconds is not None:
        command += ["-t", f"{seconds:.3f}"]
    command += ["-i", str(path), "-af", "volumedetect", "-f", "null", "-"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return -120.0
    found = re.search(r"max_volume: (-?[0-9.]+) dB", result.stderr)
    return float(found.group(1)) if found else -120.0


def loudness(path: Path, start: float = 0.0, seconds: float | None = None, mono: bool = False) -> float | None:
    """Integrated loudness (EBU R128, gated, so the silence around a sound
    does not count), in LUFS; None when there is too little to measure."""
    command = ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}"]
    if seconds is not None:
        command += ["-t", f"{seconds:.3f}"]
    chain = f"{MONO}," if mono else ""
    command += ["-i", str(path), "-af", f"{chain}ebur128", "-f", "null", "-"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.findall(r"I:\s+(-?[0-9.]+) LUFS", result.stderr)
    value = float(found[-1]) if found else None
    return value if value is not None and value > -69.0 else None


def voice_loudness(clips: Sequence[Path]) -> float:
    """The narration's loudness, the level the page's sound is set to: the
    middle of its lines'."""
    found = sorted(value for value in (loudness(clip) for clip in clips) if value is not None)
    return found[len(found) // 2] if found else VOICE_LUFS


def clip_gain(source: Path, start: float, seconds: float, voice: float) -> float | None:
    """The gain, in dB, that sets the page's sound for a segment as loud as
    the voice (less PAGE_BELOW_VOICE_LU), its peak kept under
    PEAK_CEILING_DB. None when the page was silent."""
    peak = peak_db(source, start, seconds)
    if peak <= SILENT_DB:
        return None
    # Averaged into mono, as the film plays it; a sound in both channels
    # peaks where it did.
    heard = loudness(source, start, seconds, mono=True)
    if heard is None:
        return FULL_PEAK_DB - peak
    return min(voice - PAGE_BELOW_VOICE_LU - heard, PEAK_CEILING_DB - peak)


def cut_clip(source: Path, start: float, seconds: float, output: Path, voice: float = VOICE_LUFS) -> Path | None:
    """The page's sound for one segment, mono, as loud as the voice (``voice``,
    in LUFS), with short fades so a cut does not click; duck() takes it down
    under the lines. None when the page was silent."""
    if seconds <= 0.05:
        return None
    gain = clip_gain(source, start, seconds, voice)
    if gain is None:
        return None
    fade_out = max(0.0, seconds - FADE_SECONDS)
    try:
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}",
             "-i", str(source),
             "-af", f"{MONO},volume={gain:.2f}dB,afade=t=in:d={FADE_SECONDS},afade=t=out:st={fade_out:.3f}:d={FADE_SECONDS}",
             "-ar", "48000", "-c:a", "pcm_s16le", str(output)],
            capture_output=True, timeout=120, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return output if output.is_file() and output.stat().st_size > 100 else None


def spoken(lines: Sequence[tuple[float, float]], hold: float = DUCK_HOLD) -> list[tuple[float, float]]:
    """The spans the page is kept down for: each line from start to end, and
    two lines closer than ``hold`` as one."""
    merged: list[list[float]] = []
    for start, end in sorted((float(a), float(b)) for a, b in lines if b > a):
        if merged and start - merged[-1][1] < hold:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def duck(lines: Sequence[tuple[float, float]]) -> str:
    """An ffmpeg volume filter that takes the page's sound DUCK_DB down under
    the lines (seconds on the film's clock): linear in dB, down over
    DUCK_LEAD before each line, up over DUCK_RELEASE from DUCK_AFTER past
    it. A flat sum of trapezoids: the merged spans are at least DUCK_HOLD
    apart, more than the three together, so no two overlap, and the
    expression nests no deeper with more lines. "" when nothing is said."""
    spans = spoken(lines, max(DUCK_HOLD, DUCK_LEAD + DUCK_AFTER + DUCK_RELEASE))
    if not spans:
        return ""
    terms = "+".join(
        f"clip((t-{start - DUCK_LEAD:.3f})/{DUCK_LEAD},0,1)*clip(({end + DUCK_AFTER + DUCK_RELEASE:.3f}-t)/{DUCK_RELEASE},0,1)"
        for start, end in spans
    )
    # Small frames: volume sets its gain once a frame, at the frame's start,
    # so a ramp over 1024-sample frames would step every 21 ms.
    return f"asetnsamples=n=256,volume='pow(10,-{DUCK_DB / 20:.4f}*({terms}))':eval=frame"


def window_db(samples: Sequence[int]) -> float:
    """A window's level, RMS in dBFS of 16-bit samples."""
    if not samples:
        return -120.0
    power = sum(value * value for value in samples) / len(samples)
    return 10 * math.log10(power / (32768.0 ** 2)) if power > 0 else -120.0


class Ear:
    """The sink's level as it plays: (time.monotonic(), dBFS) every
    EAR_WINDOW, from a parec of its own on the sink's monitor, so the
    recording is never held up by it."""

    def __init__(self, sink: str) -> None:
        self.levels: collections.deque[tuple[float, float]] = collections.deque(maxlen=int(600 / EAR_WINDOW))
        self.process = subprocess.Popen(
            ["parec", f"--device={sink}.monitor", "--raw", "--format=s16le", "--channels=1",
             f"--rate={EAR_RATE}", "--latency-msec=20"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        size = int(EAR_RATE * EAR_WINDOW) * 2
        stream = self.process.stdout
        try:
            while stream is not None:
                chunk = stream.read(size)
                if not chunk:
                    break
                samples = array.array("h", chunk[: len(chunk) // 2 * 2])
                self.levels.append((time.monotonic(), window_db(samples)))
        except (OSError, ValueError):
            pass

    def since(self, started: float) -> list[tuple[float, float]]:
        return [entry for entry in list(self.levels) if entry[0] >= started]

    def close(self) -> None:
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            try:
                self.process.kill()
            except OSError:
                pass


def open_ear(sink: str) -> Ear | None:
    if not shutil.which("parec"):
        return None
    try:
        return Ear(sink)
    except OSError:
        return None


def loud_db(levels: Sequence[float]) -> float | None:
    """What the page has been heard at: the level of its loud windows (the
    90th percentile of those not silent). None when nothing was heard."""
    heard = sorted(level for level in levels if level > EAR_SILENT_DB)
    return heard[min(len(heard) - 1, int(len(heard) * 0.9))] if heard else None


def paused(levels: Sequence[tuple[float, float]], now: float) -> bool:
    """Whether the page is in a pause: heard before, and the last
    PAUSE_SECONDS all PAUSE_BELOW_DB under that, or silent."""
    loud = loud_db([level for _, level in levels])
    if loud is None:
        return False
    floor = max(EAR_SILENT_DB, loud - PAUSE_BELOW_DB)
    recent = [level for at, level in levels if at > now - PAUSE_SECONDS]
    return len(recent) >= round(PAUSE_SECONDS / EAR_WINDOW) - 1 and all(level < floor for level in recent)


def turn_ended(sounds: Sequence[dict], since: float) -> dict | None:
    """The first sound, on the page's clock, that played TURN_MIN_SECONDS or
    more and ended after ``since``: the end of a turn. None if none has."""
    ended = [entry for entry in sounds
             if entry.get("seconds") is not None and float(entry["seconds"]) >= TURN_MIN_SECONDS
             and float(entry.get("at") or 0) + float(entry["seconds"]) >= since]
    return min(ended, key=lambda entry: float(entry["at"]) + float(entry["seconds"])) if ended else None


async def wait_heard(page: Any, ear: Ear | None, listen_ms: int = 0) -> dict:
    """Wait while the page is heard, before a line comments on it, and come
    in as a person would:
    - with ``listen_ms``, at the first pause the ear hears once that long has
      gone (at most PAUSE_SEARCH_MS more), the end of a sentence;
    - without it, where a sound the page played ends and the ear hears a
      pause (turn_ended): one speaker's turn, not what follows it;
    - and in any case once the page has gone quiet.
    Without an ear, listen_ms is where the line comes in, and without it the
    page is heard until quiet. Returns the seconds waited, what ended it,
    and the sound whose end it was."""
    started = time.monotonic()
    # The sounds the presses just set off count: a second back on the page's clock.
    page_since = max(0.0, await page_now(page) - 1.0) if ear is not None and not listen_ms else None
    earliest = listen_ms / 1000 if listen_ms else None
    limit = QUIET_WAIT_MAX_MS / 1000
    if earliest is not None:
        limit = min(limit, earliest + (PAUSE_SEARCH_MS / 1000 if ear is not None else 0.0))
    quiet_since: float | None = None
    ended = "limit"
    while True:
        now = time.monotonic()
        if now - started >= limit:
            break
        try:
            playing = int(await page.evaluate(PLAYING_JS))
        except Exception:
            ended = "page"
            break
        now = time.monotonic()
        if playing:
            quiet_since = None
        elif quiet_since is None:
            quiet_since = now
        elif now - quiet_since >= QUIET_SECONDS:
            ended = "quiet"
            break
        if ear is not None and earliest is not None and now - started >= earliest and paused(ear.since(started), now):
            ended = "pause"
            break
        if page_since is not None and paused(ear.since(started), now):  # type: ignore[union-attr]
            turn = turn_ended(await heard(page, page_since), page_since)
            if turn is not None:
                return {"seconds": round(time.monotonic() - started, 2), "ended": "sound_end",
                        "sound": turn.get("src") or turn.get("kind")}
        await page.wait_for_timeout(int(EAR_WINDOW * 1000))
    return {"seconds": round(time.monotonic() - started, 2), "ended": ended}


def file_levels(path: Path, start: float, seconds: float) -> list[float]:
    """A recording's level, EAR_WINDOW at a time, as the ear hears it."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{seconds:.3f}", "-i", str(path),
             "-af", MONO, "-ar", str(EAR_RATE), "-f", "s16le", "-"],
            capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    samples = array.array("h", result.stdout[: len(result.stdout) // 2 * 2])
    size = int(EAR_RATE * EAR_WINDOW)
    return [window_db(samples[index:index + size]) for index in range(0, len(samples) - size + 1, size)]


# A line that starts while the page is still this close to its loud level
# is spoken over it. Where it starts: its first TALK_OVER_SECONDS. A line in
# the pause after Tomten's greeting, with the day's voice starting 0.2 s
# into it, is the line coming in where a person would, the page going on
# under it: measured over 0.3 s, it was reported (2026-11-25).
TALK_OVER_DB = 12.0
TALK_OVER_SECONDS = 0.1


def talk_over(directory: Path, heard_offset: float, line_offset: float) -> dict | None:
    """Whether a line at ``line_offset`` (the session's clock) starts while
    the page it comments on, heard from ``heard_offset``, still speaks: its
    level where the line starts against its loud level while heard.
    None when there is no recording to tell."""
    spans = load_spans(directory)
    found = locate(spans, directory, line_offset) if spans else None
    before = locate(spans, directory, heard_offset) if spans else None
    if found is None or before is None or found[0] != before[0]:
        return None
    source, at = found
    loud = loud_db(file_levels(source, before[1], max(0.5, at - before[1])))
    start = file_levels(source, at, TALK_OVER_SECONDS)
    if loud is None or not start:
        return None
    level = max(start)
    return {"page_db": round(level, 1), "loud_db": round(loud, 1), "over": level > loud - TALK_OVER_DB}


async def heard(page: Any, since: float = 0.0) -> list[dict]:
    """What the page played since ``since`` (its own clock), for a report."""
    try:
        return await page.evaluate(READ_SOUNDS_JS, since)
    except Exception:
        return []


async def page_now(page: Any) -> float:
    try:
        return float(await page.evaluate(NOW_JS))
    except Exception:
        return 0.0


async def wait_quiet(page: Any, limit_ms: int = QUIET_WAIT_MAX_MS) -> float:
    """Wait until nothing the page plays is still playing, for QUIET_SECONDS
    in a row, or limit_ms. Returns the seconds waited."""
    started = time.monotonic()
    quiet_since: float | None = None
    while (time.monotonic() - started) * 1000 < limit_ms:
        try:
            playing = int(await page.evaluate(PLAYING_JS))
        except Exception:
            break
        now = time.monotonic()
        if playing:
            quiet_since = None
        elif quiet_since is None:
            quiet_since = now
        elif now - quiet_since >= QUIET_SECONDS:
            break
        await page.wait_for_timeout(100)
    return round(time.monotonic() - started, 2)


def sound_notes(sounds: Sequence[dict]) -> list[str]:
    """What a director should know about what was heard."""
    notes = []
    if any(entry.get("kind") == "speech" and not entry.get("voices") for entry in sounds):
        notes.append("the page speaks with the browser's speech synthesis, which has no voice here: "
                      "it will be silent in the film")
    if any(entry.get("blocked") for entry in sounds):
        notes.append("the browser blocked a sound until the page is pressed: play it with a tap on camera")
    return notes
