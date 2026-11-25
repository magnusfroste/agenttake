"""The page's own sound in a film, on the beats a director chooses.

A game film was silent but for its narrator, though the julkalender's Tomten
speaks when a door opens and Ljudkvartetten reads its words aloud. A beat's
sound lets the page be heard: under the line, or first, with the line as a
comment on what was heard (Magnus, 2026-11-25)."""
from __future__ import annotations

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
def test_a_clip_is_levelled_by_its_mode_and_silence_is_left_out(tmp_path):
    source = tmp_path / "page-sound.mka"
    _tone(source, 6, "between(t,1,3)")
    full = page_sound.cut_clip(source, 0.5, 3.0, "full", tmp_path / "full.wav")
    under = page_sound.cut_clip(source, 0.5, 3.0, "under", tmp_path / "under.wav")
    assert full and under
    assert page_sound.peak_db(full) == pytest.approx(page_sound.FULL_PEAK_DB, abs=1.0)
    assert page_sound.peak_db(under) == pytest.approx(page_sound.UNDER_PEAK_DB, abs=1.0)
    assert page_sound.cut_clip(source, 3.5, 2.0, "full", tmp_path / "quiet.wav") is None


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
    assert level(starts[1] + 0.5, durations[1] - 1.0) == pytest.approx(page_sound.FULL_PEAK_DB, abs=1.5)
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
          const c = new AudioContext();
          const buffer = c.createBuffer(1, c.sampleRate * 0.6, c.sampleRate);
          const source = c.createBufferSource(); source.buffer = buffer; source.connect(c.destination); source.start();
          const u = new SpeechSynthesisUtterance("God jul"); speechSynthesis.speak(u);
          const a = new Audio("https://example.invalid/roster/dag-01.mp3"); a.play().catch(() => {});
        }""")
        waited = await page_sound.wait_quiet(page, 5000)
        return waited, await page_sound.heard(page)

    waited, heard = _chromium(run)
    kinds = {entry["kind"]: entry for entry in heard}
    assert set(kinds) == {"webaudio", "speech", "audio"}
    assert kinds["audio"]["src"] == "dag-01.mp3" and kinds["speech"]["text"] == "God jul"
    assert kinds["webaudio"]["src"] == "0.6 s buffer"
    assert waited < 4.5
    if not kinds["speech"]["voices"]:
        assert "speech synthesis" in " ".join(page_sound.sound_notes(heard))


def test_without_pulseaudio_nothing_changes(monkeypatch):
    monkeypatch.setattr(page_sound.shutil, "which", lambda name: None)
    assert page_sound.open_sink("abc") is None and not page_sound.ensure_server()
    page_sound.close_sink(None)
