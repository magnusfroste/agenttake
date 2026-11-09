"""A beat says how close the camera goes. The recipes fixed it, so the review
could report text cut off or too small and the director had nothing to turn:
take 6's proof beat had no setting at all, and an agent found `zoom` moved the
page and not the frame."""
from __future__ import annotations

import pytest

from reel_studio import storyboard as boards


def shot_of(beat):
    return next(op for op in boards.recipe(beat) if op["op"] == "shot")


CASES = [
    ({"shot": "source", "url": "https://e.example", "wait_for_text": "H", "line": "l"}, 1.12),
    ({"shot": "highlight", "url": "https://e.example", "text": "words", "source": "https://e.example", "line": "l"}, 1.8),
    ({"shot": "system", "url": "https://e.example", "wait_for_text": "H", "line": "l"}, 1.3),
    ({"shot": "live_answer", "url": "https://e.example", "input_text": "q", "line": "l"}, 1.5),
    ({"shot": "cta", "url": "https://e.example", "wait_for_text": "H", "line": "l"}, 1.12),
]


@pytest.mark.parametrize("beat,default", CASES)
def test_a_beat_keeps_its_default_and_can_set_its_own(beat, default):
    assert shot_of(beat)["zoom"] == default
    assert shot_of({**beat, "camera": 2.1})["zoom"] == 2.1


def test_the_page_zoom_and_the_camera_are_two_things():
    beat = {"shot": "system", "url": "https://e.example", "wait_for_text": "H", "line": "l", "zoom": 1.4, "camera": 1.1}
    ops = boards.recipe(beat)
    assert any(op.get("action", {}).get("type") == "set_zoom" and op["action"]["text"] == "1.4" for op in ops)
    assert next(op for op in ops if op["op"] == "shot")["zoom"] == 1.1


def test_a_camera_outside_its_range_is_refused():
    for bad in (0.9, 2.6):
        beats, items = boards.parse_storyboard([{"shot": "system", "url": "https://e.example", "wait_for_text": "H",
                                                 "line": "l", "camera": bad}])
        assert any(item["severity"] == "fix" for item in items)


def test_a_camera_on_a_shot_with_no_move_is_flagged():
    _, items = boards.parse_storyboard([{"shot": "card", "text": "T", "line": "l", "camera": 1.5}])
    assert [item["category"] for item in items if item["severity"] == "warn"] == ["camera_not_used"]
    _, items = boards.parse_storyboard([{"shot": "system", "url": "https://e.example", "wait_for_text": "H", "line": "l", "camera": 1.5}])
    assert "camera_not_used" not in [item["category"] for item in items]


def test_changing_the_camera_makes_a_shot_beat_a_changed_one():
    from reel_studio import server

    beat = {"shot": "system", "line": "l", "url": "u"}
    assert server._beat_identity(beat) != server._beat_identity({**beat, "camera": 1.1})


def test_the_schema_offers_it():
    assert boards.beats_json_schema()["properties"]["camera"]["maximum"] == 2.5
