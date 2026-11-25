"""Music under a film, from a library of free tracks chosen by mood.

"subtle" was filtered pink noise, a hiss and not music; a director could not
lay "lite pausmusik" under a film (Magnus, 2026-11-25)."""
from __future__ import annotations

import array
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from reel_studio import music

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")


def _ffmpeg(*arguments: str) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *arguments], check=True)


@pytest.fixture
def library(tmp_path, monkeypatch):
    """Two tracks: a calm 1 kHz tone of 4 s (stereo, 44.1 kHz, no loop) and
    a bright 2 kHz loop of 3 s."""
    folder = tmp_path / "music"
    folder.mkdir()
    _ffmpeg("-f", "lavfi", "-i", "aevalsrc='0.3*sin(2*PI*1000*t)|0.3*sin(2*PI*1000*t)':s=44100:d=4",
            str(folder / "calm.wav"))
    _ffmpeg("-f", "lavfi", "-i", "aevalsrc='0.3*sin(2*PI*2000*t)':s=48000:d=3", str(folder / "bright.wav"))
    (folder / "library.json").write_text(json.dumps({"tracks": [
        {"id": "still-water", "mood": "calm", "file": "calm.wav", "title": "Still Water", "artist": "A",
         "source": "https://example.com/1", "licence": "CC0"},
        {"id": "sunny-loop", "mood": "bright", "file": "bright.wav", "title": "Sunny", "artist": "B",
         "source": "https://example.com/2", "licence": "CC0", "loop": True},
        {"id": "gone", "mood": "calm", "file": "missing.wav"},
    ]}))
    monkeypatch.setenv(music.LIBRARY_ENV, str(folder / "library.json"))
    music._load.cache_clear()
    yield folder
    music._load.cache_clear()


def test_a_mood_or_an_id_chooses_a_track_and_subtle_is_the_calm_one(library):
    assert [track.id for track in music.tracks()] == ["still-water", "sunny-loop"]  # a missing file is left out
    assert music.moods() == ["calm", "bright"]
    assert music.choose("calm").id == "still-water"
    assert music.choose("Sunny-Loop").id == "sunny-loop"
    assert music.choose("subtle").id == "still-water"
    assert music.choose("none") is None and music.choose("") is None and music.choose("jazz") is None
    assert music.normalize(" BRIGHT ") == "bright"
    assert music.normalize("jazz") == "none"
    assert music.normalize("subtle") == "subtle"


def test_without_a_library_there_is_no_music(tmp_path, monkeypatch):
    monkeypatch.setenv(music.LIBRARY_ENV, str(tmp_path / "nothing.json"))
    music._load.cache_clear()
    assert music.tracks() == [] and music.choose("subtle") is None and music.normalize("calm") == "none"
    music._load.cache_clear()


def test_the_shipped_library_is_free_and_complete():
    """Every track in the library is CC0, has its source and its file."""
    library = music.DEFAULT_LIBRARY
    # Never skipped: under an ignored folder (data/) the library was missing
    # from git, and so from the image, while this test skipped (#155).
    assert library.is_file()
    entries = json.loads(library.read_text())["tracks"]
    assert entries
    for entry in entries:
        assert entry["licence"].upper().startswith("CC0"), entry["id"]
        assert entry["source"].startswith("https://"), entry["id"]
        assert entry["mood"] and entry["title"], entry["id"]
        assert (library.parent / entry["file"]).is_file(), entry["id"]
    assert len({entry["id"] for entry in entries}) == len(entries)


def _band_levels(film: Path, frequency: int, window: float = 0.25) -> list[float]:
    """The film's level (dBFS RMS) around ``frequency`` every ``window`` s."""
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(film),
         "-af", f"bandpass=f={frequency}:width_type=h:w=150,bandpass=f={frequency}:width_type=h:w=150",
         "-f", "s16le", "-ac", "1", "-ar", "48000", "-"],
        capture_output=True, check=True).stdout
    samples = array.array("h", raw)
    size = int(48000 * window)
    levels = []
    for start in range(0, len(samples) - size + 1, size):
        chunk = samples[start:start + size]
        power = sum(value * value for value in chunk) / size
        levels.append(10 * math.log10(power / 32768.0 ** 2) if power else -120.0)
    return levels


def _at(levels: list[float], seconds: float, window: float = 0.25) -> float:
    return levels[int(seconds / window)]


@needs_ffmpeg
def test_music_goes_down_under_the_line_and_fades_with_the_film(library, tmp_path):
    from reel_studio.render import _mux_segment_audio, probe_duration

    video = tmp_path / "video.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=black:size=160x90:rate=25:d=10", "-pix_fmt", "yuv420p", str(video))
    line = tmp_path / "line.wav"
    _ffmpeg("-f", "lavfi", "-i", "aevalsrc='0.1*sin(2*PI*300*t)':s=24000:d=3", str(line))
    film = tmp_path / "film.mp4"
    _mux_segment_audio(video, [(3.0, line)], film, 10.0, music="calm")
    rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=sample_rate",
                           "-of", "csv=p=0", str(film)], capture_output=True, text=True, check=True).stdout.strip()
    assert rate == "48000"
    assert probe_duration(film) == pytest.approx(10.0, abs=0.1)
    levels = _band_levels(film, 1000)
    before, under, after = _at(levels, 2.0), _at(levels, 4.5), _at(levels, 7.5)
    # Heard where nobody speaks, 14 dB down under the line.
    assert before > -40
    assert before - under == pytest.approx(music.DUCK_DB, abs=2.0)
    # Nothing said after it: up, and the end's lift on top.
    assert after == pytest.approx(before + music.END_LIFT_DB, abs=2.0)
    # Fades in, and out with the film.
    assert _at(levels, 0.0) < before - 6
    assert _at(levels, 9.75) < after - 10
    # The 4 s track plays again for a 10 s film, crossfaded: still there at 8 s.
    assert _at(levels, 8.0) > before - 6


@needs_ffmpeg
def test_music_sits_under_the_voice_where_nobody_speaks(library, tmp_path):
    from reel_studio import sound as page_sound
    from reel_studio.render import _mux_segment_audio

    video = tmp_path / "video.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=black:size=160x90:rate=25:d=12", "-pix_fmt", "yuv420p", str(video))
    line = tmp_path / "line.wav"
    _ffmpeg("-f", "lavfi", "-i", "aevalsrc='0.1*sin(2*PI*300*t)':s=24000:d=4", str(line))
    film = tmp_path / "film.mp4"
    _mux_segment_audio(video, [(6.0, line)], film, 12.0, music="bright")
    # A loop: no crossfade, and the music between 1.5 and 5 s against the line.
    music_alone = page_sound.loudness(film, 1.5, 3.5)
    line_heard = page_sound.loudness(line)
    assert music_alone is not None and line_heard is not None
    voice_in_film = page_sound.loudness(film, 6.8, 2.4)
    assert voice_in_film is not None
    # The voice in the film, with the music 22 dB under it, is the line
    # itself plus the film's gain; the music alone is BELOW_VOICE_LU under.
    assert voice_in_film - music_alone == pytest.approx(music.BELOW_VOICE_LU, abs=1.5)


def test_list_music_and_render_config_take_the_library(library):
    import asyncio
    from reel_studio import server

    listed = asyncio.run(server.list_music())
    assert listed["moods"] == ["calm", "bright"]
    assert listed["tracks"][0]["id"] == "still-water" and listed["tracks"][0]["licence"] == "CC0"
    assert server._render_config("", "", "#000000", "", "", "calm").music == "calm"
    assert server._render_config("", "", "#000000", "", "", "jazz").music == "none"


@needs_ffmpeg
def test_music_rises_over_the_closing_card_and_then_fades(library, tmp_path):
    from reel_studio.render import _mux_segment_audio

    video = tmp_path / "video.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=black:size=160x90:rate=25:d=14", "-pix_fmt", "yuv420p", str(video))
    line = tmp_path / "line.wav"
    _ffmpeg("-f", "lavfi", "-i", "aevalsrc='0.1*sin(2*PI*300*t)':s=24000:d=3", str(line))
    film = tmp_path / "film.mp4"
    # A line from 3 to 6 s, nothing after it: the end of a film, its card.
    _mux_segment_audio(video, [(3.0, line)], film, 14.0, music="bright")
    levels = _band_levels(film, 2000)
    before, card = _at(levels, 2.0), _at(levels, 10.0)
    assert card - before == pytest.approx(music.END_LIFT_DB, abs=1.5)
    # Still up 2.5 s before the end, faded at the end.
    assert _at(levels, 11.5) == pytest.approx(card, abs=1.5)
    assert _at(levels, 13.75) < card - 10
    assert music.end_lift([], 10.0) == ""
    assert music.end_lift([(1.0, 9.5)], 10.0) == ""
