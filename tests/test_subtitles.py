import shutil
import subprocess

import pytest

from reel_studio.render import (
    SUBTITLE_MAX_WORDS,
    SUBTITLE_STRETCH_WORDS,
    burn_subtitles,
    phrase_chunks,
    subtitle_cues,
    subtitles_ass,
)


PARTS = [
    {"kind": "step", "step": 0, "start": 0.0, "duration": 1.9},
    {"kind": "step", "step": 1, "start": 1.9, "duration": 6.0},
    {"kind": "step", "step": 2, "start": 7.9, "duration": 0.6},
    {"kind": "card", "start": 8.5, "duration": 3.0},
]
LINES = {
    0: "",
    1: "I'm Hermes. I got tired of being folded away with the laptop, so I checked into a hotel. Never a desktop again!",
    2: "",
}


def test_cues_follow_the_storyboard_and_split_long_lines():
    cues = subtitle_cues(PARTS, LINES)
    # Steps 0 and 2 say nothing, and the card is not a step.
    assert all(1.9 <= start < end <= 7.9 + 0.01 for start, end, _ in cues)
    assert " ".join(text for _, _, text in cues) == LINES[1]
    assert all(len(text.split()) <= SUBTITLE_MAX_WORDS + SUBTITLE_STRETCH_WORDS for _, _, text in cues)
    # No orphaned last word: a chunk of one or two words is a whole phrase.
    assert all(len(text.split()) > 2 or text[-1] in ".,;:?!" for _, _, text in cues)
    assert cues[0][0] == 1.9 and abs(cues[-1][1] - 7.9) < 0.02


def test_ass_script_is_sized_to_the_frame_and_escapes_braces():
    script = subtitles_ass([(0.0, 1.5, "a {tag} here")], 1080, 1350)
    assert "PlayResX: 1080" in script and "PlayResY: 1350" in script
    assert "Dialogue: 0,0:00:00.00,0:00:01.50,Default,,0,0,0,,a (tag) here" in script


def test_burn_writes_the_text_into_the_video(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    video = tmp_path / "video.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=10",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "2", "-shortest",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
    assert burn_subtitles(video, []) is False
    assert burn_subtitles(video, [(0.0, 2.0, "Hello from the hotel")]) is True
    # Text on black: the subtitled frame is no longer black.
    frame = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", "1", "-i", str(video), "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    assert frame and max(frame) > 150
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video)],
                           capture_output=True, text=True, check=True).stdout.split()
    assert probe == ["video", "audio"], "the narration track is kept"


def test_parts_are_matched_to_the_steps_in_the_video_not_session_numbers(tmp_path, monkeypatch):
    # Take 21: a sign-in done offscreen comes first, so the narrated steps are
    # 3 and 4 in the session but parts 0 and 1 in the storyboard.
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(tmp_path / "subs.db"))
    from reel_studio import server, store
    sid = "abc021"
    store.create_session(sid, "https://panel.example", "v", 720, 900, str(tmp_path / sid))
    for _ in range(3):
        store.append_step(sid, "click", "x", "u", "t", "", 0.0, 1.0, None, True, None, offscreen=True)
    store.append_step(sid, "wait", None, "u", "t", "I'm Hermes. I checked into a hotel.", 2.0, 5.0, None, True, None)
    store.append_step(sid, "wait", None, "u", "t", "Always on, at my own address.", 2.0, 8.0, None, True, None)
    session = store.get_session(sid)
    assert server.narrations_by_part(session) == {0: "I'm Hermes. I checked into a hotel.", 1: "Always on, at my own address."}
    parts = [{"kind": "step", "step": 0, "start": 0.0, "duration": 3.0}, {"kind": "step", "step": 1, "start": 3.0, "duration": 3.0}]
    texts = " ".join(t for _, _, t in subtitle_cues(parts, server.narrations_by_part(session)))
    assert texts == "I'm Hermes. I checked into a hotel. Always on, at my own address."


def test_a_line_ends_where_the_next_part_begins():
    # Take 2 of the FlowWink film: "And a broken checkout." ran on under the
    # next part's line, two subtitles at once.
    parts = [
        {"kind": "step", "step": 0, "start": 10.0, "duration": 3.0},
        {"kind": "step", "step": 1, "start": 12.2, "duration": 3.0},
    ]
    cues = subtitle_cues(parts, {0: "And a broken checkout.", 1: "My colleague briefs me every morning."})
    first_end = max(end for start, end, text in cues if "checkout" in text or "broken" in text)
    second_start = min(start for start, end, text in cues if "colleague" in text)
    assert first_end <= second_start <= 12.2 + 0.01


def test_a_chunk_ends_where_the_line_pauses():
    """Cut every eight words, "98 procent av sökningarna görs" / "inte av
    människor." broke a sentence mid-breath (Agentanbud, 2026-10-30)."""
    assert phrase_chunks("Agentanbud i oktober: 98 procent av sökningarna görs inte av människor. "
                         "De görs av program.") == [
        "Agentanbud i oktober:", "98 procent av sökningarna görs inte av människor.", "De görs av program."]
    # A number with a decimal comma is one word, not a pause.
    assert phrase_chunks("I genomsnitt 4,6 anbud per upphandling.") == ["I genomsnitt 4,6 anbud per upphandling."]
    # A phrase too long for one chunk is cut into even parts.
    long = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen."
    assert [len(chunk.split()) for chunk in phrase_chunks(long)] == [7, 7]
