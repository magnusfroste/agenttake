"""One bad beat should not cost a full re-record. Filming "Hermes runs
Nordbrygg" (2026-10-24/25), six 30-minute takes were redone for a single
missing or flawed beat, and a missing intro was spliced in from another take
by hand. A beat recorded again at the end of a session now moves to where it
belongs; Escape and a card with no ref no longer need one; a live answer can
be waited for offscreen; a scroll says what it moved; and finish answers
before an MCP client gives up on a long render."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest

ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")


@pytest.fixture(scope="module")
def mods(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("beats")
    os.environ["REEL_OUTPUT_DIR"] = str(tmp)
    os.environ["REEL_DB_PATH"] = str(tmp / "beats.db")
    from reel_studio import server, store

    store.init_schema()
    return server, store


def _video(path, seconds=9, size="320x176"):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size={size}:rate=25", "-t", str(seconds), "-pix_fmt", "yuv420p",
                    str(path)], check=True)
    return path


def _new_session(store, session_id, directory):
    store.create_session(session_id, "https://example.com", "en-US-JennyNeural", 320, 176,
                         str(directory), "edge", None, None, "", "", "#1f2a44", "", "Learn more", "none")


# ---- Reorder -------------------------------------------------------------

def test_a_beat_recorded_last_plays_where_it_is_placed():
    from reel_studio import render

    steps = [(1.0, None, 0.0), (3.0, None, 0.0), (5.0, None, 0.0), (7.0, None, 0.0)]
    recorded, _ = render.plan_segments(steps, 9.0)
    moved, _ = render.plan_segments(steps, 9.0, order=[0, 2, 3, 1])
    assert [s.step_index for s in recorded] == [None, 0, 1, 2, 3]
    # The lead-in stays first; the beat recorded last plays second.
    assert [s.step_index for s in moved] == [None, 0, 3, 1, 2]
    # Each step keeps its own footage: the window after it in the recording.
    by_step = {s.step_index: (s.offset, s.source_duration) for s in recorded}
    assert all(by_step[s.step_index] == (s.offset, s.source_duration) for s in moved)


@ffmpeg
def test_a_moved_step_takes_its_camera_and_glide_with_it(tmp_path, monkeypatch):
    from reel_studio import render
    from reel_studio.camera import Camera

    video = _video(tmp_path / "screen.mp4")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:size=320x600",
                    "-frames:v", "1", str(tmp_path / "glide-7000.jpg")], check=True)
    (tmp_path / render.GLIDES_FILE).write_text(json.dumps([{
        "offset": 7.0, "image": "glide-7000.jpg", "start": 0, "distance": 300,
        "view_height": 176, "ms": 1000, "scale": 1.0,
    }]))
    camera = Camera(320, 176, 1.0)
    assert camera.move(5.0, 2.0, 160, 88)  # the push-in of the step at 5 s

    calls, frames = [], {}
    real_segment, real_pan, real_zoompan = render._render_video_segment, render._render_glide_pan, Camera.zoompan

    def segment(video_path, offset, *args, **kwargs):
        calls.append(("video", offset))
        return real_segment(video_path, offset, *args, **kwargs)

    def pan(image, glide, *args, **kwargs):
        calls.append(("pan", glide["offset"]))
        return real_pan(image, glide, *args, **kwargs)

    def zoompan(self, offset, *args, **kwargs):
        frames[offset] = real_zoompan(self, offset, *args, **kwargs)
        return frames[offset]

    monkeypatch.setattr(render, "_render_video_segment", segment)
    monkeypatch.setattr(render, "_render_glide_pan", pan)
    monkeypatch.setattr(Camera, "zoompan", zoompan)

    result = render.segmented_render(
        video, [(1.0, None, 0.0), (3.0, None, 0.0), (5.0, None, 0.0), (7.0, None, 1.0)],
        tmp_path / "video.mp4", None, render.RenderConfig(transitions="cuts"), camera,
        lead_in=False, glides=[None, None, None, 0.0], order=[0, 3, 1, 2],
    )
    # Played: 1 s, then the push-in recorded at 5 s, then the glide recorded
    # at 7 s, then the step recorded at 3 s.
    assert calls == [("video", 1.0), ("video", 5.0), ("pan", 7.0), ("video", 3.0)]
    assert [part["step"] for part in result.timeline] == [0, 2, 3, 1]
    # The camera move went with its step, and did not leak onto the step
    # that now follows it.
    assert frames[5.0] is not None and "zoompan" in frames[5.0]
    assert frames[3.0] is None and frames[1.0] is None
    assert result.path.is_file()


@ffmpeg
def test_edit_step_moves_a_beat_and_rerender_plays_that_order(mods, monkeypatch, tmp_path):
    server, store = mods
    session_dir = tmp_path / "s-move"
    session_dir.mkdir()
    _video(session_dir / "screen.mp4", size="64x36")
    shutil.copy(session_dir / "screen.mp4", session_dir / "video.mp4")
    _new_session(store, "s-move", session_dir)
    for offset in (1.0, 3.0, 6.0):
        store.append_step("s-move", "click", "button:x", "https://example.com", "t", "", 0.0, offset,
                          None, True, None)
    store.finish_session("s-move", str(session_dir / "video.mp4"), None, 9.0)

    bad = asyncio.run(server.edit_step("s-move", 2, position=3))
    assert bad["error"]["type"] == "invalid_edit"
    moved = asyncio.run(server.edit_step("s-move", 2, position=0))
    assert moved["ok"] and moved["order"] == [2, 0, 1] and moved["step"]["position"] == 0
    # Positions count from the order there is now.
    again = asyncio.run(server.edit_step("s-move", 0, position=2))
    assert again["order"] == [2, 1, 0]
    assert [s["idx"] for s in store.play_order(store.get_session("s-move")["steps"])] == [2, 1, 0]

    captured = {}

    def fake_render(source, steps, video_path, *args, **kwargs):
        captured["steps"], captured["order"] = steps, kwargs.get("order")
        return SimpleNamespace(warnings=[], duration=8.0, timeline=[])

    monkeypatch.setattr(server, "segmented_render", fake_render)
    assert asyncio.run(server.rerender("s-move"))["ok"]
    # The steps stay in recording order (the storyboard counts them so);
    # order says where each plays.
    assert [offset for offset, _, _ in captured["steps"]] == [1.0, 3.0, 6.0]
    assert captured["order"] == [2, 1, 0]


def test_a_finished_session_without_moves_plays_as_recorded(mods):
    _, store = mods
    steps = [{"idx": 0, "position": None}, {"idx": 1, "position": None}, {"idx": 2, "position": None}]
    assert [s["idx"] for s in store.play_order(steps)] == [0, 1, 2]


def test_the_director_is_told_how_to_fix_a_beat(mods):
    server, _ = mods
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    assert "position" in tools["edit_step"].inputSchema["properties"]
    assert "position=" in server.DIRECTOR_PROMPT and "finish_status" in server.DIRECTOR_PROMPT
    assert "finish_status" in tools


# ---- The action contract -------------------------------------------------

def test_a_long_wait_is_for_offscreen_steps():
    from pydantic import ValidationError
    from reel_studio.schema import Action, action_json_schema

    assert Action(type="click", ref="r", wait_timeout_ms=600000, offscreen=True).wait_timeout_ms == 600000
    assert Action(type="click", ref="r", wait_timeout_ms=60000).wait_timeout_ms == 60000
    with pytest.raises(ValidationError, match="offscreen"):
        Action(type="click", ref="r", wait_timeout_ms=240000)
    with pytest.raises(ValidationError):
        Action(type="click", ref="r", wait_timeout_ms=600001, offscreen=True)
    assert action_json_schema()["properties"]["wait_timeout_ms"]["maximum"] == 600000


def test_keys_and_clicks_need_no_ref_in_the_contract():
    from reel_studio.schema import ACTION_CONTRACT, Action

    assert Action(type="press_key", text="Escape").ref is None
    assert Action(type="click", text="Nordbrygg weekly report").ref is None
    assert "without one the key goes to the page" in ACTION_CONTRACT
    assert "text and no ref" in ACTION_CONTRACT
    assert "600000" in ACTION_CONTRACT and "scrolled" in ACTION_CONTRACT


def test_a_scroll_that_moved_nothing_says_so():
    from reel_studio.engine import scroll_report

    moved = scroll_report(800, 800, "document")
    assert moved == {"scrolled": {"pixels": 800, "requested": 800, "element": "document"}}
    still = scroll_report(800, 0, None, glide=True)
    assert still["scrolled"]["pixels"] == 0
    assert "Nothing scrolled" in still["warning"] and "still page" in still["warning"]


# ---- In a browser ----------------------------------------------------------

def _browser_session(page, directory):
    from reel_studio.engine import BrowserSession
    from reel_studio.render import RenderConfig

    return BrowserSession(
        "s-browser", "about:blank", 800, 600, "en-US-JennyNeural", "edge", None, RenderConfig(),
        directory, 0, None, None, None, page.context, page, None, time.monotonic(), refs_stale=False,
    )


def _in_browser(html, scenario):
    playwright = pytest.importorskip("playwright.async_api")

    async def run():
        async with playwright.async_playwright() as p:
            try:
                browser = await p.chromium.launch()
            except Exception as exc:  # no browser in this environment
                pytest.skip(f"chromium unavailable: {exc}")
            page = await browser.new_page(viewport={"width": 800, "height": 600})
            await page.set_content(html)
            try:
                return await scenario(page)
            finally:
                await browser.close()

    return asyncio.run(run())


def test_escape_and_a_card_with_no_ref_are_reached_without_one(tmp_path):
    from reel_studio.schema import Action

    html = """<body>
      <ul id="menu" role="menu"><li>Rename</li></ul>
      <div class="card" style="cursor:pointer" onclick="document.title = 'opened'">
        <h3>Nordbrygg weekly report</h3><p>3 agents at work</p></div>
      <script>document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') document.getElementById('menu').remove(); });</script></body>"""

    async def scenario(page):
        session = _browser_session(page, tmp_path)
        escape, _ = await session.act(Action(type="press_key", text="Escape", settle_ms=0))
        menu = await page.locator("#menu").count()
        card, _ = await session.act(Action(type="click", text="Nordbrygg weekly report", settle_ms=0))
        title = await page.title()
        missing, _ = await session.act(Action(type="click", text="No such card", settle_ms=0))
        return escape, menu, card, title, missing

    escape, menu, card, title, missing = _in_browser(html, scenario)
    assert escape["ok"] and menu == 0
    assert card["ok"] and title == "opened"
    assert missing["error"]["type"] == "text_not_found"


def test_a_scroll_reports_the_pixels_it_moved_and_what_moved(tmp_path):
    from reel_studio.schema import Action

    html = """<body style="margin:0"><div style="height:3000px">a long page</div>
      <div role="dialog" aria-label="Block library"
           style="position:fixed;right:0;top:0;width:300px;height:400px;overflow-y:auto">
        <div style="height:2000px">blocks</div></div></body>"""

    async def scenario(page):
        session = _browser_session(page, tmp_path)
        await page.mouse.move(100, 300)  # over the page, not the dialog
        wheel, _ = await session.act(Action(type="scroll", dy=400, settle_ms=800))
        glide, _ = await session.act(Action(type="scroll", dy=500, ms=300, settle_ms=0))
        to_end, _ = await session.act(Action(type="scroll", dy=99999, ms=300, settle_ms=0))
        stuck, _ = await session.act(Action(type="scroll", dy=200, ms=300, settle_ms=0))
        return wheel, glide, to_end, stuck

    wheel, glide, to_end, stuck = _in_browser(html, scenario)
    assert wheel["scrolled"]["element"] == "document" and wheel["scrolled"]["pixels"] == 400
    # A glide moves an open dialog's list before the page behind it.
    assert glide["scrolled"] == {"pixels": 500, "requested": 500, "element": "div (Block library)"}
    assert to_end["scrolled"]["pixels"] == 1100  # stops at the end, and says so
    assert stuck["scrolled"]["pixels"] == 0 and "Nothing scrolled" in stuck["warning"]


# ---- finish while a long render runs --------------------------------------

class _RenderingSession:
    """A session whose render takes a while."""

    def __init__(self, directory, video, seconds):
        self.voice = "en-US-JennyNeural"
        self.directory, self.video, self.seconds = directory, video, seconds
        self.timeline = [(1.0, None, 0.0)] * 3
        self.refs, self.refs_stale, self.last_activity, self.t0 = {}, False, 0.0, 0.0
        self.camera = None
        self.renders = 0

    def touch(self):
        self.last_activity = time.monotonic()

    async def finish(self):
        self.renders += 1
        (self.directory / ".segments-x").mkdir(exist_ok=True)
        (self.directory / ".segments-x" / "segment-0000.mp4").write_bytes(b"")
        await asyncio.sleep(self.seconds)
        return self.video

    async def abort(self):
        pass


@ffmpeg
def test_a_long_render_answers_with_a_job_that_finish_status_follows(mods, monkeypatch, tmp_path):
    server, store = mods
    monkeypatch.setattr(server, "FINISH_WAIT_SECONDS", 0.05)
    _new_session(store, "s-slow", tmp_path)
    store.append_step("s-slow", "wait", None, "https://example.com", "t", "Hello.", 0, 0.0, None, True,
                      None, "en-US-JennyNeural")
    session = _RenderingSession(tmp_path, _video(tmp_path / "video.mp4", seconds=2, size="64x36"), 0.5)
    server.sessions["s-slow"] = session

    async def scenario():
        first = await server.finish("s-slow")
        again = await server.finish("s-slow")
        rendering = await server.finish_status("s-slow")
        act = await server.act("s-slow", {"type": "wait", "ms": 1})
        reaped = await server.reap_idle_sessions(now=time.monotonic() + 10_000)
        while server._finishing("s-slow"):
            await asyncio.sleep(0.05)
        return first, again, rendering, json.loads(act.content[0].text), reaped, await server.finish_status("s-slow")

    try:
        first, again, rendering, act, reaped, done = asyncio.run(scenario())
    finally:
        server.sessions.pop("s-slow", None)
    assert first["status"] == "rendering" and first["job_id"] == "s-slow"
    assert "finish_status" in first["hint"]
    assert again["status"] == "rendering" and session.renders == 1  # a retry joins, never renders twice
    assert rendering["parts_rendered"] == 1 and rendering["parts_planned"] == 3
    assert act["error"]["type"] == "session_finishing"
    assert "s-slow" not in reaped  # a render is not an idle session
    assert done["ok"] and done["status"] == "done"
    assert done["video_path"].endswith("video.mp4") and "review" in done
    assert store.get_session("s-slow")["status"] == "finished"
    assert asyncio.run(server.finish_status("nope"))["error"]["type"] == "unknown_job"


@ffmpeg
def test_a_quick_render_answers_with_the_video_as_before(mods, monkeypatch, tmp_path):
    server, store = mods
    _new_session(store, "s-quick", tmp_path)
    store.append_step("s-quick", "wait", None, "https://example.com", "t", "Hello.", 0, 0.0, None, True,
                      None, "en-US-JennyNeural")
    server.sessions["s-quick"] = _RenderingSession(
        tmp_path, _video(tmp_path / "video.mp4", seconds=2, size="64x36"), 0.0)
    result = asyncio.run(server.finish("s-quick"))
    assert result["status"] == "done", result
    assert result["video_path"].endswith("video.mp4")
    assert "review" in result and "s-quick" not in server.sessions
