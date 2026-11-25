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
- "first": the page is heard first, at full level; the line comes after it
  has gone quiet, a comment on what was heard.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

SOUND_MODES = ("under", "first")
SOUND_FILE = "page-sound.json"
CAPTURE_FILE = "page-sound.mka"
# Where the browser's sound goes: one sink per session, so two browsers are
# never heard in each other's films.
SINK_PREFIX = "take_"
# How a page's sound sits in the film: its peak, in dB. The narration peaks
# near -1 dB; under a line the page is well below it.
FULL_PEAK_DB = -3.0
UNDER_PEAK_DB = -16.0
# Quieter than this, a clip is silence and is left out.
SILENT_DB = -55.0
FADE_SECONDS = 0.04
# The screen recording's first frame comes this much later than the sound
# recording's first sample, for recorders started together: measured with a
# beep and a white flash set off by one press, 24 presses in four sessions,
# 0.25-0.42 s, within ±0.05 s in one session (2026-11-25).
SCREEN_START_LAG = 0.34
# The buffer the browser plays into the sink with, in ms (PULSE_LATENCY_MSEC).
PULSE_LATENCY_MSEC = "30"
# A beat whose sound comes first waits at most this long for the page to go
# quiet, and counts this long in the plan's estimate.
QUIET_WAIT_MAX_MS = 20000
QUIET_SECONDS = 0.5
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


def start_capture(sink: str, output: Path) -> subprocess.Popen[bytes] | None:
    """Record the sink: Matroska with FLAC, readable even if cut short."""
    try:
        return subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-nostats", "-y", "-f", "pulse",
             "-fragment_size", "1920",
             # Stamped by the clock, and gaps filled with silence: a sink
             # that went idle delivered nothing for a while, and the file
             # came out 1.3 s short of the screen's, every sound after it
             # early by that much (2026-11-25).
             "-use_wallclock_as_timestamps", "1", "-i", f"{sink}.monitor",
             "-af", "aresample=async=1000:first_pts=0",
             "-ac", "2", "-ar", "48000", "-c:a", "flac", str(output)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None


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


def cut_clip(source: Path, start: float, seconds: float, mode: str, output: Path) -> Path | None:
    """The page's sound for one segment, at the level its mode asks for, with
    short fades so a cut does not click. None when the page was silent."""
    if seconds <= 0.05:
        return None
    peak = peak_db(source, start, seconds)
    if peak <= SILENT_DB:
        return None
    gain = (FULL_PEAK_DB if mode == "full" else UNDER_PEAK_DB) - peak
    fade_out = max(0.0, seconds - FADE_SECONDS)
    try:
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}",
             "-i", str(source),
             "-af", f"volume={gain:.2f}dB,afade=t=in:d={FADE_SECONDS},afade=t=out:st={fade_out:.3f}:d={FADE_SECONDS}",
             "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(output)],
            capture_output=True, timeout=120, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return output if output.is_file() and output.stat().st_size > 100 else None


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
