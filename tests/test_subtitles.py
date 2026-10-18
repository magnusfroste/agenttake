import shutil
import subprocess

import pytest

from reel_studio.render import burn_subtitles, subtitle_cues, subtitles_ass


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
    assert all(len(text.split()) <= 8 for _, _, text in cues)
    assert all(len(text.split()) > 2 for _, _, text in cues), "no orphaned last word"
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
