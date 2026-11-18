"""How the pros move: a spring camera with two kinds of move, the title over
the first shot, and a pointer that glides and pulses.

Research 2026-11-18 (product-video tools' defaults and editors' rules): a
push-in settles in about half a second on a critically damped spring, not a
second of ease-in-out; a small, quick emphasis push is a move of its own; the
first seconds are the hook, so no title card spends them; the pointer moves
like a hand and a click is a faint pulse, not a glow.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from reel_studio import camera as cam
from reel_studio import graphics
from reel_studio import pickup
from reel_studio import storyboard as boards
from reel_studio.camera import (
    EASE_SECONDS, EMPHASIS_PUSH, EMPHASIS_SECONDS, MAX_ZOOM, Camera, drift_share, spring_share,
)
from reel_studio.pointer import pointer_script
from reel_studio.render import RenderConfig, _has_title_card
from reel_studio import server

PAGE = "https://app.example/admin"
LINE = "Overnight it qualified three leads for you."


# The spring ------------------------------------------------------------------

def test_a_move_settles_in_well_under_a_second():
    assert EASE_SECONDS <= 0.7


def test_the_spring_starts_gently_and_does_most_of_the_way_early():
    assert spring_share(0.0) == pytest.approx(0.0)
    assert spring_share(1.0) == pytest.approx(1.0)
    # No jolt: it starts slower than an even move, and picks up speed.
    assert spring_share(0.04) < 0.04
    assert spring_share(0.02) < spring_share(0.04) / 3
    # Most of the way in the first half or so (Cap: 90 % at ~0.32 of 0.5 s).
    assert spring_share(0.6) > 0.9
    values = [spring_share(step / 50) for step in range(51)]
    assert values == sorted(values)


def test_a_long_move_is_a_drift_not_a_spring():
    # A drift is even: half way at half the time.
    assert drift_share(0.5) == pytest.approx(0.5)
    assert "exp(" in cam._ease_term("it", 1.0, EASE_SECONDS)
    assert "exp(" not in cam._ease_term("it", 1.0, cam.DRIFT_SECONDS)


def test_the_render_expression_is_the_same_curve():
    term = cam._ease_term("T", 0.0, 0.5)
    for u in (0.1, 0.3, 0.6, 0.9):
        value = eval(term.replace("clip", "_clip").replace("exp", "_exp"),
                     {"_clip": lambda v, lo, hi: min(max(v, lo), hi), "_exp": __import__("math").exp, "T": u * 0.5})
        assert value == pytest.approx(spring_share(u), abs=1e-4)


def test_a_short_line_still_gets_a_move_rather_than_a_cut():
    # 0.8 s used to be the shortest move; a 1.2 s line now still pushes in.
    assert cam.landing_ease(EASE_SECONDS, 1.2, "cut") > 0


# Emphasis ------------------------------------------------------------------

def test_an_emphasis_pushes_in_a_little_and_quickly_then_lets_go():
    camera = Camera(1920, 1080)
    camera.move(1.0, 1.5, 900, 500)
    camera.emphasize(3.0, 2.0, {"x": 1000, "y": 600, "width": 200, "height": 40})
    keys = camera.emphasis_moves()
    push, back = keys
    assert push["t"] == 3.0 and push["ease"] == EMPHASIS_SECONDS
    assert push["zoom"] == pytest.approx(1.5 * EMPHASIS_PUSH)
    # Anchored on the words: the point under them keeps its place on screen.
    level = 1.5 * EMPHASIS_PUSH
    focus_x, focus_y = 1100, 620
    assert push["cx"] == pytest.approx(focus_x + (900 - focus_x) * 1.5 / level, abs=0.1)
    before = (focus_x - 900) * 1.5 + 960
    after = (focus_x - push["cx"]) * level + 960
    assert after == pytest.approx(before, abs=0.5)
    assert (focus_y - push["cy"]) * level == pytest.approx((focus_y - 500) * 1.5, abs=0.5)
    assert back["t"] == 5.0 and back["zoom"] == 1.5 and back["cx"] == 900


def test_an_emphasis_is_a_small_move_not_a_new_shot():
    assert 1.05 <= EMPHASIS_PUSH <= 1.15
    assert EMPHASIS_SECONDS <= 0.3


def test_an_emphasis_does_not_let_go_when_the_shot_moves_on_anyway():
    camera = Camera(1920, 1080)
    camera.move(1.0, 1.5, 900, 500)
    camera.emphasize(3.0, 2.0)
    camera.move(4.5, 1.0, ease=0.0)
    assert [key["t"] for key in camera.emphasis_moves()] == [3.0]


def test_an_emphasis_never_goes_past_the_closest_framing():
    camera = Camera(1920, 1080)
    camera.move(1.0, MAX_ZOOM, 900, 500)
    camera.emphasize(2.0, 1.0)
    assert camera.emphasis_moves() == []


def test_the_render_camera_has_the_emphasis_and_the_shot_keeps_its_own():
    camera = Camera(1920, 1080)
    camera.move(1.0, 1.3, 900, 500)
    camera.emphasize(2.0, 1.5)
    rendered = camera.with_hold_moves([])
    assert [key["t"] for key in rendered.keys] == [1.0, 2.0, 3.5]
    assert [key["t"] for key in camera.keys] == [1.0]
    assert rendered.zoompan(0.0, 5.0, (1920, 1080)) is not None


def test_an_emphasis_survives_a_save_and_a_pickup(tmp_path: Path):
    camera = Camera(1920, 1080)
    camera.move(1.0, 1.3, 900, 500)
    camera.emphasize(2.0, 1.5)
    camera.save(tmp_path)
    assert Camera.load(tmp_path).emphasis == camera.emphasis
    session = tmp_path / "session"
    session.mkdir()
    Camera(1920, 1080).save(session)
    pickup.take_in(session, tmp_path, 10.0, camera)
    assert [entry["t"] for entry in Camera.load(session).emphasis] == [12.0]


def system(**fields):
    return {"shot": "system", "url": PAGE, "wait_for_text": "Leads", "line": LINE, **fields}


def categories(beats):
    return [item["category"] for item in boards.parse_storyboard(beats)[1]]


def test_a_plan_takes_an_emphasis_the_line_says():
    assert categories([system(emphasis="three leads")]) == []
    assert "emphasis_not_in_line" in categories([system(emphasis="five deals")])
    ops = boards.recipe(boards.parse_storyboard([system(emphasis="three leads")])[0][0])
    graphics_op = next(op for op in ops if op["op"] == "graphics")
    assert graphics_op["emphasis"] == "three leads" and graphics_op["target_text"] == "Leads"


def test_an_emphasis_on_a_shot_without_a_frame_is_flagged():
    beat = {"shot": "card", "text": "Three leads", "line": LINE, "emphasis": "three leads"}
    assert "emphasis_not_used" in categories([beat])


def test_storyboard_moves_are_quick():
    assert boards.PUSH_SECONDS <= 0.8 and boards.PAN_SECONDS <= 0.9


# The title -----------------------------------------------------------------

def test_a_title_no_longer_makes_a_card_unless_asked():
    assert not _has_title_card(RenderConfig(title="FlowWink"))
    assert _has_title_card(RenderConfig(title="FlowWink", title_at="card"))
    assert not _has_title_card(RenderConfig(title="", title_at="card"))


def test_the_title_is_laid_over_the_first_footage_for_three_seconds():
    parts = [{"kind": "step", "step": 0, "start": 0.0, "duration": 0.8},
             {"kind": "step", "step": 1, "start": 0.8, "duration": 5.0}]
    cue = graphics.title_cue(parts, "FlowWink", "Your agent runs the night shift")
    assert cue == {"kind": "title", "start": 0.8, "end": 3.8, "text": "FlowWink",
                   "subtitle": "Your agent runs the night shift"}
    assert graphics.title_cue(parts, "  ") is None


def test_the_title_is_drawn_top_left_with_its_subtitle():
    cue = {"kind": "title", "start": 0.0, "end": 3.0, "text": "FlowWink", "subtitle": "Night shift"}
    lines = graphics.ass_events([cue], 1920, 1080)
    assert any(",Title," in line and "FlowWink" in line for line in lines)
    assert any(",TitleSub," in line and "Night shift" in line for line in lines)
    styles = graphics.ass_styles(1920, 1080, "Inter", graphics.look_of(None, None))
    assert any(style.startswith("Style: Title,") for style in styles)


def test_an_old_film_keeps_its_title_card_and_a_new_one_has_it_over():
    assert server._title_at({"start_args": {"title": "x"}}) == "card"
    assert server._title_at({"start_args": {"title": "x", "title_at": "over"}}) == "over"
    assert server._render_config("T", "", "", "", "", "none").title_at == "over"
    assert server._render_config("T", "", "", "", "", "none", title_at="card").title_at == "card"


# The pointer -----------------------------------------------------------------

def test_the_pointer_is_drawn_in_the_page_and_follows_its_mouse():
    script = pointer_script(True)
    assert '"arrow": true' in script
    for needed in ("mousemove", "mousedown", "requestAnimationFrame", "window.top !== window"):
        assert needed in script
    # The pulse is drawn even when no arrow is shown.
    assert '"arrow": false' in pointer_script(False)


def test_the_screen_pointer_is_never_recorded():
    import inspect
    from reel_studio import engine
    assert "draw_mouse=False" in inspect.getsource(engine.BrowserSession.create)
