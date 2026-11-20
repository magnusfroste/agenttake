"""A canvas game has nothing to name: Pianot's keys and Lilla Staden's BÖRJA!
are drawn, so a click by ref or text found nothing (skolappar.com,
2026-11-20). A tap presses a point of the screen, and a system beat can get
past a start screen offscreen and play the page on camera."""
from __future__ import annotations

import asyncio
import types

import pytest
from pydantic import ValidationError

from reel_studio import storyboard as boards
from reel_studio.engine import TOUCH_MARK_JS, BrowserSession
from reel_studio.schema import Action, action_json_schema

GAME = "https://pianot.example/"


def check(beats):
    async def probe(url):
        return {"url": url, "status": 200, "content_type": "text/html"}
    return asyncio.run(boards.check_storyboard(beats, probe=probe, secrets=[]))


def test_a_tap_needs_points_on_the_screen():
    tap = Action(type="tap", points=[{"x": 0.5, "y": 0.9, "ms": 1200}])
    assert tap.points[0].ms == 1200
    with pytest.raises(ValidationError):
        Action(type="tap")
    with pytest.raises(ValidationError):
        Action(type="tap", points=[{"x": 1.4, "y": 0.5}])
    assert "points" in action_json_schema()["properties"]


def test_a_canvas_game_beat_needs_no_text_to_find():
    beat = {"shot": "system", "url": GAME, "start": ["Spela fritt", {"x": 0.5, "y": 0.93}],
            "taps": [{"x": 0.2, "y": 0.8}, {"x": 0.3, "y": 0.8}, {"x": 0.4, "y": 0.8, "ms": 900}],
            "line": "Pianot: noterna glider ner, och barnen spelar Blinka lilla stjärna."}
    report = check([beat])
    assert ("missing_field" not in {item["category"] for item in report["checklist"]})
    ops = boards.recipe(report["beats"][0])
    kinds = [(op["op"], op.get("action", {}).get("type")) for op in ops]
    assert kinds == [("act", "goto"), ("consent", None), ("act", "click"), ("act", "tap"), ("shot", None),
                     ("act", "tap")]
    goto, _, start_click, start_tap, shot, play = ops
    assert goto["action"]["settle_ms"] == 2000 and "wait_for_text" not in goto["action"]
    assert start_click["action"] == {"type": "click", "text": "Spela fritt", "offscreen": True, "settle_ms": 1000}
    assert start_tap["action"]["offscreen"] and start_tap["action"]["points"] == [{"x": 0.5, "y": 0.93, "ms": 0}]
    # The whole screen: a press near an edge stays in the frame.
    assert shot["zoom"] == 1.0 and shot["focus_text"] is None
    assert play["narration"] == beat["line"] and not play["action"].get("offscreen")
    assert [p["x"] for p in play["action"]["points"]] == [0.2, 0.3, 0.4]
    # Spread over the line, and done before it ends.
    seconds = boards.line_seconds(beat["line"], "sv")
    gaps = play["action"]["ms"] * 2 / 1000 + 0.9
    assert 0.25 <= play["action"]["ms"] / 1000 and gaps < seconds
    Action(**play["action"])


def test_a_callout_on_a_beat_with_no_text_must_be_fixed():
    report = check([{"shot": "system", "url": GAME, "callout": "Pianot", "line": "Pianot."}])
    assert (1, "nothing_to_point_at") in {(i.get("beat"), i["category"]) for i in report["checklist"]
                                          if i["severity"] == "fix"}


def test_taps_on_a_page_with_text_keep_the_framing_on_it():
    beat = {"shot": "system", "url": GAME, "wait_for_text": "GAS", "taps": [{"x": 0.07, "y": 0.85, "ms": 2500}],
            "line": "Håll in gasen uppför backen."}
    ops = boards.recipe(check([beat])["beats"][0])
    assert ops[-1]["action"]["type"] == "tap" and ops[-1]["narration"] == beat["line"]
    assert any(op["op"] == "shot" and op["focus_text"] == "GAS" for op in ops)


def test_the_touch_dot_shows_where_a_press_lands():
    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium unavailable: {exc}")
            tab = await browser.new_page(viewport={"width": 1000, "height": 500})
            await tab.set_content("<canvas id=c width=1000 height=500></canvas><script>"
                                  "window.pressed=[];document.getElementById('c').addEventListener('pointerdown',"
                                  "e=>pressed.push([e.clientX,e.clientY]))</script>")
            session = types.SimpleNamespace(page=tab)
            session._touch_mark = types.MethodType(BrowserSession._touch_mark, session)
            await session._touch_mark(250.0, 400.0, True)
            dot = await tab.evaluate("(() => { const d = document.getElementById('__take_touch');"
                                     " return [d.style.left, d.style.top, d.style.opacity,"
                                     " getComputedStyle(d).pointerEvents]; })()")
            await tab.mouse.click(250, 400)
            pressed = await tab.evaluate("window.pressed")
            await browser.close()
            return dot, pressed

    dot, pressed = asyncio.run(run())
    assert dot == ["250px", "400px", "1", "none"]
    # The dot never takes the press itself.
    assert pressed == [[250, 400]]
    assert "pointerEvents" in TOUCH_MARK_JS
