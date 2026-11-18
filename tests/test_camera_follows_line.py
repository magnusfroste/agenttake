"""The camera follows the line.

FlowWink take 2 (2026-11-17): every system beat cut to wide and pushed in for
2.5 s from the line's first word, whatever the line's length. Under lines of
1.4-2.2 s, ten shots in a row, the camera was moving for 87% of the stretch
and landed after each line was over; the review called the film ready. The
person watching said the moves did not follow the voice, and that the same
push-in came several times in a row.

A push-in has now landed by 40% of its line, a line too short for a move gets
a cut, two beats on one page are one shot with a pan between them, and the
review measures both complaints.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from reel_studio import review
from reel_studio import storyboard as boards
from reel_studio.camera import LAND_SHARE, MIN_MOVE_SECONDS, Camera, landing_ease
from reel_studio.engine import BrowserSession

PAGE = "https://app.example/admin"


def system(**fields):
    return {"shot": "system", "url": PAGE, "wait_for_text": "Orders", "line": "An order comes in overnight.", **fields}


def shot_of(beat, **kwargs):
    return next(op for op in boards.recipe(beat, **kwargs) if op["op"] == "shot")


# The rule ------------------------------------------------------------------

def test_a_move_has_landed_by_its_share_of_the_line():
    assert landing_ease(1.5, 3.0) == pytest.approx(LAND_SHARE * 3.0)
    # A long line does not stretch the move.
    assert landing_ease(1.5, 10.0) == 1.5


def test_a_line_too_short_for_a_move_gets_a_cut_or_the_shortest_move():
    # A move takes at most LAND_SHARE of its line: under 0.9 s that is no move.
    assert landing_ease(1.5, 0.9) == 0.0
    assert landing_ease(1.5, 0.9, "move") == MIN_MOVE_SECONDS
    # A 1.6 s line ("It drafts the replies,") now has room for a quick move.
    assert landing_ease(1.5, 1.6) == pytest.approx(0.64)
    # With no line to follow, the move is what was asked for.
    assert landing_ease(1.5, 0.0) == 1.5


def test_the_engine_ties_the_pending_move_to_the_line_it_plays_under():
    def seconds(land, line, ease=1.5):
        return BrowserSession._move_seconds(SimpleNamespace(pending_ease=ease, pending_land=land), line)

    assert seconds("cut", 0.9) == 0.0           # "Done."
    assert seconds("cut", 1.62) == pytest.approx(0.648)  # "It drafts the replies,"
    assert seconds("cut", 3.11) == pytest.approx(1.244)
    assert seconds("move", 0.9) == MIN_MOVE_SECONDS
    # A shot directed by hand keeps the length it asked for.
    assert seconds(None, 1.62, ease=2.5) == 2.5


# The recipes ---------------------------------------------------------------

def test_a_push_in_is_tied_to_its_line_and_a_drift_is_left_slow():
    close = shot_of(system(camera=2.0))
    assert (close["move_seconds"], close["land"]) == (boards.PUSH_SECONDS, "cut")
    # system's default is a push-in too.
    assert shot_of(system())["land"] == "cut"
    drift = shot_of({"shot": "source", "url": PAGE, "wait_for_text": "Headline", "line": "l"})
    assert (drift["move_seconds"], drift["land"]) == (3.0, None)
    # A source the director took close is a push-in like any other.
    assert shot_of({"shot": "source", "url": PAGE, "wait_for_text": "Headline", "line": "l", "camera": 2.0})["land"] == "cut"


def test_a_beat_can_say_how_its_camera_moves():
    assert shot_of(system(move="cut"))["move_seconds"] == 0.0
    push = shot_of(system(move="push"))
    assert (push["move_seconds"], push["land"]) == (boards.PUSH_SECONDS, "move")
    assert "move" in boards.beats_json_schema()["properties"]
    _, items = boards.parse_storyboard([{"shot": "card", "text": "T", "line": "l", "move": "cut"}])
    assert [item["category"] for item in items] == ["move_not_used"]
    _, items = boards.parse_storyboard([system(move="sideways")])
    assert [item["category"] for item in items] == ["invalid_beat"]


def test_a_highlight_on_the_page_before_pans_and_never_cuts():
    beats, _ = boards.parse_storyboard([
        {"shot": "source", "url": PAGE, "wait_for_text": "Headline", "line": "one"},
        {"shot": "highlight", "text": "a short quote", "line": "two"},
    ])
    assert shot_of(beats[1])["land"] == "move"
    # Opening its own page, it may cut.
    assert shot_of({"shot": "highlight", "url": PAGE, "text": "q", "source": PAGE, "line": "l"})["land"] == "cut"


# Two beats on one page -----------------------------------------------------

def test_a_second_beat_on_the_page_stays_and_pans():
    beats, items = boards.parse_storyboard([
        system(line="It knows what is yours to decide."),
        system(wait_for_text="requires approval", line="Before it spends your money, it stops and asks."),
    ])
    assert not items and not beats[0].get("stays") and beats[1]["stays"]
    ops = boards.recipe(beats[1])
    assert ops[0]["op"] == "stay" and ops[0]["url"] == PAGE
    assert [op["action"]["type"] for op in ops[0]["open"] if op["op"] == "act"] == ["goto"]
    shot = shot_of(beats[1])
    assert shot["stays"] and (shot["move_seconds"], shot["land"]) == (boards.PAN_SECONDS, "move")
    # A retake is shot after the film has moved on: it opens the page itself.
    retake = boards.recipe(beats[1], retake=True)
    assert retake[0]["op"] == "act" and retake[0]["action"]["type"] == "goto" and not shot_of(beats[1], retake=True)["stays"]


def test_another_page_another_zoom_or_an_asked_for_push_opens_the_page():
    def second(**fields):
        beats, _ = boards.parse_storyboard([system(line="one"), system(line="two", **fields)])
        return beats[1]

    assert not second(url="https://app.example/orders").get("stays")
    assert not second(zoom=1.5).get("stays")
    assert not second(move="push").get("stays")
    assert not second(move="cut").get("stays")
    # A card the film cuts to as a page of its own leaves the product page.
    beats, _ = boards.parse_storyboard([
        system(line="one"),
        {"shot": "card", "text": "x" * 200, "line": "two"},
        system(line="three"),
    ])
    assert not beats[2].get("stays")


def test_stay_with_no_page_to_stay_on_is_refused():
    _, items = boards.parse_storyboard([system(move="stay")])
    assert [(item["severity"], item["category"]) for item in items] == [("fix", "nothing_to_stay_on")]
    _, items = boards.parse_storyboard([{"shot": "cta", "url": PAGE, "line": "l", "move": "stay"}])
    assert ("fix", "nothing_to_stay_on") in [(item["severity"], item["category"]) for item in items]


class Live:
    """A session that says where its browser is and how far a scroll moved the page."""

    def __init__(self, url, moved_px=0):
        self.page = SimpleNamespace(url=url)
        self.moved_px = moved_px
        self.acts: list[str] = []
        self.shots: list[dict] = []

    async def dismiss_consent(self, *args):
        return None

    async def set_shot(self, framing, zoom, ref, text, move_seconds, **kwargs):
        self.shots.append({"move_seconds": move_seconds, "land": kwargs.get("land")})
        return {}


def run(live, beat):
    async def act(session_id, action, narration):
        live.acts.append(action["type"])
        if action["type"] == "goto":
            live.page.url = action["url"]
        return {"ok": True, **({"moved_px": live.moved_px} if action["type"] == "scroll_to_text" else {})}, None

    return asyncio.run(boards.run_ops(live, "s", boards.recipe(beat), act))


def staying_beat():
    return boards.parse_storyboard([system(line="one"), system(wait_for_text="Stock", line="two")])[0][1]


def test_a_staying_beat_does_not_open_the_page_it_is_on():
    live = Live(PAGE + "/")
    outcome = run(live, staying_beat())
    assert outcome["ok"] and "goto" not in live.acts
    assert live.shots == [{"move_seconds": boards.PAN_SECONDS, "land": "move"}]


def test_a_staying_beat_opens_the_page_when_the_browser_is_elsewhere():
    live = Live("https://app.example/orders")
    outcome = run(live, staying_beat())
    assert outcome["ok"] and live.acts[0] == "goto" and live.page.url == PAGE
    assert any("opened it" in note for note in outcome["notes"])


def test_a_page_that_scrolled_to_the_words_is_a_cut_not_a_pan():
    live = Live(PAGE, moved_px=640)
    run(live, staying_beat())
    assert live.shots == [{"move_seconds": 0.0, "land": None}]


# The review ----------------------------------------------------------------

def narrated(idx, offset, seconds, **fields):
    return {"idx": idx, "offset_seconds": offset, "narration_duration": seconds, "narration_text": f"line {idx}",
            "ok": True, "action_type": "annotate", **fields}


def film(shots):
    """A film of beats that each cut to wide and then move: (line seconds, move seconds, zoom)."""
    keys, steps = [], []
    for number, (line, move, zoom) in enumerate(shots):
        start = number * 10.0
        keys.append({"t": start, "zoom": 1.0, "cx": 960.0, "cy": 540.0, "ease": 0.0})
        if zoom > 1.0:
            keys.append({"t": start + 2.0, "zoom": zoom, "cx": 700.0, "cy": 400.0, "ease": move})
        steps.append(narrated(number, start + 2.0, line))
    return Camera(1920, 1080, 1.0, keys), steps


def found(camera, steps, parts=None):
    return [(item["category"], item["severity"], item["location"]) for item in review.move_findings(camera, steps, parts)]


def test_a_move_that_outlasts_its_line_is_reported():
    camera, steps = film([(1.6, 2.5, 2.0)])
    finding = review.move_findings(camera, steps)[0]
    assert (finding["category"], finding["severity"], finding["location"]) == ("camera_lands_late", "medium", "step[0]")
    assert "over before the camera has landed" in finding["message"]
    assert finding["measured"] == {"move_seconds": 2.5, "line_seconds": 1.6, "still_under_line": 0.0, "zoom": 2.0}


def test_a_move_that_leaves_little_of_its_line_still_is_a_low_finding():
    camera, steps = film([(3.1, 2.5, 2.1)])
    assert found(camera, steps) == [("camera_lands_late", "low", "step[0]")]


def test_a_move_tied_to_its_line_a_cut_and_a_drift_are_not_reported():
    for line, move, zoom in ((3.0, landing_ease(1.5, 3.0), 2.0),   # landed at 40%
                             (1.6, 0.0, 2.0),                       # a cut
                             (2.0, 3.0, 1.12),                      # a drift near wide
                             (4.0, 2.5, 1.5)):                      # a second and a half of it still
        camera, steps = film([(line, move, zoom)])
        assert found(camera, steps) == [], (line, move, zoom)


def test_the_same_push_in_shot_after_shot_is_reported():
    camera, steps = film([(4.0, 1.5, 2.0)] * 3)
    finding = review.move_findings(camera, steps)[0]
    assert (finding["category"], finding["severity"]) == ("same_move_in_a_row", "low")
    assert finding["measured"] == {"shots": 3, "steps": [0, 1, 2]}
    camera, steps = film([(4.0, 1.5, 2.0)] * 5)
    assert found(camera, steps) == [("same_move_in_a_row", "medium", "step[0]")]


def test_a_cut_or_a_wide_beat_between_them_breaks_the_run():
    for between in ((1.6, 0.0, 2.0), (3.0, 0.0, 1.0)):
        camera, steps = film([(4.0, 1.5, 2.0), (4.0, 1.5, 2.0), between, (4.0, 1.5, 2.0), (4.0, 1.5, 2.0)])
        assert found(camera, steps) == [], between


def test_the_run_is_counted_in_the_films_order_not_the_recordings():
    # Recorded: push, push, cut, and a retake of the cut as a push, placed third.
    camera, steps = film([(4.0, 1.5, 2.0), (4.0, 1.5, 2.0), (1.6, 0.0, 2.0), (4.0, 1.5, 2.0)])
    steps[2]["offscreen"] = True     # the take the retake replaced
    parts = [{"kind": "step", "step": number} for number in range(3)]
    assert found(camera, steps, parts) == [("same_move_in_a_row", "low", "step[0]")]


def test_flowwink_take_two_would_not_have_been_called_ready():
    # Its midsection: five phrases of 1.4-2.2 s, each under a 2.5 s push-in to 2x.
    camera, steps = film([(1.62, 2.5, 2.0), (1.38, 2.5, 2.1), (2.17, 2.5, 2.1), (1.93, 2.5, 2.1), (1.85, 2.5, 2.0)])
    assert found(camera, steps) == [("camera_lands_late", "medium", f"step[{n}]") for n in range(5)] + [
        ("same_move_in_a_row", "medium", "step[0]")]


def test_a_shot_that_opens_with_a_cut_is_framed_on_its_first_frame():
    # The key sits where the step's footage starts. As a step in time it was
    # still wide at it=0: one frame of the whole page before the close shot.
    camera = Camera(1920, 1080, 1.0, [
        {"t": 28.0, "zoom": 1.0, "cx": 960.0, "cy": 540.0, "ease": 0.0},
        {"t": 30.0, "zoom": 2.0, "cx": 700.0, "cy": 400.0, "ease": 0.0},
    ])
    expression = camera.zoompan(30.0, 34.0, (1920, 1080))
    assert "z='max(1,1.0000+1.0000*1)'" in expression
    assert "clip(" not in expression.split(":x=")[0]
    # The segment before it ends before the key and is still wide.
    assert camera.zoompan(20.0, 29.0, (1920, 1080)) is None or "+1.0000" not in camera.zoompan(20.0, 29.0, (1920, 1080))
