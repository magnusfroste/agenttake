"""The review measures the film. Sluss take 5 left Hermes's review as "all 16
ok" with a white page for a second and a half and a line running over the next
page; take 2 said "Most" over a chart reading 48%. A person found those from
the film, so the film is what gets read."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from reel_studio import review
from reel_studio.camera import Camera

W, H = review.DETAIL_WIDTH, review.DETAIL_HEIGHT


def page(seed: int = 0) -> bytes:
    """A frame with detail everywhere; another seed moves all of it."""
    return bytes((((x // 3 + y // 3 + seed) % 2) * 180 + 30) for y in range(H) for x in range(W))


WHITE = bytes([250]) * (W * H)


def part_changed(first: bytes, share: float) -> bytes:
    """first, with this share of its rows inverted."""
    rows = int(H * share)
    return b"".join(
        bytes(255 - value for value in first[y * W:(y + 1) * W]) if y < rows else first[y * W:(y + 1) * W]
        for y in range(H)
    )


def film(*runs: tuple[bytes, float]) -> list[bytes]:
    return [frame for frame, seconds in runs for _ in range(int(seconds * review.SCAN_FPS))]


def steps_and_parts(length: float, kind: str = "wait", line: str = "A line."):
    return [{"idx": 0, "action_type": kind, "offset_seconds": 1.0, "offscreen": False, "narration_text": line}], [
        {"kind": "step", "step": 0, "start": 0.0, "duration": length}
    ]


def categories(findings, name):
    return [finding for finding in findings if finding["category"] == name]


def test_a_second_of_empty_page_is_found_where_it_is():
    steps, parts = steps_and_parts(8.0)
    frames = film((page(), 3.0), (WHITE, 1.0), (page(), 4.0))
    flash = categories(review.film_findings(frames, parts, steps), "blank_flash")
    assert len(flash) == 1 and flash[0]["severity"] == "high"
    assert flash[0]["at_seconds"] == 3.0 and flash[0]["measured"]["seconds"] == 1.0 and flash[0]["fix"]


def test_an_instant_of_white_is_not_a_flash_and_a_card_may_be_plain():
    steps, parts = steps_and_parts(8.0)
    assert not categories(review.film_findings(film((page(), 3.0), (WHITE, 0.25), (page(), 4.75)), parts, steps), "blank_flash")
    steps, parts = steps_and_parts(5.0, kind="card")
    assert not categories(review.film_findings(film((WHITE, 5.0)), parts, steps), "blank_flash")


def test_a_cut_under_a_line_is_found_and_the_dissolve_at_its_end_is_not():
    steps, parts = steps_and_parts(8.0)
    late = review.film_findings(film((page(0), 5.0), (page(1), 3.0)), parts, steps)
    found = categories(late, "picture_changes_under_line")
    assert len(found) == 1 and found[0]["at_seconds"] == 5.0 and found[0]["measured"]["seconds_into_line"] == 5.0
    tail = review.film_findings(film((page(0), 7.5), (page(1), 0.5)), parts, steps)
    assert not categories(tail, "picture_changes_under_line")


def test_the_camera_easing_in_is_not_a_cut_but_a_wrong_page_is():
    steps, parts = steps_and_parts(9.0)
    soft = part_changed(page(), 0.2)
    easing = film((page(), 2.0), (soft, 7.0))
    assert not categories(review.film_findings(easing, parts, steps), "picture_changes_under_line")
    after_it = film((page(), 5.0), (soft, 4.0))
    assert categories(review.film_findings(after_it, parts, steps), "picture_changes_under_line")
    wrong_page = film((page(0), 2.0), (page(1), 7.0))
    assert categories(review.film_findings(wrong_page, parts, steps), "picture_changes_under_line")


@pytest.mark.parametrize("kind", ["scroll", "card"])
def test_a_glide_and_a_card_are_meant_to_move(kind):
    steps, parts = steps_and_parts(8.0, kind=kind)
    assert not categories(review.film_findings(film((page(0), 5.0), (page(1), 3.0)), parts, steps), "picture_changes_under_line")


@pytest.mark.parametrize("seconds,expected", [(6.0, None), (8.0, "low"), (13.0, "medium")])
def test_a_long_still_is_measured(seconds, expected):
    steps, parts = steps_and_parts(14.0)
    found = categories(review.film_findings(film((page(0), seconds), (page(1), 0.25)), parts, steps), "long_still")
    if expected is None:
        assert not found
    else:
        assert found[0]["severity"] == expected and found[0]["measured"]["seconds"] == seconds


def test_a_line_that_never_reached_the_film_is_found():
    steps = [{"idx": 0, "offset_seconds": 4.0, "offscreen": False, "narration_text": "One."},
             {"idx": 1, "offset_seconds": 27.0, "offscreen": True, "narration_text": ""},
             {"idx": 3, "offset_seconds": 97.0, "offscreen": False, "narration_text": "Two."},
             {"idx": 6, "offset_seconds": 140.0, "offscreen": False, "narration_text": "Three."}]
    parts = [{"kind": "lead", "step": None}, {"kind": "step", "step": 0}, {"kind": "outro", "step": None}]
    found = review.lost_step_findings(parts, steps)
    assert [finding["location"] for finding in found] == ["step[3]", "step[6]"] and found[0]["severity"] == "high"
    assert not review.lost_step_findings([], steps)
    all_parts = [{"kind": "step", "step": index} for index in range(3)]
    assert not review.lost_step_findings(all_parts, steps)


def _camera(facts, zoom=1.5, cx=960.0, cy=540.0):
    return Camera(1920, 1080, 1.0, [{"t": 2.0, "zoom": zoom, "cx": cx, "cy": cy, "facts": facts}])


STEP = [{"idx": 4, "action_type": "wait", "offset_seconds": 2.0, "offscreen": False, "ok": True, "narration_text": "The audit log."}]
FOCUS = {"x": 900.0, "y": 500.0, "width": 120.0, "height": 24.0}


def test_a_focus_the_frame_loses_is_found():
    facts = {"focus": {**FOCUS, "x": 1400.0, "width": 400.0}, "lines": 10, "cut_count": 0, "cut": []}
    found = categories(review.shot_findings(_camera(facts), STEP), "focus_cropped")
    assert found and found[0]["severity"] == "high" and found[0]["measured"]["in_frame"] < 0.95
    assert not categories(review.shot_findings(_camera({"focus": FOCUS, "lines": 9, "cut": [], "cut_count": 0}), STEP), "focus_cropped")


def test_text_cut_next_to_the_focus_matters_more_than_text_far_from_it():
    near = {"focus": FOCUS, "lines": 20, "cut_count": 1,
            "cut": [{"text": "verify offline", "side": "right", "top": 500.0, "bottom": 522.0}]}
    found = categories(review.shot_findings(_camera(near), STEP), "text_cut_at_edge")
    assert found[0]["severity"] == "medium" and "verify offline" in found[0]["message"]

    far = {"focus": FOCUS, "lines": 20, "cut_count": 1,
           "cut": [{"text": "a footer", "side": "left", "top": 900.0, "bottom": 920.0}]}
    assert not categories(review.shot_findings(_camera(far), STEP), "text_cut_at_edge")

    most = {"focus": FOCUS, "lines": 20, "cut_count": 12,
            "cut": [{"text": "a line", "side": "left", "top": 900.0, "bottom": 920.0}] * 12}
    assert categories(review.shot_findings(_camera(most), STEP), "text_cut_at_edge")[0]["severity"] == "low"


def test_small_focus_text_is_measured_as_it_lands_in_the_frame():
    small = {"focus": FOCUS, "lines": 5, "cut_count": 0, "cut": [], "font_px": 13.0}
    assert categories(review.shot_findings(_camera(small, zoom=1.2), STEP), "focus_text_small")
    assert not categories(review.shot_findings(_camera(small, zoom=2.0), STEP), "focus_text_small")


def test_camera_facts_survive_the_file(tmp_path):
    _camera({"focus": FOCUS, "lines": 1, "cut_count": 0, "cut": []}).save(tmp_path)
    assert Camera.load(tmp_path).keys[0]["facts"]["focus"] == FOCUS


def _claims(line, screen):
    return review.claim_findings(
        [{"idx": 3, "narration_text": line, "offscreen": False, "ok": True}], {3: screen}
    )


def test_most_over_a_chart_reading_48_percent_is_contradicted():
    findings, _ = _claims("Most stayed in the house.", "176 · 48% Stayed in the house 108 · 29% cloud 85 · 23% stopped")
    assert categories(findings, "claim_contradicted")[0]["severity"] == "high"
    findings, _ = _claims("Most stayed in the house.", "61% stayed in the house")
    assert not categories(findings, "claim_contradicted")


def test_the_most_expensive_is_not_a_claim_about_a_share():
    findings, to_check = _claims("This is the most expensive part.", "23% of it")
    assert not findings and not to_check


@pytest.mark.parametrize("line,screen,missing", [
    ("47% of users use personal apps.", "47% of genAI users using personal AI apps", []),
    ("82 percent of pastes.", "82 percent of pastes coming from", []),
    ("47% of users.", "Nothing about it here", ["47%"]),
    ("In 2026 it changed.", "no year here", []),
    ("NIS2 applies.", "no number here", []),
    ("It costs 3,000 euros.", "it costs 3 000 euros", []),
])
def test_a_number_has_to_be_on_the_screen(line, screen, missing):
    findings, _ = _claims(line, screen)
    assert [number for finding in categories(findings, "claim_not_on_screen") for number in finding["measured"]["numbers"]] == missing


def test_a_share_in_words_is_listed_with_the_screens_percentages():
    findings, to_check = _claims("Nearly half stayed in the house.", "48% stayed, 29% cloud")
    assert not findings
    assert to_check == [{"step": 3, "line": "Nearly half stayed in the house.", "on_screen_percentages": [48.0, 29.0]}]
    _, swedish = _claims("Nästan hälften stannade.", "48 %")
    assert swedish[0]["on_screen_percentages"] == [48.0]


@pytest.fixture(scope="module")
def mods(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("measured")
    os.environ["REEL_OUTPUT_DIR"] = str(tmp)
    os.environ["REEL_DB_PATH"] = str(tmp / "measured.db")
    from reel_studio import server, store

    store.init_schema()
    return server, store, tmp


def test_a_finding_says_which_beat_it_is_on_and_when(mods):
    server, store, tmp = mods
    sid = "s-place"
    store.create_session(sid, "https://example.com", "en-US-JennyNeural", 320, 176, str(tmp),
                         "edge", None, None, "", "", "#1f2a44", "", "Learn more", "none", "cuts")
    store.save_storyboard("plan-place", [{"number": 3, "label": "audit", "line": "x",
                                          "state": {"attempts": [{"steps": [4, 5]}]}}], [], 60, sid)
    steps = [{"idx": 5, "offset_seconds": 2.0, "offscreen": False}]
    parts = [{"kind": "step", "step": 0, "start": 12.5, "duration": 4.0}]
    findings = [{"location": "step[5]", "at_seconds": None}, {"location": "session", "at_seconds": None}]
    server._place_findings(findings, sid, steps, parts)
    assert findings[0] == {"location": "step[5]", "at_seconds": 12.5, "beat": 3, "label": "audit"}
    assert findings[1] == {"location": "session"}


def test_a_narrated_step_keeps_what_the_screen_said(mods, tmp_path):
    server, store, tmp = mods
    sid = "s-text"
    store.create_session(sid, "https://example.com", "en-US-JennyNeural", 320, 176, str(tmp_path),
                         "edge", None, None, "", "", "#1f2a44", "", "Learn more", "none", "cuts")

    async def page_text():
        return "48% stayed"

    store.append_step(sid, "wait", None, "u", "t", "A line.", 0.0, 1.0, None, True, None)
    session = SimpleNamespace(directory=str(tmp_path), page_text=page_text)
    asyncio.run(server._remember_page_text(session, sid, SimpleNamespace(type="wait", text=None)))
    store.append_step(sid, "card", None, "u", "t", "Another.", 0.0, 2.0, None, True, None)
    asyncio.run(server._remember_page_text(session, sid, SimpleNamespace(type="card", text="Measure first.")))
    assert review.read_page_texts(tmp_path) == {0: "48% stayed", 1: "Measure first."}


media = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")


@media
def test_scanning_a_real_film_gives_one_frame_per_quarter_second(tmp_path):
    video = tmp_path / "film.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x176:rate=25",
                    "-t", "3", "-pix_fmt", "yuv420p", str(video)], check=True)
    frames = review.scan_frames(video)
    assert len(frames) == 3 * review.SCAN_FPS and {len(frame) for frame in frames} == {W * H}


@media
def test_a_window_films_margin_is_left_out_of_the_scan(tmp_path):
    video = tmp_path / "film.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=white:size=320x176:rate=25",
                    "-t", "1", "-pix_fmt", "yuv420p", str(video)], check=True)
    frames = review.scan_frames(video, margin=0.04)
    assert frames and {len(frame) for frame in frames} == {W * H}


@media
def test_the_contact_sheet_reply_carries_the_picture(mods, monkeypatch):
    server, store, tmp = mods
    sid = "s-sheet"
    directory = tmp / sid
    directory.mkdir()
    video = directory / "video.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
                    "-t", "7", "-pix_fmt", "yuv420p", str(video)], check=True)
    store.create_session(sid, "https://example.com", "en-US-JennyNeural", 640, 360, str(directory),
                         "edge", None, None, "", "", "#1f2a44", "", "Learn more", "none", "cuts")
    store.finish_session(sid, str(video), None, 7.0)
    result = asyncio.run(server.contact_sheet(sid))
    assert [item.type for item in result.content] == ["text", "image"]
    assert result.structuredContent["ok"] and result.content[1].data
