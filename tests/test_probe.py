"""A director can scout a page without a session: every point of the Kotten
film (2026-11-24) was found with an operator's Playwright scripts, and an MCP
agent has none. probe shows the page gridded in screen fractions, rings each
press, lists what can be pressed and reads the game's state."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from reel_studio import probe as scout

GAME = """<!doctype html><html><body style="margin:0">
<button id="go" style="position:fixed;left:400px;top:200px;width:200px;height:100px">BÖRJA</button>
<canvas id="c" width="1000" height="500" style="display:block"></canvas>
<script>
const SCENES = {bakery: {rollDone: false, moves: 0}};
var G = {scene: "intro"};
function drawKotten() {}
document.getElementById("go").onclick = (e) => { G.scene = "bakery"; e.target.remove(); };
const c = document.getElementById("c");
let down = false;
c.addEventListener("mousedown", () => { down = true; });
c.addEventListener("mouseup", () => { down = false; });
c.addEventListener("mousemove", (e) => {
  if (down && G.scene === "bakery") { SCENES.bakery.moves++; if (SCENES.bakery.moves > 10) SCENES.bakery.rollDone = true; }
});
</script></body></html>"""


async def _page(p):
    browser = await p.chromium.launch()
    page = await browser.new_page(viewport={"width": 1000, "height": 500})
    await page.set_content(GAME)
    return browser, page


def _chromium(run):
    async def wrapped():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            try:
                return await run(p)
            except Exception as exc:
                if "Executable doesn't exist" in str(exc) or "chromium" in str(exc).lower() and "launch" in str(exc).lower():
                    pytest.skip(f"chromium unavailable: {exc}")
                raise
    return asyncio.run(wrapped())


def test_what_can_be_pressed_is_listed_in_screen_fractions(tmp_path):
    async def run(p):
        browser, page = await _page(p)
        found = await scout.elements(page)
        names = await scout.page_globals(page)
        await browser.close()
        return found, names

    found, names = _chromium(run)
    button = next(item for item in found if item["kind"] == "button")
    assert button["text"] == "BÖRJA" and button["x"] == 0.5 and button["y"] == 0.5
    assert button["w"] == 0.2 and button["h"] == 0.2
    assert any(item["kind"] == "canvas" for item in found)
    # var and function globals are found; a const is read by name instead.
    assert "G" in names and "drawKotten" in names and "SCENES" not in names
    assert "document" not in names and "fetch" not in names


def test_a_game_state_is_read_even_a_top_level_const(tmp_path):
    async def run(p):
        browser, page = await _page(p)
        values = await scout.read_values(page, ["SCENES.bakery", "G.scene", "NOPE.x", "Object.keys(SCENES)"])
        await browser.close()
        return values

    values = _chromium(run)
    assert values["SCENES.bakery"] == '{"rollDone":false,"moves":0}'
    assert values["G.scene"] == '"intro"'
    assert values["Object.keys(SCENES)"] == '["bakery"]'
    assert "ReferenceError" in values["NOPE.x"]


def test_the_still_has_the_grid_and_the_presses_and_leaves_the_page_as_it_was(tmp_path):
    async def run(p):
        browser, page = await _page(p)
        before = await page.evaluate("document.body.innerHTML")
        still = await scout.gridded_still(page, tmp_path / "s.jpg", [{"x": 0.5, "y": 0.5, "to_x": 0.8, "to_y": 0.2}])
        after = await page.evaluate("document.body.innerHTML")
        gone = await page.evaluate("!document.getElementById('__take_probe')")
        await browser.close()
        return still, before == after, gone

    still, same, gone = _chromium(run)
    assert still and Path(still).stat().st_size > 5000 and Path(still).read_bytes()[:2] == b"\xff\xd8"
    assert same and gone


def test_a_press_mark_is_a_point_or_a_drag_never_a_text():
    from reel_studio.schema import TapPoint

    assert scout.press_mark("BÖRJA") is None
    assert scout.press_mark({"x": 0.2, "y": 0.3}) == {"x": 0.2, "y": 0.3, "to_x": None, "to_y": None}
    drag = TapPoint(x=0.1, y=0.2, to_x=0.5, to_y=0.6, ms=900)
    assert scout.press_mark(drag) == {"x": 0.1, "y": 0.2, "to_x": 0.5, "to_y": 0.6}


class _Live:
    """Just enough of a BrowserSession around a real page: goto, click by
    text, and taps that press and drag with the mouse."""

    def __init__(self, page, directory: Path):
        self.page, self.directory = page, directory
        directory.mkdir(parents=True, exist_ok=True)
        self.aborted = False

    async def act(self, action, narration=""):
        if action.type == "goto":
            return {"ok": True}, None
        if action.type == "click":
            await self.page.get_by_text(action.text).first.click()
            return {"ok": True}, None
        if action.type == "tap":
            width, height = await self.page.evaluate("[innerWidth, innerHeight]")
            for point in action.points:
                await self.page.mouse.move(point.x * width, point.y * height)
                await self.page.mouse.down()
                if point.is_drag:
                    await self.page.mouse.move(point.to_x * width, point.to_y * height, steps=20)
                await self.page.mouse.up()
            return {"ok": True}, None
        return {"ok": False, "error": {"type": "unsupported", "message": action.type}}, None

    async def page_text(self):
        return await self.page.evaluate("document.body.innerText")

    async def main_text(self):
        return ""

    async def dismiss_consent(self, *args):
        return None

    async def abort(self):
        self.aborted = True


def test_probe_presses_reads_and_answers_with_a_gridded_still(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REEL_DB_PATH", str(tmp_path / "probe.db"))
    from reel_studio import server, store

    store.init_schema()
    server.sessions.clear()
    made = []

    async def run(p):
        browser, page = await _page(p)

        async def create(*args, **options):
            live = _Live(page, tmp_path / "live")
            made.append(live)
            return live

        monkeypatch.setattr(server.BrowserSession, "create", create)
        each = await server.probe("https://game.example/", presses=[
            "BÖRJA", {"x": 0.1, "y": 0.8, "to_x": 0.9, "to_y": 0.8, "ms": 600}],
            read=["SCENES.bakery.rollDone", "G.scene"], stills="each")
        end = await server.probe("https://game.example/", read=["G.scene"])
        await browser.close()
        return each, end

    each, end = _chromium(run)
    body = each.structuredContent
    assert body["ok"] and [step["ok"] for step in body["presses"]] == [True, True]
    assert body["presses"][0]["read"]["G.scene"] == '"bakery"'
    assert body["read"] == {"SCENES.bakery.rollDone": "true", "G.scene": '"bakery"'}
    assert "fractions of the screen" in body["coordinates"] and "expect" in body["next"]
    # A still after opening and after each press, as images in the reply.
    assert [item.type for item in each.content] == ["text", "image", "image", "image"]
    assert all(live.aborted and not live.directory.exists() for live in made)
    assert [item.type for item in end.content] == ["text", "image"]


def test_probe_refuses_what_it_cannot_do(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_OUTPUT_DIR", str(tmp_path))
    from reel_studio import server

    server.sessions.clear()
    assert asyncio.run(server.probe("ftp://x")).structuredContent["error"]["type"] == "invalid_url"
    many = [{"x": 0.5, "y": 0.5}] * (scout.PROBE_PRESSES_MAX + 1)
    assert asyncio.run(server.probe("https://x.example/", presses=many)).structuredContent["error"]["type"] == "too_many"
    server.sessions["s"] = object()
    try:
        assert asyncio.run(server.probe("https://x.example/")).structuredContent["error"]["type"] == "busy"
    finally:
        server.sessions.clear()
