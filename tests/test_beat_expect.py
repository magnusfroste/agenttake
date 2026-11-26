"""A beat can say what its presses must have done, and a rehearsal runs as a
shoot would. Kotten take 3 (skolappar.com, 2026-11-24) lost its bakery: the
dough never rolled, nothing said so until a contact sheet showed a still oven,
and the rehearsal had passed it on an idle page. Rehearsed alone, the oven beat
found the game on the dentist, because its bun was made in the beat before."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from reel_studio import storyboard as boards

LILL = "https://lillstaden.example/"
BEATS = [
    {"shot": "system", "label": "ljud", "url": "https://ljud.example/", "line": "Rim.",
     "taps": [{"x": 0.25, "y": 0.69}]},
    {"shot": "system", "label": "bageriet", "url": LILL, "line": "Man kavlar degen.",
     "start": [{"x": 0.5, "y": 0.85}, "OK"],
     "taps": [{"x": 0.267, "y": 0.8457, "to_x": 0.55, "to_y": 0.68, "ms": 1200}],
     "expect": "SCENES.bakery.rollDone"},
    {"shot": "system", "label": "ugnen", "url": LILL, "line": "Kotten gräddar bullarna.",
     "taps": [{"x": 0.47, "y": 0.68, "to_x": 0.79, "to_y": 0.58, "ms": 1000}],
     "expect": "SCENES.bakery.bake > 0"},
]


def _parsed():
    beats, items = boards.parse_storyboard([dict(beat) for beat in BEATS])
    assert not [item for item in items if item["severity"] == "fix"], items
    return beats


def test_expect_is_read_after_the_presses():
    beats = _parsed()
    ops = boards.recipe(beats[1])
    assert ops[-1] == {"op": "expect", "js": "SCENES.bakery.rollDone", "seconds": boards.EXPECT_SECONDS}
    assert ops[-2]["action"]["type"] == "tap"
    assert all(op["op"] != "expect" for op in boards.recipe(beats[0]))


def test_expect_off_a_system_shot_or_too_long_is_flagged():
    _, items = boards.parse_storyboard([{"shot": "card", "text": "Hej", "line": "Hej.", "expect": "x"}])
    assert "expect_not_used" in {item["category"] for item in items}
    long = dict(BEATS[1], expect="a" * (boards.EXPECT_MAX_CHARS + 1))
    _, items = boards.parse_storyboard([long])
    assert "long_expect" in {item["category"] for item in items}


def test_a_beat_that_stays_is_brought_to_its_page_state_first():
    beats = _parsed()
    assert beats[2].get("stays") and not beats[1].get("stays")
    assert boards.page_state_ops(beats, beats[1]) == []
    state = boards.page_state_ops(beats, beats[2])
    acts = [op["action"] for op in state if op["op"] == "act"]
    assert acts[0]["type"] == "goto" and acts[0]["url"] == LILL
    # The opener's start presses, then its taps: all offscreen, the drag kept.
    assert [a.get("text") or a["points"][0].get("to_x") or a["points"][0]["x"] for a in acts[1:]] == \
        [0.5, "OK", 0.55]
    assert all(a["offscreen"] for a in acts)
    retake = boards.beat_ops(beats, beats[2], retake=True)
    assert retake[:len(state)] == state
    # Then the beat itself goes on from that page instead of opening it again.
    assert retake[len(state)]["op"] == "stay"
    # A first take is shot in order and needs none of it.
    assert boards.beat_ops(beats, beats[2]) == boards.recipe(beats[2])


def test_a_rehearsal_goes_on_from_the_beat_before_or_replays_it():
    beats = _parsed()
    assert boards.rehearsal_ops(beats[2], beats=beats, continues=True)[0]["op"] == "stay"
    replayed = boards.rehearsal_ops(beats[2], beats=beats)
    assert replayed[0]["action"]["type"] == "goto" and replayed[0]["action"]["url"] == LILL
    assert len(replayed) > len(boards.recipe(beats[2]))
    # Without the plan it opens the page afresh, as before.
    assert boards.rehearsal_ops(beats[2]) == boards.recipe(beats[2], retake=True)


class _Page:
    def __init__(self, answers):
        self.answers = list(answers)

    async def evaluate(self, script):
        assert "SCENES.bakery.rollDone" in script
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def test_an_unmet_expect_fails_the_beat_and_says_its_value(monkeypatch):
    monkeypatch.setattr(boards, "EXPECT_POLL_SECONDS", 0.0)
    session = SimpleNamespace(page=_Page([{"met": False, "value": "false"}]))
    ops = [{"op": "expect", "js": "SCENES.bakery.rollDone", "seconds": 0.05}]
    outcome = asyncio.run(boards.run_ops(session, "s", ops, None))
    assert not outcome["ok"] and outcome["reason"].startswith("expect_unmet: 'SCENES.bakery.rollDone' was false")
    session = SimpleNamespace(page=_Page([{"met": False, "value": "false"}, {"met": True, "value": "true"}]))
    assert asyncio.run(boards.run_ops(session, "s", ops, None))["ok"]


def test_expect_reads_the_game_in_a_real_page_even_where_eval_is_forbidden():
    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium unavailable: {exc}")
            tab = await browser.new_page()
            await tab.set_content(
                "<meta http-equiv='Content-Security-Policy' content=\"script-src 'unsafe-inline'\">"
                "<script>const SCENES = {bakery: {rollDone: false}};"
                "setTimeout(() => { SCENES.bakery.rollDone = true; }, 300);</script>")
            late = await boards.read_expect(tab, "SCENES.bakery.rollDone", 3.0, 0.05)
            never = await boards.read_expect(tab, "SCENES.bakery.bake > 0", 0.2, 0.05)
            broken = await boards.read_expect(tab, "NOPE.x", 0.1, 0.05)
            await browser.close()
            return late, never, broken

    late, never, broken = asyncio.run(run())
    assert late[0] and 0.2 <= late[2] < 2.0
    assert never[:2] == (False, "false")
    assert not broken[0] and "ReferenceError" in broken[1]


def test_a_rehearsal_runs_on_a_slowed_page_by_default(monkeypatch):
    from reel_studio import server

    monkeypatch.delenv("REEL_REHEARSAL_CPU_SLOWDOWN", raising=False)
    assert server.rehearsal_cpu_slowdown() == 4.0
    monkeypatch.setenv("REEL_REHEARSAL_CPU_SLOWDOWN", "1")
    assert server.rehearsal_cpu_slowdown() == 1.0
    assert asyncio.run(server._slow_down(SimpleNamespace(page=None), 1.0)) is False
    # A page that cannot be slowed (no Chromium devtools) is rehearsed as it is.
    assert asyncio.run(server._slow_down(SimpleNamespace(page=SimpleNamespace()), 4.0)) is False


def test_setup_runs_a_page_hook_offscreen_before_the_camera_rolls():
    """Klockan asks for a random hour; its own hook fixes it, so the drag on
    camera sets the time the line says (skolappar video 1, 2026-11-26)."""
    from reel_studio import storyboard as boards

    beat = {"shot": "system", "url": "https://klockan.example/", "setup": "window.__kl.setTask(7, 0)",
            "taps": [{"x": 0.3, "y": 0.6, "to_x": 0.37, "to_y": 0.66}], "line": "Ställ klockan på sju."}
    beats, items = boards.parse_storyboard([beat])
    assert not [item for item in items if item["severity"] == "fix"], items
    ops = boards.recipe(beats[0])
    kinds = [op["op"] for op in ops]
    assert "setup" in kinds and kinds.index("setup") < kinds.index("shot")
    assert ops[kinds.index("setup")]["js"] == "window.__kl.setTask(7, 0)"

    class Page:
        def __init__(self, answer):
            self.answer, self.ran = answer, []

        async def evaluate(self, script):
            self.ran.append(script)
            return self.answer

    class Session:
        def __init__(self, answer):
            self.page = Page(answer)

    async def never(*_):
        raise AssertionError("no action should run")

    import asyncio
    setup_only = [op for op in ops if op["op"] == "setup"]
    ok = asyncio.run(boards.run_ops(Session({"ok": True}), "s", setup_only, never))
    assert ok["ok"]
    failed = asyncio.run(boards.run_ops(Session({"ok": False, "error": "TypeError: __kl is undefined"}), "s",
                                        setup_only, never))
    assert not failed["ok"] and failed["reason"].startswith("setup_failed")
    # Only a system shot has a page to set up.
    _, items = boards.parse_storyboard([{"shot": "card", "text": "A", "line": "A.", "setup": "x()"}])
    assert "setup_not_used" in {item["category"] for item in items}
