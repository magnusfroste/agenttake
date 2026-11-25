"""The page's own sound in a film, on the beats a director chooses.

A game film was silent but for its narrator, though the julkalender's Tomten
speaks when a door opens and Ljudkvartetten reads its words aloud. A beat's
sound lets the page be heard: under the line, or first, with the line as a
comment on what was heard (Magnus, 2026-11-25)."""
from __future__ import annotations

import array
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from reel_studio import sound as page_sound
from reel_studio import storyboard as boards

DOOR = {"shot": "system", "label": "luckan", "url": "https://julkalender.example/",
        "taps": [{"x": 0.278, "y": 0.24}], "line": "Tomten har något att säga."}


def _acts(beat: dict) -> list[dict]:
    beats, items = boards.parse_storyboard([beat])
    assert not [item for item in items if item["severity"] == "fix"], items
    return [op["action"] for op in boards.recipe(beats[0]) if op["op"] == "act" and not op["action"].get("offscreen")]


def test_sound_under_plays_the_page_under_the_line():
    acts = _acts({**DOOR, "sound": "under"})
    assert [act.get("sound") for act in acts] == ["under"]
    assert acts[0]["type"] == "tap"
    # Without sound the film is as before: silent but for the line.
    assert all("sound" not in act for act in _acts(DOOR))


def test_sound_first_is_heard_to_the_end_and_then_commented_on():
    beats, _ = boards.parse_storyboard([{**DOOR, "sound": "first", "react": 1}])
    ops = [op for op in boards.recipe(beats[0]) if op["op"] == "act" and not op["action"].get("offscreen")]
    heard, comment = ops
    assert heard["action"] == {"type": "tap", "points": [{"x": 0.278, "y": 0.24, "ms": 0}], "quiet": True,
                               "until_quiet": True, "sound": "full", "then_ms": 1000}
    assert heard["narration"] == ""
    assert comment["narration"] == DOOR["line"] and comment["action"]["sound"] == "under"
    line = boards.line_seconds(DOOR["line"], beats[0].get("language", "en"))
    assert boards.beat_seconds(beats[0]) == pytest.approx(line + page_sound.FIRST_ESTIMATE_SECONDS + 1)


def test_listen_caps_how_long_the_page_is_heard_before_the_line():
    # Door 1 plays Tomten's greeting (8.5 s) and then the day's voice: the
    # beat listened to both, 20.6 s in all, in the first proof film.
    beats, items = boards.parse_storyboard([{**DOOR, "sound": "first", "listen": 9}])
    assert not [item for item in items if item["severity"] == "fix"], items
    heard = next(op for op in boards.recipe(beats[0]) if op.get("action", {}).get("until_quiet"))
    assert heard["action"]["listen_ms"] == 9000
    line = boards.line_seconds(DOOR["line"], beats[0].get("language", "en"))
    assert boards.beat_seconds(beats[0]) == pytest.approx(line + 9)
    assert "listen" in boards.beats_json_schema()["properties"]
    # Only a sound first beat listens; longer than the wait is refused.
    _, items = boards.parse_storyboard([{**DOOR, "sound": "under", "listen": 5}])
    assert any(item["category"] == "listen_not_used" for item in items)
    _, items = boards.parse_storyboard([{**DOOR, "sound": "first", "listen": 30}])
    assert any(item["severity"] == "fix" for item in items)


def test_wait_quiet_stops_at_its_limit_while_the_page_still_plays():
    async def run(page):
        await page.evaluate("""() => {
          const c = new AudioContext(); const o = c.createOscillator();
          o.connect(c.destination); o.start();
        }""")
        return await page_sound.wait_quiet(page, 1200)

    assert 1.1 <= _chromium(run) < 2.0


def test_a_heard_step_is_not_reported_as_a_still_picture():
    from reel_studio import review

    frame = bytes(((x // 3 + y // 3) % 2) * 180 + 30 for y in range(review.DETAIL_HEIGHT)
                  for x in range(review.DETAIL_WIDTH))
    frames = [frame] * int(16 * review.SCAN_FPS)
    steps = [{"idx": 0, "action_type": "tap", "offset_seconds": 1.0, "offscreen": False, "narration_text": "",
              "sound": "full"},
             {"idx": 1, "action_type": "wait", "offset_seconds": 14.0, "offscreen": False,
              "narration_text": "Där hör ni.", "sound": "under"}]
    parts = [{"kind": "step", "step": 0, "start": 0.0, "duration": 12.0},
             {"kind": "step", "step": 1, "start": 12.0, "duration": 4.0}]
    stills = lambda found: [item for item in found if item["category"] == "long_still"]
    assert not stills(review.film_findings(frames, parts, steps))
    # The same picture without the page heard is still for 16 s.
    silent = [{**step, "sound": ""} for step in steps]
    assert [item["severity"] for item in stills(review.film_findings(frames, parts, silent))] == ["medium"]


def test_what_sound_will_not_do():
    _, items = boards.parse_storyboard([{**DOOR, "taps": None, "sound": "first"}])
    assert [item["severity"] for item in items if item["category"] == "sound_first_without_taps"] == ["fix"]
    _, items = boards.parse_storyboard([{"shot": "card", "text": "Hej", "line": "Hej.", "sound": "under"}])
    assert any(item["category"] == "sound_not_used" for item in items)
    _, items = boards.parse_storyboard([{**DOOR, "sound": "loud"}])
    assert any(item["severity"] == "fix" for item in items)
    assert "sound" in boards.beats_json_schema()["properties"]


def test_a_sound_is_found_on_the_session_clock_across_a_pickup(tmp_path):
    (tmp_path / "page-sound.mka").write_bytes(b"x")
    (tmp_path / "page-sound-90000.mka").write_bytes(b"x")
    page_sound.record_span(tmp_path, "page-sound.mka", 0.0, 0.3)
    page_sound.record_span(tmp_path, "page-sound-90000.mka", 90.0, 0.2)
    spans = page_sound.load_spans(tmp_path)
    assert page_sound.locate(spans, tmp_path, 10.0) == (tmp_path / "page-sound.mka", pytest.approx(10.3))
    assert page_sound.locate(spans, tmp_path, 95.0) == (tmp_path / "page-sound-90000.mka", pytest.approx(5.2))
    # A recording that is gone is not offered.
    (tmp_path / "page-sound.mka").unlink()
    assert page_sound.locate(page_sound.load_spans(tmp_path), tmp_path, 10.0) is None


def test_a_pickup_brings_its_sound_in_where_it_was_joined(tmp_path):
    from reel_studio import pickup

    session, picked = tmp_path / "s", tmp_path / "p"
    session.mkdir(), picked.mkdir()
    (picked / page_sound.CAPTURE_FILE).write_bytes(b"sound")
    page_sound.record_span(picked, page_sound.CAPTURE_FILE, 0.0, 0.25)
    pickup.take_in(session, picked, 61.5, None)
    spans = page_sound.load_spans(session)
    assert spans == [{"file": "page-sound-61500.mka", "video_start": 61.5, "shift": 0.25}]
    assert (session / "page-sound-61500.mka").read_bytes() == b"sound"


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")


def _tone(path: Path, seconds: float, beeps: str) -> None:
    """A recording that is silent but where ``beeps`` (an ffmpeg expression
    of t) is true: a 440 Hz tone there."""
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"aevalsrc='if({beeps},0.5*sin(2*PI*440*t),0)':s=48000:d={seconds}",
                    "-ac", "2", "-c:a", "flac", str(path)], check=True)


@needs_ffmpeg
def test_a_clip_is_as_loud_as_the_voice_and_silence_is_left_out(tmp_path):
    source = tmp_path / "page-sound.mka"
    _tone(source, 6, "between(t,1,3)")
    full = page_sound.cut_clip(source, 0.5, 3.0, tmp_path / "full.wav", voice=-24.0)
    assert full
    # Two seconds of tone in three of clip: the silence is gated out.
    assert page_sound.loudness(full) == pytest.approx(-24.0 - page_sound.PAGE_BELOW_VOICE_LU, abs=0.7)
    assert page_sound.cut_clip(source, 3.5, 2.0, tmp_path / "quiet.wav") is None


@needs_ffmpeg
def test_a_short_beep_and_a_long_tone_come_out_as_loud_and_never_over_the_ceiling(tmp_path):
    source = tmp_path / "page-sound.mka"
    # A 0.6 s beep, then a long tone of the same amplitude.
    _tone(source, 10, "between(t,1,1.6)+between(t,4,9)")
    beep = page_sound.cut_clip(source, 0.5, 2.5, tmp_path / "beep.wav", voice=-20.0)
    tone = page_sound.cut_clip(source, 3.5, 6.0, tmp_path / "tone.wav", voice=-20.0)
    assert abs(page_sound.loudness(beep) - page_sound.loudness(tone)) < 1.5
    # Asked louder than its peak allows, it stops at the ceiling.
    loud = page_sound.cut_clip(source, 3.5, 6.0, tmp_path / "loud.wav", voice=-2.0)
    assert page_sound.peak_db(loud) <= page_sound.PEAK_CEILING_DB + 0.2


@needs_ffmpeg
def test_the_voice_is_measured_as_the_middle_of_its_lines(tmp_path):
    quiet, loud = tmp_path / "quiet.wav", tmp_path / "loud.wav"
    for path, amplitude in ((quiet, 0.05), (loud, 0.5)):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"aevalsrc='{amplitude}*sin(2*PI*440*t)':s=24000:d=3", str(path)], check=True)
    assert page_sound.voice_loudness([quiet, loud, loud]) == pytest.approx(page_sound.loudness(loud), abs=0.1)
    assert page_sound.voice_loudness([]) == page_sound.VOICE_LUFS


def test_lines_closer_than_the_hold_keep_the_page_down():
    assert page_sound.spoken([(7.0, 8.0), (1.0, 2.0), (2.5, 4.0)]) == [(1.0, 4.0), (7.0, 8.0)]
    assert page_sound.duck([]) == ""
    # Deep films do not nest deeper: one flat sum, whatever the number of lines.
    many = page_sound.duck([(index * 4.0, index * 4.0 + 1.5) for index in range(80)])
    assert many.count("clip(") == 160 and "max(" not in many


@needs_ffmpeg
def test_the_page_goes_down_under_a_line_and_up_when_it_ends(tmp_path):
    from reel_studio.render import _mux_segment_audio

    video = tmp_path / "video.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=black:size=160x90:rate=25:d=12",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    # Lines as silence: only their times matter here, so what is measured is
    # the page alone.
    def line(name, seconds):
        path = tmp_path / name
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"anullsrc=r=24000:cl=mono", "-t", str(seconds), str(path)], check=True)
        return path
    source = tmp_path / "page-sound.mka"
    _tone(source, 11, "1")
    page = page_sound.cut_clip(source, 0.0, 10.0, tmp_path / "page.wav")
    film = tmp_path / "film.mp4"
    # A line at 2-4 s, then two at 6-7 s and 7.5-8.5 s: a 0.5 s pause.
    # Between the first two, 2 s: the page comes up.
    _mux_segment_audio(video, [(2.0, line("a.wav", 2.0)), (6.0, line("b.wav", 1.0)), (7.5, line("c.wav", 1.0))],
                       film, 12.0, page=[(0.0, page)])
    level = lambda at, seconds: page_sound.peak_db(film, at, seconds)
    # Measured against the page alone, before the first line.
    full = level(0.2, 1.2)
    under = full - page_sound.DUCK_DB
    # Already down when the line starts, and down all through it.
    assert level(2.0, 2.0) == pytest.approx(under, abs=1.0)
    # Halfway down before it, and up again a release after it.
    assert under + 3 < level(2.0 - page_sound.DUCK_LEAD / 2 - 0.025, 0.05) < full - 3
    # Still down just past the line's end, up again after the release.
    assert level(4.0, page_sound.DUCK_AFTER - 0.05) == pytest.approx(under, abs=1.0)
    up = 4.0 + page_sound.DUCK_AFTER + page_sound.DUCK_RELEASE
    assert level(up + 0.05, 5.7 - up - 0.1) == pytest.approx(full, abs=1.0)
    # The short pause between the two lines does not bring the page up.
    assert level(7.0, 0.5) == pytest.approx(under, abs=1.0)
    # Nothing after the page's sound: it fades out, not cut.
    assert level(9.0, 0.4) == pytest.approx(full, abs=1.0)
    assert level(9.85, 0.1) < full - 6


@needs_ffmpeg
@pytest.mark.parametrize("transitions", ["cut", "smooth"])
def test_the_film_has_the_page_sound_only_where_a_step_asks_for_it(tmp_path, transitions):
    from reel_studio.render import RenderConfig, segmented_render

    directory = tmp_path / "session"
    directory.mkdir()
    screen = directory / "screen.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:d=12",
                    "-pix_fmt", "yuv420p", str(screen)], check=True)
    # The page sounds the whole time; the recording started 0.5 s before
    # the screen's, so the screen's t is the sound's t + 0.5.
    _tone(directory / page_sound.CAPTURE_FILE, 13, "1")
    page_sound.record_span(directory, page_sound.CAPTURE_FILE, 0.0, 0.5)
    steps = [(1.0, None, 3.0), (5.0, None, 3.0), (9.0, None, 2.0)]
    result = segmented_render(screen, steps, tmp_path / "film.mp4", config=RenderConfig(transitions=transitions),
                              floors=[1.0] * 3, lead_in=False, sounds=[None, "full", None])
    film = result.path
    starts = [part["start"] for part in result.timeline if part["kind"] == "step"]
    durations = [part["duration"] for part in result.timeline if part["kind"] == "step"]
    level = lambda at, seconds: page_sound.peak_db(film, at, seconds)
    assert level(starts[0] + 0.5, durations[0] - 1.0) < -60
    from reel_studio.render import FILM_LUFS
    assert page_sound.loudness(film, starts[1] + 0.5, durations[1] - 1.0) == pytest.approx(FILM_LUFS, abs=1.0)
    assert level(starts[2] + 0.5, durations[2] - 1.0) < -60


def _chromium(run):
    async def wrapped():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium unavailable: {exc}")
            page = await browser.new_page()
            await page.set_content("<button id=b>play</button>")
            # set_content runs no init script; a session's pages do.
            await page.evaluate(page_sound.SOUND_JS)
            try:
                return await run(page)
            finally:
                await browser.close()
    return asyncio.run(wrapped())


def test_the_page_says_what_it_played_and_when_it_is_quiet():
    async def run(page):
        await page.evaluate("""() => {
          const c = new AudioContext({latencyHint: "interactive"});
          const buffer = c.createBuffer(1, c.sampleRate * 0.6, c.sampleRate);
          const source = c.createBufferSource(); source.buffer = buffer; source.connect(c.destination); source.start();
          const u = new SpeechSynthesisUtterance("God jul"); speechSynthesis.speak(u);
          const a = new Audio("https://example.invalid/roster/dag-01.mp3"); a.play().catch(() => {});
        }""")
        waited = await page_sound.wait_quiet(page, 5000)
        # Asked for "interactive", the page gets the playback buffer all the same.
        assert await page.evaluate("() => AudioContext.__take === true && new AudioContext() instanceof AudioContext")
        return waited, await page_sound.heard(page)

    waited, heard = _chromium(run)
    kinds = {entry["kind"]: entry for entry in heard}
    assert set(kinds) == {"webaudio", "speech", "audio"}
    assert kinds["audio"]["src"] == "dag-01.mp3" and kinds["speech"]["text"] == "God jul"
    assert kinds["webaudio"]["src"] == "0.6 s buffer"
    assert waited < 4.5
    if not kinds["speech"]["voices"]:
        assert "speech synthesis" in " ".join(page_sound.sound_notes(heard))


def test_the_capture_clock_takes_the_earliest_start_its_progress_gives():
    import io
    import time

    stream = io.BytesIO(b"frame=0\nout_time_us=N/A\nout_time_us=2000000\nout_time_us=1500000\nprogress=end\n")
    clock = page_sound.CaptureClock(stream)
    for _ in range(50):
        if clock.started is not None:
            break
        time.sleep(0.01)
    time.sleep(0.05)
    # Read at once, the 2 s report says the recording began 2 s ago.
    assert clock.started == pytest.approx(time.monotonic() - 2.0, abs=0.3)


@needs_ffmpeg
def test_the_sink_is_recorded_whole_and_its_clock_read(tmp_path):
    """Stamped by the wall clock and resampled to it, a steady tone came out
    of the recording with 2.5 s of its 7.8 s gone in pieces (2026-11-25)."""

    import time

    if not page_sound.available() or not shutil.which("paplay"):
        pytest.skip("no PulseAudio")
    opened = page_sound.open_sink("wholetest")
    if opened is None:
        pytest.skip("no PulseAudio server")
    sink, module = opened
    tone = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=3", "-ac", "2", "-ar",
                    "48000", str(tone)], check=True)
    try:
        process = page_sound.start_capture(sink, tmp_path / "capture.mka")
        time.sleep(1)
        subprocess.run(["paplay", "-d", sink, str(tone)], check=True, timeout=20)
        time.sleep(0.5)
        from reel_studio.render import stop_recording
        stop_recording(process)
    finally:
        page_sound.close_sink(module)
    assert process.clock.started is not None
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(tmp_path / "capture.mka"), "-ac", "1", "-f", "s16le",
                          "-"], capture_output=True, check=True).stdout
    samples = array.array("h", raw)
    loud = [i for i in range(0, len(samples), 48) if abs(samples[i]) > 1000]
    inside = samples[loud[0] + 4800:loud[-1] - 4800]
    run = longest = 0
    for value in inside:
        run = run + 1 if abs(value) < 2 else 0
        longest = max(longest, run)
    assert longest < 48, f"a gap of {longest / 48:.1f} ms in a steady tone"


def test_without_pulseaudio_nothing_changes(monkeypatch):
    monkeypatch.setattr(page_sound.shutil, "which", lambda name: None)
    assert page_sound.open_sink("abc") is None and not page_sound.ensure_server()
    page_sound.close_sink(None)


@needs_ffmpeg
def test_the_film_is_set_to_its_loudness_with_its_peaks_limited_and_in_time(tmp_path):
    from reel_studio.render import FILM_LUFS, _mux_segment_audio

    video = tmp_path / "video.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=black:size=160x90:rate=25:d=6",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    # A line with one loud click at 2 s: the gain lifts the click over the
    # limit, and the limiter must not move it.
    line = tmp_path / "line.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "aevalsrc='0.1*sin(2*PI*300*t)+if(between(t,1.0,1.01),0.9*sin(2*PI*1000*t),0)':s=24000:d=4",
                    str(line)], check=True)
    film = tmp_path / "film.mp4"
    _mux_segment_audio(video, [(1.0, line)], film, 6.0)
    assert page_sound.loudness(film) == pytest.approx(FILM_LUFS, abs=1.0)
    assert page_sound.peak_db(film) <= -1.0
    # The click is still at 2 s.
    wave = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(film), "-f", "s16le", "-ac", "1", "-ar", "24000", "-"],
                          capture_output=True, check=True).stdout

    samples = array.array("h", wave)
    loudest = max(range(len(samples)), key=lambda index: abs(samples[index]))
    assert loudest / 24000 == pytest.approx(2.0, abs=0.02)
    from reel_studio.render import probe_duration
    assert probe_duration(film) == pytest.approx(6.0, abs=0.1)


def test_a_pause_is_heard_after_sound_and_not_in_silence_or_a_word():
    windows = lambda pattern, start=0.0: [(start + index * page_sound.EAR_WINDOW, level)
                                          for index, level in enumerate(pattern)]
    speech = [-20.0, -24.0, -21.0, -48.0, -22.0] * 8  # one 50 ms gap between words
    breath = [-60.0] * 7
    assert not page_sound.paused(windows(speech), len(speech) * page_sound.EAR_WINDOW)
    levels = windows(speech + breath)
    assert page_sound.paused(levels, len(levels) * page_sound.EAR_WINDOW)
    # Nothing heard yet: no pause to come in at.
    assert not page_sound.paused(windows([-90.0] * 20), 1.0)
    assert page_sound.window_db([16384] * 400) == pytest.approx(-6.0, abs=0.1)


class _Page:
    def __init__(self, playing):
        self.playing = playing

    async def evaluate(self, script, *args):
        return self.playing()

    async def wait_for_timeout(self, ms):
        await asyncio.sleep(ms / 1000)


class _Ear:
    """Speech that pauses for a breath 1.5 s in, and again 3.0 s in."""

    def __init__(self):
        import time
        self.started = time.monotonic()

    def since(self, started):
        import time
        now = time.monotonic()
        levels, at = [], self.started
        while at <= now:
            t = at - self.started
            levels.append((at, -60.0 if 1.5 <= t < 1.9 or 3.0 <= t < 3.4 else -20.0))
            at += page_sound.EAR_WINDOW
        return [entry for entry in levels if entry[0] >= started]


def test_with_listen_the_line_comes_in_at_the_first_pause_after_it():
    page = _Page(lambda: 1)
    # Listen 2 s: the breath at 1.5 s is too early, the one at 3.0 s is it.
    waited = asyncio.run(page_sound.wait_heard(page, _Ear(), 2000))
    assert waited["ended"] == "pause"
    assert 3.2 <= waited["seconds"] <= 3.5
    # Without an ear, listen is where the line comes in.
    waited = asyncio.run(page_sound.wait_heard(page, None, 1000))
    assert waited["ended"] == "limit" and waited["seconds"] == pytest.approx(1.0, abs=0.15)
    # Without listen, the page is heard to its end.
    waited = asyncio.run(page_sound.wait_heard(_Page(lambda: 0), _Ear(), 0))
    assert waited["ended"] == "quiet"


@needs_ffmpeg
def test_a_line_over_the_page_still_speaking_is_found(tmp_path):
    from reel_studio import review

    _tone(tmp_path / page_sound.CAPTURE_FILE, 8, "lt(t,3)")
    page_sound.record_span(tmp_path, page_sound.CAPTURE_FILE, 0.0, 0.0)
    steps = [{"idx": 0, "offset_seconds": 0.5, "sound": "full", "narration_duration": 0.0},
             {"idx": 1, "offset_seconds": 2.0, "narration_duration": 2.0}]
    found = review.talk_over_findings(tmp_path, steps)
    assert [finding["category"] for finding in found] == ["talks_over_page"]
    assert found[0]["location"] == "step[1]"
    steps[1]["offset_seconds"] = 3.5
    assert review.talk_over_findings(tmp_path, steps) == []
    # In a pause, with the page going on 0.2 s into the line: where a person
    # would come in.
    _tone(tmp_path / page_sound.CAPTURE_FILE, 8, "lt(t,3)+gt(t,3.7)")
    assert review.talk_over_findings(tmp_path, steps) == []


def test_the_ear_hears_the_sink(tmp_path):
    import time

    if not page_sound.available() or not shutil.which("paplay") or not shutil.which("parec"):
        pytest.skip("no PulseAudio")
    opened = page_sound.open_sink("eartest")
    if opened is None:
        pytest.skip("no PulseAudio server")
    sink, module = opened
    tone = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=1", "-ac", "2", "-ar",
                    "48000", str(tone)], check=True)
    try:
        ear = page_sound.open_ear(sink)
        assert ear is not None
        started = time.monotonic()
        time.sleep(0.5)
        subprocess.run(["paplay", "-d", sink, str(tone)], check=True, timeout=20)
        time.sleep(0.6)
        ear.close()
    finally:
        page_sound.close_sink(module)
    levels = ear.since(started)
    assert max(level for _, level in levels) > -35
    assert page_sound.paused(levels, levels[-1][0])
