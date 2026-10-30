"""A mid-film card and subtitles that read on light pages. The "Shadow AI"
film researches a news source, cuts to a full-screen text card, then shows
the product: the card is generated, cut in where its step was recorded, and
kept by rerender. In the FlowWink film white subtitles over a 40% box were
hard to read on light, busy pages; subtitle_style makes the box darker."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from reel_studio import render
from reel_studio.schema import ACTION_CONTRACT, Action, action_json_schema

media = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or not Path(render.FONT_PATH).is_file(),
    reason="ffmpeg and the card font are required",
)


@pytest.fixture(scope="module")
def mods(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cards")
    os.environ["REEL_OUTPUT_DIR"] = str(tmp)
    os.environ["REEL_DB_PATH"] = str(tmp / "cards.db")
    from reel_studio import server, store

    store.init_schema()
    return server, store


def _video(path, seconds=9, size="320x176", source="testsrc2="):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"{source}size={size}:rate=25", "-t", str(seconds), "-pix_fmt", "yuv420p",
                    str(path)], check=True)
    return path


def _rgb(video, at, crop="iw:ih:0:0"):
    """The mean colour of a region of one frame."""
    data = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"crop={crop},scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    return tuple(data[:3])


def _brightest(video, at, crop="iw:ih:0:0"):
    data = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"crop={crop},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    return max(data), min(data)


# ---- The card --------------------------------------------------------------

def test_the_card_action_is_in_the_contract():
    action = Action(type="card", text="Shadow AI.\nYou're not alone.", background="blur")
    assert action.background == "blur"
    assert "card" in ACTION_CONTRACT and "background" in ACTION_CONTRACT
    schema = action_json_schema()
    assert "card" in schema["properties"]["type"]["enum"]
    assert "background" in schema["properties"]


def test_a_card_background_is_brand_blur_or_a_hex_colour():
    assert render.card_background(None) == "brand"
    assert render.card_background(" Blur ") == "blur"
    assert render.card_background("0B1320") == "#0b1320"
    assert render.card_background("#ffffff") == "#ffffff"
    assert render.card_background("red") is None
    assert render.card_background("https://example.com/a.png") is None


def test_a_card_lasts_its_line_plus_a_tail_and_at_least_two_and_a_half_seconds():
    steps = [(1.0, None, 0.0), (3.0, None, 4.0), (3.6, None, 0.0), (5.0, None, 1.0)]
    segments, warnings = render.plan_segments(
        steps, 9.0, lead_in=False, cards=[False, True, False, True],
    )
    by_step = {s.step_index: s for s in segments}
    # A 4 s line holds 4.8 s, though the next step was recorded 0.6 s later;
    # that is no warning — the card is generated, not cut from the footage.
    assert by_step[1].output_duration == pytest.approx(4.0 + render.CARD_TAIL)
    assert by_step[3].output_duration == pytest.approx(render.CARD_MIN_SECONDS)
    assert warnings == []
    # A card moved with edit_step(position) plays where it is put.
    moved, _ = render.plan_segments(steps, 9.0, lead_in=False, cards=[False, True, False, True],
                                    order=[0, 2, 3, 1])
    assert [s.step_index for s in moved] == [0, 3, 1, 2]


@media
def test_a_card_segment_renders_at_its_length_and_carries_the_text(tmp_path):
    card = {"text": "Shadow AI is a question many CISOs struggle with", "background": "brand"}
    out = render.render_card_segment(card, 3.3, tmp_path / "card.mp4", (640, 360), "#1f2a44")
    assert render.probe_duration(out) == pytest.approx(3.3, abs=0.1)
    assert render.probe_video_size(out) == (640, 360)
    red, green, blue = _rgb(out, 0.1, "iw:ih*0.1:0:0")  # the top: the accent colour
    assert blue > red and abs(red - 0x1f) < 12 and abs(blue - 0x44) < 12
    # White text in the middle of it.
    bright, _ = _brightest(out, 1.5, "iw:ih*0.5:0:ih*0.25")
    assert bright > 200
    # On a light colour the text is dark, so it reads.
    light = render.render_card_segment({"text": "You're not alone", "background": "#f5f5f5"}, 2.5,
                                       tmp_path / "light.mp4", (640, 360), "#1f2a44")
    _, darkest = _brightest(light, 1.0, "iw:ih*0.5:0:ih*0.25")
    assert darkest < 60


@media
def test_card_text_is_drawn_as_written(tmp_path):
    # "you're not alone": an apostrophe broke the whole filter chain, and the
    # blur card fell back to a flat one. Drawn from a file, the text needs no
    # escaping at all: the escaped text must draw exactly the same picture.
    text = "you're: 50% done, a\\b — ok"
    (tmp_path / "line.txt").write_text(text)

    def frame(drawtext):
        return subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=900x80:d=1",
             "-vf", f"drawtext=fontfile={render.FONT_PATH}:{drawtext}:fontcolor=white:fontsize=30:x=10:y=10,format=gray",
             "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout

    expected = frame(f"textfile={tmp_path / 'line.txt'}:expansion=none")
    assert frame(f"text='{render._escape_drawtext(text)}'") == expected


@media
@pytest.mark.parametrize("transitions", ["smooth", "cuts"])
def test_a_card_is_cut_in_where_its_step_was_recorded(tmp_path, transitions):
    video = _video(tmp_path / "screen.mp4")
    render.record_card(tmp_path, 3.0, "You're not alone", "#ff0000")
    voice = tmp_path / "line.m4a"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    str(voice)], check=True)
    result = render.segmented_render(
        video, [(1.0, None, 0.0), (3.0, voice, 2.0), (5.0, None, 0.0)], tmp_path / "video.mp4",
        None, render.RenderConfig(transitions=transitions), lead_in=False,
    )
    parts = {part["step"]: part for part in result.timeline}
    card = parts[1]
    assert card["duration"] == pytest.approx(2.0 + render.CARD_TAIL, abs=0.06)
    if transitions == "smooth":
        # It dissolves in and out.
        assert card["start"] == pytest.approx(parts[0]["start"] + parts[0]["duration"] - render.CARD_FADE, abs=0.06)
        assert parts[2]["start"] == pytest.approx(card["start"] + card["duration"] - render.CARD_FADE, abs=0.06)
    red, green, blue = _rgb(result.path, card["start"] + card["duration"] / 2, "iw:ih*0.15:0:0")
    assert red > 200 and green < 60 and blue < 60, "the card, not the recorded footage"


@media
def test_a_blur_card_is_built_on_the_frame_before_it(tmp_path):
    # The footage is a flat teal; a blur card over it stays teal-ish (dimmed),
    # not the accent colour.
    video = _video(tmp_path / "screen.mp4", source="color=c=teal:")
    render.record_card(tmp_path, 3.0, "Shadow AI", "blur")
    result = render.segmented_render(
        video, [(1.0, None, 0.0), (3.0, None, 0.0)], tmp_path / "video.mp4",
        None, render.RenderConfig(accent="#ff0000", transitions="cuts"), lead_in=False,
    )
    card = next(part for part in result.timeline if part["step"] == 1)
    assert card["duration"] == pytest.approx(render.CARD_MIN_SECONDS, abs=0.06)
    red, green, blue = _rgb(result.path, card["start"] + 1.0, "iw:ih*0.15:0:0")
    assert green > red and blue > red


@media
def test_rerender_keeps_the_card(mods, tmp_path):
    server, store = mods
    sid = "s-card"
    session_dir = tmp_path / sid
    session_dir.mkdir()
    _video(session_dir / "screen.mp4")
    shutil.copy(session_dir / "screen.mp4", session_dir / "video.mp4")
    store.create_session(sid, "https://example.com", "en-US-JennyNeural", 320, 176, str(session_dir),
                         "edge", None, None, "", "", "#1f2a44", "", "Learn more", "none", "cuts")
    for offset, kind in ((1.0, "goto"), (3.0, "card"), (6.0, "click")):
        store.append_step(sid, kind, None, "https://example.com", "t", "", 0.0, offset, None, True, None)
    render.record_card(session_dir, 3.0, "You're not alone", "brand")
    store.finish_session(sid, str(session_dir / "video.mp4"), None, 9.0)
    assert asyncio.run(server.rerender(sid))["ok"]
    parts = [part for part in render.read_storyboard(session_dir) if part["kind"] == "step"]
    assert [part["step"] for part in parts] == [0, 1, 2]
    assert parts[1]["duration"] == pytest.approx(render.CARD_MIN_SECONDS, abs=0.06)


@media
def test_a_plain_card_is_not_a_blank_frame(tmp_path):
    from reel_studio import review

    still = tmp_path / "storyboard"
    still.mkdir()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=0x1f2a44:size=480x270",
                    "-frames:v", "1", str(still / "part-000.jpg")], check=True)
    video = tmp_path / "video.mp4"
    video.touch()
    parts = [{"kind": "step", "step": 0, "start": 0.0, "duration": 2.5, "frame": "part-000.jpg"}]
    page = [{"idx": 0, "offset_seconds": 1.0, "action_type": "click", "narration_text": "A line."}]
    card = [{**page[0], "action_type": "card"}]
    assert review.blank_frame_findings(review.step_frames(video, parts, page, tmp_path / "f"))
    assert review.step_frames(video, parts, card, tmp_path / "f") == []


def test_the_director_is_told_about_cards_and_subtitle_styles(mods):
    server, _ = mods
    assert '"type": "card"' in server.DIRECTOR_PROMPT
    assert "subtitle_style" in server.DIRECTOR_PROMPT


# ---- Subtitle styles -------------------------------------------------------

@pytest.mark.parametrize("style, back", [
    ("soft", "&H96000000"), ("box", "&H40000000"), ("solid", "&H00000000"), ("unknown", "&H96000000"),
])
def test_each_subtitle_style_sets_the_box_colour(style, back):
    script = render.subtitles_ass([(0.0, 1.0, "Hello")], 1920, 1080, style)
    fields = next(line for line in script.splitlines() if line.startswith("Format: Name"))[8:].split(", ")
    values = next(line for line in script.splitlines() if line.startswith("Style: "))[7:].split(",")
    style_line = dict(zip(fields, values))
    assert style_line["BackColour"] == back
    assert style_line["BorderStyle"] == "3"


def test_the_default_subtitle_style_is_soft():
    assert render.subtitles_ass([(0.0, 1.0, "Hi")], 640, 360) == render.subtitles_ass([(0.0, 1.0, "Hi")], 640, 360, "soft")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_a_darker_style_draws_a_darker_box_on_a_light_page(tmp_path):
    def burned(style):
        video = tmp_path / style / "video.mp4"
        video.parent.mkdir()
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=white:s=640x360:r=10",
                        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "2", "-shortest",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
        assert render.burn_subtitles(video, [(0.0, 2.0, "Shadow AI on a light page")], style)
        return sum(_rgb(video, 1.0, "iw*0.5:ih*0.2:iw*0.25:ih*0.75")) / 3

    soft, box, solid = burned("soft"), burned("box"), burned("solid")
    assert solid < box < soft


def test_subtitle_style_is_kept_and_rerender_changes_it(mods, monkeypatch, tmp_path):
    server, store = mods
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    for name in ("start_session", "rerender"):
        prop = tools[name].inputSchema["properties"]["subtitle_style"]
        assert "box" in json.dumps(prop) and "solid" in json.dumps(prop)

    sid = "s-style"
    session_dir = tmp_path / sid
    session_dir.mkdir()
    (session_dir / "screen.mp4").write_bytes(b"x")
    (session_dir / "video.mp4").write_bytes(b"x")
    store.create_session(sid, "https://example.com", "v", 320, 176, str(session_dir))
    store.finish_session(sid, str(session_dir / "video.mp4"), None, 5.0)
    store.update_session_subtitles(sid, True)
    assert store.get_session(sid)["subtitle_style"] == "soft"

    burned = []
    monkeypatch.setattr(server, "probe_duration", lambda path: 5.0)
    monkeypatch.setattr(server, "segmented_render",
                        lambda *args, **kwargs: SimpleNamespace(warnings=[], duration=5.0, timeline=[]))
    monkeypatch.setattr(server, "write_storyboard", lambda *args: [])
    monkeypatch.setattr(server, "subtitle_cues", lambda *args: [(0.0, 1.0, "Hi")])
    monkeypatch.setattr(server, "burn_subtitles", lambda video, cues, style="soft": burned.append(style) or True)
    assert asyncio.run(server.rerender(sid, subtitle_style="solid"))["subtitles"]
    assert store.get_session(sid)["subtitle_style"] == "solid"
    # A rerender without it keeps the style.
    asyncio.run(server.rerender(sid))
    assert burned == ["solid", "solid"]


@media
def test_a_film_that_ends_on_a_card_cuts_to_the_closing_card(tmp_path):
    """A fade laid the last card's text over the closing card's: both read
    at once, muddy (Agentanbud, 1:08, 2026-10-30). Two cards meet in a cut."""
    video = _video(tmp_path / "screen.mp4")
    render.record_card(tmp_path, 3.0, "Regi: Hermes", "#ff0000")
    render.record_card(tmp_path, 5.0, "Second card", "#00ff00")
    voice = tmp_path / "line.m4a"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    str(voice)], check=True)
    result = render.segmented_render(
        video, [(1.0, None, 0.0), (3.0, voice, 2.0), (5.0, voice, 2.0)], tmp_path / "video.mp4",
        None, render.RenderConfig(cta_url="https://example.org"), lead_in=False,
    )
    parts = {part["step"]: part for part in result.timeline if part["kind"] == "step"}
    outro = next(part for part in result.timeline if part["kind"] == "outro")
    first, second = parts[1], parts[2]
    assert second["start"] == pytest.approx(first["start"] + first["duration"] - render.CUT_FRAME, abs=0.06)
    assert outro["start"] == pytest.approx(second["start"] + second["duration"] - render.CUT_FRAME, abs=0.06)
